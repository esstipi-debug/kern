"""S4 inventory-aware SEO priority (Linchpin 3.0 PR-21, plan section 8 "Track B --
SEO"): cross-references three ALREADY-EXISTING engine outputs into ONE monthly
product-page action plan -- push / cut / hold. Deliberately zero new
dependencies (the plan's S4 row is first in Fase D precisely because it is pure
cross-referencing, not new computation):

  - ``src.classification.classify_portfolio`` (via ``jobs.abc_xyz_job``) --
    ABC (importance) / XYZ (predictability).
  - ``src.excess_obsolete.classify_excess_obsolete`` (via
    ``jobs.excess_obsolete_job``'s stock prep) -- healthy / excess / dead, plus
    the ``SkuStock.days_since_last_sale`` evidence that justified it.
  - ``jobs.forecast_job`` -- the per-SKU next-period point forecast.

This module invents NO new demand or inventory-health computation (Golden Rule
1: ``src/`` funcs stay pure, playbooks compose). The only new logic here is a
business RULE that maps three already-computed numbers to an action:

  - A-class + E&O healthy + forecast trending up -> **push** (increase content
    investment / promotion on that product's page).
  - E&O status DEAD (per ``src.excess_obsolete``'s own threshold) -> **cut**, a
    301-redirect / deindex RECOMMENDATION. This is flagged
    ``requires_human_signoff=True`` and is never auto-applied: no CMS /
    storefront writeback connector exists in this repo (``src/writeback.py``'s
    connectors are Odoo and Excel only), so this module only recommends.
  - Everything else (B/C-class, excess-but-not-dead, flat/declining/unproven
    forecast) -> **hold** (no change this cycle).

A SKU present in only one of ABC-XYZ / E&O is EXCLUDED from the action plan
and reported by product_id + reason (Golden Rule 14 -- no silent caps); it
never falls out of the plan silently. A SKU missing from (or too short in) the
forecast input still gets an action (E&O + ABC-XYZ are the two REQUIRED join
keys) but its trend reads ``insufficient_signal`` rather than a guessed
direction -- this module never fabricates "up"/"down" when the forecast
tool's own numbers don't clearly support it.

Trend (up / down / flat / insufficient_signal) is read off two numbers that
already exist elsewhere -- the forecast tool's point forecast
(``SkuForecast.forecast``) vs the SAME SKU's ABC-XYZ mean historical demand
(``SkuClassification.mean_demand``) -- never a new demand model.

**Not registered as an ``scm_agent`` Tool.** Like ``jobs.integrated_plan``,
this playbook's natural shape needs TWO file shapes (a demand-history CSV for
ABC-XYZ + forecast, and a stock snapshot CSV for E&O) that don't fit the
registry's single-``data_path`` Tool contract; ``prepare()`` follows
``jobs.integrated_plan``'s ``params['stock_path']`` convention instead. Every
sub-step reuses the SAME already-registered job's own ``prepare``/``run`` (
``jobs.abc_xyz_job``, ``jobs.excess_obsolete_job``'s stock prep,
``jobs.forecast_job``) rather than re-deriving any of their math.

QA (``verify``/``seo_priority_passed``, matching ``jobs/qa.py``'s
``verify_*``/``*_passed`` naming convention): every SKU missing from ABC-XYZ or
E&O is reported, not silently dropped; every "cut" recommendation carries a
citable E&O status + days-since-last-sale + dead_threshold_days and is flagged
for human sign-off; every action's trend is one of the four honest labels
(never a bare guess).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from jobs import abc_xyz_job, forecast_job
from jobs.excess_obsolete_job import prepare_records as _prepare_stock_records
from jobs.forecast_job import SkuForecast
from src.classification import SkuClassification
from src.excess_obsolete import DEAD, HEALTHY, SkuEO, SkuStock, classify_excess_obsolete
from src.export import write_summary_csv

PUSH = "push"
CUT = "cut"
HOLD = "hold"

TREND_UP = "up"
TREND_DOWN = "down"
TREND_FLAT = "flat"
TREND_INSUFFICIENT = "insufficient_signal"

# Mirror src.excess_obsolete.classify_sku's own defaults so a caller building the
# run() payload by hand (bypassing prepare()) gets the identical threshold this
# module then cites in every "cut" reason -- see run()'s explicit pass-through.
_DEFAULT_TARGET_COVER_DAYS = 90.0
_DEFAULT_DEAD_THRESHOLD_DAYS = 180.0
# A SKU needs at least this many demand periods before its forecast is trusted
# for a trend read (matches jobs.forecast_job's own default min_backtest_periods).
_DEFAULT_MIN_FORECAST_PERIODS = 4
# +/-10% vs the ABC-XYZ mean demand is the "clearly up/down" band; inside it the
# signal is called flat rather than guessed.
_DEFAULT_TREND_THRESHOLD_PCT = 0.10

_ACTION_CSV_COLUMNS = (
    "product_id", "action", "abc", "xyz", "eo_classification", "forecast_trend",
    "requires_human_signoff", "reason",
)
_EXCLUDED_CSV_COLUMNS = ("product_id", "reason")


@dataclass(frozen=True)
class SkuSeoAction:
    """One SKU's monthly SEO action + the citable engine outputs behind it."""

    product_id: str
    action: str                    # push | cut | hold
    abc: str
    xyz: str
    eo_classification: str         # healthy | excess | dead
    trend: str                     # up | down | flat | insufficient_signal
    reason: str                    # citable basis (Golden Rule 7)
    requires_human_signoff: bool


@dataclass(frozen=True)
class ExcludedSku:
    """A SKU missing from one of the two required inputs -- reported, never dropped."""

    product_id: str
    reason: str


@dataclass(frozen=True)
class SeoPriorityReport:
    actions: tuple[SkuSeoAction, ...]
    excluded: tuple[ExcludedSku, ...]
    n_push: int
    n_cut: int
    n_hold: int
    n_excluded: int
    dead_threshold_days: float
    trend_threshold_pct: float
    summary: str


def _fmt_cover(days_of_cover: float) -> str:
    return "inf" if math.isinf(days_of_cover) else f"{days_of_cover:.1f}"


def _forecast_trend(
    forecast_record: SkuForecast | None,
    mean_demand: float,
    *,
    min_periods: int,
    trend_threshold_pct: float,
) -> tuple[str, str]:
    """Read the trend off two already-computed numbers only -- never a new demand
    model. Falls back to ``insufficient_signal`` (never a guessed direction)
    whenever the forecast tool's own output doesn't clearly support one."""
    if forecast_record is None:
        return TREND_INSUFFICIENT, "no forecast tool output for this SKU"
    if not math.isfinite(forecast_record.forecast):
        return TREND_INSUFFICIENT, "forecast tool produced a non-finite forecast"
    if forecast_record.n_periods < min_periods:
        return TREND_INSUFFICIENT, (
            f"only {forecast_record.n_periods} period(s) of history "
            f"(< {min_periods} minimum to trust a trend read)"
        )
    if not math.isfinite(mean_demand) or mean_demand <= 0:
        return TREND_INSUFFICIENT, "no positive ABC-XYZ mean_demand to compare the forecast against"

    pct_change = (forecast_record.forecast - mean_demand) / mean_demand
    detail = (
        f"forecast={forecast_record.forecast:.2f} vs ABC-XYZ mean_demand={mean_demand:.2f} "
        f"({pct_change * 100:+.0f}%)"
    )
    if pct_change >= trend_threshold_pct:
        return TREND_UP, detail
    if pct_change <= -trend_threshold_pct:
        return TREND_DOWN, detail
    return TREND_FLAT, detail


def _assign_action(classification: SkuClassification, eo: SkuEO, trend: str) -> str:
    """The S4 business rule (plan section 8) -- mutually exclusive on the E&O
    axis alone, since a SKU is exactly one of healthy/excess/dead."""
    if eo.classification == DEAD:
        return CUT
    if classification.abc == "A" and eo.classification == HEALTHY and trend == TREND_UP:
        return PUSH
    return HOLD


def _cut_reason(stock: SkuStock, dead_threshold_days: float) -> str:
    """Golden Rule 7: cite the EXACT E&O status + days-since-last-sale that
    justified a cut, and flag it as a recommendation, never an applied change."""
    trigger = (
        f"zero/negative demand (daily_demand={stock.daily_demand:.2f})"
        if stock.daily_demand <= 0
        else f"no sale in {stock.days_since_last_sale:.0f}+ days"
    )
    return (
        f"E&O status=dead ({trigger}; days_since_last_sale={stock.days_since_last_sale:.0f}, "
        f"dead_threshold_days={dead_threshold_days:.0f}). RECOMMENDATION requiring human sign-off "
        "before any 301-redirect / deindex action -- no CMS/storefront writeback connector exists "
        "in this repo; this plan only recommends, it does not apply the change."
    )


def _push_reason(classification: SkuClassification, eo: SkuEO, trend_detail: str) -> str:
    return (
        f"ABC class=A (annual_value={classification.annual_value:,.2f}, "
        f"cumulative_share={classification.cumulative_share:.2f}); E&O status=healthy "
        f"(days_of_cover={_fmt_cover(eo.days_of_cover)}); forecast trend=up ({trend_detail})."
    )


def _hold_reason(classification: SkuClassification, eo: SkuEO, trend: str, trend_detail: str) -> str:
    return (
        f"ABC class={classification.abc}, XYZ class={classification.xyz}; E&O status="
        f"{eo.classification} (days_of_cover={_fmt_cover(eo.days_of_cover)}); forecast trend="
        f"{trend} ({trend_detail}); no change this cycle."
    )


def _reason(
    action: str, classification: SkuClassification, eo: SkuEO, stock: SkuStock,
    dead_threshold_days: float, trend: str, trend_detail: str,
) -> str:
    if action == CUT:
        return _cut_reason(stock, dead_threshold_days)
    if action == PUSH:
        return _push_reason(classification, eo, trend_detail)
    return _hold_reason(classification, eo, trend, trend_detail)


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read the demand-history CSV (feeds ABC-XYZ + the forecast tool) and the
    required ``params['stock_path']`` E&O stock CSV (the SAME file shape
    ``jobs.excess_obsolete_job`` reads) -- then RUN each of the three
    already-existing jobs so this module never re-derives their math, only
    cross-references their output. Mirrors ``jobs.integrated_plan``'s
    multi-file ``params['stock_path']`` convention (see module docstring)."""
    params = params or {}
    stock_path = params.get("stock_path")
    if not stock_path:
        raise ValueError(
            "params['stock_path'] is required: the same stock CSV jobs.excess_obsolete_job "
            "reads (product_id, on_hand, daily_demand [, unit_cost, days_since_last_sale])"
        )

    abc_items = abc_xyz_job.prepare(data_path, params)
    abc_report = abc_xyz_job.run(
        abc_items,
        abc_thresholds=params.get("abc_thresholds", (0.80, 0.95)),
        cv_cuts=params.get("cv_cuts", (0.5, 1.0)),
    )

    stock_payload = _prepare_stock_records(pd.read_csv(stock_path), params)

    series_by_name = forecast_job.prepare(data_path, params)
    forecast_report = forecast_job.run(
        series_by_name,
        holdout_fraction=float(params.get("holdout_fraction", 0.25)),
        min_backtest_periods=int(params.get("min_backtest_periods", 4)),
    )

    return {
        "classifications": list(abc_report.classifications),
        "stocks": stock_payload["stocks"],
        "target_cover_days": stock_payload["target_cover_days"],
        "dead_threshold_days": stock_payload["dead_threshold_days"],
        "forecasts": list(forecast_report.skus),
        "trend_threshold_pct": float(params.get("trend_threshold_pct", _DEFAULT_TREND_THRESHOLD_PCT)),
        "min_forecast_periods": int(params.get("min_forecast_periods", _DEFAULT_MIN_FORECAST_PERIODS)),
    }


def run(payload: dict) -> SeoPriorityReport:
    """Join ABC-XYZ classification with the E&O stock classification on
    product_id, cross the forecast trend in, and assign push/cut/hold.

    ``payload`` (as built by :func:`prepare`, or hand-built for tests):
      - ``classifications``: ``list[SkuClassification]`` (required)
      - ``stocks``: ``list[SkuStock]`` (required -- classified here via the
        SAME ``src.excess_obsolete.classify_excess_obsolete`` the E&O tool
        itself uses, so this module cites the exact same status)
      - ``forecasts``: ``list[SkuForecast]`` (optional; a SKU missing here
        still gets an action, with trend=``insufficient_signal``)
      - ``target_cover_days`` / ``dead_threshold_days``: optional overrides
        (default to ``src.excess_obsolete``'s own defaults)
      - ``trend_threshold_pct`` / ``min_forecast_periods``: optional overrides
    """
    classifications: list[SkuClassification] = payload["classifications"]
    stocks: list[SkuStock] = payload["stocks"]
    forecasts: list[SkuForecast] = payload.get("forecasts") or []
    target_cover_days = float(payload.get("target_cover_days", _DEFAULT_TARGET_COVER_DAYS))
    dead_threshold_days = float(payload.get("dead_threshold_days", _DEFAULT_DEAD_THRESHOLD_DAYS))
    trend_threshold_pct = float(payload.get("trend_threshold_pct", _DEFAULT_TREND_THRESHOLD_PCT))
    min_forecast_periods = int(payload.get("min_forecast_periods", _DEFAULT_MIN_FORECAST_PERIODS))

    eo_lines = classify_excess_obsolete(
        stocks, target_cover_days=target_cover_days, dead_threshold_days=dead_threshold_days,
    )

    # Duplicate product_id in an input resolves to its LAST occurrence (matches
    # jobs.markdown_liquidation_job.resolve_competitor_contexts's precedent).
    class_by_pid = {c.product_id: c for c in classifications}
    stock_by_pid = {s.product_id: s for s in stocks}
    eo_by_pid = {e.product_id: e for e in eo_lines}
    forecast_by_pid = {f.name: f for f in forecasts}

    class_pids = set(class_by_pid)
    eo_pids = set(eo_by_pid)
    joined_pids = sorted(class_pids & eo_pids)

    excluded: list[ExcludedSku] = [
        ExcludedSku(pid, "present in ABC-XYZ classification but missing from the E&O (stock) input")
        for pid in sorted(class_pids - eo_pids)
    ] + [
        ExcludedSku(pid, "present in the E&O (stock) input but missing from ABC-XYZ classification")
        for pid in sorted(eo_pids - class_pids)
    ]
    excluded.sort(key=lambda e: e.product_id)

    actions: list[SkuSeoAction] = []
    for pid in joined_pids:
        classification = class_by_pid[pid]
        eo = eo_by_pid[pid]
        stock = stock_by_pid[pid]
        forecast_record = forecast_by_pid.get(pid)

        trend, trend_detail = _forecast_trend(
            forecast_record, classification.mean_demand,
            min_periods=min_forecast_periods, trend_threshold_pct=trend_threshold_pct,
        )
        action = _assign_action(classification, eo, trend)
        reason = _reason(action, classification, eo, stock, dead_threshold_days, trend, trend_detail)
        actions.append(SkuSeoAction(
            product_id=pid, action=action, abc=classification.abc, xyz=classification.xyz,
            eo_classification=eo.classification, trend=trend, reason=reason,
            requires_human_signoff=(action == CUT),
        ))

    n_push = sum(1 for a in actions if a.action == PUSH)
    n_cut = sum(1 for a in actions if a.action == CUT)
    n_hold = sum(1 for a in actions if a.action == HOLD)

    summary = (
        f"SEO priority plan over {len(actions)} SKU(s) (ABC-XYZ x E&O join): "
        f"{n_push} push, {n_cut} cut (301/deindex RECOMMENDATION, human sign-off required), "
        f"{n_hold} hold."
    )
    if excluded:
        shown = ", ".join(e.product_id for e in excluded[:5]) + ("..." if len(excluded) > 5 else "")
        summary += f" {len(excluded)} SKU(s) excluded for missing input: {shown}."

    return SeoPriorityReport(
        actions=tuple(actions), excluded=tuple(excluded),
        n_push=n_push, n_cut=n_cut, n_hold=n_hold, n_excluded=len(excluded),
        dead_threshold_days=dead_threshold_days, trend_threshold_pct=trend_threshold_pct,
        summary=summary,
    )


def verify(report: SeoPriorityReport) -> list[str]:
    """QA gate (matches ``jobs/qa.py``'s ``verify_*`` naming). Empty list = passed.

    Checks: SKUs are accounted for (never silently dropped), every "cut" has a
    citable E&O reason and is flagged for human sign-off, every trend is one of
    the four honest labels, and a "push" is never assigned without an "up" trend.
    """
    issues: list[str] = []
    if not report.actions and not report.excluded:
        issues.append("no SKUs assessed (empty ABC-XYZ and E&O inputs)")
    if report.n_push + report.n_cut + report.n_hold != len(report.actions):
        issues.append("action counts do not match len(actions)")

    valid_actions = {PUSH, CUT, HOLD}
    valid_trends = {TREND_UP, TREND_DOWN, TREND_FLAT, TREND_INSUFFICIENT}
    for a in report.actions:
        if a.action not in valid_actions:
            issues.append(f"{a.product_id}: invalid action {a.action!r}")
        if a.trend not in valid_trends:
            issues.append(f"{a.product_id}: invalid trend {a.trend!r}")
        if a.action == CUT:
            if "dead" not in a.reason.lower() or "days_since_last_sale" not in a.reason:
                issues.append(f"{a.product_id}: cut recommendation missing a citable E&O reason")
            if not a.requires_human_signoff:
                issues.append(f"{a.product_id}: cut recommendation must require human sign-off")
        if a.action == PUSH and a.trend != TREND_UP:
            issues.append(f"{a.product_id}: push action without an up trend")
        if not a.reason:
            issues.append(f"{a.product_id}: action has no citable reason")

    for e in report.excluded:
        if not e.reason:
            issues.append(f"{e.product_id}: excluded without a reason")

    return issues


def seo_priority_passed(report: SeoPriorityReport) -> bool:
    return not verify(report)


def write_operational(report: SeoPriorityReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable monthly deliverable: one row per joined SKU
    (action + citable reason) plus the excluded SKUs, so nothing drops
    silently (Golden Rule 14). An empty action list still writes a header-only
    CSV (mirrors ``jobs.markdown_liquidation_job.write_operational``)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)

    action_path = d / "seo_priority.csv"
    if not report.actions:
        pd.DataFrame(columns=list(_ACTION_CSV_COLUMNS)).to_csv(action_path, index=False)
        out: dict[str, Path] = {"csv": action_path}
    else:
        action_rows = [
            {
                "product_id": a.product_id,
                "action": a.action,
                "abc": a.abc,
                "xyz": a.xyz,
                "eo_classification": a.eo_classification,
                "forecast_trend": a.trend,
                "requires_human_signoff": a.requires_human_signoff,
                "reason": a.reason,
            }
            for a in report.actions
        ]
        out = {"csv": write_summary_csv(action_rows, action_path)}

    excluded_path = d / "seo_priority_excluded.csv"
    if report.excluded:
        excluded_rows = [{"product_id": e.product_id, "reason": e.reason} for e in report.excluded]
        out["excluded_csv"] = write_summary_csv(excluded_rows, excluded_path)
    else:
        pd.DataFrame(columns=list(_EXCLUDED_CSV_COLUMNS)).to_csv(excluded_path, index=False)
        out["excluded_csv"] = excluded_path

    return out

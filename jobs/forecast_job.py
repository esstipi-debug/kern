"""Demand-forecasting agent job: a demand-history CSV -> forecastability-aware forecasts.

The data-prep + deck + guided-options half of the forecast tool. Reads a long-format demand
history (sku, period, quantity) with pandas directly (deliberately *not* via jobs/intake.py,
which the parallel loop owns), then for each SKU:

  1. cleans the series (missing -> 0, negatives floored),
  2. segments it by forecastability (Syntetos-Boylan ADI x CV^2 -> smooth / erratic /
     intermittent / lumpy) via ``src.forecastability``,
  3. auto-selects the matching method (AutoETS/TSB via StatsForecast when installed,
     else SES/Croston) and backtests it on a holdout,
  4. quantifies **Forecast Value-Add** vs a naive baseline (MASE < 1 == beats naive),

and emits a protected ``GuidedOutcome`` with **ranked forecasting-policy options**
(auto-per-segment / global SES / review-the-lumpy) so the tool offers *choices to act*.

Mirrors jobs/whatif_job.py (prepare/sniff) and jobs/returns_job.py (guided options) style.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.decision_options import Objective, Scenario, decide
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.forecast_metrics import compute_metrics
from src.forecastability import segment
from src.forecasting import forecast_demand
from src.guided import GuidedOutcome, Residual, recommend, verify_guided

_PRODUCT_COLS = ("sku", "product_id", "product", "item", "SKU", "Product", "material")
_QTY_COLS = ("demand", "quantity", "qty", "units", "sales", "Demand", "Quantity", "value")
_PERIOD_COLS = ("period", "date", "week", "month", "Period", "Date", "t")


@dataclass(frozen=True)
class SkuForecast:
    """One SKU's forecastability class, chosen method, next-period forecast and value-add."""

    name: str
    quadrant: str
    adi: float
    cv2: float
    method: str
    forecast: float
    mae: float
    mape: float
    mase: float
    fva: float            # 1 - MASE; > 0 means the method beats the naive baseline
    beats_naive: bool
    n_periods: int


@dataclass(frozen=True)
class ForecastJobReport:
    """Portfolio forecast: per-SKU results (hardest-first), the mix and the policy options."""

    skus: tuple[SkuForecast, ...]
    mix: dict
    hardest: tuple[str, float]
    n_skus: int
    n_beating_naive: int
    mean_mase: float
    outcome: GuidedOutcome
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict[str, list[float]]:
    """Sniff the demand columns and build one cleaned, period-ordered series per SKU.

    Required columns: product and quantity (raises ValueError listing any missing). A period
    column, when present, orders each SKU's series. Missing demand -> 0; negatives floored.
    """
    params = params or {}
    product_col = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    qty_col = _pick_column(df, params.get("qty_col"), _QTY_COLS)
    missing = [n for n, c in (("product", product_col), ("quantity", qty_col)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(
            f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})"
        )
    period_col = _pick_column(df, params.get("period_col"), _PERIOD_COLS)

    cols = [product_col, qty_col] + ([period_col] if period_col else [])
    work = df[cols].copy()
    work[qty_col] = pd.to_numeric(work[qty_col], errors="coerce").fillna(0.0).clip(lower=0.0)

    series_by_name: dict[str, list[float]] = {}
    for product, group in work.groupby(product_col, sort=False):
        if period_col:
            group = group.sort_values(period_col)
        series_by_name[str(product)] = [float(v) for v in group[qty_col].tolist()]
    if not series_by_name:
        raise ValueError("no demand series found in the data")
    return series_by_name


def prepare(data_path: str, params: dict | None = None) -> dict[str, list[float]]:
    """Read a demand-history CSV and build the cleaned per-SKU series."""
    return prepare_records(pd.read_csv(data_path), params)


def _backtest(series: list[float], method: str, *, holdout_fraction: float, min_periods: int):
    """Hold out the tail, fit on the head, and score the flat forecast (None if too short)."""
    n = len(series)
    if n < min_periods:
        return None
    h = min(max(1, round(n * holdout_fraction)), n - 2)
    if h < 1:
        return None
    train = series[:-h]
    test = series[-h:]
    fc = forecast_demand(train, method=method).forecast
    return compute_metrics(test, [fc] * h, train=train)


def _policy_outcome(
    *, base_summary: str, beating_share: float, regular_share: float, lumpy_share: float,
    n_unvalidated: int = 0, n_skus: int = 0,
) -> GuidedOutcome:
    """Rank three forecasting policies (accuracy vs ops simplicity vs manual effort).

    ``n_unvalidated`` is the count of SKUs too short to backtest (MASE=inf) - too
    little history to know whether the chosen method actually beats naive. Instead
    of silently shipping the same confident label as a well-backed forecast, this
    states the gap as a residual (never a bare/empty risk) and dampens confidence
    proportionally to how much of the portfolio could not be validated.
    """
    scenarios = [
        Scenario(
            "auto_per_segment",
            "Adopt the SBC-recommended method per SKU (AutoETS for regular, TSB for intermittent when available)",
            {"accuracy": beating_share, "ops_simplicity": 0.5, "manual_effort": 0.15},
            action="apply per-segment auto method selection",
            tradeoffs="best accuracy; one-time setup of per-segment automation",
        ),
        Scenario(
            "global_ses",
            "Run a single smoothing model across every SKU (SES / AutoETS)",
            {"accuracy": regular_share, "ops_simplicity": 1.0, "manual_effort": 0.0},
            action="apply one global smoothing model",
            tradeoffs="simplest to operate; over-forecasts intermittent/lumpy demand",
        ),
        Scenario(
            "review_lumpy",
            "Auto-forecast the forecastable SKUs; route lumpy demand to planner review",
            {"accuracy": min(1.0, beating_share + 0.1), "ops_simplicity": 0.5, "manual_effort": lumpy_share},
            action="auto for smooth/erratic/intermittent; manual review for lumpy",
            tradeoffs="captures the hardest SKUs with human judgement; adds review effort",
        ),
    ]
    objectives = [
        Objective("accuracy", weight=3.0, maximize=True),
        Objective("ops_simplicity", weight=1.0, maximize=True),
        Objective("manual_effort", weight=0.5),  # cost-like -> minimized
    ]
    confidence = 0.8
    residuals: list[Residual] | None = None
    if n_unvalidated > 0:
        unvalidated_share = n_unvalidated / n_skus if n_skus else 0.0
        confidence = round(confidence * (1.0 - 0.5 * unvalidated_share), 2)
        residuals = [Residual(
            description=f"{n_unvalidated} of {n_skus} SKU(s) had too little demand history to "
                        "backtest - their forecast accuracy (MASE) could not be validated.",
            risk_if_skipped="the chosen method for these SKUs is unverified; a poor fit could go "
                            "unnoticed until a stockout or an excess-stock event exposes it.",
        )]
    return decide(base_summary, scenarios, objectives, confidence=confidence, residuals=residuals)


def run(
    series_by_name: dict[str, list[float]],
    *,
    holdout_fraction: float = 0.25,
    min_backtest_periods: int = 4,
) -> ForecastJobReport:
    """Segment, auto-forecast + backtest each SKU, and present ranked forecasting policies."""
    seg = segment(series_by_name)
    skus: list[SkuForecast] = []
    for f in seg.items:
        series = series_by_name[f.name]
        point = float(forecast_demand(series, method=f.recommended_method).forecast)
        metrics = _backtest(
            series, f.recommended_method,
            holdout_fraction=holdout_fraction, min_periods=min_backtest_periods,
        )
        if metrics is not None and math.isfinite(metrics.mase):
            mase, mape, mae = metrics.mase, metrics.mape, metrics.mae
            fva, beats = 1.0 - mase, mase < 1.0
        else:
            mase, mape, mae = float("inf"), float("inf"), float("nan")
            fva, beats = 0.0, False
        skus.append(SkuForecast(
            name=f.name, quadrant=f.quadrant, adi=f.adi, cv2=f.cv2, method=f.recommended_method,
            forecast=point, mae=mae, mape=mape, mase=mase, fva=fva, beats_naive=beats,
            n_periods=f.n_periods,
        ))

    n = len(skus)
    n_beating = sum(1 for s in skus if s.beats_naive)
    finite_mases = [s.mase for s in skus if math.isfinite(s.mase)]
    mean_mase = sum(finite_mases) / len(finite_mases) if finite_mases else float("inf")
    regular_share = (seg.mix["smooth"] + seg.mix["erratic"]) / n if n else 0.0
    lumpy_share = seg.mix["lumpy"] / n if n else 0.0

    base_summary = (
        f"{n} SKUs segmented (smooth {seg.mix['smooth']}, erratic {seg.mix['erratic']}, "
        f"intermittent {seg.mix['intermittent']}, lumpy {seg.mix['lumpy']}); "
        f"{n_beating}/{n} beat the naive baseline."
    )
    outcome = _policy_outcome(
        base_summary=base_summary,
        beating_share=(n_beating / n if n else 0.0),
        regular_share=regular_share,
        lumpy_share=lumpy_share,
        n_unvalidated=n - len(finite_mases),
        n_skus=n,
    )
    recommended = recommend(outcome.options).label
    summary = f"{base_summary} Recommended policy: {recommended}."

    return ForecastJobReport(
        skus=tuple(skus),
        mix=seg.mix,
        hardest=seg.hardest,
        n_skus=n,
        n_beating_naive=n_beating,
        mean_mase=mean_mase,
        outcome=outcome,
        summary=summary,
    )


def verify(report: ForecastJobReport) -> list[str]:
    """QA gate: the options outcome honours the never-unprotected contract + finite forecasts."""
    issues = list(verify_guided(report.outcome))
    if report.n_skus <= 0:
        issues.append("no SKUs to forecast")
    if not all(math.isfinite(s.forecast) for s in report.skus):
        issues.append("a forecast is not finite")
    return issues


def _fmt(x: float) -> object:
    """Round for the operational CSV, leaving non-finite values blank-friendly."""
    return round(x, 4) if math.isfinite(x) else ""


def write_operational(report: ForecastJobReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one row per SKU with its class, method and value-add."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "name": s.name,
            "quadrant": s.quadrant,
            "adi": _fmt(s.adi),
            "cv2": round(s.cv2, 4),
            "method": s.method,
            "forecast": round(s.forecast, 2),
            "mape": _fmt(s.mape),
            "mase": _fmt(s.mase),
            "fva": round(s.fva, 4),
            "beats_naive": s.beats_naive,
            "n_periods": s.n_periods,
        }
        for s in report.skus
    ]
    return {"csv": write_summary_csv(rows, d / "forecast_register.csv")}


def build_deck(
    report: ForecastJobReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.8,
) -> Deliverable:
    """Compose the forecast study: which SKUs are forecastable, by what method, and the value-add."""
    mix = report.mix
    n = report.n_skus
    beat_pct = (report.n_beating_naive / n * 100) if n else 0.0
    hard_name, _ = report.hardest
    mean_mase_txt = f"{report.mean_mase:.2f}" if math.isfinite(report.mean_mase) else "n/a"

    summary = (
        f"Forecast study across {n} SKU(s): {mix['smooth']} smooth / {mix['erratic']} erratic / "
        f"{mix['intermittent']} intermittent / {mix['lumpy']} lumpy. {report.n_beating_naive}/{n} "
        f"({beat_pct:.0f}%) beat the naive baseline (mean MASE {mean_mase_txt})."
    )

    findings = [
        Finding(
            "Forecastability mix (Syntetos-Boylan)",
            f"smooth {mix['smooth']}, erratic {mix['erratic']}, intermittent {mix['intermittent']}, "
            f"lumpy {mix['lumpy']} - timing (ADI) x size variability (CV^2) sets the method per SKU.",
            impact="regular SKUs take SES; intermittent/lumpy take Croston - one global model misfits both",
        ),
        Finding(
            "Forecast value-add vs naive",
            f"{report.n_beating_naive}/{n} SKU(s) beat the naive baseline on a holdout backtest "
            f"(mean MASE {mean_mase_txt}); MASE < 1 means the chosen method adds value.",
            impact="adopt the method only where it beats naive; elsewhere naive is the honest default",
        ),
    ]
    hardest_skus = [s for s in report.skus if s.quadrant == "lumpy"][:3]
    if hardest_skus:
        names = ", ".join(s.name for s in hardest_skus)
        findings.append(Finding(
            "Hardest SKUs (lumpy) need a human in the loop",
            f"{names} are lumpy (irregular timing AND variable size) - statistical methods are weakest here.",
            impact="route lumpy SKUs to planner review or a service-level buffer, not a point forecast",
        ))
    else:
        findings.append(Finding(
            f"Hardest SKU to forecast: {hard_name}",
            "no lumpy SKUs; the hardest series still carries the most timing/size irregularity.",
            impact="watch this SKU's accuracy; it sets the floor on planning confidence",
        ))

    opts = report.outcome.options
    findings.append(Finding(
        "Recommended forecasting policy (choose one)",
        "; ".join(
            f"{i + 1}. {o.label}{' [recommended]' if o.recommended else ''} - {o.tradeoffs}"
            for i, o in enumerate(opts)
        ),
        impact="pick a policy; the recommended one is the best accuracy/effort balance",
    ))

    kpis = (
        Kpi("SKUs forecast", f"{n}", rationale="Series segmented and backtested"),
        Kpi("Beat naive", f"{report.n_beating_naive}/{n} ({beat_pct:.0f}%)", target="maximize",
            rationale="SKUs where the chosen method beats a naive baseline (MASE < 1)"),
        Kpi("Mean MASE", mean_mase_txt, target="minimize",
            rationale="Mean scaled error across backtested SKUs (< 1 is better than naive)"),
        Kpi("Intermittent + lumpy", f"{mix['intermittent'] + mix['lumpy']}", target="minimize",
            rationale="SKUs needing Croston / human review rather than smoothing"),
        Kpi("Hardest SKU", hard_name, rationale="Most irregular timing x size (lowest forecastability)"),
    )

    data_sources = (
        DataSource("Demand history (sku / period / quantity)", "ERP / sales history", "weekly"),
        DataSource("Forecastability cut-offs (ADI 1.32 / CV^2 0.49)", "Syntetos-Boylan (2005)", "fixed"),
    )

    recommendations = [
        "Adopt the recommended forecasting policy as the default; re-segment quarterly as demand shifts.",
        "Use sigma_e (forecast-error std), not raw demand std, to size safety stock downstream.",
        "Hand lumpy SKUs to planner review or a service-level policy instead of a point forecast.",
    ]

    return Deliverable(
        title="Demand Forecasting & Forecastability",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        options=tuple(report.outcome.options),   # ranked policies -> the deck's "Options to act"
        citations=tuple(citations),
        confidence=confidence,
        residual="forecasts assume the history is representative; confirm promotions, new-product "
                 "introductions and seasonality with the planner before locking the plan.",
        prepared=prepared,
    )

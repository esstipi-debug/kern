"""Supplier-sourcing agent job: delivery records -> scorecards -> TOPSIS award.

The data-prep + deck half of the sourcing tool. Aggregates supplier delivery records
into OTIF/lead-time/PPM scorecards with pandas directly (deliberately *not* via
jobs/intake.py, which the parallel loop owns), ranks suppliers by TOPSIS over those
criteria (+ price when present), and composes the award deck inline.

``score_suppliers`` / ``run`` / ``verify`` / ``build_deck`` are deterministic;
``prepare`` reads a file. Column names are sniffed and overridable via params.

A column the sniffer could not find yields ``None`` (absent), never ``False``/``0.0``.
An absent criterion is dropped from the TOPSIS rather than scoring every supplier a
tied zero, and is rendered "no medido" rather than a confident 0%.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.deliverable import NOT_MEASURED, DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.guided import GuidedOutcome, HandoffPacket, as_handoff, verify_guided
from src.mcdm import Criterion, RankingResult, award_outcome, topsis_rank
from src.supplier_scorecard import SupplierScore, score_supplier

_SUPPLIER_COLS = ("supplier", "Supplier", "vendor", "Vendor", "seller_id", "supplier_id")
_ON_TIME_COLS = ("on_time", "On Time", "on_time_delivery", "ontime")
_IN_FULL_COLS = ("in_full", "In Full", "complete", "infull")
_LEAD_COLS = ("lead_time_days", "lead_time", "Lead Time", "lead")
_UNITS_COLS = ("units", "Units", "quantity", "Quantity", "qty")
_DEFECTS_COLS = ("defects", "Defects", "rejects", "ppm_defects")
_PRICE_COLS = ("unit_price", "Unit Price", "price", "Price", "unit_cost", "cost")

# criterion name -> (higher is better, how to read it off a scorecard)
_CRITERIA: tuple[tuple[str, bool, object], ...] = (
    ("otif", True, lambda card, prices: card.otif),
    ("lead_time", False, lambda card, prices: card.avg_lead_time),
    ("ppm", False, lambda card, prices: card.ppm),
    ("price", False, lambda card, prices: prices.get(card.supplier)),
)

_DEFAULT_WEIGHTS = {"otif": 0.4, "lead_time": 0.2, "ppm": 0.2, "price": 0.2}


@dataclass(frozen=True)
class SourcingReport:
    scorecards: tuple[SupplierScore, ...]
    prices: dict[str, float]
    criteria: tuple[Criterion, ...]
    weights: dict[str, float]
    ranking: RankingResult
    outcome: GuidedOutcome
    recommended: str            # "" when nothing could be ranked
    summary: str
    # Criteria dropped because at least one supplier on the panel never measured
    # them. Ranking on a tie of zeros awards alphabetically, which is not a decision.
    unmeasured: tuple[str, ...] = field(default_factory=tuple)


def _pct(rate: float | None) -> str:
    """A share as a percentage - or the words that say it was never observed."""
    return NOT_MEASURED if rate is None else f"{rate * 100:.0f}%"


def _days(value: float | None) -> str:
    return NOT_MEASURED if value is None else f"{value:.0f} days"


def _ppm(value: float | None) -> str:
    return NOT_MEASURED if value is None else f"{value:,.0f} PPM"


def _cell(value: float | None, digits: int) -> float | str:
    """A CSV cell: the rounded number, or blank. Never 0 standing in for absent."""
    return "" if value is None else round(value, digits)


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def score_suppliers(
    df: pd.DataFrame,
    *,
    supplier_col: str,
    on_time_col: str | None = None,
    in_full_col: str | None = None,
    lead_col: str | None = None,
    units_col: str | None = None,
    defects_col: str | None = None,
    price_col: str | None = None,
) -> tuple[list[SupplierScore], dict[str, float]]:
    """Aggregate delivery rows into one scorecard per supplier (+ avg price if present).

    A column the caller could not resolve contributes ``None`` to every row - absent.
    It used to contribute ``False``/``0.0``, which reads downstream as a measured
    failure (OTIF 0%) or a measured perfection (0 defects, free supplier).
    """
    cards: list[SupplierScore] = []
    prices: dict[str, float] = {}
    for supplier, g in df.groupby(supplier_col):
        deliveries = [
            {
                "on_time": row[on_time_col] if on_time_col else None,
                "in_full": row[in_full_col] if in_full_col else None,
                "lead_time_days": row[lead_col] if lead_col else None,
                "units": row[units_col] if units_col else None,
                "defects": row[defects_col] if defects_col else None,
            }
            for _, row in g.iterrows()
        ]
        cards.append(score_supplier(str(supplier), deliveries))
        if price_col:
            mean_price = float(g[price_col].mean())
            # An unpriced supplier is not a free one: leave it out and let `run`
            # drop price as a criterion rather than award it the ideal cost.
            if math.isfinite(mean_price):
                prices[str(supplier)] = mean_price
    return cards, prices


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a supplier delivery CSV and aggregate it into scorecards + prices."""
    params = params or {}
    df = pd.read_csv(data_path)
    supplier = _pick_column(df, params.get("supplier_col"), _SUPPLIER_COLS)
    if supplier is None:
        raise ValueError(f"could not find supplier_col; pass it in params (columns seen: {list(df.columns)[:10]})")
    cards, prices = score_suppliers(
        df,
        supplier_col=supplier,
        on_time_col=_pick_column(df, params.get("on_time_col"), _ON_TIME_COLS),
        in_full_col=_pick_column(df, params.get("in_full_col"), _IN_FULL_COLS),
        lead_col=_pick_column(df, params.get("lead_col"), _LEAD_COLS),
        units_col=_pick_column(df, params.get("units_col"), _UNITS_COLS),
        defects_col=_pick_column(df, params.get("defects_col"), _DEFECTS_COLS),
        price_col=_pick_column(df, params.get("price_col"), _PRICE_COLS),
    )
    return {"scorecards": cards, "prices": prices}


def run(
    scorecards: list[SupplierScore],
    prices: dict[str, float] | None = None,
    *,
    weights: dict[str, float] | None = None,
) -> SourcingReport:
    """Rank the scored suppliers by TOPSIS and present the award as protected options.

    Only criteria measured for **every** supplier on the panel enter the ranking. A
    criterion nobody recorded scores everyone an identical zero, and TOPSIS on a full
    tie falls back to input order - which is how an all-zero scorecard still produced
    a confident "recommended award".
    """
    prices = prices or {}
    measured, unmeasured = [], []
    for name, benefit, read in _CRITERIA:
        if name == "price" and not prices:
            continue      # no price column at all: not a criterion, not a gap either
        if scorecards and all(read(card, prices) is not None for card in scorecards):
            measured.append(Criterion(name, benefit=benefit))
        else:
            unmeasured.append(name)

    merged = {**_DEFAULT_WEIGHTS, **(weights or {})}
    used = {c.name: merged[c.name] for c in measured}
    readers = {name: read for name, _, read in _CRITERIA}

    if not measured:
        return _nothing_to_rank(scorecards, prices, tuple(unmeasured))

    alternatives = {
        card.supplier: {c.name: float(readers[c.name](card, prices)) for c in measured}
        for card in scorecards
    }
    ranking = topsis_rank(alternatives, measured, used)
    summary = (
        f"Compared {len(scorecards)} suppliers on "
        + " / ".join(c.name for c in measured)
        + f"; recommended award: {ranking.best}."
        + (f" Not measured in this data: {', '.join(unmeasured)}." if unmeasured else "")
    )
    return SourcingReport(
        scorecards=tuple(scorecards),
        prices=prices,
        criteria=tuple(measured),
        weights=used,
        ranking=ranking,
        outcome=award_outcome(ranking, summary=summary),
        recommended=ranking.best,
        summary=summary,
        unmeasured=tuple(unmeasured),
    )


def _nothing_to_rank(
    scorecards: list[SupplierScore], prices: dict[str, float], unmeasured: tuple[str, ...]
) -> SourcingReport:
    """No criterion survived: hand the gap to a human instead of awarding on nothing."""
    gap = ", ".join(unmeasured) if unmeasured else "every criterion"
    summary = (
        f"Cannot rank {len(scorecards)} supplier(s): {gap} was never measured in this data. "
        "No award is defensible until at least one criterion is populated."
    )
    outcome = as_handoff(
        summary,
        [HandoffPacket(
            title="Supply the missing supplier-performance columns",
            steps=[
                f"Add one of: {gap} to the delivery export (or map it via params).",
                "On-time needs order/delivery dates or a promised date; in-full needs "
                "ordered vs received quantity; quality needs units and defects.",
                "Re-run the sourcing brief once at least one criterion is populated.",
            ],
            risk_if_skipped="Awarding without a measured criterion ranks suppliers by "
                            "input order, not by performance.",
        )],
    )
    return SourcingReport(
        scorecards=tuple(scorecards),
        prices=prices,
        criteria=(),
        weights={},
        ranking=RankingResult({}, []),
        outcome=outcome,
        recommended="",
        summary=summary,
        unmeasured=unmeasured,
    )


def verify(report: SourcingReport) -> list[str]:
    """QA gate: a usable award honours the never-unprotected contract and scored suppliers."""
    issues = list(verify_guided(report.outcome))
    if not report.scorecards:
        issues.append("no suppliers scored")
    if not report.criteria:
        gap = ", ".join(report.unmeasured) if report.unmeasured else "every criterion"
        issues.append(f"no measured criterion to rank suppliers on ({gap} absent)")
    return issues


def write_operational(report: SourcingReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one row per supplier scorecard + TOPSIS score."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "supplier": c.supplier,
            "deliveries": c.deliveries,
            "otif": _cell(c.otif, 4),
            "otif_observations": c.evaluable.otif,
            "on_time_rate": _cell(c.on_time_rate, 4),
            "in_full_rate": _cell(c.in_full_rate, 4),
            "avg_lead_time": _cell(c.avg_lead_time, 2),
            "ppm": _cell(c.ppm, 1),
            "avg_price": _cell(report.prices.get(c.supplier), 2),
            "topsis_score": _cell(report.ranking.scores.get(c.supplier), 4),
            "rank": (
                report.ranking.ranking.index(c.supplier) + 1
                if c.supplier in report.ranking.ranking else ""
            ),
        }
        for c in report.scorecards
    ]
    return {"csv": write_summary_csv(rows, d / "supplier_scorecards.csv")}


def build_deck(
    report: SourcingReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the sourcing study: the recommended award and why, with the scorecard spread.

    Every criterion prints what was measured, or the words ``no medido``. A rate over
    zero observations is not zero percent, and a deck that prints it as one is the
    exact failure this tool is supposed to catch in the client's own reporting.
    """
    if not report.recommended:
        raise ValueError(
            "no award to present: " + report.summary + " (verify() blocks this report)"
        )
    best = next(c for c in report.scorecards if c.supplier == report.recommended)
    others = [c for c in report.scorecards if c.supplier != report.recommended]
    ranked = " / ".join(c.name for c in report.criteria)

    summary = (
        f"Compared {len(report.scorecards)} suppliers on {ranked}; recommended award to "
        f"{report.recommended} (OTIF {_pct(best.otif)}, lead {_days(best.avg_lead_time)}, "
        f"{_ppm(best.ppm)})."
    )

    findings = [
        Finding(
            f"Recommended award: {report.recommended}",
            f"Best TOPSIS closeness ({report.ranking.scores[report.recommended]:.3f}) across the "
            f"weighted criteria actually measured here ({ranked}).",
            impact="consolidate volume here, with a backup qualified",
        )
    ]
    if report.unmeasured:
        findings.append(Finding(
            "Criteria this data could not measure",
            f"{', '.join(report.unmeasured)} - not present for every supplier on the panel, so "
            "excluded from the ranking rather than scored as zero.",
            impact=f"the award rests on {ranked} alone; populate the rest to widen it",
        ))
    otifs = [c.otif for c in report.scorecards if c.otif is not None]
    if otifs:
        findings.append(Finding(
            "OTIF spread across the panel",
            f"On-time-in-full ranges {_pct(min(otifs))}-{_pct(max(otifs))} across the "
            f"{len(otifs)} supplier(s) that recorded it - the gap is the reliability "
            "premium the award buys.",
            impact="hold underperformers to a corrective plan",
        ))
    if others:
        alt = "; ".join(
            f"{c.supplier} (OTIF {_pct(c.otif)}, {report.ranking.scores[c.supplier]:.3f})"
            for c in others
        )
        findings.append(Finding("Alternatives considered", f"Ranked against: {alt}.",
                                impact="documented for the sourcing decision"))

    kpis = (
        Kpi("Recommended supplier", report.recommended, rationale="Top TOPSIS-ranked award"),
        Kpi("Its OTIF", _pct(best.otif), target="95%+",
            rationale=f"On-time-in-full over {best.evaluable.otif} scoreable delivery record(s)"),
        Kpi("Its lead time", _days(best.avg_lead_time), target="minimize",
            rationale=f"Replenishment responsiveness over {best.evaluable.lead_time} record(s)"),
        Kpi("Its quality (PPM)", _ppm(best.ppm), target="minimize",
            rationale=f"Defect parts per million over {best.evaluable.ppm} record(s)"),
        Kpi("Suppliers compared", str(len(report.scorecards)), rationale="Panel breadth"),
        Kpi("Criteria ranked on", ranked or NOT_MEASURED,
            rationale="Only criteria measured for every supplier enter the TOPSIS"),
    )

    data_sources = (
        DataSource("Supplier delivery records (on-time / in-full / lead / defects)", "ERP / receiving data", "monthly"),
        DataSource("Criteria weights", "engagement parameters", "per run"),
    )

    # Only prescribe a target on something this data actually measured: "hold them to
    # an OTIF target" is advice the reader cannot act on without an OTIF.
    measured_names = {c.name for c in report.criteria}
    corrective = (
        "Put underperforming suppliers on a corrective-action plan with an OTIF target."
        if "otif" in measured_names
        else f"Put underperforming suppliers on a corrective-action plan against {ranked}, "
             "the criteria this data supports."
    )
    recommendations = [
        f"Award the primary volume to {report.recommended}; keep the runner-up qualified as backup.",
        corrective,
    ]
    if report.unmeasured:
        recommendations.append(
            f"Add {', '.join(report.unmeasured)} to the delivery export so the next award "
            "weighs service and quality, not just what happened to be in the file."
        )

    return Deliverable(
        title="Supplier Sourcing & Selection",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual="The final award, price negotiation, and contract terms are commercial / legal "
                 "decisions - this ranks and prepares the packet; a human signs the award.",
        prepared=prepared,
    )

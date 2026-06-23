"""Supplier-sourcing agent job: delivery records -> scorecards -> TOPSIS award.

The data-prep + deck half of the sourcing tool. Aggregates supplier delivery records
into OTIF/lead-time/PPM scorecards with pandas directly (deliberately *not* via
jobs/intake.py, which the parallel loop owns), ranks suppliers by TOPSIS over those
criteria (+ price when present), and composes the award deck inline.

``score_suppliers`` / ``run`` / ``verify`` / ``build_deck`` are deterministic;
``prepare`` reads a file. Column names are sniffed and overridable via params.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.guided import GuidedOutcome, verify_guided
from src.mcdm import Criterion, RankingResult, award_outcome, topsis_rank
from src.supplier_scorecard import SupplierScore, score_supplier

_SUPPLIER_COLS = ("supplier", "Supplier", "vendor", "Vendor", "seller_id", "supplier_id")
_ON_TIME_COLS = ("on_time", "On Time", "on_time_delivery", "ontime")
_IN_FULL_COLS = ("in_full", "In Full", "complete", "infull")
_LEAD_COLS = ("lead_time_days", "lead_time", "Lead Time", "lead")
_UNITS_COLS = ("units", "Units", "quantity", "Quantity", "qty")
_DEFECTS_COLS = ("defects", "Defects", "rejects", "ppm_defects")
_PRICE_COLS = ("unit_price", "Unit Price", "price", "Price", "unit_cost", "cost")

_DEFAULT_WEIGHTS = {"otif": 0.4, "lead_time": 0.2, "ppm": 0.2, "price": 0.2}


@dataclass(frozen=True)
class SourcingReport:
    scorecards: tuple[SupplierScore, ...]
    prices: dict[str, float]
    criteria: tuple[Criterion, ...]
    weights: dict[str, float]
    ranking: RankingResult
    outcome: GuidedOutcome
    recommended: str
    summary: str


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
    """Aggregate delivery rows into one scorecard per supplier (+ avg price if present)."""
    cards: list[SupplierScore] = []
    prices: dict[str, float] = {}
    for supplier, g in df.groupby(supplier_col):
        deliveries = [
            {
                "on_time": bool(row[on_time_col]) if on_time_col else False,
                "in_full": bool(row[in_full_col]) if in_full_col else False,
                "lead_time_days": float(row[lead_col]) if lead_col else 0.0,
                "units": float(row[units_col]) if units_col else 0.0,
                "defects": float(row[defects_col]) if defects_col else 0.0,
            }
            for _, row in g.iterrows()
        ]
        cards.append(score_supplier(str(supplier), deliveries))
        if price_col:
            prices[str(supplier)] = float(g[price_col].mean())
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
    """Rank the scored suppliers by TOPSIS and present the award as protected options."""
    prices = prices or {}
    criteria = [Criterion("otif", benefit=True), Criterion("lead_time", benefit=False),
                Criterion("ppm", benefit=False)]
    alternatives: dict[str, dict[str, float]] = {}
    for s in scorecards:
        alt = {"otif": s.otif, "lead_time": s.avg_lead_time, "ppm": s.ppm}
        if prices:
            alt["price"] = prices.get(s.supplier, 0.0)
        alternatives[s.supplier] = alt
    if prices:
        criteria.append(Criterion("price", benefit=False))

    merged = {**_DEFAULT_WEIGHTS, **(weights or {})}
    used = {c.name: merged[c.name] for c in criteria}

    ranking = topsis_rank(alternatives, criteria, used)
    summary = (
        f"Compared {len(scorecards)} suppliers on OTIF / lead time / quality"
        + (" / price" if prices else "")
        + f"; recommended award: {ranking.best}."
    )
    outcome = award_outcome(ranking, summary=summary)
    return SourcingReport(
        scorecards=tuple(scorecards),
        prices=prices,
        criteria=tuple(criteria),
        weights=used,
        ranking=ranking,
        outcome=outcome,
        recommended=ranking.best,
        summary=summary,
    )


def verify(report: SourcingReport) -> list[str]:
    """QA gate: a usable award honours the never-unprotected contract and scored suppliers."""
    issues = list(verify_guided(report.outcome))
    if not report.scorecards:
        issues.append("no suppliers scored")
    return issues


def write_operational(report: SourcingReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one row per supplier scorecard + TOPSIS score."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "supplier": c.supplier,
            "deliveries": c.deliveries,
            "otif": round(c.otif, 4),
            "on_time_rate": round(c.on_time_rate, 4),
            "in_full_rate": round(c.in_full_rate, 4),
            "avg_lead_time": round(c.avg_lead_time, 2),
            "ppm": round(c.ppm, 1),
            "avg_price": round(report.prices.get(c.supplier, 0.0), 2),
            "topsis_score": round(report.ranking.scores.get(c.supplier, 0.0), 4),
            "rank": report.ranking.ranking.index(c.supplier) + 1,
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
    """Compose the sourcing study: the recommended award and why, with the scorecard spread."""
    best = next(c for c in report.scorecards if c.supplier == report.recommended)
    others = [c for c in report.scorecards if c.supplier != report.recommended]

    summary = (
        f"Compared {len(report.scorecards)} suppliers; recommended award to "
        f"{report.recommended} (OTIF {best.otif * 100:.0f}%, lead {best.avg_lead_time:.0f}d, "
        f"{best.ppm:,.0f} PPM)."
    )

    findings = [
        Finding(
            f"Recommended award: {report.recommended}",
            f"Best TOPSIS closeness ({report.ranking.scores[report.recommended]:.3f}) across the "
            "weighted criteria - the best balance of service, lead time and quality.",
            impact="consolidate volume here, with a backup qualified",
        )
    ]
    if report.scorecards:
        otifs = [c.otif for c in report.scorecards]
        findings.append(Finding(
            "OTIF spread across the panel",
            f"On-time-in-full ranges {min(otifs) * 100:.0f}%-{max(otifs) * 100:.0f}% - the gap is the "
            "reliability premium the award buys.",
            impact="hold underperformers to a corrective plan",
        ))
    if others:
        alt = "; ".join(
            f"{c.supplier} (OTIF {c.otif * 100:.0f}%, {report.ranking.scores[c.supplier]:.3f})"
            for c in others
        )
        findings.append(Finding("Alternatives considered", f"Ranked against: {alt}.",
                                impact="documented for the sourcing decision"))

    kpis = (
        Kpi("Recommended supplier", report.recommended, rationale="Top TOPSIS-ranked award"),
        Kpi("Its OTIF", f"{best.otif * 100:.0f}%", target="95%+", rationale="On-time-in-full reliability"),
        Kpi("Its lead time", f"{best.avg_lead_time:.0f} days", target="minimize",
            rationale="Replenishment responsiveness"),
        Kpi("Its quality (PPM)", f"{best.ppm:,.0f}", target="minimize", rationale="Defect parts per million"),
        Kpi("Suppliers compared", str(len(report.scorecards)), rationale="Panel breadth"),
    )

    data_sources = (
        DataSource("Supplier delivery records (on-time / in-full / lead / defects)", "ERP / receiving data", "monthly"),
        DataSource("Criteria weights", "engagement parameters", "per run"),
    )

    recommendations = [
        f"Award the primary volume to {report.recommended}; keep the runner-up qualified as backup.",
        "Put underperforming suppliers on a corrective-action plan with an OTIF target.",
    ]

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

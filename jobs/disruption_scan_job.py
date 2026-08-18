"""Supplier Disruption Exposure Scan job -- the free GDELT-backed sales hook.

Reads a supplier list (supplier, country, optional annual_spend) with pandas
directly (deliberately *not* via jobs/intake.py), screens each supplier against
the GDELT DOC 2.0 news API (``src.disruption``), maps the signal onto the risk
engine (``src.risk``), and emits a protected ``GuidedOutcome`` ranking which
suppliers to investigate first.

This is the read-only "here's the disruption near YOUR suppliers this quarter"
diagnostic: no key, no client system access -- just a supplier list. The GDELT
signal is a SCREENING proxy, not a calibrated probability; the deck says so.

Mirrors jobs/risk_job.py (prepare/run/verify/write_operational/build_deck +
guided options) so it reuses the same 5x5 / EMV / options plumbing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from nosilo_core.guided import (
    ExecutionOption,
    GuidedOutcome,
    HandoffPacket,
    Residual,
    as_executed,
    as_handoff,
    as_options,
    verify_guided,
)

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.disruption import (
    GDELT_ATTRIBUTION,
    Fetcher,
    SupplierDisruption,
    SupplierRow,
    scan_suppliers,
    to_risk_factor,
)
from src.export import write_summary_csv
from src.risk import RiskReport, assess_portfolio

_SUPPLIER_COLS = ("supplier", "Supplier", "vendor", "Vendor", "supplier_name", "name", "Name")
_COUNTRY_COLS = ("country", "Country", "location", "Location", "region", "hq_country", "origin", "pais")
_SPEND_COLS = ("annual_spend", "spend", "Spend", "annual_value", "value", "purchase_value", "gasto_anual")

_SCREEN_RESIDUAL = Residual(
    description=(
        "The GDELT signal is a news-coverage screen, not a calibrated disruption "
        "probability -- it prioritises which suppliers to investigate."
    ),
    risk_if_skipped=(
        "Treating the likelihood as a forecast over/under-states real risk; confirm "
        "each flagged supplier with the owner before acting."
    ),
)


@dataclass(frozen=True)
class DisruptionScanReport:
    """The scan result: per-supplier signals, the risk roll-up, and the guided options."""

    signals: tuple[SupplierDisruption, ...]     # ranked by exposure desc
    risk_report: RiskReport
    outcome: GuidedOutcome
    n_suppliers: int
    n_signalled: int
    n_failed: int
    window: str
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[SupplierRow]:
    """Sniff the supplier / country / spend columns and build one SupplierRow per line."""
    params = params or {}
    supplier_col = _pick_column(df, params.get("supplier_col"), _SUPPLIER_COLS)
    if supplier_col is None:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find a supplier column; pass supplier_col (columns seen: {cols})")
    country_col = _pick_column(df, params.get("country_col"), _COUNTRY_COLS)
    spend_col = _pick_column(df, params.get("spend_col"), _SPEND_COLS)

    rows: list[SupplierRow] = []
    for _, row in df.iterrows():
        name = str(row[supplier_col]).strip()
        if not name or name.lower() == "nan":
            continue
        country = (
            str(row[country_col]).strip() if country_col and pd.notna(row[country_col]) else ""
        )
        # Coerce per cell: a messy-but-present spend ("1,200,000", "$4.2M", "N/A")
        # becomes unknown (0.0) for that one supplier instead of aborting the whole
        # file. Negative spend is treated as unknown too (impact falls back to nominal).
        spend = 0.0
        if spend_col and pd.notna(row[spend_col]):
            parsed = pd.to_numeric(row[spend_col], errors="coerce")
            if pd.notna(parsed) and parsed > 0:
                spend = float(parsed)
        rows.append(SupplierRow(supplier=name, country=country, annual_spend=spend))
    if not rows:
        raise ValueError("no suppliers found in the data")
    return rows


def prepare(data_path: str, params: dict | None = None) -> list[SupplierRow]:
    """Read a supplier-list CSV and build the SupplierRow records."""
    return prepare_records(pd.read_csv(data_path), params)


def _recency_days(sig: SupplierDisruption) -> float:
    """Display recency, floored at 0 (a future-dated crawl timestamp shows as 0d)."""
    return max(sig.recency_days, 0.0) if math.isfinite(sig.recency_days) else 0.0


def _likely(category: str) -> str:
    """Label the inferred category honestly wherever it surfaces to a client."""
    return f"likely {category} (inferred from headlines)"


def _investigate_action(sig: SupplierDisruption) -> str:
    return (
        f"investigate {sig.supplier}: {sig.article_count} disruption article(s) "
        f"from {sig.distinct_sources} source(s), {_likely(sig.dominant_category)}"
    )


def run(
    records: list[SupplierRow],
    *,
    fetcher: Fetcher | None = None,
    timespan: str = "3m",
    now=None,
) -> DisruptionScanReport:
    """Screen the suppliers against GDELT, score exposure, and present ranked options."""
    signals = scan_suppliers(records, fetcher=fetcher, timespan=timespan, now=now)
    signals = tuple(sorted(signals, key=lambda s: s.exposure_score, reverse=True))
    n_failed = sum(1 for s in signals if s.fetch_failed)
    signalled = [s for s in signals if s.exposure_score > 0 and not s.fetch_failed]

    risk_report = assess_portfolio([to_risk_factor(s) for s in signalled]) if signalled else \
        assess_portfolio([to_risk_factor(s) for s in signals[:1]]) if signals else \
        RiskReport((), 0.0, 0.0, {"critical": 0, "high": 0, "medium": 0, "low": 0}, ("n/a", 0.0))

    window = timespan
    if signalled:
        options = [
            ExecutionOption(
                label=sig.supplier,
                summary=(
                    f"{sig.article_count} disruption article(s), {sig.distinct_sources} source(s), "
                    f"most recent {_recency_days(sig):.0f}d ago; exposure {sig.exposure_score:.2f}"
                ),
                score=sig.exposure_score,
                action=_investigate_action(sig),
                tradeoffs=(
                    f"{_likely(sig.dominant_category)}; "
                    + (f"${sig.annual_spend:,.0f}/yr spend at stake" if sig.annual_spend > 0
                       else "spend unknown")
                ),
            )
            for sig in signalled
        ]
        summary = (
            f"{len(signalled)} of {len(records)} supplier(s) show disruption news in the last "
            f"{window}; top exposure: {signalled[0].supplier} "
            f"({signalled[0].article_count} article(s), {_likely(signalled[0].dominant_category)})."
        )
        if n_failed:
            summary += f" {n_failed} supplier(s) could not be retrieved (unknown, not clear)."
        outcome = as_options(summary, options, confidence=0.6, residuals=[_SCREEN_RESIDUAL])
    elif n_failed == len(records) and records:
        summary = (
            f"Could not retrieve GDELT signal for any of {len(records)} supplier(s) "
            "(network unavailable or rate-limited)."
        )
        outcome = as_handoff(
            summary,
            [HandoffPacket(
                title="Retry the disruption scan",
                steps=[
                    "Confirm outbound access to api.gdeltproject.org.",
                    "Re-run the scan; GDELT limits to 1 request / 5s per IP.",
                ],
                risk_if_skipped="No disruption screen was produced; supplier exposure is unknown.",
            )],
            confidence=0.3,
        )
    else:
        reached = len(records) - n_failed
        if n_failed:
            summary = (
                f"No disruption news on the {reached} reachable supplier(s) in the last {window}; "
                f"{n_failed} could not be retrieved -- treat those as unknown, not clear. Keep monitoring."
            )
            confidence = 0.4
        else:
            summary = (
                f"No disruption news detected for any of {len(records)} supplier(s) in the last {window}. "
                "Screen clear on the tracked signals; keep monitoring."
            )
            confidence = 0.6
        outcome = as_executed(summary, confidence=confidence, residuals=[_SCREEN_RESIDUAL])

    return DisruptionScanReport(
        signals=signals,
        risk_report=risk_report,
        outcome=outcome,
        n_suppliers=len(records),
        n_signalled=len(signalled),
        n_failed=n_failed,
        window=window,
        summary=summary,
    )


def verify(report: DisruptionScanReport) -> list[str]:
    """QA gate: the outcome honours the never-unprotected contract; the scan actually ran."""
    issues = list(verify_guided(report.outcome))
    if report.n_suppliers <= 0:
        issues.append("no suppliers to scan")
    if report.n_suppliers > 0 and report.n_failed == report.n_suppliers:
        issues.append("every GDELT query failed; no disruption signal was retrieved")
    if not math.isfinite(report.risk_report.total_emv):
        issues.append("total EMV is not finite")
    return issues


def write_operational(
    report: DisruptionScanReport, out_dir: str | Path, client: str = "Client"
) -> dict[str, Path]:
    """The machine-readable deliverable: one row per supplier with its exposure signal."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "supplier": s.supplier,
            "country": s.country,
            "annual_spend": round(s.annual_spend, 2),
            "articles": s.article_count,
            "distinct_sources": s.distinct_sources,
            "recency_days": "" if math.isinf(s.recency_days) else round(max(s.recency_days, 0.0), 1),
            "likely_category_inferred": s.dominant_category,
            "exposure_score": s.exposure_score,
            "signal": "no data" if s.fetch_failed else ("flag" if s.exposure_score > 0 else "clear"),
            "top_article": s.sample_articles[0].url if s.sample_articles else "",
        }
        for s in report.signals
    ]
    return {"csv": write_summary_csv(rows, d / "disruption_scan.csv")}


def build_deck(
    report: DisruptionScanReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.6,
) -> Deliverable:
    """Compose the Supplier Disruption Exposure Scan: who is exposed, and to what."""
    signalled = [s for s in report.signals if s.exposure_score > 0 and not s.fetch_failed]

    findings: list[Finding] = []
    if signalled:
        top = signalled[0]
        arts = "; ".join(f"{a.domain} ({a.seendate:%Y-%m-%d})" for a in top.sample_articles[:3])
        findings.append(Finding(
            f"Highest disruption exposure: {top.supplier}",
            f"{top.article_count} disruption article(s) from {top.distinct_sources} source(s) in the "
            f"last {report.window}, most recent {_recency_days(top):.0f} day(s) ago; dominant signal: "
            f"{_likely(top.dominant_category)}. Recent coverage: {arts}.",
            impact="investigate this supplier first; it carries the strongest disruption signal",
        ))
        for s in signalled[1:3]:
            findings.append(Finding(
                f"Next exposure: {s.supplier}",
                f"{s.article_count} article(s), {s.distinct_sources} source(s), "
                f"{_likely(s.dominant_category)}, exposure {s.exposure_score:.2f}.",
                impact="queue behind the top-exposure supplier",
            ))
    else:
        findings.append(Finding(
            "No disruption signal on the tracked themes",
            f"None of {report.n_suppliers} supplier(s) show disruption news in the last {report.window} "
            "on the strike / disaster / port / bankruptcy / shortage themes screened.",
            impact="screen clear on tracked signals; keep the scan on a recurring cadence",
        ))
    if report.n_failed:
        findings.append(Finding(
            "Partial coverage",
            f"{report.n_failed} of {report.n_suppliers} supplier query(ies) could not be retrieved "
            "(network or GDELT rate limit); those rows are marked 'no data', not 'clear'.",
            impact="re-run to close the gap before treating a 'no data' supplier as low-risk",
        ))

    kpis = (
        Kpi("Suppliers scanned", f"{report.n_suppliers}", rationale="Rows in the supplier list"),
        Kpi("Suppliers with disruption signal", f"{report.n_signalled}", target="0",
            rationale="Suppliers with >=1 disruption article on the tracked themes"),
        Kpi("Top exposure", signalled[0].supplier if signalled else "none",
            rationale="Highest news-signal exposure this window"),
        Kpi("Coverage gaps", f"{report.n_failed}", target="0",
            rationale="Queries that could not be retrieved"),
        Kpi("Lookback window", report.window, rationale="GDELT DOC 2.0 timespan"),
    )

    data_sources = (
        DataSource("Supplier list (supplier / country / annual spend)", "client-supplied", "per run"),
        DataSource("Disruption news (article metadata)", GDELT_ATTRIBUTION, f"live, last {report.window}"),
    )

    recommendations = [
        "Investigate the top-exposure supplier(s) before the next planning cycle.",
        "Run this scan on a recurring cadence -- disruption signal is only useful while fresh.",
        "Confirm every flagged supplier with its owner; the screen prioritises, it does not diagnose.",
    ]

    return Deliverable(
        title="Supplier Disruption Exposure Scan",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        options=tuple(report.outcome.options),
        citations=tuple(citations) + (f"Source: {GDELT_ATTRIBUTION}",),
        confidence=confidence,
        residual=(
            "The GDELT signal is a news-coverage screen, not a calibrated disruption probability; "
            "article metadata only (links/titles), never article text. The category is inferred "
            "from headlines (best-effort), so confirm the actual disruption type from the linked "
            "articles before acting on it."
        ),
        prepared=prepared,
    )

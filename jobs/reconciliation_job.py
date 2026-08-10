"""Inventory-reconciliation / IRA agent job: a count CSV -> record-accuracy study.

The data-prep + deck half of the reconciliation tool. Reads count lines (system vs physical
qty + unit cost) with pandas directly (deliberately *not* via jobs/intake.py, which the
parallel loop owns) and reconciles them against a tolerance band via ``src.reconciliation``.

Two absences used to be printed as measurements, and both are now first-class:

**A missing ``unit_cost`` used to default to 0.0**, and the sum of those zeros shipped as
"Dollar impact of the discrepancies: 0 total absolute variance value". Nothing told the
reader that apart from a real, measured zero, so the variances read as free. The same
default poisoned the ranking: ``|variance| * 0.0`` sorted the 20-unit discrepancy nobody
could price below the 2-unit one that had a price, and the deck named the wrong SKU as
the biggest problem.

**A blank ``physical_qty`` used to be a line counted at zero.** It landed in the IRA
denominator as a failure and turned the variance total into ``nan``; dropping it produced
the mirror image - "accuracy 100%" read off the single line anyone actually counted.

The contract now: an unmeasurable figure is ``None`` plus a legible motive, the deck prose
never asserts anything derived from it, an IRA computed over part of the sheet always says
so, and ``verify()`` refuses a report that states a number it had no basis for. A genuine
measured zero stays a zero - that distinction is the whole point.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.reconciliation import CountResult, inventory_record_accuracy, reconcile

_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "Product")
_SYSTEM_COLS = ("system_qty", "system", "book_qty", "book", "on_hand_system", "System")
_PHYSICAL_COLS = ("physical_qty", "physical", "counted", "count", "actual", "Physical")
_UNIT_COST_COLS = ("unit_cost", "cost", "Unit Cost", "price")

# Inventory-record-accuracy is judged against a class-leading target.
_IRA_TARGET = 0.97

#: What a cell says when the sheet never carried the basis for it. A number here - any
#: number, 0 included - reads as a measurement, and 0 is the most favourable one.
_NOT_MEASURED = "not measured"

#: What an unmeasurable money half costs the deck's confidence, and the floor it stops at.
_MISSING_BASIS_PENALTY = 0.25
_CONFIDENCE_FLOOR = 0.10

#: How many SKUs a note names before it starts counting instead. A 400-line sheet must
#: not paste 400 ids into a finding; the CSV carries every row.
_MAX_NAMED = 6


@dataclass(frozen=True)
class IRAReport:
    n_lines: int                       # every line on the sheet
    n_counted: int                     # the ones with a physical quantity
    n_within: int
    ira: float | None                  # None => nothing was counted, never 0.0 for it
    ira_note: str | None               # why the IRA is partial or absent
    total_variance_value: float | None  # None => at least one variance had no price
    variance_value_note: str | None
    tolerance_pct: float
    tolerance_units: float
    worst: tuple[CountResult, ...]     # out-of-tolerance AND priced, ranked by $ desc
    unvalued: tuple[CountResult, ...]  # out-of-tolerance and unpriceable: ranked nowhere
    unpriced: tuple[str, ...]          # product ids whose nonzero variance has no cost
    uncounted: tuple[str, ...]         # product ids nobody counted
    results: tuple[CountResult, ...]

    def __post_init__(self) -> None:
        """Make the dishonest shapes unconstructible, so no caller has to remember.

        ``verify()`` still repeats these checks: this guards construction, that one holds
        when a caller mutates a frozen instance past it.
        """
        if self.total_variance_value is not None and self.unpriced:
            raise ValueError(
                f"a dollar impact was stated without the cost basis for it: "
                f"{len(self.unpriced)} line(s) with a nonzero variance carry no unit cost "
                f"({', '.join(self.unpriced[:_MAX_NAMED])})"
            )
        if self.total_variance_value is None and not self.variance_value_note:
            raise ValueError(
                "an absent dollar impact without a motive is not a finding: say which "
                "lines could not be priced and what to send"
            )
        if self.ira is None and not self.ira_note:
            raise ValueError("an absent IRA without a motive is not a finding")


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _cell(row: pd.Series, col: str | None) -> float | None:
    """The number the sheet actually held, or None when it held nothing.

    Absent column and empty cell are the same answer - "nobody wrote this down" - and
    both must stay distinguishable from a 0 somebody did write.
    """
    if not col or not pd.notna(row[col]):
        return None
    return float(row[col])


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[dict]:
    """Sniff the count columns and build one record per line, absences intact."""
    params = params or {}
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    system = _pick_column(df, params.get("system_col"), _SYSTEM_COLS)
    physical = _pick_column(df, params.get("physical_col"), _PHYSICAL_COLS)
    missing = [n for n, c in (("product_id", product), ("system_qty", system), ("physical_qty", physical)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    unit_cost = _pick_column(df, params.get("unit_cost_col"), _UNIT_COST_COLS)
    return [
        {
            "product_id": str(row[product]),
            "system_qty": float(row[system]),
            "physical_qty": _cell(row, physical),
            "unit_cost": _cell(row, unit_cost),
        }
        for _, row in df.iterrows()
    ]


def prepare(data_path: str, params: dict | None = None) -> list[dict]:
    """Read a count CSV and build the reconcile records."""
    return prepare_records(pd.read_csv(data_path), params)


def _name_list(ids: tuple[str, ...]) -> str:
    shown = ", ".join(ids[:_MAX_NAMED])
    return shown + (f", and {len(ids) - _MAX_NAMED} more" if len(ids) > _MAX_NAMED else "")


def run(records: list[dict], *, tolerance_pct: float = 0.0, tolerance_units: float = 0.0) -> IRAReport:
    """Reconcile the lines that were counted, price the ones that can be priced, declare the rest."""
    counted = [r for r in records if r.get("physical_qty") is not None]
    uncounted = tuple(str(r["product_id"]) for r in records if r.get("physical_qty") is None)

    # The engine takes floats, so an unpriced line still enters as 0.0 - but from here on
    # nothing is allowed to believe that zero.
    priced_ids = {str(r["product_id"]) for r in counted if r.get("unit_cost") is not None}
    engine_records = [
        {**r, "unit_cost": r["unit_cost"] if r.get("unit_cost") is not None else 0.0}
        for r in counted
    ]
    results = tuple(reconcile(engine_records, tolerance_pct=tolerance_pct, tolerance_units=tolerance_units))

    # A line that matched exactly contributes 0 whatever its cost, so its missing price
    # cannot corrupt a sum. Only a *nonzero* variance without a price can.
    unpriced = tuple(
        r.product_id for r in results
        if r.variance != 0 and r.product_id not in priced_ids
    )

    if unpriced:
        total_value = None
        note = (
            f"dollar impact not measured: {len(unpriced)} line(s) with a nonzero variance "
            f"carry no unit cost ({_name_list(unpriced)}). Send a unit cost for those lines "
            "and the impact can be totalled."
        )
    else:
        total_value = sum(abs(r.variance) * r.unit_cost for r in results)
        note = None

    if not results:
        ira: float | None = None
        ira_note: str | None = (
            f"inventory record accuracy not measured: none of the {len(records)} line(s) on "
            f"the sheet carries a physical quantity ({_name_list(uncounted)})"
        )
    else:
        ira = inventory_record_accuracy(list(results))
        ira_note = (
            f"accuracy is read off {len(results)} of {len(records)} line(s): "
            f"{len(uncounted)} were never counted ({_name_list(uncounted)}) and are neither "
            "accurate nor inaccurate"
        ) if uncounted else None

    out_of_tol = [r for r in results if not r.within_tolerance]
    worst = tuple(sorted(
        (r for r in out_of_tol if r.product_id in priced_ids),
        key=lambda r: abs(r.variance) * r.unit_cost, reverse=True,
    ))
    unvalued = tuple(r for r in out_of_tol if r.product_id not in priced_ids)

    return IRAReport(
        n_lines=len(records),
        n_counted=len(results),
        n_within=sum(1 for r in results if r.within_tolerance),
        ira=ira,
        ira_note=ira_note,
        total_variance_value=total_value,
        variance_value_note=note,
        tolerance_pct=tolerance_pct,
        tolerance_units=tolerance_units,
        worst=worst,
        unvalued=unvalued,
        unpriced=unpriced,
        uncounted=uncounted,
        results=results,
    )


def verify(report: IRAReport) -> list[str]:
    """QA gate, and an independent one: it recomputes rather than trusts.

    A declared gap is a legal delivery. What it refuses is the undeclared one - a number
    stated without the basis to compute it, or an absence hidden behind silence.
    """
    issues: list[str] = []
    if report.n_lines <= 0:
        issues.append("no count lines to reconcile")
    if report.n_counted <= 0:
        issues.append(
            "nothing on this sheet was counted: there is no accuracy to report until a "
            "physical quantity arrives"
        )
    if report.ira is not None and not 0.0 <= report.ira <= 1.0:
        issues.append(f"IRA out of [0,1]: {report.ira}")

    if report.unpriced and report.total_variance_value is not None:
        issues.append(
            f"a dollar impact is stated without the cost basis for it: "
            f"{_name_list(report.unpriced)} carry a nonzero variance and no unit cost"
        )
    if report.total_variance_value is None and not report.variance_value_note:
        issues.append("the dollar impact is absent and no motive is recorded")
    if report.uncounted and not report.ira_note:
        issues.append(
            f"{len(report.uncounted)} line(s) were never counted and the accuracy figure "
            "does not say so"
        )
    if report.ira is None and not report.ira_note:
        issues.append("the accuracy is absent and no motive is recorded")

    # Recompute rather than trust: a stated total that drifted from its own lines is the
    # exact failure a QA gate exists to catch.
    if not report.unpriced and report.total_variance_value is not None:
        recomputed = sum(abs(r.variance) * r.unit_cost for r in report.results)
        if abs(recomputed - report.total_variance_value) > 0.005:
            issues.append(
                f"the stated dollar impact {report.total_variance_value:,.2f} does not "
                f"recompute from its own lines ({recomputed:,.2f})"
            )
    return issues


def write_operational(report: IRAReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the out-of-tolerance lines, priced where possible.

    An unvalued variance leaves the cell BLANK. A spreadsheet full of 0 in that column
    reads as "these cost nothing", which is the most favourable reading available and
    nobody chose it.
    """
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    unpriced = set(report.unpriced)
    rows = [
        {
            "product_id": r.product_id,
            "system_qty": round(r.system_qty, 2),
            "physical_qty": round(r.physical_qty, 2),
            "variance": round(r.variance, 2),
            "variance_value": None if r.product_id in unpriced else round(abs(r.variance) * r.unit_cost, 2),
        }
        for r in (*report.worst, *report.unvalued)
    ]
    return {"csv": write_summary_csv(rows, d / "reconciliation.csv")}


def build_deck(
    report: IRAReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the IRA study: how trustworthy the records are and where they break."""
    n_out = report.n_counted - report.n_within
    money = _NOT_MEASURED if report.total_variance_value is None else f"{report.total_variance_value:,.0f}"
    accuracy = _NOT_MEASURED if report.ira is None else f"{report.ira * 100:.0f}%"

    summary = (
        f"Inventory record accuracy {accuracy} across {report.n_counted} of {report.n_lines} "
        f"line(s); {n_out} out of tolerance, variance value {money}."
    )
    if report.ira_note:
        summary += f" {report.ira_note}."

    findings = [
        Finding(
            "Inventory record accuracy vs target",
            (f"IRA {accuracy} against a {_IRA_TARGET * 100:.0f}% target; "
             f"{report.n_within}/{report.n_counted} counted line(s) within tolerance."
             + (f" {report.ira_note}." if report.ira_note else "")),
            impact="below target means the system stock can't be trusted for planning",
        ),
        Finding(
            "Dollar impact of the discrepancies",
            (f"{money} total absolute variance value across {n_out} mismatched line(s)."
             if report.total_variance_value is not None
             else f"{report.variance_value_note}."),
            impact=("prioritize the high-value variances for root-cause"
                    if report.total_variance_value is not None
                    else "the biggest variance may well be one of the unpriced lines"),
        ),
    ]
    if report.unvalued:
        # Named explicitly, because the ranking below cannot see them and the reader
        # would otherwise take the top of that ranking as the top of the sheet.
        findings.append(Finding(
            "Discrepancies that could not be priced",
            f"{_name_list(tuple(r.product_id for r in report.unvalued))} carry a variance "
            "with no unit cost, so they sit outside the dollar ranking entirely.",
            impact="send a unit cost for these before trusting any ranking by value",
        ))
    elif report.worst:
        w = report.worst[0]
        findings.append(Finding(
            f"Biggest discrepancy: {w.product_id}",
            f"System {w.system_qty:,.0f} vs physical {w.physical_qty:,.0f} "
            f"(variance {w.variance:+,.0f}, {abs(w.variance) * w.unit_cost:,.0f} value).",
            impact="root-cause this SKU first (receiving, picking, or shrink)",
        ))
    if report.uncounted:
        findings.append(Finding(
            "Lines nobody counted",
            f"{len(report.uncounted)} of {report.n_lines} line(s) carry no physical "
            f"quantity ({_name_list(report.uncounted)}): not counted, so neither accurate "
            "nor inaccurate.",
            impact="the accuracy figure describes the counted subset, not the sheet",
        ))

    kpis = (
        Kpi("Inventory record accuracy", accuracy, target="maximize",
            rationale="Share of counted lines within tolerance (target ~97%+)"),
        Kpi("Lines counted", f"{report.n_counted} of {report.n_lines}",
            rationale="Size of the count sample against the sheet"),
        Kpi("Out of tolerance", f"{n_out}", target="minimize",
            rationale="Lines whose variance exceeds the band"),
        Kpi("Variance value", money, target="minimize",
            rationale="Absolute dollar impact of all variances"),
    )

    data_sources = (
        DataSource("Count lines (system vs physical qty + unit cost)", "WMS / cycle-count sheets", "per count"),
    )

    recommendations = ["Root-cause the highest-value variances first (receiving, picking, shrink)."]
    if report.unpriced:
        recommendations.append(
            "Send a unit cost for every line with a variance: without it the dollar impact "
            "cannot be totalled and the ranking by value is incomplete."
        )
    if report.uncounted:
        recommendations.append(
            f"Count the {len(report.uncounted)} line(s) still blank before reading the "
            "accuracy figure as the sheet's."
        )
    recommendations.append("Raise cycle-count frequency on A-items until IRA holds above target.")

    degraded = confidence
    if report.variance_value_note or report.ira_note or report.ira is None:
        degraded = max(_CONFIDENCE_FLOOR, confidence - _MISSING_BASIS_PENALTY)

    return Deliverable(
        title="Inventory Record Accuracy (IRA) Study",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        # The caller's confidence is a ceiling, not an override: scm_agent passes 0.85 in
        # unconditionally and cannot know what the sheet was missing.
        confidence=degraded,
        residual="Counts are a sample at a point in time - confirm the count method and "
                 "tolerance band with operations before acting on the IRA figure.",
        prepared=prepared,
    )

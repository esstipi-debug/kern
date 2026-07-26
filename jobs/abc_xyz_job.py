"""ABC-XYZ classification agent job: per-SKU demand history -> the 9-cell matrix.

The data-prep + deck half of the ABC-XYZ tool. Aggregates a long-format demand history
(one row per SKU-period) into the classifier's item shape with pandas directly
(deliberately *not* via jobs/intake.py, which the parallel loop owns), runs
``src.classification``, and composes the client deck inline.

Pure-ish: ``aggregate_skus`` / ``run`` / ``verify`` / ``build_deck`` are deterministic;
``prepare`` reads a file. Column names are sniffed and overridable via params.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.classification import (
    SkuClassification,
    ValueBasis,
    classify_portfolio,
    portfolio_summary,
    value_basis,
)
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.guided import confidence_from_coverage
from src.export import write_summary_csv

_PRODUCT_COLS = ("product_id", "ProductID", "sku", "SKU", "Product", "item")
_DEMAND_COLS = ("quantity", "Quantity", "demand", "Demand", "units", "qty", "Sales", "sales")
_COST_COLS = ("unit_cost", "Unit Cost", "cost", "Cost", "price", "Price")
# Editorial prior for a complete ABC-XYZ study; coverage gaps subtract from it.
_DECK_BASE_CONFIDENCE = 0.9


@dataclass(frozen=True)
class AbcXyzReport:
    classifications: tuple[SkuClassification, ...]
    summary: dict          # per-cell counts / value share
    n_skus: int
    n_a: int
    n_cz: int              # erratic + low-value -> discontinuation candidates
    a_value_share: float   # share of total annual value held by the A class
    # Whether annual_value ranks on value or (flat unit cost) merely on units
    value_basis: ValueBasis = ValueBasis(0, 0.0, True)


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def aggregate_skus(
    df: pd.DataFrame, *, product_col: str, demand_col: str, cost_col: str
) -> list[dict]:
    """Roll a long-format history into one classifier item per SKU."""
    items: list[dict] = []
    for product, g in df.groupby(product_col):
        items.append({
            "product_id": str(product),
            "unit_cost": float(g[cost_col].mean()),
            "demand": [float(x) for x in g[demand_col].to_numpy()],
        })
    return items


def prepare(data_path: str, params: dict | None = None) -> list[dict]:
    """Read a per-SKU demand CSV and aggregate it to classifier items."""
    params = params or {}
    df = pd.read_csv(data_path)
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    demand = _pick_column(df, params.get("demand_col"), _DEMAND_COLS)
    cost = _pick_column(df, params.get("cost_col"), _COST_COLS)
    missing = [n for n, c in (("product_col", product), ("demand_col", demand), ("cost_col", cost)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")
    return aggregate_skus(df, product_col=product, demand_col=demand, cost_col=cost)


def run(
    items: list[dict],
    *,
    abc_thresholds: tuple[float, float] = (0.80, 0.95),
    cv_cuts: tuple[float, float] = (0.5, 1.0),
) -> AbcXyzReport:
    """Classify the portfolio into the ABC-XYZ matrix and roll up the headline counts."""
    classifications = classify_portfolio(items, abc_thresholds=abc_thresholds, cv_cuts=cv_cuts)
    summary = portfolio_summary(classifications)
    total_value = sum(c.annual_value for c in classifications) or 1.0
    a_value = sum(c.annual_value for c in classifications if c.abc == "A")
    return AbcXyzReport(
        classifications=tuple(classifications),
        summary=summary,
        n_skus=len(classifications),
        n_a=sum(1 for c in classifications if c.abc == "A"),
        n_cz=sum(1 for c in classifications if c.cell == "CZ"),
        a_value_share=a_value / total_value,
        value_basis=value_basis(items),
    )


def verify(report: AbcXyzReport) -> list[str]:
    """QA gate: a usable classification has SKUs and a valid cell per SKU."""
    issues: list[str] = []
    if not report.classifications:
        issues.append("no SKUs to classify")
    for c in report.classifications:
        if len(c.cell) != 2 or c.abc not in "ABC" or c.xyz not in "XYZ":
            issues.append(f"invalid ABC-XYZ cell for {c.product_id}: {c.cell}")
    return issues


def write_operational(report: AbcXyzReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one row per SKU with its class + policy."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "product_id": c.product_id,
            "annual_value": round(c.annual_value, 2),
            "cumulative_share": round(c.cumulative_share, 4),
            "abc": c.abc,
            "cv": round(c.cv, 3) if c.cv != float("inf") else "inf",
            "xyz": c.xyz,
            "cell": c.cell,
            "service_level": c.service_level,
            "policy": c.policy,
        }
        for c in report.classifications
    ]
    return {"csv": write_summary_csv(rows, d / "abc_xyz_classification.csv")}


def build_deck(
    report: AbcXyzReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float | None = None,
) -> Deliverable:
    """Compose the client deck: where the value concentrates, what to focus vs cull.

    ``confidence`` is derived when not supplied: an ABC that reduces to a units
    ranking because unit cost is flat is worth less than one ranked on real value
    (see src/guided.py::confidence_from_coverage).
    """
    if confidence is None:
        confidence = confidence_from_coverage(
            [] if report.value_basis.discriminates else ["value_ranks_on_units"],
            base=_DECK_BASE_CONFIDENCE,
        )
    a_share = report.a_value_share * 100
    a_pct_skus = (report.n_a / report.n_skus * 100) if report.n_skus else 0.0
    n_z = sum(1 for c in report.classifications if c.xyz == "Z")

    summary = (
        f"Classified {report.n_skus} SKUs into the ABC-XYZ matrix: the {report.n_a} A-items "
        f"({a_pct_skus:.0f}% of SKUs) hold {a_share:.0f}% of annual value."
    )

    findings = [
        Finding("Value concentration (Pareto)",
                f"{report.n_a} A-items ({a_pct_skus:.0f}% of the catalog) drive {a_share:.0f}% of "
                "annual usage value - tightest control and highest service belong here.",
                impact="focus cycle counts + review cadence on the A class"),
        Finding(f"{n_z} erratic (Z) SKUs",
                "High demand variability (CV >= 1.0); a fixed reorder point misfires, so these go "
                "on a buffered / periodic-review policy.",
                impact="avoid stockouts and dead stock on lumpy movers"),
    ]
    if report.n_cz:
        findings.append(Finding(
            f"{report.n_cz} discontinuation candidates (CZ)",
            "Low value and erratic demand - the classic make-to-order / cull cell.",
            impact="free working capital; shrink the long tail"))
    if not report.value_basis.discriminates:
        findings.append(Finding(
            "Ranked on units, not value",
            f"Every SKU carries the same unit cost, so unit cost did not discriminate "
            f"and annual usage value reduces to a units ranking "
            f"({report.value_basis.unit_cost_nunique} distinct cost(s) across "
            f"{report.n_skus} SKUs). The A/B/C split below is by volume.",
            impact="supply real unit costs before using this to set service levels or "
                   "cull the tail - a high-volume cheap SKU is not an A item"))

    kpis = (
        Kpi("SKUs classified", str(report.n_skus), rationale="Catalog coverage of the analysis"),
        Kpi("A-items", str(report.n_a), rationale="The few SKUs that drive most of the value"),
        Kpi("A value share", f"{a_share:.0f}%", target="~80%",
            rationale="Concentration of annual usage value in the A class (Pareto)"),
        Kpi("Erratic (Z) SKUs", str(n_z), rationale="Lumpy demand needing buffered policies"),
        Kpi("Discontinuation candidates", str(report.n_cz), target="review",
            rationale="Low-value, erratic SKUs to cull or make-to-order"),
    )

    data_sources = (
        DataSource("Per-SKU demand history & unit cost", "order/sales data", "monthly"),
        DataSource("ABC thresholds / CV cuts", "engagement parameters", "per run"),
    )

    recommendations = [
        f"Apply each cell's service-level target and review policy (A {report.n_a} SKUs at 98%).",
        "Concentrate cycle counts and forecast effort on the A class.",
    ]
    if report.n_cz:
        recommendations.append(f"Review the {report.n_cz} CZ SKUs for make-to-order or discontinuation.")

    return Deliverable(
        title="ABC-XYZ Classification",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual="Confirm the ABC thresholds and service-level targets against the client's "
                 "commercial priorities before locking policies per cell.",
        prepared=prepared,
    )

"""Data-quality / SKU-master (MDM) agent job: a product master CSV -> a clean-up plan.

The data-prep + deck + guided-options half of the data-quality tool. Reads a product master
(sku, name, gtin, unit cost) with pandas directly (deliberately *not* via jobs/intake.py,
which the parallel loop owns), then audits it:

  1. **dedup** - clusters likely-duplicate SKUs via ``src.sku_dedup`` (shared valid GTIN, then
     fuzzy name match),
  2. **validate** - checks each GTIN's GS1 mod-10 check digit via ``src.data_quality``,
  3. **completeness** - flags missing names, non-positive costs and exact-duplicate ids,

scores overall quality (share of fully-clean records) and emits a protected ``GuidedOutcome``
with **ranked remediation options** (merge+remediate / merge-only / steward exception list)
so the tool offers *choices to act*, not just an error list.

Mirrors jobs/returns_job.py (engine + guided options) and jobs/whatif_job.py (prepare/sniff).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from nosilo_core.guided import GuidedOutcome, recommend, verify_guided
from src.data_quality import is_valid_gtin
from src.decision_options import Objective, Scenario, decide
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.sku_dedup import DuplicateCluster, find_duplicates

_PRODUCT_COLS = ("sku", "product_id", "product", "item", "SKU", "Product", "material", "article")
_NAME_COLS = ("name", "description", "product_name", "desc", "title", "Name", "Description")
_GTIN_COLS = ("gtin", "upc", "ean", "barcode", "GTIN", "UPC", "EAN")
_COST_COLS = ("unit_cost", "cost", "price", "unit_price", "cogs", "Unit Cost")

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class QualityIssue:
    """One data-quality defect on one record: the field, the issue type and its severity."""

    product_id: str
    field: str
    issue: str
    severity: str   # high | medium | low
    detail: str


@dataclass(frozen=True)
class DataQualityReport:
    """The audit: duplicate clusters, GTIN validity, the issue register and a quality score."""

    n_records: int
    n_clean: int
    quality_score: float                          # n_clean / n_records, 0..1
    duplicate_clusters: tuple[DuplicateCluster, ...]
    n_duplicate_skus: int
    issues: tuple[QualityIssue, ...]              # ranked by severity
    issue_counts: dict
    gtin_valid: int
    gtin_invalid: int
    gtin_missing: int
    outcome: GuidedOutcome                        # ranked, executable remediation options
    recommended_action: str
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[dict]:
    """Sniff the master columns and build one record dict per row (product_id, name, gtin, cost).

    Required column: product (raises ValueError if absent). Name/GTIN/cost are optional.
    """
    params = params or {}
    product_col = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    if product_col is None:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find a product/sku column; pass product_col in params (columns seen: {cols})")
    name_col = _pick_column(df, params.get("name_col"), _NAME_COLS)
    gtin_col = _pick_column(df, params.get("gtin_col"), _GTIN_COLS)
    cost_col = _pick_column(df, params.get("cost_col"), _COST_COLS)

    records: list[dict] = []
    for _, row in df.iterrows():
        cost = None
        if cost_col and pd.notna(row[cost_col]):
            try:
                cost = float(row[cost_col])
            except (TypeError, ValueError):
                cost = None
        records.append({
            "product_id": str(row[product_col]).strip(),
            "name": str(row[name_col]).strip() if name_col and pd.notna(row[name_col]) else "",
            "gtin": str(row[gtin_col]).strip() if gtin_col and pd.notna(row[gtin_col]) else "",
            "unit_cost": cost,
        })
    if not records:
        raise ValueError("no records found in the data")
    return records


def prepare(data_path: str, params: dict | None = None) -> list[dict]:
    """Read a product-master CSV (codes as strings, to keep GTIN leading zeros) and build records."""
    return prepare_records(pd.read_csv(data_path, dtype=str), params)


def _audit_issues(records: list[dict], clusters: tuple[DuplicateCluster, ...]) -> tuple[list[QualityIssue], int, int, int]:
    """Walk the records once, collecting issues and GTIN validity counts."""
    issues: list[QualityIssue] = []
    counts_by_id: dict[str, int] = {}
    gtin_valid = gtin_invalid = gtin_missing = 0
    for r in records:
        pid = r["product_id"]
        counts_by_id[pid] = counts_by_id.get(pid, 0) + 1
        if not pid:
            issues.append(QualityIssue("", "product_id", "missing_id", "high", "blank product identifier"))
        if not r["name"]:
            issues.append(QualityIssue(pid, "name", "missing_name", "medium", "blank product name"))
        gtin = r["gtin"]
        if not gtin:
            gtin_missing += 1
        elif is_valid_gtin(gtin):
            gtin_valid += 1
        else:
            gtin_invalid += 1
            issues.append(QualityIssue(pid, "gtin", "invalid_gtin", "high", f"GTIN '{gtin}' fails the GS1 check digit"))
        cost = r["unit_cost"]
        if cost is not None and cost <= 0:
            issues.append(QualityIssue(pid, "unit_cost", "nonpositive_cost", "medium", f"unit cost {cost:g} <= 0"))
    for pid, cnt in counts_by_id.items():
        if pid and cnt > 1:
            issues.append(QualityIssue(pid, "product_id", "duplicate_id", "high", f"{cnt} rows share id '{pid}'"))
    for c in clusters:
        for pid in c.product_ids:
            others = ", ".join(p for p in c.product_ids if p != pid)
            issues.append(QualityIssue(
                pid, "sku", f"duplicate_{c.reason}", "medium",
                f"{c.reason} duplicate of {others} (score {c.score:.0f})",
            ))
    return issues, gtin_valid, gtin_invalid, gtin_missing


def _remediation_outcome(base_summary: str, *, dirty_share: float, dup_share: float) -> GuidedOutcome:
    """Rank three remediation policies (quality gain vs effort vs safety of auto-editing)."""
    scenarios = [
        Scenario(
            "merge_and_remediate",
            "Merge duplicate clusters, fix invalid GTINs and complete missing fields",
            {"quality_gain": dirty_share, "effort": 0.9, "safety": 0.5},
            action="auto-merge duplicates + remediate every flagged field",
            tradeoffs="restores the most quality; auto-editing master data carries merge risk",
        ),
        Scenario(
            "merge_duplicates",
            "Merge only the duplicate clusters (leave other fields for later)",
            {"quality_gain": dup_share, "effort": 0.4, "safety": 0.8},
            action="merge the duplicate SKU clusters only",
            tradeoffs="quick win on the biggest issue; leaves GTIN / completeness gaps",
        ),
        Scenario(
            "steward_exception_list",
            "Hand the issue register to a data steward to review and fix",
            {"quality_gain": dirty_share, "effort": 0.2, "safety": 1.0},
            action="route the exception list to the data steward",
            tradeoffs="safest for master data (human review); slower to resolve",
        ),
    ]
    objectives = [
        Objective("quality_gain", weight=2.0, maximize=True),
        Objective("effort", weight=1.0),            # cost-like -> minimized
        Objective("safety", weight=1.0, maximize=True),
    ]
    return decide(base_summary, scenarios, objectives, confidence=0.8)


def run(records: list[dict], *, name_threshold: float = 90.0) -> DataQualityReport:
    """Audit the master (dedup + GTIN + completeness) and present ranked remediation options."""
    dup_items = [{"product_id": r["product_id"], "name": r["name"], "gtin": r["gtin"]} for r in records]
    clusters = tuple(find_duplicates(dup_items, name_threshold=name_threshold))
    clustered_ids = {pid for c in clusters for pid in c.product_ids}

    issues, gtin_valid, gtin_invalid, gtin_missing = _audit_issues(records, clusters)
    issues.sort(key=lambda i: _SEVERITY_RANK.get(i.severity, 3))

    issue_pids = {i.product_id for i in issues}
    n = len(records)
    n_clean = sum(1 for r in records if r["product_id"] not in issue_pids)
    quality_score = n_clean / n if n else 0.0
    n_dup = len(clustered_ids)
    issue_counts: dict[str, int] = {}
    for i in issues:
        issue_counts[i.issue] = issue_counts.get(i.issue, 0) + 1

    base_summary = (
        f"{n} SKUs audited: quality {quality_score * 100:.0f}%; {len(clusters)} duplicate cluster(s) "
        f"({n_dup} SKUs), {gtin_invalid} invalid GTIN(s), {len(issue_pids)} record(s) with issues."
    )
    outcome = _remediation_outcome(
        base_summary,
        dirty_share=(len(issue_pids) / n if n else 0.0),
        dup_share=(n_dup / n if n else 0.0),
    )
    recommended = recommend(outcome.options).label
    summary = f"{base_summary} Recommended: {recommended}."

    return DataQualityReport(
        n_records=n,
        n_clean=n_clean,
        quality_score=quality_score,
        duplicate_clusters=clusters,
        n_duplicate_skus=n_dup,
        issues=tuple(issues),
        issue_counts=issue_counts,
        gtin_valid=gtin_valid,
        gtin_invalid=gtin_invalid,
        gtin_missing=gtin_missing,
        outcome=outcome,
        recommended_action=recommended,
        summary=summary,
    )


def verify(report: DataQualityReport) -> list[str]:
    """QA gate: the options outcome honours the never-unprotected contract + a sane score."""
    issues = list(verify_guided(report.outcome))
    if report.n_records <= 0:
        issues.append("no records to audit")
    if not 0.0 <= report.quality_score <= 1.0:
        issues.append("quality score out of [0,1]")
    return issues


def write_operational(report: DataQualityReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the exception register, one row per detected issue."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "product_id": i.product_id,
            "field": i.field,
            "issue": i.issue,
            "severity": i.severity,
            "detail": i.detail,
        }
        for i in report.issues
    ]
    return {"csv": write_summary_csv(rows, d / "data_quality_issues.csv")}


def build_deck(
    report: DataQualityReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.8,
) -> Deliverable:
    """Compose the data-quality study: where the master is dirty and the ranked ways to fix it."""
    n = report.n_records
    summary = (
        f"Data-quality audit of {n} SKU(s): quality score {report.quality_score * 100:.0f}% "
        f"({report.n_clean}/{n} clean). {len(report.duplicate_clusters)} duplicate cluster(s) "
        f"covering {report.n_duplicate_skus} SKUs; {report.gtin_invalid} invalid / "
        f"{report.gtin_valid} valid GTINs."
    )

    findings = []
    if report.duplicate_clusters:
        shown = "; ".join(
            f"{{{', '.join(c.product_ids)}}} ({c.reason}, score {c.score:.0f})"
            for c in report.duplicate_clusters[:3]
        )
        findings.append(Finding(
            f"Duplicate SKUs: {len(report.duplicate_clusters)} cluster(s), {report.n_duplicate_skus} SKUs",
            f"{shown} - shared GTIN or near-identical name. Duplicates split demand history and double-count stock.",
            impact="merge to one master record so forecasting and inventory see the true series",
        ))
    else:
        findings.append(Finding(
            "No duplicate SKUs detected",
            "no shared-GTIN or fuzzy-name duplicates at the current threshold.",
            impact="the master is free of obvious duplicates",
        ))
    findings.append(Finding(
        "GTIN validity (GS1 check digit)",
        f"{report.gtin_valid} valid, {report.gtin_invalid} invalid, {report.gtin_missing} missing - "
        "an invalid GTIN breaks barcode scanning and EDI/marketplace listings.",
        impact="correct invalid GTINs before they propagate to labels and channel feeds",
    ))
    top_issues = ", ".join(f"{k} ({v})" for k, v in sorted(report.issue_counts.items(), key=lambda kv: -kv[1])[:4])
    findings.append(Finding(
        "Completeness & issue register",
        f"{len(report.issues)} issue(s) across {n} record(s); top types: {top_issues or 'none'}.",
        impact="work the high-severity issues (invalid GTIN, duplicate id) first",
    ))
    opts = report.outcome.options
    findings.append(Finding(
        "Remediation policy (choose one)",
        "; ".join(
            f"{i + 1}. {o.label}{' [recommended]' if o.recommended else ''} - {o.tradeoffs}"
            for i, o in enumerate(opts)
        ),
        impact="pick a policy; master data usually warrants steward review before auto-merging",
    ))

    kpis = (
        Kpi("Quality score", f"{report.quality_score * 100:.0f}%", target="maximize",
            rationale="Share of records with no detected issue"),
        Kpi("Clean records", f"{report.n_clean}/{n}", target="maximize",
            rationale="Fully-clean master records"),
        Kpi("Duplicate clusters", f"{len(report.duplicate_clusters)} ({report.n_duplicate_skus} SKUs)",
            target="0", rationale="Likely-duplicate SKUs to merge"),
        Kpi("Invalid GTINs", f"{report.gtin_invalid}", target="0",
            rationale="GTINs that fail the GS1 mod-10 check digit"),
        Kpi("Records with issues", f"{len({i.product_id for i in report.issues})}", target="minimize",
            rationale="Distinct records carrying at least one defect"),
    )

    data_sources = (
        DataSource("Product master (sku / name / gtin / unit cost)", "ERP / PIM export", "on change"),
        DataSource("GTIN check digit + fuzzy dedup", "GS1 mod-10 + src.sku_dedup", "deterministic"),
    )

    recommendations = [
        "Adopt the recommended remediation policy; for master data, prefer steward review before auto-merging.",
        "Fix invalid GTINs at the source (PIM) so corrections propagate to labels and channel feeds.",
        "Re-run on every master export; clean inputs lift every downstream tool (forecast, inventory, ABC).",
    ]

    return Deliverable(
        title="Data Quality & SKU Master (MDM)",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        options=tuple(report.outcome.options),   # ranked remediation -> the deck's "Options to act"
        citations=tuple(citations),
        confidence=confidence,
        residual="duplicate/validity flags are heuristics; confirm merges and GTIN fixes with the "
                 "data owner before editing the master.",
        prepared=prepared,
    )

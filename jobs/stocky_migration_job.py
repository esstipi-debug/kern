"""Stocky migration check -- the productized service behind Kern catalog rung
#2 (documentation/MONETIZATION_BRIEF.md, "Chequeo de migracion Stocky",
USD 350-400 fijo).

A merchant with Shopify Stocky (shutting down 2026-08-31) exports their CSVs;
this job parses them (``jobs.stocky_importer``), audits the migrated SKU master
(``jobs.data_quality_job``), and renders the honest verdict
(``src.stocky_migration``): *does Shopify native cover what Stocky did, or is
there a gap you need Kern for?* Read-only from CSV -- no Shopify API, no
writeback, no live connector (that is Fase-2 territory). Operator-run, not a
registered agent tool (same standing as ``src/decision_support.py`` /
``/decisiones``): the deliverable is a migration report a human sends, sold
with a landing + payment link + Calendly.

prepare -> run -> write_operational, mirroring the other jobs' shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.stocky_migration import (
    MigrationAssessment,
    SkuMasterAudit,
    assess_migration,
    audit_sku_master,
)

from .stocky_importer import (
    StockyMigrationBatch,
    parse_stocky_purchase_orders_csv,
    parse_stocky_reorder_points_csv,
    parse_stocky_suppliers_csv,
    to_client_profile_params,
)


@dataclass(frozen=True)
class StockyMigrationResult:
    """Everything the deliverable needs: the migration verdict, the structural
    SKU-master audit, the profile params the batch can seed, and the counts."""

    assessment: MigrationAssessment
    sku_audit: SkuMasterAudit
    batch_summary: dict
    client_profile_params: dict
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _read(path: str | Path | None) -> str | None:
    if not path:
        return None
    return Path(path).read_text(encoding="utf-8")


def prepare(
    *,
    suppliers_csv: str | Path | None = None,
    purchase_orders_csv: str | Path | None = None,
    reorder_points_csv: str | Path | None = None,
) -> StockyMigrationBatch:
    """Parse whichever of the three Stocky exports the merchant provided into a
    single :class:`~jobs.stocky_importer.StockyMigrationBatch`. Every argument
    is optional -- a merchant who exported only reorder points still gets a
    (partial) assessment, with the missing categories honestly reported as
    ``not_in_export`` rather than assumed clean."""
    batch = StockyMigrationBatch()
    suppliers_text = _read(suppliers_csv)
    pos_text = _read(purchase_orders_csv)
    reorder_text = _read(reorder_points_csv)
    if suppliers_text is not None:
        batch.suppliers = parse_stocky_suppliers_csv(suppliers_text)
    if pos_text is not None:
        batch.purchase_orders = parse_stocky_purchase_orders_csv(pos_text)
    if reorder_text is not None:
        batch.reorder_points = parse_stocky_reorder_points_csv(reorder_text)
    return batch


def run(batch: StockyMigrationBatch) -> StockyMigrationResult:
    """Assess Shopify-native migration readiness + run the structural
    SKU-master audit + seed the client profile from what the batch honestly
    supports."""
    assessment = assess_migration(batch)
    sku_audit = audit_sku_master(batch)
    profile_params = to_client_profile_params(batch)

    warnings: list[str] = []
    if not batch.reorder_points:
        warnings.append("Sin export de puntos de reorden -- la brecha de reorden no pudo cuantificarse por SKU.")
    if not batch.suppliers:
        warnings.append("Sin export de proveedores -- lead times/MOQ no disponibles para el perfil.")

    return StockyMigrationResult(
        assessment=assessment,
        sku_audit=sku_audit,
        batch_summary=batch.summary(),
        client_profile_params=profile_params,
        warnings=tuple(warnings),
    )


def _verdict_report_md(result: StockyMigrationResult, *, client: str) -> str:
    a = result.assessment
    lines = [
        f"# Chequeo de migracion Stocky -- {client}",
        "",
        "## Veredicto",
        "",
        a.headline,
        "",
        "## Cobertura por categoria (Stocky -> Shopify nativo)",
        "",
        "| Categoria | En export | Registros | Shopify nativo | Veredicto |",
        "|---|---|---|---|---|",
    ]
    label = {"migrates_clean": "migra limpio", "gap": "BRECHA (necesita Kern)", "not_in_export": "no exportado"}
    for c in a.categories:
        native = "si" if c.shopify_native_covers else "no"
        present = "si" if c.present else "no"
        lines.append(f"| {c.category} | {present} | {c.record_count} | {native} | {label.get(c.verdict, c.verdict)} |")

    lines += ["", "## Detalle de brechas", ""]
    gap_cats = [c for c in a.categories if c.verdict == "gap"]
    if gap_cats:
        for c in gap_cats:
            lines.append(f"- **{c.category}**: {c.detail}")
    else:
        lines.append("Sin brechas: los datos exportados migran a Shopify nativo.")

    lines += ["", "## Calidad del maestro de SKUs (pre-migracion)", ""]
    lines.append(result.sku_audit.summary)
    if result.sku_audit.duplicate_skus:
        lines.append(f"- SKUs duplicados: {', '.join(result.sku_audit.duplicate_skus)}")
    if result.sku_audit.inconsistent_minmax:
        lines.append(f"- min>max: {', '.join(result.sku_audit.inconsistent_minmax)}")
    if result.sku_audit.nonpositive_reorder:
        lines.append(f"- reorden <=0: {', '.join(result.sku_audit.nonpositive_reorder)}")

    if result.client_profile_params:
        lines += ["", "## Parametros de cliente detectados", ""]
        for k, v in result.client_profile_params.items():
            lines.append(f"- {k}: {v}")

    lines += ["", "## Proximos pasos recomendados", ""]
    for i, opt in enumerate(a.recommended_options, 1):
        lines.append(f"{i}. **{opt.label}** -- {opt.rationale}")

    if result.warnings:
        lines += ["", "## Advertencias", ""]
        for w in result.warnings:
            lines.append(f"- {w}")

    lines += ["", "---", "*Chequeo read-only sobre tus exports de Stocky. Kern no accede a tu tienda.*"]
    return "\n".join(lines)


def write_operational(result: StockyMigrationResult, out_dir: str | Path, *, client: str = "Cliente") -> dict[str, Path]:
    """Write the migration verdict as a markdown report (utf-8) into ``out_dir``.
    Returns the paths written, matching the other jobs' ``write_operational``
    contract."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "stocky_migration_report.md"
    report_path.write_text(_verdict_report_md(result, client=client), encoding="utf-8")
    return {"report": report_path}

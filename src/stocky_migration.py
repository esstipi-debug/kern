"""Stocky -> Shopify-native migration-readiness assessment (Kern catalog rung
#2, the "Chequeo de migracion Stocky" -- documentation/MONETIZATION_BRIEF.md).

Shopify is shutting Stocky down; a merchant exporting their Stocky data has to
answer one question before the deadline: **does Shopify's own native inventory
cover what Stocky did for me, or is there a gap I need to fill?** This module
answers it honestly.

The whole assessment is grounded in ONE explicit, auditable constant --
:data:`SHOPIFY_NATIVE_COVERAGE` -- so every "this migrates cleanly" / "this is
a gap" verdict traces to a stated fact, never to prose invented per run. The
honest framing matters: Shopify native is NOT featureless -- it now offers
basic purchase-order creation/receiving and a single supplier Vendor field. The
gap is about MIGRATION, not feature presence: suppliers can't be exported from
Stocky, a native PO CSV import only creates draft line items (no past statuses,
received quantities, or supplier links), and native has no automated reorder
points and no demand forecasting (verified against Shopify's own transition
guide). Those concrete losses -- not a made-up "native does nothing" -- are the
hook to Starter / the full Diagnostico, sold as facts the merchant can verify,
never as a scare.

Pure functions over a parsed ``jobs.stocky_importer.StockyMigrationBatch``: no
IO, no network, no Shopify API. The merchant provides CSV exports; this reads
the already-parsed batch and returns a verdict object for the job layer to
render.
"""

from __future__ import annotations

from dataclasses import dataclass

from jobs.stocky_importer import StockyMigrationBatch

# The single source of truth for every verdict below: whether Shopify's OWN
# native inventory (no third-party app) covers a given Stocky data category,
# and the stated reason. Auditable and easy to correct in one place if
# Shopify's native feature set changes.
# The bool = does this Stocky-provided capability/data MIGRATE cleanly to
# Shopify native? (False = gap.) NOT "does native have the feature at all" --
# native DOES have a basic version of several of these now; the point of a
# MIGRATION check is whether your Stocky data/automation carries over, and for
# all four below it does not (verified against Shopify's own transition guide,
# help.shopify.com/en/manual/products/inventory/transitioning-from-stocky, and
# the native purchase-orders help page). Reasons state honestly what native
# DOES have before naming the concrete loss -- an overclaim a merchant can
# refute in 30 seconds would kill the "honest verdict" the check is sold on.
SHOPIFY_NATIVE_COVERAGE: dict[str, tuple[bool, str]] = {
    "reorder_points": (
        False,
        "Shopify nativo muestra la cantidad en stock pero NO recrea el punto de reorden "
        "automatico de Stocky (calculado por lead time x ventas/dia): esa automatizacion "
        "de cuando/cuanto reponer se pierde en la migracion.",
    ),
    "purchase_orders": (
        False,
        "Shopify nativo SI crea y recibe ordenes de compra basicas, pero migrar desde Stocky "
        "es con perdida: el import CSV solo crea lineas en una OC draft y NO trae estados de "
        "OC pasados, cantidades recibidas ni el vinculo a proveedor (help.shopify.com, guia "
        "de transicion de Stocky).",
    ),
    "suppliers": (
        False,
        "Los proveedores NO se pueden exportar de Stocky, y el nativo solo tiene un campo "
        "Vendor por producto: multiples proveedores, lead times, MOQs y case packs se pierden "
        "o requieren metafields.",
    ),
    "demand_forecasting": (
        False,
        "Shopify nativo (fuera de Stocky) no pronostica demanda ni sugiere cantidades de "
        "reorden basadas en historial. Requiere una herramienta con tu historial de ventas.",
    ),
}

# Which parsed categories the assessment inspects, and how to count them.
_CATEGORY_COUNTERS = {
    "suppliers": lambda b: len(b.suppliers),
    "purchase_orders": lambda b: len(b.purchase_orders),
    "reorder_points": lambda b: len(b.reorder_points),
}


@dataclass(frozen=True)
class SkuMasterAudit:
    """Purpose-built structural check on the Stocky reorder/PO data BEFORE
    migration -- the honest "is this data clean enough to carry over?" audit.

    Deliberately NOT Kern's generic ``data_quality`` tool: that one fuzzy-dedups
    on product NAMES and validates GTINs, neither of which a Stocky reorder/PO
    export carries -- running it here would false-cluster every name-less SKU
    into one "duplicate" bucket and report a meaningless 0% score. These three
    checks are exactly the defects that actually break a reorder-point
    migration: the same SKU listed twice, a min above its max, and a
    non-positive reorder point."""

    n_skus: int
    duplicate_skus: tuple[str, ...]
    inconsistent_minmax: tuple[str, ...]
    nonpositive_reorder: tuple[str, ...]
    clean: bool
    summary: str


def audit_sku_master(batch: StockyMigrationBatch) -> SkuMasterAudit:
    """Structural pre-migration audit of the reorder-point master."""
    seen: dict[str, int] = {}
    for rp in batch.reorder_points:
        seen[rp.sku] = seen.get(rp.sku, 0) + 1
    duplicate = tuple(sorted(sku for sku, n in seen.items() if n > 1))
    inconsistent = tuple(
        sorted({rp.sku for rp in batch.reorder_points if rp.max_reorder_point and rp.min_reorder_point > rp.max_reorder_point})
    )
    nonpositive = tuple(sorted({rp.sku for rp in batch.reorder_points if rp.min_reorder_point <= 0}))

    n_skus = len({rp.sku for rp in batch.reorder_points} | {po.sku for po in batch.purchase_orders})
    n_issues = len(set(duplicate) | set(inconsistent) | set(nonpositive))
    clean = n_issues == 0 and bool(batch.reorder_points)
    if not batch.reorder_points:
        summary = "Sin puntos de reorden exportados -- no hay maestro de SKUs para auditar."
    elif clean:
        summary = f"{n_skus} SKUs: maestro de reorden limpio (sin duplicados, min<=max, reorden positivo)."
    else:
        summary = (
            f"{n_skus} SKUs, {n_issues} con defectos: {len(duplicate)} SKU duplicado(s), "
            f"{len(inconsistent)} con min>max, {len(nonpositive)} con reorden <=0. Limpiar antes de migrar."
        )
    return SkuMasterAudit(
        n_skus=n_skus,
        duplicate_skus=duplicate,
        inconsistent_minmax=inconsistent,
        nonpositive_reorder=nonpositive,
        clean=clean,
        summary=summary,
    )


@dataclass(frozen=True)
class CategoryVerdict:
    """One Stocky data category's migration verdict.

    ``verdict`` is ``"migrates_clean"`` (Shopify native covers it),
    ``"gap"`` (present in the export but no native home -> needs Kern), or
    ``"not_in_export"`` (the merchant did not export this category, so no
    claim is made -- golden rule "ningun cap silencioso": absence is reported,
    never assumed clean)."""

    category: str
    present: bool
    record_count: int
    shopify_native_covers: bool
    verdict: str
    detail: str


@dataclass(frozen=True)
class MigrationOption:
    label: str
    rationale: str


@dataclass(frozen=True)
class MigrationAssessment:
    """The honest answer to "does Shopify native replace Stocky for me?"."""

    categories: tuple[CategoryVerdict, ...]
    shopify_native_sufficient: bool
    headline: str
    gaps: tuple[str, ...]
    recommended_options: tuple[MigrationOption, ...]


def _category_verdict(category: str, count: int) -> CategoryVerdict:
    covered, reason = SHOPIFY_NATIVE_COVERAGE.get(
        category, (False, "Sin cobertura nativa conocida.")
    )
    present = count > 0
    if not present:
        return CategoryVerdict(
            category=category,
            present=False,
            record_count=0,
            shopify_native_covers=covered,
            verdict="not_in_export",
            detail="No incluido en los exports entregados -- no se evalua.",
        )
    verdict = "migrates_clean" if covered else "gap"
    return CategoryVerdict(
        category=category,
        present=True,
        record_count=count,
        shopify_native_covers=covered,
        verdict=verdict,
        detail=reason if not covered else "Cubierto por Shopify nativo.",
    )


def assess_migration(batch: StockyMigrationBatch) -> MigrationAssessment:
    """Classify each Stocky data category against Shopify-native coverage and
    return the overall verdict plus ranked next-step options.

    The verdict is data-driven: a category is a ``gap`` only when the merchant
    actually exported records for it AND Shopify native does not cover it.
    ``demand_forecasting`` is always evaluated as a gap-if-relevant signal --
    Stocky's migration exports never carry a sales history, so forecasting is
    inherently something the merchant loses on the move to native (surfaced
    whenever ANY inventory data was exported, since a catalog worth migrating
    is a catalog worth forecasting).
    """
    verdicts: list[CategoryVerdict] = [
        _category_verdict(cat, counter(batch)) for cat, counter in _CATEGORY_COUNTERS.items()
    ]

    # demand_forecasting has no direct Stocky export; treat it as "present"
    # (a real loss) whenever the merchant exported any inventory data at all.
    has_any_inventory = any(v.present for v in verdicts)
    forecasting_count = 1 if has_any_inventory else 0
    verdicts.append(_category_verdict("demand_forecasting", forecasting_count))

    gaps = tuple(v.category for v in verdicts if v.verdict == "gap")

    if not has_any_inventory:
        # Nothing to assess -- do NOT reassure the merchant that native "alcanza"
        # off zero data (that would be a silent cap on an empty input).
        return MigrationAssessment(
            categories=tuple(verdicts),
            shopify_native_sufficient=False,
            headline="No se exportaron datos de Stocky para evaluar. Entrega los CSV de proveedores, "
            "ordenes de compra y puntos de reorden para correr el chequeo.",
            gaps=(),
            recommended_options=(
                MigrationOption(
                    "Entrega tus exports de Stocky",
                    "El chequeo necesita al menos uno de: proveedores, ordenes de compra o puntos de reorden.",
                ),
            ),
        )

    sufficient = not gaps

    if sufficient:
        headline = (
            "Shopify nativo te alcanza: los datos exportados no dependen de capacidades "
            "que solo Stocky/Kern ofrecen."
        )
        options = (
            MigrationOption(
                "Quedate en Shopify nativo",
                "No se detectaron brechas: migra tus cantidades y segui operando en nativo.",
            ),
        )
    else:
        gap_labels = ", ".join(gaps)
        headline = (
            f"Shopify nativo NO te alcanza: {len(gaps)} brecha(s) sin hogar nativo "
            f"({gap_labels}). Ese trabajo lo hacia Stocky y lo reemplaza Kern."
        )
        options = (
            MigrationOption(
                "Kern Starter LatAm (USD 250-300/mes)",
                "Cubre las brechas detectadas (reorden, proveedores, forecasting) con revision "
                "humana; el escalon natural desde el chequeo.",
            ),
            MigrationOption(
                "Diagnostico de Arranque (USD 1.500-2.500, una vez)",
                "Si primero queres cuantificar el ahorro/riesgo antes de un retainer mensual.",
            ),
            MigrationOption(
                "Quedate en Shopify nativo (con las brechas asumidas)",
                "Viable solo si aceptas perder ordenes de compra, proveedores y automatizacion "
                "de reorden.",
            ),
        )

    return MigrationAssessment(
        categories=tuple(verdicts),
        shopify_native_sufficient=sufficient,
        headline=headline,
        gaps=gaps,
        recommended_options=options,
    )

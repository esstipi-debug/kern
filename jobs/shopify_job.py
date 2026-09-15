"""Shopify replenishment agent job: read a live storefront -> forecast -> ranked options.

The data-prep + deck half of the ``shopify_replenishment`` tool, and the DTC/marketplace
counterpart to ``jobs/odoo_job.py``. ``prepare`` reads from Shopify (the live
``ShopifyCommerceClient`` when SHOPIFY_* credentials are present, else the offline
``demo_shopify`` stand-in), so the whole connect -> decide -> hand-off loop demos with no
credentials. ``run`` forecasts each SKU from real order history and plans the restock to a
target cover, presenting it as >=2 ranked, executable options honouring the
never-unprotected contract.

**Where this deliberately differs from the Odoo job.** Odoo's recommended option stages
``product_min_qty`` -- a reorder point -- so the ERP raises the POs itself. Shopify has no
reorder point and no purchase order; its only writable inventory number is ``available``,
the physical count. Writing a target cover there would not buy a single unit, it would
tell the storefront it holds stock it does not hold and oversell against it. So the
recommended option here is a protected **handoff** (the purchasing happens in the ERP or
with the supplier), and the writeback plane is offered only for the Shopify write that is
actually true: reconciling ``available`` against a physical count, passed as ``counted``.

``run`` / ``verify`` / ``build_deck`` are deterministic given the connector; ``prepare``
opens the connection. Credentials come from params or the SHOPIFY_* env vars; tests inject
a ready RPC via ``params['shopify_rpc']``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from nosilo_core.escalation import escalation_banner, maybe_escalate_financial
from nosilo_core.guided import ExecutionOption, GuidedOutcome, as_options, verify_guided
from src.connectors.replenish import ReplenishmentLine, plan_replenishment
from src.connectors.shopify import ShopifyCommerceClient, ShopifyConnector, demo_shopify
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv

DEFAULT_LOCATION = "gid://shopify/Location/1"


@dataclass(frozen=True)
class ShopifyReplenishmentReport:
    source: str
    location_id: str
    lines: tuple[ReplenishmentLine, ...]
    restock: dict[str, float]
    n_skus: int
    n_restock: int
    total_restock: float
    restock_value: float
    cover_periods: float
    truncated: bool
    outcome: GuidedOutcome
    summary: str


def _make_connector(params: dict) -> tuple[ShopifyConnector, str]:
    """Resolve the Shopify transport: an injected RPC (tests) -> live client -> stand-in."""
    location = params.get("location_id") or os.environ.get("SHOPIFY_LOCATION_ID") or DEFAULT_LOCATION

    injected = params.get("shopify_rpc")
    if injected is not None:
        return ShopifyConnector(injected, location_id=location), "injected Shopify RPC"

    shop = params.get("shopify_shop_url") or os.environ.get("SHOPIFY_SHOP_URL")
    token = params.get("shopify_access_token") or os.environ.get("SHOPIFY_ACCESS_TOKEN")
    if shop and token:
        import httpx

        from src.connectors.shopify_inventory import ShopifyInventoryClient

        http = httpx.Client(base_url=shop, headers={"X-Shopify-Access-Token": token}, timeout=30.0)
        # Two clients over ONE http session: the commerce half reads catalog/orders, the
        # inventory half reads levels and holds the writeback store. Sharing the session
        # keeps a single connection pool and a single set of rate-limit headers.
        return (
            ShopifyConnector(ShopifyCommerceClient(http), ShopifyInventoryClient(http), location_id=location),
            f"live Shopify ({shop})",
        )

    return ShopifyConnector(demo_shopify(), location_id=location), "in-memory Shopify stand-in (demo data)"


def prepare(data_path: str | None = None, params: dict | None = None) -> dict:
    """Open the Shopify connection and read the catalog. ``data_path`` is unused (live source)."""
    params = params or {}
    connector, source = _make_connector(params)
    products = connector.list_products()
    return {
        "connector": connector,
        "source": source,
        "n_products": len(products),
        "costs": {p.sku: p.cost for p in products},
        "counted": dict(params.get("counted") or {}),
    }


def _build_outcome(
    n_restock: int,
    total_restock: float,
    *,
    restock_value: float = 0.0,
    financial_threshold: float = 50_000.0,
    counted: dict[str, float] | None = None,
) -> GuidedOutcome:
    """Replenishment as >=2 ranked, executable options (first = recommended default).

    The recommended option never writes to Shopify -- see the module docstring for why a
    storefront's ``available`` is not a place to put a plan. The writeback option appears
    only when the caller supplied a physical ``counted`` quantity, because without one
    there is no true number to write.
    """
    counted = counted or {}

    if n_restock > 0:
        options = [
            ExecutionOption(
                label="Export the restock plan for purchasing",
                summary=f"Hand the {total_restock:,.0f} units short across {n_restock} SKU(s) to "
                        "purchasing: Shopify holds no supplier and raises no PO, so the buy happens "
                        "in the ERP or with the supplier directly.",
                score=3.0, recommended=True,
                action="export the replenishment plan (no write-back to Shopify)",
                tradeoffs="zero risk to the storefront; the PO is raised outside Shopify",
            ),
            ExecutionOption(
                label="Lower the cover target",
                summary="Re-plan against a shorter cover to release working capital instead of buying.",
                score=1.0,
                action="re-run with a shorter cover_periods",
                tradeoffs="frees cash; less buffer against a demand spike",
            ),
        ]
        summary = (
            f"{n_restock} SKU(s) below target cover ({total_restock:,.0f} units short): "
            "choose how to replenish."
        )
    else:
        options = [
            ExecutionOption(
                label="Hold - inventory above target cover",
                summary="Every SKU is above its target cover; no replenishment is needed now.",
                score=3.0, recommended=True,
                action="monitor; no write-back needed", tradeoffs="no cost",
            ),
            ExecutionOption(
                label="Export the current position",
                summary="Export the stock-vs-target position for the review file.",
                score=1.0,
                action="export the replenishment position (no write-back)", tradeoffs="zero risk",
            ),
        ]
        summary = "All SKUs above target cover: choose how to proceed."

    if counted:
        # Ranked between the two above: a real correction beats re-planning, but never
        # outranks the plan itself -- reconciling stock is not replenishing it.
        options.insert(
            1,
            ExecutionOption(
                label=f"Reconcile Shopify's on-hand against the count ({len(counted)} SKU(s))",
                summary="Correct what the storefront believes is on the shelf to the counted "
                        "quantity, staged as a reversible dry-run.",
                score=2.0,
                action="stage + apply Shopify inventorySetQuantities (reversible, human-approved)",
                tradeoffs="fixes overselling from a drifted count; needs the count to be right",
            ),
        )

    return maybe_escalate_financial(as_options(summary, options), restock_value, financial_threshold)


def run(
    payload: dict, *, cover_periods: float = 8.0, financial_threshold: float = 50_000.0
) -> ShopifyReplenishmentReport:
    """Forecast each SKU from Shopify order history and plan the restock to ``cover_periods``.

    ``financial_threshold`` gates the options outcome on the restock's estimated $ value,
    priced from Shopify's own ``inventoryItem.unitCost``.
    """
    connector: ShopifyConnector = payload["connector"]
    # No `store=`: plan_replenishment would stage a restock into the writeback plane, and
    # the only field this connector's store writes is physical on-hand. See _build_outcome.
    plan = plan_replenishment(connector, cover_periods=cover_periods)
    restock = dict(plan.restock)
    total = sum(restock.values())
    n_skus = len(plan.lines)
    costs: dict[str, float] = payload.get("costs", {})
    restock_value = sum(qty * costs.get(sku, 0.0) for sku, qty in restock.items())
    summary = (
        f"Read {n_skus} product(s) from {payload['source']}; {len(restock)} below a "
        f"{cover_periods:.0f}-period cover ({total:,.0f} units short)."
    )
    return ShopifyReplenishmentReport(
        source=str(payload["source"]),
        location_id=connector.location_id,
        lines=plan.lines,
        restock=restock,
        n_skus=n_skus,
        n_restock=len(restock),
        total_restock=total,
        restock_value=restock_value,
        cover_periods=cover_periods,
        truncated=connector.truncated,
        outcome=_build_outcome(
            len(restock),
            total,
            restock_value=restock_value,
            financial_threshold=financial_threshold,
            counted=payload.get("counted"),
        ),
        summary=summary,
    )


def verify(report: ShopifyReplenishmentReport) -> list[str]:
    """QA gate: a usable plan honours the never-unprotected contract over real products."""
    issues = list(verify_guided(report.outcome))
    if report.n_skus == 0:
        issues.append("no variants with a SKU found in Shopify")
    if report.n_skus > 0 and not any(ln.forecast_per_period > 0 for ln in report.lines):
        # Every target is then zero and nothing is ever short: the plan would report
        # "all SKUs above cover" from an empty order history, which reads as good news.
        issues.append("no order history read from Shopify: every forecast is zero, so no SKU can be short")
    return issues


def write_operational(
    report: ShopifyReplenishmentReport, out_dir: str | Path, client: str = "Client"
) -> dict[str, Path]:
    """Machine-readable deliverable: one row per SKU with on-hand / forecast / target / restock.

    No lead-time column, unlike the Odoo export: Shopify models no supplier, so there is
    no lead time to read and a zero-filled column would be indistinguishable from a
    measured one.
    """
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "sku": ln.sku,
            "on_hand": round(ln.on_hand, 2),
            "forecast_per_period": round(ln.forecast_per_period, 2),
            "target": round(ln.target, 2),
            "restock_qty": round(ln.restock_qty, 2),
        }
        for ln in report.lines
    ]
    return {"csv": write_summary_csv(rows, d / "shopify_replenishment.csv")}


def build_deck(
    report: ShopifyReplenishmentReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float,
) -> Deliverable:
    """Compose the replenishment study: what is short, by how much, and how to act.

    ``confidence`` is required rather than defaulted, unlike the sibling jobs. Those
    carry a literal ``0.85`` that ``tests/test_qa_confidence_from_coverage.py`` counts
    as known debt under a ratchet that may only go down. Deriving it properly needs a
    ``COVERAGE_PENALTY`` term for "read a catalog with no order history behind it",
    and that vocabulary lives in the pinned ``nosilo_core`` -- a change in another
    repository, not this one. Requiring the caller to supply it adds no new literal
    and loses nothing: ``scm_agent/tools.py`` already passes the QA layer's figure on
    every production path.
    """
    short = [ln for ln in report.lines if ln.restock_qty > 0]
    findings: list[Finding] = []
    banner = escalation_banner(report.outcome)
    if banner:
        findings.append(Finding("Requires finance sign-off before acting", banner,
                                impact="do not act until the named approver signs off"))
    findings.append(
        Finding(
            f"{report.n_restock} SKU(s) below target cover",
            f"{report.total_restock:,.0f} units short of a {report.cover_periods:.0f}-period cover "
            f"across {report.n_skus} variant(s) read from {report.source}.",
            impact="replenish to avoid stockouts on the thin SKUs",
        )
    )
    if short:
        worst = max(short, key=lambda ln: ln.restock_qty)
        findings.append(
            Finding(
                f"Thinnest SKU: {worst.sku}",
                f"{worst.on_hand:.0f} on hand vs a {worst.target:.0f} target (+{worst.restock_qty:.0f} needed).",
                impact="prioritize this replenishment line",
            )
        )
    if report.truncated:
        findings.append(
            Finding(
                "Catalog read hit the page limit",
                "Shopify returned a full page of variants, so the storefront may hold more than "
                "were read. The plan covers the variants in this page only.",
                impact="re-read with pagination before treating this as a full-catalog plan",
            )
        )
    kpis = (
        Kpi("Variants read", str(report.n_skus), rationale=f"From {report.source}"),
        Kpi("SKUs to replenish", str(report.n_restock), target="0", rationale="Below target cover"),
        Kpi("Units short", f"{report.total_restock:,.0f}", target="0", rationale="Total restock to reach target"),
        Kpi("Restock value", f"${report.restock_value:,.0f}",
            rationale="Priced at Shopify's own inventoryItem.unitCost"),
    )
    data_sources = (
        DataSource("Catalog / orders", "Shopify Admin API (GraphQL: productVariants, orders)", "live"),
        DataSource("On-hand", f"Shopify inventory levels at {report.location_id}", "live"),
        DataSource("Supplier lead times", "not modelled by Shopify - unavailable", "absent"),
    )
    recommendations = [
        "Export the restock plan and raise the POs where the supplier actually lives (ERP or direct).",
        "Reconcile Shopify's on-hand against a physical count before trusting the cover figures.",
    ]
    return Deliverable(
        title="Shopify Replenishment",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual=(f"{banner} " if banner else "")
                 + "The recommended option writes nothing to Shopify: a storefront's on-hand is "
                   "physical stock, not a plan. The one write offered - reconciling on-hand to a "
                   "physical count - goes through the safe-staging plane (dry-run, reversible) and "
                   "requires an explicit human approval; it never auto-applies.",
        prepared=prepared,
    )

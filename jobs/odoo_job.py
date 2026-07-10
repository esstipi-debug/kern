"""Odoo replenishment agent job: read a live Odoo -> forecast -> ranked restock options.

The data-prep + deck half of the ``odoo_replenishment`` tool. Unlike the CSV tools, its
``prepare`` reads from Odoo (the live ``OdooClient`` when ODOO_* credentials are present,
else the offline ``InMemoryOdoo`` stand-in), so the agent can demo the whole connect ->
decide -> write-back loop with no credentials. ``run`` forecasts each SKU and plans the
restock to a target cover, then presents the replenishment as >=2 ranked, executable
options (apply reorder points / raise draft POs / export) honouring the never-unprotected
contract. Writing back to Odoo goes through the connector's safe-staging plane.

``run`` / ``verify`` / ``build_deck`` are deterministic given the connector; ``prepare``
opens the connection. Credentials come from params or the ODOO_* env vars; tests inject a
ready RPC via ``params['odoo_rpc']``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from src.connectors.odoo import OdooClient, OdooConnector, OdooRPC, demo_odoo
from src.connectors.replenish import ReplenishmentLine, plan_replenishment
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.escalation import escalation_banner, maybe_escalate_financial
from src.export import write_summary_csv
from src.guided import ExecutionOption, GuidedOutcome, as_options, verify_guided


@dataclass(frozen=True)
class OdooReplenishmentReport:
    source: str
    lines: tuple[ReplenishmentLine, ...]
    restock: dict[str, float]
    lead_times: dict[str, float]
    n_skus: int
    n_restock: int
    total_restock: float
    cover_periods: float
    outcome: GuidedOutcome
    summary: str


def _make_rpc(params: dict) -> tuple[OdooRPC, str]:
    """Resolve the Odoo transport: an injected RPC (tests) -> live OdooClient -> stand-in."""
    injected = params.get("odoo_rpc")
    if injected is not None:
        return injected, "injected Odoo RPC"
    url = params.get("odoo_url") or os.environ.get("ODOO_URL")
    db = params.get("odoo_db") or os.environ.get("ODOO_DB")
    user = params.get("odoo_username") or os.environ.get("ODOO_USERNAME")
    key = params.get("odoo_api_key") or os.environ.get("ODOO_API_KEY")
    if url and db and user and key:
        return OdooClient(url, db, user, key), f"live Odoo (db={db})"
    return demo_odoo(), "in-memory Odoo stand-in (demo data)"


def prepare(data_path: str | None = None, params: dict | None = None) -> dict:
    """Open the Odoo connection and read the catalog. ``data_path`` is unused (live source)."""
    params = params or {}
    rpc, source = _make_rpc(params)
    connector = OdooConnector(rpc)
    products = connector.list_products()
    return {
        "connector": connector, "source": source, "n_products": len(products),
        "costs": {p.sku: p.cost for p in products},
    }


def _build_outcome(
    n_restock: int, total_restock: float, *, restock_value: float = 0.0, financial_threshold: float = 50_000.0
) -> GuidedOutcome:
    """Replenishment as >=2 ranked, executable options (first = recommended default).

    Above ``financial_threshold`` (estimated from Odoo's own product cost), the
    options are gated behind a required finance sign-off instead of being freely
    actionable - see ``src.escalation.maybe_escalate_financial``.
    """
    if n_restock > 0:
        options = [
            ExecutionOption(
                label="Apply reorder points in Odoo",
                summary=f"Raise the reorder point (min qty) on {n_restock} SKU(s) to the target cover "
                        "so Odoo's own replenishment generates the orders.",
                score=3.0, recommended=True,
                action="stage + apply stock.warehouse.orderpoint.product_min_qty (reversible)",
                tradeoffs="lowest touch; Odoo drives the POs; fully reversible",
            ),
            ExecutionOption(
                label="Raise draft purchase orders",
                summary=f"Create draft POs for the {total_restock:,.0f} units short across {n_restock} SKU(s).",
                score=2.0,
                action="create draft purchase.order lines for the restock quantities",
                tradeoffs="direct buy; needs a supplier + buyer confirmation in Odoo",
            ),
            ExecutionOption(
                label="Export the plan for review",
                summary="Hand off the replenishment plan without writing anything to Odoo.",
                score=1.0,
                action="export the replenishment plan (no write-back)",
                tradeoffs="zero risk; manual follow-up",
            ),
        ]
        summary = f"{n_restock} SKU(s) below target cover ({total_restock:,.0f} units short): choose how to replenish."
        return maybe_escalate_financial(as_options(summary, options), restock_value, financial_threshold)
    else:
        options = [
            ExecutionOption(
                label="Hold - inventory above target cover",
                summary="Every SKU is above its target cover; no replenishment is needed now.",
                score=3.0, recommended=True,
                action="monitor; no write-back needed", tradeoffs="no cost",
            ),
            ExecutionOption(
                label="Tighten the cover target",
                summary="Lower the target cover to release working capital tied up in stock.",
                score=2.0,
                action="re-run with a shorter cover_periods", tradeoffs="frees cash; less buffer",
            ),
            ExecutionOption(
                label="Export the current position",
                summary="Export the stock-vs-target position for the review file.",
                score=1.0,
                action="export the replenishment position (no write-back)", tradeoffs="zero risk",
            ),
        ]
        summary = "All SKUs above target cover: choose how to proceed."
    return as_options(summary, options)


def run(
    payload: dict, *, cover_periods: float = 8.0, financial_threshold: float = 50_000.0
) -> OdooReplenishmentReport:
    """Forecast each SKU from Odoo sales and plan the restock to ``cover_periods`` of demand.

    ``financial_threshold`` gates the options outcome on the restock's estimated $
    value (from Odoo's own product cost) - see ``_build_outcome``.
    """
    connector: OdooConnector = payload["connector"]
    plan = plan_replenishment(connector, cover_periods=cover_periods, store=connector)
    restock = dict(plan.restock)
    total = sum(restock.values())
    n_skus = len(plan.lines)
    costs: dict[str, float] = payload.get("costs", {})
    restock_value = sum(qty * costs.get(sku, 0.0) for sku, qty in restock.items())
    summary = (
        f"Read {n_skus} product(s) from {payload['source']}; {len(restock)} below a "
        f"{cover_periods:.0f}-period cover ({total:,.0f} units short)."
    )
    return OdooReplenishmentReport(
        source=str(payload["source"]),
        lines=plan.lines,
        restock=restock,
        lead_times=connector.lead_times(),
        n_skus=n_skus,
        n_restock=len(restock),
        total_restock=total,
        cover_periods=cover_periods,
        outcome=_build_outcome(
            len(restock), total, restock_value=restock_value, financial_threshold=financial_threshold
        ),
        summary=summary,
    )


def verify(report: OdooReplenishmentReport) -> list[str]:
    """QA gate: a usable plan honours the never-unprotected contract over real products."""
    issues = list(verify_guided(report.outcome))
    if report.n_skus == 0:
        issues.append("no products read from Odoo")
    return issues


def write_operational(report: OdooReplenishmentReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """Machine-readable deliverable: one row per SKU with on-hand / forecast / target / restock."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "sku": ln.sku,
            "on_hand": round(ln.on_hand, 2),
            "forecast_per_period": round(ln.forecast_per_period, 2),
            "target": round(ln.target, 2),
            "restock_qty": round(ln.restock_qty, 2),
            "lead_time_days": round(report.lead_times.get(ln.sku, 0.0), 1),
        }
        for ln in report.lines
    ]
    return {"csv": write_summary_csv(rows, d / "odoo_replenishment.csv")}


def build_deck(
    report: OdooReplenishmentReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the replenishment study: what is short, by how much, and how to act in Odoo."""
    short = [ln for ln in report.lines if ln.restock_qty > 0]
    findings: list[Finding] = []
    banner = escalation_banner(report.outcome)
    if banner:
        # Leads the deck - the data being correct (outcome.status == ESCALATED)
        # is not the same guarantee as a human ever reading it; state it in words,
        # first, not buried under the routine findings.
        findings.append(Finding("Requires finance sign-off before acting", banner,
                                impact="do not apply until the named approver signs off"))
    findings.append(
        Finding(
            f"{report.n_restock} SKU(s) below target cover",
            f"{report.total_restock:,.0f} units short of a {report.cover_periods:.0f}-period cover "
            f"across {report.n_skus} product(s) read from {report.source}.",
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
    kpis = (
        Kpi("Products read", str(report.n_skus), rationale=f"From {report.source}"),
        Kpi("SKUs to replenish", str(report.n_restock), target="0", rationale="Below target cover"),
        Kpi("Units short", f"{report.total_restock:,.0f}", target="0", rationale="Total restock to reach target"),
        Kpi("Cover target", f"{report.cover_periods:.0f} periods", rationale="Demand periods to cover"),
    )
    data_sources = (
        DataSource("Products / stock / sales", "Odoo ERP (XML-RPC: product.product, stock.quant, sale.order)", "live"),
        DataSource("Reorder rules write-back", "Odoo stock.warehouse.orderpoint", "on apply"),
    )
    recommendations = [
        "Apply the reorder points (min qty = target cover) so Odoo replenishes automatically.",
        "Or raise draft POs for the short quantities for buyer confirmation in Odoo.",
    ]
    return Deliverable(
        title="Odoo Replenishment",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual=(f"{banner} " if banner else "")
                 + "Applying reorder points or POs writes to Odoo through the connector's safe-staging "
                   "plane (dry-run, reversible); a human approves before anything is committed.",
        prepared=prepared,
    )

"""The three MVP capabilities, each wrapping existing job machinery as a Tool."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from jobs import (
    abc_xyz_job,
    cost_to_serve_deliverable,
    cost_to_serve_job,
    ddmrp_job,
    deliverables,
    financial_kpis_job,
    intake,
    inventory_deliverable,
    landed_cost_job,
    leadership,
    qa,
    reconciliation_job,
    returns_job,
    sop_deliverable,
    sop_job,
    sourcing_job,
    whatif_job,
)
from jobs.inventory_optimization import run as run_inventory
from jobs.pricing import prepare_pricing
from jobs.pricing import run as run_pricing

from . import tool_options
from .llm import LLMProvider
from .registry import Prepared, Produced, Tool, ToolRegistry
from .types import JobRequest

from src.cost_to_serve import ServiceCostRates  # isort: skip  (local package)
from src.sop import CostModel  # isort: skip  (local package)

LEADERSHIP_SCHEMA = {
    "type": "object",
    "properties": {
        "C": {"type": "integer", "minimum": 0, "maximum": 4},
        "H": {"type": "integer", "minimum": 0, "maximum": 4},
        "A": {"type": "integer", "minimum": 0, "maximum": 4},
        "I": {"type": "integer", "minimum": 0, "maximum": 4},
        "N": {"type": "integer", "minimum": 0, "maximum": 4},
        "evidence": {"type": "object"},
    },
    "required": ["C", "H", "A", "I", "N"],
}


# ---- inventory_optimization --------------------------------------------------

def _inventory_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a demand CSV/Excel file is required"])
    try:
        demand = intake.prepare(request.data_path, period=request.params.get("period", "W"))
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=demand)


def _inventory_run(payload: object, params: dict) -> Produced:
    report = run_inventory(
        payload,
        service_level=params.get("service_level", 0.95),
        holding_rate=params.get("holding_rate", 0.25),
        order_cost=params.get("order_cost", 75.0),
        budget=params.get("budget"),
        periods_per_year=params.get("periods_per_year", 52.0),
        service_levels=params.get("service_levels"),
        differentiate_by_class=params.get("differentiate_by_class", False),
        lead_times=params.get("lead_times"),
        observed_fill_rates=params.get("observed_fill_rates"),
        target_fill_rate=params.get("target_fill_rate", 0.95),
    )
    summary = (
        f"Analyzed {report.n_skus} SKUs; recommended inventory investment "
        f"${report.final_investment:,.0f} at {report.params['service_level'] * 100:.0f}% service level."
    )
    return Produced(report=report, summary=summary)


def inventory_tool() -> Tool:
    return Tool(
        key="inventory_optimization",
        title="Inventory Optimization",
        description="Forecast demand, set (s,Q)/(R,S) policies and allocate an inventory budget.",
        intent_keywords=(
            "reorder", "safety stock", "stock level", "inventory", "replenish",
            "eoq", "service level", "reorder point", "order quantity",
        ),
        requires_data=True,
        options=tool_options.inventory_options,
        prepare=_inventory_prepare,
        run=_inventory_run,
        qa=lambda report: qa.verify(report),
        deliver=lambda report, out_dir, client: deliverables.write_all(report, out_dir, client=client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            inventory_deliverable.build(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- pricing -----------------------------------------------------------------

def _pricing_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a price/quantity CSV/Excel file is required"])
    try:
        demand = prepare_pricing(request.data_path, period=request.params.get("period", "W"))
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=demand)


def _pricing_run(payload: object, params: dict) -> Produced:
    report = run_pricing(payload, cost_ratio=params.get("cost_ratio", 0.6))
    summary = (
        f"Analyzed {report.n_skus} SKUs; {report.n_actionable} with a confident price move "
        f"({report.n_inelastic} inelastic, {report.n_insufficient} insufficient data)."
    )
    return Produced(report=report, summary=summary)


def pricing_tool() -> Tool:
    return Tool(
        key="pricing",
        title="Price Optimization",
        description="Estimate per-SKU elasticity and recommend a margin-maximizing price.",
        intent_keywords=(
            "price", "pricing", "elasticity", "margin", "markdown",
            "optimal price", "what price", "profit",
        ),
        requires_data=True,
        options=tool_options.pricing_options,
        prepare=_pricing_prepare,
        run=_pricing_run,
        qa=lambda report: qa.verify_pricing(report),
        deliver=lambda report, out_dir, client: deliverables.write_pricing_all(report, out_dir, client=client),
    )


# ---- leadership_chain --------------------------------------------------------

def _llm_leadership_scores(provider: LLMProvider, brief: str) -> tuple[dict[str, int], dict[str, str]] | None:
    prompt = (
        "You are scoring supply-chain leadership on the CHAIN model (C Colaborativo, "
        "H Holístico, A Adaptable, I Influyente, N Narrativo), each 0-4, with one short "
        "evidence phrase per dimension drawn from the brief. Evidence over impression: if "
        "the brief gives no observable example for a dimension, cap it at 1.\n\n"
        f"Brief:\n{brief}"
    )
    obj = provider.extract(prompt, LEADERSHIP_SCHEMA)
    scores = leadership.coerce_scores([obj.get(c) for c, _ in leadership.DIMS])
    if scores is None:
        return None
    raw_evidence = obj.get("evidence") or {}
    evidence = {c: str(raw_evidence.get(c, "")) for c, _ in leadership.DIMS}
    return scores, evidence


def _leadership_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    scores = leadership.coerce_scores(request.params.get("scores"))
    evidence: dict[str, str] = {}
    if scores is None and provider.available():
        extracted = _llm_leadership_scores(provider, request.brief)
        if extracted is not None:
            scores, evidence = extracted
    if scores is None:
        return Prepared(status="needs_clarification", messages=leadership.diagnostic_questions())
    profile = leadership.score_profile(scores, evidence=evidence, name=request.params.get("name"))
    return Prepared(status="ok", payload=profile)


def _leadership_run(payload: object, params: dict) -> Produced:
    profile = payload
    summary = (
        f"CHAIN {profile.average:.1f}/4 · archetype: {profile.archetype} · "
        f"priority lever: {profile.lever_name} ({profile.lever_code})."
    )
    return Produced(report=profile, summary=summary)


def leadership_tool() -> Tool:
    return Tool(
        key="leadership_chain",
        title="Leadership (CHAIN)",
        description="Score supply-chain leadership on the CHAIN model: profile, archetype, "
                    "priority lever and active directives.",
        intent_keywords=(
            # NOTE: no bare "chain" — it matches "supply chain" in nearly every
            # brief in this domain and would mis-route. Use "chain model" instead.
            "leadership", "liderazgo", "líder", "ceo", "director",
            "chain model", "manager", "team",
        ),
        requires_data=False,
        options=tool_options.leadership_options,
        prepare=_leadership_prepare,
        run=_leadership_run,
        qa=lambda profile: qa.verify_leadership(profile),
        deliver=lambda profile, out_dir, client: leadership.write_all(profile, out_dir, client=client),
    )


# ---- cost_to_serve -----------------------------------------------------------

def _cost_to_serve_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["an order/sales CSV (with a segment column) is required"])
    try:
        activities = cost_to_serve_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not activities:
        return Prepared(status="needs_data", messages=["no segments found in the data"])
    return Prepared(status="ok", payload=activities)


def _cost_to_serve_run(payload: object, params: dict) -> Produced:
    rates = ServiceCostRates(
        cost_per_order=params.get("cost_per_order", 0.0),
        cost_per_unit_shipped=params.get("cost_per_unit_shipped", 0.0),
        return_handling_per_unit=params.get("return_handling_per_unit", 0.0),
    )
    report = cost_to_serve_job.run(
        payload, rates=rates,
        dio=params.get("dio"), dso=params.get("dso"), dpo=params.get("dpo"),
        dio_days=params.get("dio_days", 0.0), dso_days=params.get("dso_days", 0.0),
        dpo_days=params.get("dpo_days", 0.0),
    )
    return Produced(report=report, summary=report.summary)


def cost_to_serve_tool() -> Tool:
    return Tool(
        key="cost_to_serve",
        title="Cost-to-Serve & Working Capital",
        description="Allocate the true cost of serving each customer/channel/SKU segment "
                    "(product/fulfillment/returns/overhead), flag loss-makers, and size the "
                    "working-capital / cash-release opportunity.",
        intent_keywords=(
            "cost to serve", "cost-to-serve", "working capital", "cash to cash",
            "cash conversion", "segment profitability", "channel profitability",
            "net to serve", "loss-making", "whale curve", "profitability by",
        ),
        requires_data=True,
        options=tool_options.cost_to_serve_options,
        prepare=_cost_to_serve_prepare,
        run=_cost_to_serve_run,
        qa=lambda report: cost_to_serve_job.verify(report),
        deliver=lambda report, out_dir, client: cost_to_serve_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            cost_to_serve_deliverable.build(
                report.portfolio, working_cap=report.working_cap, cash_release=report.cash_release,
                client=client, citations=tuple(citations), confidence=confidence,
            ),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- sop (Sales & Operations Planning) ---------------------------------------

def _sop_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a demand/order history CSV (date + quantity) is required"])
    try:
        payload = sop_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=payload)


def _sop_run(payload: object, params: dict) -> Produced:
    cost = None
    if any(k in params for k in ("holding_cost", "shortage_cost", "capacity_change_cost")):
        cost = CostModel(
            holding_per_unit_per_period=params.get("holding_cost", 1.0),
            shortage_per_unit_per_period=params.get("shortage_cost", 5.0),
            capacity_change_per_unit=params.get("capacity_change_cost", 2.0),
        )
    review = sop_job.run(
        payload,
        opening_inventory=params.get("opening_inventory", 0.0),
        target=params.get("target", 0.0),
        cost=cost,
    )
    return Produced(report=review, summary=review.summary)


def sop_tool() -> Tool:
    return Tool(
        key="sop",
        title="Sales & Operations Planning (S&OP / IBP)",
        description="Run the monthly demand->supply->reconciliation cadence: compare chase / "
                    "level / hybrid supply strategies and recommend a ranked, exec-ready plan.",
        intent_keywords=(
            "s&op", "sales and operations", "sales & operations", "ibp",
            "integrated business planning", "aggregate plan", "aggregate planning",
            "demand and supply plan", "demand-supply", "supply plan", "production plan",
            "monthly demand plan",
        ),
        requires_data=True,
        options=lambda report: report.outcome,
        prepare=_sop_prepare,
        run=_sop_run,
        qa=lambda review: sop_job.verify(review),
        deliver=lambda review, out_dir, client: sop_job.write_operational(review, out_dir, client),
        deck=lambda review, out_dir, client, citations, confidence, options: replace(
            sop_deliverable.build(review, client=client, citations=tuple(citations)),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- abc_xyz (ABC-XYZ classification) ----------------------------------------

def _abc_xyz_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a per-SKU demand CSV (product, demand, unit cost) is required"])
    try:
        items = abc_xyz_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not items:
        return Prepared(status="needs_data", messages=["no SKUs found in the data"])
    return Prepared(status="ok", payload=items)


def _abc_xyz_run(payload: object, params: dict) -> Produced:
    report = abc_xyz_job.run(
        payload,
        abc_thresholds=tuple(params.get("abc_thresholds", (0.80, 0.95))),
        cv_cuts=tuple(params.get("cv_cuts", (0.5, 1.0))),
    )
    return Produced(report=report, summary=(
        f"Classified {report.n_skus} SKUs; {report.n_a} A-items hold "
        f"{report.a_value_share * 100:.0f}% of value, {report.n_cz} discontinuation candidates."
    ))


def abc_xyz_tool() -> Tool:
    return Tool(
        key="abc_xyz",
        title="ABC-XYZ Classification",
        description="Classify SKUs by value (ABC / Pareto) and demand variability (XYZ) into the "
                    "9-cell matrix, assigning a review policy and service level per cell.",
        intent_keywords=(
            "abc-xyz", "abc xyz", "abc analysis", "abc classification", "abc class",
            "xyz analysis", "pareto", "sku classification", "classify", "classification",
        ),
        requires_data=True,
        options=tool_options.abc_xyz_options,
        prepare=_abc_xyz_prepare,
        run=_abc_xyz_run,
        qa=lambda report: abc_xyz_job.verify(report),
        deliver=lambda report, out_dir, client: abc_xyz_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            abc_xyz_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- sourcing (supplier selection / MCDM award) ------------------------------

def _sourcing_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a supplier delivery CSV (supplier, on-time, in-full, lead, defects) is required"])
    try:
        payload = sourcing_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["scorecards"]:
        return Prepared(status="needs_data", messages=["no suppliers found in the data"])
    return Prepared(status="ok", payload=payload)


def _sourcing_run(payload: object, params: dict) -> Produced:
    report = sourcing_job.run(
        payload["scorecards"], payload["prices"], weights=params.get("weights"),
    )
    return Produced(report=report, summary=report.summary)


def sourcing_tool() -> Tool:
    return Tool(
        key="sourcing",
        title="Supplier Sourcing & Selection",
        description="Score competing suppliers on OTIF / lead time / quality / price and rank them "
                    "(TOPSIS) into a recommended, exec-ready award.",
        intent_keywords=(
            "supplier selection", "supplier scorecard", "sourcing", "supplier performance",
            "supplier award", "vendor selection", "procurement", "supplier comparison",
            "otif", "difot", "best supplier",
        ),
        requires_data=True,
        options=lambda report: report.outcome,
        prepare=_sourcing_prepare,
        run=_sourcing_run,
        qa=lambda report: sourcing_job.verify(report),
        deliver=lambda report, out_dir, client: sourcing_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            sourcing_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- ddmrp (Demand-Driven MRP buffers) ---------------------------------------

def _ddmrp_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a parts CSV (part, ADU, decoupled lead time, on-hand/on-order/demand) is required"])
    try:
        records = ddmrp_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not records:
        return Prepared(status="needs_data", messages=["no parts found in the data"])
    return Prepared(status="ok", payload=records)


def _ddmrp_run(payload: object, params: dict) -> Produced:
    report = ddmrp_job.run(payload)
    return Produced(report=report, summary=(
        f"Sized DDMRP buffers for {report.n_parts} parts; {report.n_red} in the red, "
        f"{report.n_order} need an order ({report.total_order_qty:,.0f} units)."
    ))


def ddmrp_tool() -> Tool:
    return Tool(
        key="ddmrp",
        title="DDMRP Buffer Plan",
        description="Size demand-driven (red/yellow/green) buffers and compute the net-flow "
                    "planning signal per part: what to order now, ranked by execution priority.",
        intent_keywords=(
            "ddmrp", "demand driven mrp", "demand-driven", "buffer zones", "buffer sizing",
            "buffer profile", "net flow", "decoupling point", "red yellow green",
        ),
        requires_data=True,
        options=tool_options.ddmrp_options,
        prepare=_ddmrp_prepare,
        run=_ddmrp_run,
        qa=lambda report: ddmrp_job.verify(report),
        deliver=lambda report, out_dir, client: ddmrp_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            ddmrp_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- landed_cost (Incoterm-aware total landed cost) --------------------------

def _landed_cost_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a shipment CSV (sku, unit cost, qty, freight, duty rate, incoterm) is required"])
    try:
        records = landed_cost_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not records:
        return Prepared(status="needs_data", messages=["no shipment lines found in the data"])
    return Prepared(status="ok", payload=records)


def _landed_cost_run(payload: object, params: dict) -> Produced:
    report = landed_cost_job.run(payload)
    return Produced(report=report, summary=(
        f"Landed cost for {report.n_lines} SKU(s): {report.total_landed:,.0f} total, "
        f"{report.landed_uplift_pct * 100:.0f}% over goods value."
    ))


def landed_cost_tool() -> Tool:
    return Tool(
        key="landed_cost",
        title="Landed-Cost Study",
        description="Compute the Incoterm-aware fully-landed cost per SKU (goods + freight + "
                    "insurance + duty + handling + broker) so suppliers are compared on true cost.",
        intent_keywords=(
            "landed cost", "total cost of ownership", "incoterm", "duty", "customs",
            "tariff", "freight cost", "import cost", "total landed",
        ),
        requires_data=True,
        options=tool_options.landed_cost_options,
        prepare=_landed_cost_prepare,
        run=_landed_cost_run,
        qa=lambda report: landed_cost_job.verify(report),
        deliver=lambda report, out_dir, client: landed_cost_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            landed_cost_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


def _warehouse_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    return Prepared(status="ok", payload=dict(request.params or {}))


def _warehouse_run(payload: object, params: dict) -> Produced:
    from jobs.warehouse_job import run as run_warehouse

    layout, report_md = run_warehouse(payload if isinstance(payload, dict) else {})
    summary = (
        f"Generated a {layout.building.width_m:.0f}x{layout.building.depth_m:.0f} m warehouse: "
        f"{len(layout.racks)} racks, {len(layout.slots)} slots, "
        f"{len(layout.docks)} docks, {len(layout.gates)} gates."
    )
    return Produced(report=(layout, report_md), summary=summary)


def _warehouse_deliver(report: object, out_dir: object, client: str) -> dict[str, Path]:
    import json as _json

    from warehouse.html_export import to_html

    layout, report_md = report
    target = Path(str(out_dir)) / "warehouse_layout"
    target.mkdir(parents=True, exist_ok=True)
    (target / "layout.json").write_text(_json.dumps(layout.to_dict(), indent=2), encoding="utf-8")
    (target / "report.md").write_text(report_md, encoding="utf-8")
    (target / "warehouse.html").write_text(to_html(layout, title=f"Warehouse - {client}"), encoding="utf-8")
    return {
        "layout": target / "layout.json",
        "report": target / "report.md",
        "viewer": target / "warehouse.html",
    }


def warehouse_layout_tool() -> Tool:
    from warehouse.qa import validate as validate_layout

    return Tool(
        key="warehouse_layout",
        title="Warehouse Layout (3D)",
        description="Generate a parametric, navigable 3D warehouse: building, yard, docks, gates, racks and slots.",
        intent_keywords=(
            "warehouse", "layout", "bodega", "almacen", "almacen 3d", "3d",
            "rack", "racks", "estanteria", "dock", "anden", "patio", "yard", "floor plan",
        ),
        requires_data=False,
        prepare=_warehouse_prepare,
        run=_warehouse_run,
        qa=lambda report: validate_layout(report[0]),
        deliver=_warehouse_deliver,
        options=lambda report: tool_options.warehouse_options(report[0]),
    )


# ---- whatif (sensitivity / what-if over the inventory policy cost) -----------

def _whatif_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a drivers CSV (driver, base, low, high) is required"])
    try:
        payload = whatif_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=payload)


def _whatif_run(payload: object, params: dict) -> Produced:
    report = whatif_job.run(
        payload,
        metric=params.get("metric", "annual_cost"),
        budget_pct=params.get("budget_pct", 0.10),
        maximize=params.get("maximize", False),
    )
    be = f"; budget break-even at {report.breakeven_value:,.2f}" if report.breakeven_found else ""
    return Produced(report=report, summary=(
        f"Swept {report.n_drivers} assumption(s); '{report.top_driver}' moves {report.metric} most "
        f"(range {report.optimistic_value:,.0f}-{report.pessimistic_value:,.0f}){be}."
    ))


def whatif_tool() -> Tool:
    return Tool(
        key="whatif",
        title="What-If / Sensitivity Study",
        description="Sweep the planning assumptions (demand, holding, lead time, ...) over their "
                    "bands against the inventory policy cost: rank them by impact (tornado), bound "
                    "the optimistic/pessimistic corners, and find the budget break-even.",
        intent_keywords=(
            "what-if", "what if", "sensitivity", "sensitivity analysis", "tornado",
            "break-even", "break even", "scenario analysis", "stress test",
            "how sensitive", "downside", "best case", "worst case",
        ),
        requires_data=True,
        options=tool_options.whatif_options,
        prepare=_whatif_prepare,
        run=_whatif_run,
        qa=lambda report: whatif_job.verify(report),
        deliver=lambda report, out_dir, client: whatif_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            whatif_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- financial_kpis (inventory finance dashboard) ----------------------------

def _financial_kpis_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a per-SKU financials CSV (cogs, avg inventory value, margin) is required"])
    try:
        records = financial_kpis_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not records:
        return Prepared(status="needs_data", messages=["no SKUs found in the data"])
    return Prepared(status="ok", payload=records)


def _financial_kpis_run(payload: object, params: dict) -> Produced:
    report = financial_kpis_job.run(
        payload, dso=params.get("dso", 0.0), dpo=params.get("dpo", 0.0),
    )
    return Produced(report=report, summary=(
        f"Inventory finance for {report.n_skus} SKU(s): {report.turns:.1f} turns, "
        f"GMROI {report.gmroi:.2f}, {report.dio:.0f}-day DIO."
    ))


def financial_kpis_tool() -> Tool:
    return Tool(
        key="financial_kpis",
        title="Inventory Financial KPIs",
        description="Roll up the per-SKU finance pack: inventory turns, DIO, GMROI, sell-through, "
                    "inventory-to-sales and cash-to-cash, and flag the weakest-GMROI SKUs.",
        intent_keywords=(
            "gmroi", "inventory turns", "turnover", "days inventory", "sell-through",
            "sell through", "inventory to sales", "weeks of supply", "inventory kpi",
            "financial kpi", "inventory health", "financial dashboard",
        ),
        requires_data=True,
        options=tool_options.financial_kpis_options,
        prepare=_financial_kpis_prepare,
        run=_financial_kpis_run,
        qa=lambda report: financial_kpis_job.verify(report),
        deliver=lambda report, out_dir, client: financial_kpis_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            financial_kpis_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- reconciliation (inventory record accuracy / IRA) ------------------------

def _reconciliation_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a count CSV (product, system qty, physical qty) is required"])
    try:
        records = reconciliation_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not records:
        return Prepared(status="needs_data", messages=["no count lines found in the data"])
    return Prepared(status="ok", payload=records)


def _reconciliation_run(payload: object, params: dict) -> Produced:
    report = reconciliation_job.run(
        payload,
        tolerance_pct=params.get("tolerance_pct", 0.0),
        tolerance_units=params.get("tolerance_units", 0.0),
    )
    return Produced(report=report, summary=(
        f"IRA {report.ira * 100:.0f}% across {report.n_counted} line(s); "
        f"{report.n_counted - report.n_within} out of tolerance, "
        f"{report.total_variance_value:,.0f} in variance value."
    ))


def reconciliation_tool() -> Tool:
    return Tool(
        key="reconciliation",
        title="Inventory Record Accuracy (IRA)",
        description="Reconcile system vs physical counts against a tolerance band, report inventory "
                    "record accuracy (IRA) and the dollar impact of variances, and rank the worst lines.",
        intent_keywords=(
            "inventory accuracy", "record accuracy", "reconcile", "reconciliation",
            "physical count", "stock count", "cycle count", "count variance",
            "book vs physical", "system vs physical", "shrinkage", "stock discrepancy",
        ),
        requires_data=True,
        options=tool_options.reconciliation_options,
        prepare=_reconciliation_prepare,
        run=_reconciliation_run,
        qa=lambda report: reconciliation_job.verify(report),
        deliver=lambda report, out_dir, client: reconciliation_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            reconciliation_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- returns (reverse logistics / disposition) -------------------------------

def _returns_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a returns CSV (product, returned units, unit cost, reason) is required"])
    try:
        lines = returns_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not lines:
        return Prepared(status="needs_data", messages=["no return lines found in the data"])
    return Prepared(status="ok", payload=lines)


def _returns_run(payload: object, params: dict) -> Produced:
    report = returns_job.run(
        payload,
        restock_handling_per_unit=params.get("restock_handling_per_unit", 0.0),
        refurbish_cost_per_unit=params.get("refurbish_cost_per_unit", 0.0),
        refurbish_resale_factor=params.get("refurbish_resale_factor", 0.6),
        liquidation_recovery_pct=params.get("liquidation_recovery_pct", 0.2),
        scrap_cost_per_unit=params.get("scrap_cost_per_unit", 0.0),
    )
    return Produced(report=report, summary=(
        f"{report.n_lines} return line(s); recommended strategy '{report.recommended_strategy}', "
        f"{report.recovered_value:,.0f} recovered ({report.recovery_rate * 100:.0f}%)."
    ))


def returns_tool() -> Tool:
    return Tool(
        key="returns",
        title="Returns & Reverse Logistics",
        description="Rank each returned lot's disposition (restock / refurbish / liquidate / scrap) "
                    "by net recovery, roll up recovery rate + value at risk + the reason Pareto, and "
                    "offer ranked, executable recovery strategies to choose from.",
        intent_keywords=(
            "reverse logistics", "reverse-logistics", "product returns", "returns analysis",
            "returns disposition", "disposition", "return rate", "handle returns",
            "refurbish", "salvage value", "returns recovery", "returned goods",
        ),
        requires_data=True,
        prepare=_returns_prepare,
        run=_returns_run,
        qa=lambda report: returns_job.verify(report),
        deliver=lambda report, out_dir, client: returns_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            returns_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
        # The disposition decision IS a set of ranked, executable choices -> surface them as
        # the guided OPTIONS outcome on success (not just an "executed" deck).
        options=lambda report: report.outcome,
    )


def build_default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(inventory_tool())
    reg.register(pricing_tool())
    reg.register(leadership_tool())
    reg.register(cost_to_serve_tool())
    reg.register(sop_tool())
    reg.register(abc_xyz_tool())
    reg.register(sourcing_tool())
    reg.register(ddmrp_tool())
    reg.register(landed_cost_tool())
    reg.register(whatif_tool())
    reg.register(financial_kpis_tool())
    reg.register(reconciliation_tool())
    reg.register(returns_tool())
    reg.register(warehouse_layout_tool())
    return reg

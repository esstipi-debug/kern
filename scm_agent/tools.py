"""The three MVP capabilities, each wrapping existing job machinery as a Tool."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from jobs import (
    abc_xyz_job,
    acceptance_sampling_job,
    cost_to_serve_deliverable,
    cost_to_serve_job,
    cycle_count_job,
    data_quality_job,
    ddmrp_job,
    dea_job,
    deliverables,
    digital_twin_job,
    drp_job,
    earned_value_job,
    excel_replenishment_job,
    excess_obsolete_job,
    facility_location_job,
    fefo_job,
    financial_kpis_job,
    forecast_job,
    intake,
    inventory_deliverable,
    landed_cost_job,
    leadership,
    learning_curve_job,
    markdown_liquidation_job,
    multi_echelon_job,
    newsvendor_job,
    odoo_job,
    price_watch_deliverable,
    qa,
    queuing_job,
    reconciliation_job,
    returns_job,
    risk_job,
    scheduling_job,
    simulation_job,
    slotting_job,
    sop_deliverable,
    sop_job,
    sourcing_job,
    transportation_job,
    vehicle_routing_job,
    whatif_job,
)
from jobs.inventory_optimization import run as run_inventory
from jobs.pricing import prepare_pricing
from jobs.pricing import run as run_pricing

from . import tool_options
from .llm import LLMProvider
from .registry import Prepared, Produced, Tool, ToolRegistry
from .types import JobRequest

from src.connectors.odoo import OdooError  # isort: skip  (local package)
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
        # _tracked: also returns IntakeQuality (how much of the file survived
        # cleaning, and why) so a heavily-filtered source file can be escalated
        # instead of silently delivered with unqualified confidence — see
        # scm_agent/tool_options.py's _apply_intake_quality.
        demand, quality = intake.prepare_tracked(
            request.data_path,
            period=request.params.get("period", "W"),
            # Client-wide lead time (typically from the client profile) fills in only
            # where the CSV carries none; per-SKU CSV values always win.
            default_lead_days=float(request.params.get("lead_time_days", 14.0)),
        )
    except (ValueError, FileNotFoundError, TypeError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload={"demand": demand, "intake_quality": quality})


def _inventory_run(payload: object, params: dict) -> Produced:
    report = run_inventory(
        payload["demand"],
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
        intake_quality=payload["intake_quality"],
        intake_quality_threshold=params.get("intake_quality_threshold", 0.20),
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
        required_client_params=("holding_rate", "service_level"),
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


# ---- queuing (waiting-line / staffing) ---------------------------------------

def _queuing_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a stations CSV (station, arrival rate, service rate) is required"])
    try:
        records = queuing_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not records:
        return Prepared(status="needs_data", messages=["no service stations found in the data"])
    return Prepared(status="ok", payload=records)


def _queuing_run(payload: object, params: dict) -> Produced:
    report = queuing_job.run(
        payload,
        wait_cost=params.get("wait_cost", 10.0),
        server_cost=params.get("server_cost", 5.0),
        max_servers=params.get("max_servers", 30),
    )
    return Produced(report=report, summary=(
        f"Sized {report.n_stations} service point(s); staffing cost {report.total_cost:,.0f}, "
        f"busiest '{report.busiest_station}', worst wait {report.max_wait:.2f}."
    ))


def queuing_tool() -> Tool:
    return Tool(
        key="queuing",
        title="Queuing / Staffing",
        description="Size each service point (pick station, returns desk, support queue) to the "
                    "cost-optimal number of servers from its arrival/service rates, and report the "
                    "utilization, wait and the wait-vs-labour trade-off.",
        intent_keywords=(
            "waiting line", "queuing", "queue", "wait time", "how many servers",
            "staffing level", "service capacity", "congestion", "server count",
            "checkout lanes", "call center staffing", "service desk",
        ),
        requires_data=True,
        options=tool_options.queuing_options,
        prepare=_queuing_prepare,
        run=_queuing_run,
        qa=lambda report: queuing_job.verify(report),
        deliver=lambda report, out_dir, client: queuing_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            queuing_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- scheduling (job sequencing) ---------------------------------------------

def _scheduling_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a jobs CSV (job, processing time, optional due date) is required"])
    try:
        jobs = scheduling_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not jobs:
        return Prepared(status="needs_data", messages=["no jobs found in the data"])
    return Prepared(status="ok", payload=jobs)


def _scheduling_run(payload: object, params: dict) -> Produced:
    report = scheduling_job.run(payload, objective=params.get("objective", "auto"))
    return Produced(report=report, summary=(
        f"Sequenced {report.n_jobs} job(s) by '{report.recommended_rule}'; mean flow time "
        f"{report.mean_flow_time:.2f}, max lateness {report.max_lateness:.2f}."
    ))


def scheduling_tool() -> Tool:
    return Tool(
        key="scheduling",
        title="Job Sequencing",
        description="Recommend the run order for a set of jobs on a resource (SPT to minimize flow "
                    "time, EDD to minimize lateness) and report the throughput/on-time trade-off.",
        intent_keywords=(
            "job sequencing", "sequence the jobs", "dispatching rule", "dispatch",
            "shortest processing", "earliest due date", "minimize lateness",
            "minimize flow time", "run order", "job scheduling", "what order to run",
            "shop floor schedule",
        ),
        requires_data=True,
        options=tool_options.scheduling_options,
        prepare=_scheduling_prepare,
        run=_scheduling_run,
        qa=lambda report: scheduling_job.verify(report),
        deliver=lambda report, out_dir, client: scheduling_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            scheduling_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- risk (supply-chain risk assessment / mitigation) ------------------------

def _risk_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a risk register CSV (name, likelihood, impact_value) is required"])
    try:
        records = risk_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not records:
        return Prepared(status="needs_data", messages=["no risk factors found in the data"])
    return Prepared(status="ok", payload=records)


def _risk_run(payload: object, params: dict) -> Produced:
    thresholds = params.get("severity_thresholds")
    report = (
        risk_job.run(payload, severity_thresholds=tuple(thresholds))
        if thresholds else risk_job.run(payload)
    )
    return Produced(report=report, summary=report.summary)


def risk_tool() -> Tool:
    return Tool(
        key="risk",
        title="Supply-Chain Risk Assessment",
        description="Score a risk register (likelihood x impact), rank by expected loss (EMV) and "
                    "FMEA RPN, bucket into a 5x5 heatmap, flag TTR>TTS resilience gaps, and rank "
                    "mitigation options by net benefit into an exec-ready, executable plan.",
        intent_keywords=(
            "risk assessment", "risk register", "risk management", "supply chain risk",
            "risk heatmap", "likelihood impact", "mitigation plan", "disruption risk",
            "risk exposure", "value at risk", "resilience",
        ),
        requires_data=True,
        prepare=_risk_prepare,
        run=_risk_run,
        qa=lambda report: risk_job.verify(report),
        deliver=lambda report, out_dir, client: risk_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            risk_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
        # The mitigation decision IS a set of ranked, executable choices -> surface them as
        # the guided OPTIONS outcome on success (recommended default flagged).
        options=lambda report: report.outcome,
    )


# ---- forecast (demand forecasting & forecastability) -------------------------

def _forecast_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a demand-history CSV (sku, period, quantity) is required"])
    try:
        series = forecast_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not series:
        return Prepared(status="needs_data", messages=["no demand series found in the data"])
    return Prepared(status="ok", payload=series)


def _forecast_run(payload: object, params: dict) -> Produced:
    report = forecast_job.run(
        payload,
        holdout_fraction=params.get("holdout_fraction", 0.25),
        min_backtest_periods=params.get("min_backtest_periods", 4),
    )
    return Produced(report=report, summary=report.summary)


def forecast_tool() -> Tool:
    return Tool(
        key="forecast",
        title="Demand Forecasting & Forecastability",
        description="Segment each SKU by forecastability (Syntetos-Boylan ADI x CV^2: smooth / "
                    "erratic / intermittent / lumpy), auto-select and backtest the matching method "
                    "(AutoETS/TSB when installed, else SES/Croston), quantify forecast value-add "
                    "vs naive, and rank forecasting "
                    "policies into an exec-ready, executable plan.",
        intent_keywords=(
            "forecast", "forecasting", "forecast demand", "demand forecast", "forecastability",
            "intermittent demand", "croston", "forecast accuracy", "forecast value add",
            "which forecast method", "lumpy demand", "demand pattern", "predict demand",
        ),
        requires_data=True,
        prepare=_forecast_prepare,
        run=_forecast_run,
        qa=lambda report: forecast_job.verify(report),
        deliver=lambda report, out_dir, client: forecast_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            forecast_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
        # The forecasting-policy decision IS a set of ranked, executable choices -> surface them
        # as the guided OPTIONS outcome on success (recommended default flagged).
        options=lambda report: report.outcome,
    )


# ---- data_quality (SKU master / MDM: dedup + GTIN validation + cleansing) ----

def _data_quality_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a product-master CSV (sku, name, gtin, unit cost) is required"])
    try:
        records = data_quality_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not records:
        return Prepared(status="needs_data", messages=["no records found in the data"])
    return Prepared(status="ok", payload=records)


def _data_quality_run(payload: object, params: dict) -> Produced:
    report = data_quality_job.run(payload, name_threshold=params.get("name_threshold", 90.0))
    return Produced(report=report, summary=report.summary)


def data_quality_tool() -> Tool:
    return Tool(
        key="data_quality",
        title="Data Quality & SKU Master (MDM)",
        description="Audit a product master: find duplicate SKUs (shared GTIN or fuzzy name), "
                    "validate GTIN check digits (GS1 mod-10), flag completeness gaps, score "
                    "overall quality, and rank remediation options into an exec-ready clean-up plan.",
        intent_keywords=(
            "data quality", "data cleansing", "data cleaning", "deduplicate", "deduplication",
            "duplicate sku", "duplicate skus", "sku master", "master data", "mdm", "gtin",
            "validate gtin", "upc", "ean", "data validation", "clean the data", "data audit",
            "standardize skus",
        ),
        requires_data=True,
        prepare=_data_quality_prepare,
        run=_data_quality_run,
        qa=lambda report: data_quality_job.verify(report),
        deliver=lambda report, out_dir, client: data_quality_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            data_quality_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
        # The remediation decision IS a set of ranked, executable choices -> surface them as
        # the guided OPTIONS outcome on success (recommended default flagged).
        options=lambda report: report.outcome,
    )


# ---- dea (efficiency benchmarking) -------------------------------------------

def _dea_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a units CSV (name + input_* and output_* columns) is required"])
    try:
        payload = dea_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=payload)


def _dea_run(payload: object, params: dict) -> Produced:
    report = dea_job.run(payload)
    return Produced(report=report, summary=(
        f"Benchmarked {report.n_units} unit(s); {report.n_efficient} on the frontier, mean "
        f"efficiency {report.mean_efficiency * 100:.0f}%, weakest '{report.worst_unit}'."
    ))


def dea_tool() -> Tool:
    return Tool(
        key="dea",
        title="Efficiency Benchmarking (DEA)",
        description="Rate comparable units (suppliers, warehouses, DCs, stores) on a data-driven "
                    "efficiency frontier from their inputs and outputs, with no preset weights, and "
                    "rank the laggards.",
        intent_keywords=(
            "data envelopment", "dea", "efficiency frontier", "relative efficiency",
            "benchmark efficiency", "efficiency benchmarking", "peer efficiency", "best in class",
        ),
        requires_data=True,
        options=tool_options.dea_options,
        prepare=_dea_prepare,
        run=_dea_run,
        qa=lambda report: dea_job.verify(report),
        deliver=lambda report, out_dir, client: dea_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            dea_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- acceptance_sampling (receiving quality) ---------------------------------

def _acceptance_sampling_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a parts CSV (part, aql, ltpd) is required"])
    try:
        records = acceptance_sampling_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not records:
        return Prepared(status="needs_data", messages=["no parts found in the data"])
    return Prepared(status="ok", payload=records)


def _acceptance_sampling_run(payload: object, params: dict) -> Produced:
    report = acceptance_sampling_job.run(
        payload,
        producer_risk=params.get("producer_risk", 0.05),
        consumer_risk=params.get("consumer_risk", 0.10),
    )
    return Produced(report=report, summary=(
        f"Designed sampling plans for {report.n_parts} part(s); {report.total_sample} units to "
        f"inspect, strictest '{report.strictest_part}'."
    ))


def acceptance_sampling_tool() -> Tool:
    return Tool(
        key="acceptance_sampling",
        title="Acceptance Sampling (Receiving Quality)",
        description="Design the smallest receiving inspection plan (inspect n, accept on <= c) per "
                    "part from its AQL/LTPD quality targets, protecting both producer and consumer risk.",
        intent_keywords=(
            "acceptance sampling", "sampling plan", "incoming inspection", "receiving inspection",
            "aql", "ltpd", "inspect how many", "lot acceptance", "quality sampling",
        ),
        requires_data=True,
        options=tool_options.acceptance_sampling_options,
        prepare=_acceptance_sampling_prepare,
        run=_acceptance_sampling_run,
        qa=lambda report: acceptance_sampling_job.verify(report),
        deliver=lambda report, out_dir, client: acceptance_sampling_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            acceptance_sampling_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- earned_value (project control) ------------------------------------------

def _earned_value_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a tasks CSV (task, planned, earned, actual) is required"])
    try:
        records = earned_value_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not records:
        return Prepared(status="needs_data", messages=["no tasks found in the data"])
    return Prepared(status="ok", payload=records)


def _earned_value_run(payload: object, params: dict) -> Produced:
    report = earned_value_job.run(payload)
    p = report.portfolio
    return Produced(report=report, summary=(
        f"Project across {report.n_tasks} task(s): SPI {p.spi:.2f}, CPI {p.cpi:.2f}; "
        f"{report.n_behind} behind, {report.n_over} over budget."
    ))


def earned_value_tool() -> Tool:
    return Tool(
        key="earned_value",
        title="Earned Value (Project Control)",
        description="Roll up project cost/schedule control from work-package planned/earned/actual "
                    "cost: SV, CV, SPI, CPI, and the worst-performing tasks.",
        intent_keywords=(
            "earned value", "project control", "spi", "cpi", "schedule variance",
            "cost variance", "cost performance index", "project performance", "bcwp",
        ),
        requires_data=True,
        options=tool_options.earned_value_options,
        prepare=_earned_value_prepare,
        run=_earned_value_run,
        qa=lambda report: earned_value_job.verify(report),
        deliver=lambda report, out_dir, client: earned_value_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            earned_value_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- learning_curve (cost-down) ----------------------------------------------

def _learning_curve_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a products CSV (product, first unit cost, learning rate, planned volume) is required"])
    try:
        records = learning_curve_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not records:
        return Prepared(status="needs_data", messages=["no products found in the data"])
    return Prepared(status="ok", payload=records)


def _learning_curve_run(payload: object, params: dict) -> Produced:
    report = learning_curve_job.run(payload)
    return Produced(report=report, summary=(
        f"Projected cost-down for {report.n_products} product(s): {report.total_cost:,.0f} total, "
        f"{report.total_savings:,.0f} saved vs. no learning."
    ))


def learning_curve_tool() -> Tool:
    return Tool(
        key="learning_curve",
        title="Learning-Curve Cost-Down",
        description="Project unit and total cost at volume from a first-unit cost and learning rate "
                    "(Yx = K*x^n), and the saving vs. no learning - for quoting and cost-down planning.",
        intent_keywords=(
            "learning curve", "experience curve", "cost-down", "cost down", "unit cost at volume",
            "cost reduction with volume", "quote at volume", "learning rate",
        ),
        requires_data=True,
        options=tool_options.learning_curve_options,
        prepare=_learning_curve_prepare,
        run=_learning_curve_run,
        qa=lambda report: learning_curve_job.verify(report),
        deliver=lambda report, out_dir, client: learning_curve_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            learning_curve_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- odoo_replenishment (live ERP connector) ---------------------------------

def _odoo_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    try:
        payload = odoo_job.prepare(request.data_path, request.params)
    except (ValueError, OdooError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if payload["n_products"] == 0:
        return Prepared(status="needs_data",
                        messages=["no products with an internal reference (default_code) found in Odoo"])
    return Prepared(status="ok", payload=payload)


def _odoo_run(payload: object, params: dict) -> Produced:
    report = odoo_job.run(
        payload,
        cover_periods=params.get("cover_periods", 8.0),
        financial_threshold=params.get("financial_threshold", 50_000.0),
    )
    return Produced(report=report, summary=report.summary)


def odoo_replenishment_tool() -> Tool:
    return Tool(
        key="odoo_replenishment",
        title="Odoo Replenishment (live ERP)",
        description="Connect to a live Odoo ERP (or an offline stand-in), read products / stock / "
                    "sales, forecast each SKU and recommend replenishment - staged back to Odoo as "
                    "reversible reorder rules through the safe-staging plane.",
        intent_keywords=(
            "odoo", "odoo inventory", "odoo erp", "odoo replenishment", "connect odoo",
            "connect to odoo", "sync odoo", "from odoo", "pull from odoo", "erp connector",
        ),
        requires_data=False,
        options=lambda report: report.outcome,
        prepare=_odoo_prepare,
        run=_odoo_run,
        qa=lambda report: odoo_job.verify(report),
        deliver=lambda report, out_dir, client: odoo_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            odoo_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- excel_replenishment (client planilla as system of record) ----------------

def _excel_replenishment_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data",
                        messages=["the client's inventory Excel file (.xlsx/.xlsm) is required"])
    try:
        payload = excel_replenishment_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=payload)


def _excel_replenishment_run(payload: object, params: dict) -> Produced:
    report = excel_replenishment_job.run(
        payload,
        cover_periods=params.get("cover_periods", 8.0),
        order_up_to_factor=params.get("order_up_to_factor", 2.0),
        idempotency_key=params.get("idempotency_key", "excel-replenish-1"),
        financial_threshold=params.get("financial_threshold", 50_000.0),
    )
    return Produced(report=report, summary=report.summary)


def excel_replenishment_tool() -> Tool:
    return Tool(
        key="excel_replenishment",
        title="Excel Replenishment (client planilla)",
        description="Read the client's own inventory spreadsheet (auto-detecting the sheet and the "
                    "SKU/stock/reorder-point/demand columns, Spanish or English), plan the restock, "
                    "and stage the recommended order quantities back INTO the planilla as a "
                    "reversible dry-run through the Excel safe-staging connector - approved by a "
                    "human before anything is written.",
        intent_keywords=(
            # Multi-word AND action-anchored on purpose: bare "excel"/"planilla" would
            # hijack briefs that merely mention a spreadsheet while asking for another
            # capability (classification, layout, pricing...). Spanish phrases carry
            # both accented and unaccented spellings - the matcher does not fold accents.
            "excel replenishment", "replenish my excel", "update my excel",
            "write back to excel", "excel writeback",
            "reponer planilla", "reponer mi planilla", "repone mi planilla",
            "reposicion de mi planilla", "reposición de mi planilla",
            "actualiza la reposicion", "actualiza la reposición",
            "actualizar planilla", "actualiza mi planilla",
            "spreadsheet replenishment", "update the spreadsheet",
            "replenish spreadsheet", "replenish my spreadsheet",
        ),
        requires_data=True,
        options=lambda report: report.outcome,
        prepare=_excel_replenishment_prepare,
        run=_excel_replenishment_run,
        qa=lambda report: excel_replenishment_job.verify(report),
        deliver=lambda report, out_dir, client: excel_replenishment_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            excel_replenishment_job.build_deck(report, client=client, citations=tuple(citations),
                                               confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- newsvendor (single-period / perishable order) ---------------------------

def _newsvendor_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data",
                        messages=["a single-period CSV (product, mean_demand, std_demand, price, unit_cost) is required"])
    try:
        records = newsvendor_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not records:
        return Prepared(status="needs_data", messages=["no SKUs found in the data"])
    return Prepared(status="ok", payload=records)


def _newsvendor_run(payload: object, params: dict) -> Produced:
    report = newsvendor_job.run(payload)
    return Produced(report=report, summary=report.summary)


def newsvendor_tool() -> Tool:
    return Tool(
        key="newsvendor",
        title="Single-Period (Newsvendor) Order",
        description="Set the profit-maximizing one-shot order quantity per SKU for perishable / "
                    "seasonal / fashion / spare-part demand: the critical-ratio optimum, expected "
                    "profit, and the in-stock service level it implies.",
        intent_keywords=(
            "newsvendor", "single period", "single-period", "perishable", "perishables",
            "one-time order", "one-shot order", "seasonal buy", "fashion buy", "spoilage",
            "critical ratio",
        ),
        requires_data=True,
        options=tool_options.newsvendor_options,
        prepare=_newsvendor_prepare,
        run=_newsvendor_run,
        qa=lambda report: newsvendor_job.verify(report),
        deliver=lambda report, out_dir, client: newsvendor_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            newsvendor_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- cycle_count (cycle-count program / schedule) ----------------------------

def _cycle_count_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data",
                        messages=["a SKU CSV (product + abc class, or product + value to classify) is required"])
    try:
        items = cycle_count_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not items:
        return Prepared(status="needs_data", messages=["no SKUs found in the data"])
    return Prepared(status="ok", payload=items)


def _cycle_count_run(payload: object, params: dict) -> Produced:
    report = cycle_count_job.run(payload, working_days=params.get("working_days", 250))
    return Produced(report=report, summary=report.summary)


def cycle_count_tool() -> Tool:
    return Tool(
        key="cycle_count",
        title="Cycle-Count Program",
        description="Build the cycle-count schedule that replaces the annual wall-to-wall count: "
                    "count frequency per ABC class, each SKU's counts spread evenly across the "
                    "working year, and a balanced daily counting workload.",
        intent_keywords=(
            "cycle count program", "cycle-count program", "cycle count schedule", "count schedule",
            "count cadence", "counting program", "count frequency", "cycle counting plan",
            "how often to count", "count program",
        ),
        requires_data=True,
        options=tool_options.cycle_count_options,
        prepare=_cycle_count_prepare,
        run=_cycle_count_run,
        qa=lambda report: cycle_count_job.verify(report),
        deliver=lambda report, out_dir, client: cycle_count_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            cycle_count_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- multi_echelon (serial-chain safety-stock placement) ---------------------

def _multi_echelon_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data",
                        messages=["a serial-chain CSV (stage, lead_time, holding_cost + demand mean/std) is required"])
    try:
        payload = multi_echelon_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["stages"]:
        return Prepared(status="needs_data", messages=["no stages found in the data"])
    return Prepared(status="ok", payload=payload)


def _multi_echelon_run(payload: object, params: dict) -> Produced:
    report = multi_echelon_job.run(payload)
    return Produced(report=report, summary=report.summary)


def multi_echelon_tool() -> Tool:
    return Tool(
        key="multi_echelon",
        title="Multi-Echelon Safety-Stock Placement",
        description="Optimize where to hold safety stock across a serial supply chain "
                    "(supplier -> DC -> store) via the Guaranteed-Service Model: the cost-minimizing "
                    "placement, per-stage and echelon order-up-to levels, and total network holding cost.",
        intent_keywords=(
            "multi-echelon", "multi echelon", "multiechelon", "echelon", "two-echelon",
            "network inventory", "inventory positioning", "stock positioning",
            "safety stock placement", "multi-tier inventory", "risk pooling",
        ),
        requires_data=True,
        options=tool_options.multi_echelon_options,
        prepare=_multi_echelon_prepare,
        run=_multi_echelon_run,
        qa=lambda report: multi_echelon_job.verify(report),
        deliver=lambda report, out_dir, client: multi_echelon_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            multi_echelon_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- transportation (freight mode selection + lane freight) ------------------

def _transportation_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data",
                        messages=["a shipments CSV (weight_kg, distance_km + optional lane/units/value) is required"])
    try:
        payload = transportation_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["shipments"]:
        return Prepared(status="needs_data", messages=["no shipments found in the data"])
    return Prepared(status="ok", payload=payload)


def _transportation_run(payload: object, params: dict) -> Produced:
    report = transportation_job.run(payload)
    return Produced(report=report, summary=report.summary)


def transportation_tool() -> Tool:
    return Tool(
        key="transportation",
        title="Transport-Mode Selection & Lane Freight",
        description="Pick the cheapest feasible transport mode (parcel / LTL / FTL / intermodal) per "
                    "shipment from a configurable rate card, quantify the saving vs defaulting to LTL, "
                    "and rank lanes by freight cost-to-serve. Offline - no carrier API needed.",
        intent_keywords=(
            "transportation", "transport mode", "freight mode", "mode selection", "shipping mode",
            "ltl", "ftl", "full truckload", "less-than-truckload", "intermodal", "truckload",
            "how to ship", "cheapest way to ship", "freight optimization", "carrier mode",
        ),
        requires_data=True,
        options=tool_options.transportation_options,
        prepare=_transportation_prepare,
        run=_transportation_run,
        qa=lambda report: transportation_job.verify(report),
        deliver=lambda report, out_dir, client: transportation_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            transportation_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- fefo (lot expiry / FEFO disposition) ------------------------------------

def _fefo_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data",
                        messages=["a lots CSV (product, lot, quantity, days_to_expiry + optional cost/price/demand) is required"])
    try:
        payload = fefo_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["lots"]:
        return Prepared(status="needs_data", messages=["no lots found in the data"])
    return Prepared(status="ok", payload=payload)


def _fefo_run(payload: object, params: dict) -> Produced:
    report = fefo_job.run(payload)
    return Produced(report=report, summary=report.summary)


def fefo_tool() -> Tool:
    return Tool(
        key="fefo",
        title="Lot Expiry & FEFO Disposition",
        description="For perishable / dated stock: the shelf-life aging report, the First-Expired-"
                    "First-Out issue order, the quantity demand cannot sell before expiry, and a "
                    "markdown-vs-scrap recommendation to recover value before write-off.",
        intent_keywords=(
            "fefo", "first expired first out", "first-expired", "expiry", "expiration date",
            "expiring stock", "shelf life", "shelf-life", "lot expiry", "batch expiry",
            "best before", "use by date", "expiry risk", "markdown before expiry", "aging stock",
        ),
        requires_data=True,
        options=tool_options.fefo_options,
        prepare=_fefo_prepare,
        run=_fefo_run,
        qa=lambda report: fefo_job.verify(report),
        deliver=lambda report, out_dir, client: fefo_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            fefo_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- slotting (COI zone slotting + affinity co-location) ---------------------

def _slotting_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data",
                        messages=["an order-lines CSV (order_id, product_id + optional unit_volume) is required"])
    try:
        payload = slotting_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["skus"]:
        return Prepared(status="needs_data", messages=["no SKUs found in the data"])
    return Prepared(status="ok", payload=payload)


def _slotting_run(payload: object, params: dict) -> Produced:
    report = slotting_job.run(payload)
    return Produced(report=report, summary=report.summary)


def slotting_tool() -> Tool:
    return Tool(
        key="slotting",
        title="Warehouse Slotting (COI + Affinity)",
        description="Assign SKUs to pick zones A/B/C by the cube-per-order index (fast / dense movers "
                    "to the closest zone) and co-locate SKUs frequently ordered together, from order "
                    "history - to cut pick travel.",
        intent_keywords=(
            "slotting", "slot assignment", "warehouse slotting", "pick slot", "pick face",
            "golden zone", "cube per order", "cube-per-order", "coi slotting", "zone assignment",
            "co-location", "re-slot", "slot the skus",
        ),
        requires_data=True,
        options=tool_options.slotting_options,
        prepare=_slotting_prepare,
        run=_slotting_run,
        qa=lambda report: slotting_job.verify(report),
        deliver=lambda report, out_dir, client: slotting_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            slotting_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- simulation (Monte-Carlo (R,S) policy optimization) ----------------------

def _simulation_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data",
                        messages=["a per-SKU CSV (product, mean_demand, std_demand, lead_time + costs) is required"])
    try:
        payload = simulation_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["records"]:
        return Prepared(status="needs_data", messages=["no SKUs found in the data"])
    return Prepared(status="ok", payload=payload)


def _simulation_run(payload: object, params: dict) -> Produced:
    report = simulation_job.run(payload)
    return Produced(report=report, summary=report.summary)


def simulation_tool() -> Tool:
    return Tool(
        key="simulation",
        title="Simulation-Optimized (R,S) Policy",
        description="Find the safety stock / order-up-to level that minimizes simulated total cost "
                    "(holding + ordering + backorder) per SKU via Monte-Carlo search, and quantify "
                    "the edge over the analytical optimum.",
        intent_keywords=(
            "simulation", "simulate", "monte carlo", "monte-carlo", "simulation optimization",
            "simulated policy", "stochastic simulation", "policy simulation", "simulate the policy",
            "simulation-based",
        ),
        requires_data=True,
        options=tool_options.simulation_options,
        prepare=_simulation_prepare,
        run=_simulation_run,
        qa=lambda report: simulation_job.verify(report),
        deliver=lambda report, out_dir, client: simulation_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            simulation_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- digital_twin (network scenario factory) ----------------------------------

def _digital_twin_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    try:
        payload = digital_twin_job.prepare(request.params)
    except ValueError as exc:
        return Prepared(status="needs_clarification", messages=[str(exc)])
    return Prepared(status="ok", payload=payload)


def _digital_twin_run(payload: object, params: dict) -> Produced:
    report = digital_twin_job.run(payload)
    return Produced(report=report, summary=report.summary)


def digital_twin_tool() -> Tool:
    return Tool(
        key="digital_twin",
        title="Digital Twin - Network Scenario Factory",
        description="Simulate a configurable supplier -> DC -> store network (demand patterns, "
                    "policies, disruptions) and emit the resulting demand / inventory / order "
                    "datasets, shaped like a client export so the rest of the suite can be "
                    "exercised on complex scenarios with known ground truth.",
        intent_keywords=(
            "digital twin", "digital-twin", "gemelo digital", "network simulation",
            "simulacion de red", "simular la red", "red de suministro simulada",
            "synthetic data", "datos sinteticos", "scenario generator",
            "generador de escenarios", "stress test", "prueba de estres",
            "disruption scenario", "escenario de disrupcion", "twin",
        ),
        requires_data=False,
        options=tool_options.digital_twin_options,
        prepare=_digital_twin_prepare,
        run=_digital_twin_run,
        qa=lambda report: digital_twin_job.verify(report),
        deliver=lambda report, out_dir, client: digital_twin_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            digital_twin_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- excess_obsolete (E&O / dead-stock) --------------------------------------

def _excess_obsolete_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data",
                        messages=["a stock CSV (product, on_hand, daily_demand + optional unit_cost / days_since_last_sale) is required"])
    try:
        payload = excess_obsolete_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["stocks"]:
        return Prepared(status="needs_data", messages=["no stock lines found in the data"])
    return Prepared(status="ok", payload=payload)


def _excess_obsolete_run(payload: object, params: dict) -> Produced:
    report = excess_obsolete_job.run(payload)
    return Produced(report=report, summary=report.summary)


def excess_obsolete_tool() -> Tool:
    return Tool(
        key="excess_obsolete",
        title="Excess & Obsolete (E&O) Stock",
        description="Classify on-hand stock into healthy / excess / dead from days-of-cover and "
                    "time-since-last-sale, size the cash tied up in slow and non-moving inventory, "
                    "and recommend a disposition (liquidate / return / draw down) per SKU.",
        intent_keywords=(
            "excess and obsolete", "excess inventory", "obsolete inventory", "dead stock",
            "deadstock", "slow moving", "slow-moving", "e&o", "overstock", "excess stock",
            "stranded inventory", "write off inventory", "non-moving stock", "months of cover",
        ),
        requires_data=True,
        options=tool_options.excess_obsolete_options,
        prepare=_excess_obsolete_prepare,
        run=_excess_obsolete_run,
        qa=lambda report: excess_obsolete_job.verify(report),
        deliver=lambda report, out_dir, client: excess_obsolete_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            excess_obsolete_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- markdown_liquidation (E&O x clearance pricing) --------------------------

def _markdown_liquidation_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data",
                        messages=["a stock CSV (product, on_hand, daily_demand + optional unit_cost / "
                                  "days_since_last_sale) is required; pass a price/quantity history via "
                                  "params['price_history_path'] to price clearances off elasticity"])
    try:
        payload = markdown_liquidation_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["stocks"]:
        return Prepared(status="needs_data", messages=["no stock lines found in the data"])
    return Prepared(status="ok", payload=payload)


def _markdown_liquidation_run(payload: object, params: dict) -> Produced:
    report = markdown_liquidation_job.run(payload)
    return Produced(report=report, summary=report.summary)


def markdown_liquidation_tool() -> Tool:
    return Tool(
        key="markdown_liquidation",
        title="Markdown Liquidation Plan",
        description="Cross the excess & obsolete classification with clearance pricing to produce a "
                    "per-SKU disposition plan: a clearance price, weeks-to-clear, and cash recovered "
                    "vs. writing the stock to zero - elasticity-priced where price history exists, a "
                    "documented default markdown or salvage recovery otherwise.",
        intent_keywords=(
            "markdown liquidation", "liquidation", "liquidation plan", "clearance", "clearance plan",
            "clearance pricing", "markdown plan", "markdown schedule", "sell through", "sell-through plan",
            "liquidate excess stock", "liquidate dead stock", "disposition plan", "clear excess inventory",
        ),
        requires_data=True,
        options=tool_options.markdown_liquidation_options,
        prepare=_markdown_liquidation_prepare,
        run=_markdown_liquidation_run,
        qa=lambda report: markdown_liquidation_job.verify(report),
        deliver=lambda report, out_dir, client: markdown_liquidation_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            markdown_liquidation_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- facility_location (network design / center of gravity) ------------------

def _facility_location_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data",
                        messages=["a demand-points CSV (x, y + optional name/weight) is required"])
    try:
        payload = facility_location_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["points"]:
        return Prepared(status="needs_data", messages=["no demand points found in the data"])
    return Prepared(status="ok", payload=payload)


def _facility_location_run(payload: object, params: dict) -> Produced:
    report = facility_location_job.run(payload)
    return Produced(report=report, summary=report.summary)


def facility_location_tool() -> Tool:
    return Tool(
        key="facility_location",
        title="Facility Location (Network Design)",
        description="Place a single facility (DC / hub / plant) to minimize weighted travel to a set "
                    "of demand points: the center of gravity and the Weiszfeld 1-median optimum, vs "
                    "the current site. Offline, straight-line distance.",
        intent_keywords=(
            "facility location", "network design", "center of gravity", "centre of gravity",
            "center-of-gravity", "dc location", "distribution center location", "hub location",
            "site selection", "where to locate", "optimal location", "greenfield site",
        ),
        requires_data=True,
        options=tool_options.facility_location_options,
        prepare=_facility_location_prepare,
        run=_facility_location_run,
        qa=lambda report: facility_location_job.verify(report),
        deliver=lambda report, out_dir, client: facility_location_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            facility_location_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- drp (distribution requirements planning) --------------------------------

def _drp_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data",
                        messages=["a long-format demand CSV (branch, period, demand) is required"])
    try:
        payload = drp_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["branches"]:
        return Prepared(status="needs_data", messages=["no branches found in the data"])
    return Prepared(status="ok", payload=payload)


def _drp_run(payload: object, params: dict) -> Produced:
    report = drp_job.run(payload)
    return Produced(report=report, summary=report.summary)


def drp_tool() -> Tool:
    return Tool(
        key="drp",
        title="Distribution Requirements Planning (DRP)",
        description="Run the time-phased DRP grid per branch (gross -> net -> planned order releases, "
                    "offset by lead time), then roll the branches' releases up as the central DC's "
                    "gross requirements and plan it - a network-wide replenishment schedule.",
        intent_keywords=(
            "drp", "distribution requirements planning", "distribution requirement planning",
            "time-phased", "time phased", "planned order release", "central dc replenishment",
            "branch replenishment", "network replenishment", "drp grid", "push distribution",
        ),
        requires_data=True,
        options=tool_options.drp_options,
        prepare=_drp_prepare,
        run=_drp_run,
        qa=lambda report: drp_job.verify(report),
        deliver=lambda report, out_dir, client: drp_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            drp_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- vehicle_routing (route design & scheduling) -----------------------------

def _vehicle_routing_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data",
                        messages=["a stops CSV (x, y, demand + optional service time/time window) "
                                  "and a vehicle capacity (params.capacity) are required"])
    try:
        payload = vehicle_routing_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["stops"]:
        return Prepared(status="needs_data", messages=["no stops found in the data"])
    return Prepared(status="ok", payload=payload)


def _vehicle_routing_run(payload: object, params: dict) -> Produced:
    report = vehicle_routing_job.run(payload)
    return Produced(report=report, summary=report.summary)


def vehicle_routing_tool() -> Tool:
    return Tool(
        key="vehicle_routing",
        title="Vehicle Routing & Scheduling",
        description="Group delivery stops into capacity-feasible vehicle routes and sequence each "
                    "one, comparing the Clarke-Wright savings method against the sweep method, "
                    "against a one-truck-per-stop baseline. Optional time windows are flagged as a "
                    "lightweight feasibility check. Offline, straight-line distance from one depot.",
        intent_keywords=(
            "vehicle routing", "vehicle routing problem", "route optimization", "delivery routes",
            "delivery route planning", "truck routing", "route sequencing", "route design",
            "savings algorithm", "sweep algorithm", "clarke-wright", "vrp",
            "ruteo de vehiculos", "diseno de rutas", "programacion de rutas",
        ),
        requires_data=True,
        options=tool_options.vehicle_routing_options,
        prepare=_vehicle_routing_prepare,
        run=_vehicle_routing_run,
        qa=lambda report: vehicle_routing_job.verify(report),
        deliver=lambda report, out_dir, client: vehicle_routing_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            vehicle_routing_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )


# ---- price_intelligence (the pricing titan, one-shot mode) -------------------
#
# `jobs.price_intelligence` is deliberately NOT imported in this file's top-of-
# module `from jobs import (...)` block above: it (transitively, via
# src.pricing_intel.sanity) imports `scm_agent.events`, and importing that
# submodule for the first time forces `scm_agent/__init__.py` to finish
# running -- which itself does `from .tools import build_default_registry`,
# i.e. THIS module. Importing `jobs.price_intelligence` eagerly here would
# recreate the exact circular-import hazard `jobs/qa.py` already documents and
# avoids (via TYPE_CHECKING) for `jobs.digest_job`. Each function below does
# its own local import instead -- cheap (Python caches the module) and it
# defers the import until `scm_agent`'s own package init has already
# completed.


def _price_intelligence_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    from jobs import price_intelligence as price_intelligence_job

    if not request.data_path:
        return Prepared(
            status="needs_data",
            messages=["a refs CSV (product_id, competitor_url [+ our_price, currency, html_path]) is required"],
        )
    try:
        payload = price_intelligence_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["refs"]:
        return Prepared(status="needs_data", messages=["no refs found in the data"])
    return Prepared(status="ok", payload=payload)


def _price_intelligence_run(payload: object, params: dict) -> Produced:
    from jobs import price_intelligence as price_intelligence_job
    from src.pricing_intel.ledger import PriceLedger

    # Same cross-thread sqlite3 hazard as _price_watch_run above (see that
    # function's docstring comment) -- price_intelligence_job.run() defaults
    # its own `ledger` to the SAME process-wide default_ledger() singleton
    # when called with none, and this tool is reachable via POST /api/jobs's
    # asyncio.to_thread pool exactly like price_watch is. Own a fresh,
    # call-scoped PriceLedger here unless the caller supplied one.
    ledger = params.get("ledger")
    owns_ledger = ledger is None
    if owns_ledger:
        ledger = PriceLedger()
    try:
        report = price_intelligence_job.run(payload, ledger=ledger)
        return Produced(report=report, summary=report.summary)
    finally:
        if owns_ledger:
            ledger.close()


def price_intelligence_tool() -> Tool:
    from jobs import price_intelligence as price_intelligence_job

    def _deck(report, out_dir, client, citations, confidence, options):
        # citations here are the orchestrator's ungated `ground_citations` (the
        # same convention every other tool's deck uses) -- but golden rule 7 +
        # the E5 gate are explicit requirements for THIS deliverable, so the deck
        # recomputes its own E5-gated citations via
        # `price_intelligence_job.gated_citations` (filter_citations, not a
        # parallel mechanism) instead of trusting the ungated list passed in.
        deliverable = replace(
            price_intelligence_job.build_deck(
                report, client=client, citations=price_intelligence_job.gated_citations(""),
                confidence=confidence,
            ),
            options=tuple(options),
        )
        report_path = price_intelligence_job.write_report_md(deliverable, report, out_dir, deliverable.lang)
        return {"report_md": report_path}

    return Tool(
        key="price_intelligence",
        title="Price Position Diagnostic",
        description="Compare your own price against confirmed competitor observations (a client-supplied "
                    "SKU-to-URL refs file), flag suspect/quarantined reads separately, and produce a "
                    "price-position matrix with full acquisition-tier provenance per datum.",
        intent_keywords=(
            "price position", "price intelligence", "competitor pricing", "competitor prices",
            "competitive pricing", "price competitiveness", "price monitoring",
            "monitorea precios de la competencia", "donde estoy caro", "posicion de precios",
            "precio de la competencia", "inteligencia de precios",
        ),
        requires_data=True,
        options=tool_options.price_intelligence_options,
        prepare=_price_intelligence_prepare,
        run=_price_intelligence_run,
        qa=lambda report: qa.verify_price_intel(report),
        deliver=lambda report, out_dir, client: price_intelligence_job.write_operational(report, out_dir, client),
        deck=_deck,
    )


# ---- price_watch (discovery-assisted continuous monitoring) -----------------
#
# Same lazy-import hazard as price_intelligence_tool() above, for TWO reasons:
# `jobs.price_watch` imports `scm_agent.events` directly, and `jobs.price_priority`
# imports `jobs.price_intelligence` (which itself imports `scm_agent.citation_gate`/
# `scm_agent.events`/`scm_agent.knowledge` at top level) -- either import at this
# module's top level would recreate the exact circular-import hazard documented
# above. Every function below does its own local import instead. (`price_watch_
# deliverable` is safe to import eagerly -- see that module's own docstring for
# why it carries none of this hazard.)
#
# Finding 1 (final whole-branch review): `_price_watch_run` used to discard
# the `HomologationReport` (only its counts landed in the summary string) and
# never build the price-position/price-priority steps at all, so the
# registered tool's `deliver` hook only ever wrote `price_watch_cycle.csv` --
# materially less than `examples/run_price_watch.py`'s CLI, which chains
# prepare -> homologate -> watch-cycle -> price-position -> price_priority
# and writes the full deliverable set. `_price_watch_run` now ports that SAME
# wiring (via `jobs.price_watch_position`, itself ported from the CLI's own
# previously-private helper) so the tool reaches parity as closely as the
# `Tool` interface's single-URL-input shape allows: `our_catalog`/
# `our_gtins`/`our_prices` are pre-parsed params (mirroring the existing
# `our_catalog`/`our_gtins` convention `run_homologation`'s payload already
# used), while `catalog_path` -- a raw CSV, the one new param this adds,
# following the SAME "takes a plain string, like `seed_url`" shape --
# feeds `jobs.price_priority.prepare`'s own ABC-XYZ step, which genuinely
# needs a file (no pre-parsed-object equivalent exists for it). Absent ->
# the priority step is honestly skipped (no fabricated plan), never crashes
# the whole call on a malformed catalog either.


def _price_watch_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    from jobs import price_watch

    seed_url = request.params.get("seed_url") or request.data_path
    if not seed_url:
        return Prepared(
            status="needs_data",
            messages=["a competitor category/product-listing seed_url is required (params['seed_url'])"],
        )
    payload = price_watch.prepare(str(seed_url), request.params)
    if payload["skipped_reason"]:
        return Prepared(status="needs_data", messages=[f"site gate refused the crawl: {payload['skipped_reason']}"])
    if not payload["discovered"]:
        return Prepared(status="needs_data", messages=[
            f"no product page(s) discovered at {payload['domain']} ({payload['pages_crawled']} page(s) crawled)"
        ])
    return Prepared(status="ok", payload=payload)


def _price_watch_run(payload: object, params: dict) -> Produced:
    from jobs import price_priority, price_watch
    from jobs.price_intelligence import DEFAULT_SLA_HOURS
    from jobs.price_watch_position import PriceWatchToolReport, price_report_from_confirmed_pairs
    from src.pricing_intel.ledger import PriceLedger
    from src.pricing_intel.match.sku_map import SkuMap

    now = params.get("now")
    # Thread-safety: POST /api/jobs dispatches tool.run() via
    # asyncio.to_thread(...), whose default executor draws from a POOL of
    # worker threads -- it does NOT pin this call to the same OS thread every
    # time. `src.pricing_intel.ledger.default_ledger()`/
    # `match.sku_map.default_sku_map()` are deliberately process-wide CACHED
    # sqlite3 connections (see their own docstrings) -- correct for a
    # single-threaded caller (the CLI, or PR-151's run-scheduled cron
    # endpoint, which uses a dedicated single-thread executor specifically to
    # stay safe with them), but sqlite3 connections default to
    # check_same_thread=True: whichever pool thread first touches the
    # singleton binds it, and any OTHER pool thread that later reaches this
    # same singleton raises sqlite3.ProgrammingError. A caller-supplied
    # sku_map/ledger (params) is honored as-is and its lifecycle stays the
    # caller's; otherwise this owns a FRESH, call-scoped connection to the
    # SAME on-disk path for the life of this call only -- never the shared
    # singleton -- matching webapp/app.py's own _get_price_ledger()/
    # _get_sku_map() "fresh connection per call" convention (same reasoning,
    # same fix shape PR-151 already shipped and reviewed for this exact
    # hazard class).
    owns_sku_map = params.get("sku_map") is None
    owns_ledger = params.get("ledger") is None
    sku_map = params.get("sku_map") if not owns_sku_map else SkuMap()
    ledger = params.get("ledger") if not owns_ledger else PriceLedger()
    try:
        # `_price_watch_prepare` threads params["config_dir"] into price_watch.
        # prepare()'s own config_dir seam (auto-onboarding + the hard gate); the
        # watch cycle's site lookup must resolve the SAME directory or a caller
        # who only set config_dir would auto-onboard correctly but then have
        # this step silently fall back to the real config/sites/ (mirrors
        # examples/run_price_watch.py's run_pipeline, which threads one
        # config_dir to both steps). sites_config_dir stays available as an
        # explicit override for a caller who genuinely wants the two to differ.
        sites_config_dir = params.get("sites_config_dir", params.get("config_dir"))
        homologation = price_watch.run_homologation(
            {
                **payload,
                "our_catalog": params.get("our_catalog") or [],
                "our_gtins": params.get("our_gtins"),
                "llm": params.get("llm"),
            },
            sku_map=sku_map,
            now=now,
        )
        cycle = price_watch.run_price_watch_cycle(
            sku_map=sku_map,
            ledger=ledger,
            event_ledger=params.get("event_ledger"),
            http_client=params.get("http_client"),
            sites_config_dir=sites_config_dir,
            scaling_request_for=params.get("scaling_request_for"),
            now=now,
        )

        # Price-position report from THIS cycle's own fresh ledger reads
        # (Finding 1, ported logic -- never a second acquisition run) --
        # `sku_map`/`ledger` are the SAME instance the two calls above already
        # used (either the caller's own, or this call's owned fresh one).
        our_prices = params.get("our_prices") or {}
        sla_hours = float(params.get("sla_hours", DEFAULT_SLA_HOURS))
        confirmed_pairs = sku_map.list_all_confirmed()
        report_prices = {
            pid: our_prices[pid] for pid in {e.our_product_id for e in confirmed_pairs} if pid in our_prices
        }
        price_report = price_report_from_confirmed_pairs(
            confirmed_pairs, report_prices, ledger, now=cycle.now, sla_hours=sla_hours,
        )

        # Per-SKU price priority plan -- only when a catalog_path was supplied
        # (see the module-level comment above for why this one param stays a raw
        # CSV path); a malformed/unreadable file degrades to "no priority plan
        # this run" rather than failing the whole tool call.
        catalog_path = params.get("catalog_path")
        priority = None
        if catalog_path:
            try:
                pp_payload = price_priority.prepare(str(catalog_path), {"price_report": price_report})
                priority = price_priority.run(pp_payload)
            except (ValueError, FileNotFoundError):
                priority = None

        report = PriceWatchToolReport(
            cycle=cycle, homologation=homologation, price_report=price_report, priority=priority,
        )
        summary = (
            f"Discovery crawl at {payload['domain']}: {len(payload['discovered'])} product page(s) found, "
            f"{homologation.n_confirmed} confirmed / {homologation.n_suspect} suspect match(es). {cycle.summary}"
        )
        if priority is not None:
            summary += f" {priority.summary}"
        return Produced(report=report, summary=summary)
    finally:
        if owns_sku_map:
            sku_map.close()
        if owns_ledger:
            ledger.close()


def price_watch_tool() -> Tool:
    return Tool(
        key="price_watch",
        title="Discovery-Assisted Price Watch",
        description="Auto-onboard a never-seen competitor site (robots.txt-only, limited tier), crawl "
                    "and homologate its product pages against your own catalog, then re-acquire and "
                    "monitor the confirmed pairs on a recurring cadence, producing a homologation table, "
                    "a price-position matrix, and a per-SKU price-priority plan - read-only observation; "
                    "a tier raise beyond the approved ceiling is never applied automatically.",
        intent_keywords=(
            "price watch cycle", "recurring competitor price watch", "competitor price discovery",
            "discovery-assisted competitor watch", "vigila los precios de la competencia",
            "descubre productos de la competencia", "homologa productos competencia",
        ),
        requires_data=False,
        options=tool_options.price_watch_options,
        prepare=_price_watch_prepare,
        run=_price_watch_run,
        qa=lambda report: qa.verify_price_watch(report),
        deliver=lambda report, out_dir, client: price_watch_deliverable.write_operational(report, out_dir, client),
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
    reg.register(queuing_tool())
    reg.register(scheduling_tool())
    reg.register(risk_tool())
    reg.register(forecast_tool())
    reg.register(data_quality_tool())
    reg.register(dea_tool())
    reg.register(acceptance_sampling_tool())
    reg.register(earned_value_tool())
    reg.register(learning_curve_tool())
    reg.register(odoo_replenishment_tool())
    reg.register(excel_replenishment_tool())
    reg.register(newsvendor_tool())
    reg.register(cycle_count_tool())
    reg.register(multi_echelon_tool())
    reg.register(transportation_tool())
    reg.register(fefo_tool())
    reg.register(slotting_tool())
    reg.register(simulation_tool())
    reg.register(digital_twin_tool())
    reg.register(excess_obsolete_tool())
    reg.register(markdown_liquidation_tool())
    reg.register(facility_location_tool())
    reg.register(drp_tool())
    reg.register(vehicle_routing_tool())
    reg.register(price_intelligence_tool())
    reg.register(price_watch_tool())
    return reg

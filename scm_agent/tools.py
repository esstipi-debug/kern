"""The three MVP capabilities, each wrapping existing job machinery as a Tool."""

from __future__ import annotations

from jobs import (
    abc_xyz_job,
    cost_to_serve_deliverable,
    cost_to_serve_job,
    deliverables,
    intake,
    inventory_deliverable,
    leadership,
    qa,
    sop_deliverable,
    sop_job,
    sourcing_job,
)
from jobs.inventory_optimization import run as run_inventory
from jobs.pricing import prepare_pricing
from jobs.pricing import run as run_pricing

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
        prepare=_inventory_prepare,
        run=_inventory_run,
        qa=lambda report: qa.verify(report),
        deliver=lambda report, out_dir, client: deliverables.write_all(report, out_dir, client=client),
        deck=lambda report, out_dir, client, citations, confidence: inventory_deliverable.build(
            report, client=client, citations=tuple(citations), confidence=confidence
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
        prepare=_cost_to_serve_prepare,
        run=_cost_to_serve_run,
        qa=lambda report: cost_to_serve_job.verify(report),
        deliver=lambda report, out_dir, client: cost_to_serve_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence: cost_to_serve_deliverable.build(
            report.portfolio, working_cap=report.working_cap, cash_release=report.cash_release,
            client=client, citations=tuple(citations), confidence=confidence,
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
        prepare=_sop_prepare,
        run=_sop_run,
        qa=lambda review: sop_job.verify(review),
        deliver=lambda review, out_dir, client: sop_job.write_operational(review, out_dir, client),
        deck=lambda review, out_dir, client, citations, confidence: sop_deliverable.build(
            review, client=client, citations=tuple(citations),
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
        prepare=_abc_xyz_prepare,
        run=_abc_xyz_run,
        qa=lambda report: abc_xyz_job.verify(report),
        deliver=lambda report, out_dir, client: abc_xyz_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence: abc_xyz_job.build_deck(
            report, client=client, citations=tuple(citations), confidence=confidence,
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
        prepare=_sourcing_prepare,
        run=_sourcing_run,
        qa=lambda report: sourcing_job.verify(report),
        deliver=lambda report, out_dir, client: sourcing_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence: sourcing_job.build_deck(
            report, client=client, citations=tuple(citations), confidence=confidence,
        ).write_all(out_dir),
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
    return reg

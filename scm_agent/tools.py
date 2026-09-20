"""The three MVP capabilities, each wrapping existing job machinery as a Tool."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from jobs import (
    abc_xyz_job,
    acceptance_sampling_job,
    audit_evidence_job,
    channel_replenishment_job,
    cost_to_serve_deliverable,
    cost_to_serve_job,
    cross_impact_job,
    cycle_count_job,
    data_quality_job,
    ddmrp_job,
    dea_job,
    deliverables,
    digital_twin_job,
    disruption_scan_job,
    drp_job,
    earned_value_job,
    excel_replenishment_job,
    excess_obsolete_job,
    facility_location_job,
    fefo_job,
    financial_kpis_job,
    flow_map_job,
    forecast_cycle,
    forecast_job,
    hats_job,
    import_case_job,
    intake,
    intake_scan_job,
    inventory_deliverable,
    iso_ops_job,
    landed_cost_job,
    launch_readiness_job,
    leadership,
    learning_curve_job,
    markdown_liquidation_job,
    multi_echelon_job,
    network_design_job,
    newsvendor_job,
    odoo_job,
    org_friction_job,
    price_watch_deliverable,
    procedure_audit_job,
    procedure_draft_job,
    qa,
    queuing_job,
    reconciliation_job,
    repricing_job,
    returns_job,
    risk_job,
    scheduling_job,
    scor_audit_job,
    service_scan_deliverable,
    service_scan_job,
    shopify_job,
    simulation_job,
    slotting_job,
    sop_deliverable,
    sop_job,
    sourcing_job,
    state_bridge,
    supplier_management_job,
    transportation_job,
    vehicle_routing_job,
    whatif_job,
)
from jobs.inventory_optimization import run as run_inventory
from jobs.inventory_optimization import unsized_note
from jobs.pricing import prepare_pricing
from jobs.pricing import run as run_pricing
from nosilo_core.llm import LLMProvider
from nosilo_core.registry import Prepared, Produced, Tool, ToolRegistry
from nosilo_core.types import JobRequest

from . import tool_options

from src.connectors.odoo import OdooError  # isort: skip  (local package)
from src.connectors.shopify import ShopifyCommerceError  # isort: skip  (local package)
from src.connectors.shopify_inventory import ShopifyInventoryError  # isort: skip  (local package)
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
        period = request.params.get("period", "W")
        demand, quality = intake.prepare_tracked(
            request.data_path,
            period=period,
            # Client-wide lead time (typically from the client profile) fills in only
            # where the CSV carries none; per-SKU CSV values always win.
            default_lead_days=float(request.params.get("lead_time_days", 14.0)),
            aliases=request.params.get("column_aliases"),
        )
    except (ValueError, FileNotFoundError, TypeError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    # El servicio realizado, si el archivo lo trae. Es el productor que le faltaba a
    # `run_inventory(order_lines=...)`: ese argumento existe desde que aterrizo el motor de
    # servicio y ningun llamador lo pasaba, asi que cada corrida reportaba `not_measured`
    # sobre archivos que a veces SI traian con que medirse.
    #
    # Se lee el archivo una segunda vez a proposito, y no sale de `demand`: la demanda se
    # AGREGA a periodos y el servicio no. Dos pedidos del mismo SKU en la misma semana son
    # dos resultados distintos, y sumarlos borra justamente el que fallo.
    #
    # Y va envuelto: el servicio es un extra sobre lo que el cliente pidio. Si su deteccion
    # falla, la politica de inventario tiene que salir igual -- un adorno que rompe el
    # entregable es peor que no tenerlo.
    try:
        order_lines = intake.service_lines(
            request.data_path, aliases=request.params.get("column_aliases")
        )
    except (ValueError, KeyError, TypeError, FileNotFoundError):
        order_lines = None
    # La posicion de inventario, si el archivo la trae (stock en estante y, opcional,
    # stock en camino). Es el productor que le faltaba a `run(on_hand_by_sku=...)`: sin
    # el, el tool dice cuanto pedir y nunca cuando, y menos descuenta lo que ya viene.
    # Misma envoltura que el servicio: es un extra, y un extra que falla no puede
    # tumbar la politica.
    try:
        stock_position = intake.stock_position(
            request.data_path, aliases=request.params.get("column_aliases")
        )
    except (ValueError, KeyError, TypeError, FileNotFoundError):
        stock_position = None
    # `period` rides along: src/sources.py needs it to rebuild the COMPLETE period
    # grid per SKU. Without it a SKU's series is only the periods it sold in, which
    # makes every real export look like regular demand (ADI == 1.0) and overstates
    # annual demand by the SKU's true demand interval.
    return Prepared(
        status="ok",
        payload={
            "demand": demand,
            "intake_quality": quality,
            "period": period,
            "order_lines": order_lines,
            "stock_position": stock_position,
        },
    )


def _record_state(report, summary: str, params: dict, *, tool: str, lines, n_candidates=None,
                  demand_points=None, lead_times_by_sku=None):
    """Escribe la memoria del Tower y devuelve (reporte, resumen) actualizados.

    N1c: hasta esto, el unico escritor del dominio ``stock`` era el puente del marcador
    (``examples/run_world_sim.py``), asi que ``run_all_monitors`` sensaba sobre vacio en
    produccion -- hallazgo H1. Los cinco jobs que leen una posicion lo escriben ahora por
    ``jobs/state_bridge.py``, que es donde vive el mapeo de cada uno.

    ``demand_points`` (N1a) agrega el OTRO dominio que al Tower le faltaba: la demanda
    REALIZADA como ``outcomes``/``actual_qty``. ``forecast_cycle.freeze`` escribe el
    pronostico y el MAPE, nunca lo realizado, asi que ``forecast_error_out_of_band``
    tenia con que comparar solo cuando corria el marcador. Viene del plan, que ya leyo
    los pedidos: no cuesta una segunda lectura del backend.

    La particion sale de ``params['state_client_id']`` con la MISMA regla que la memoria
    de ciclos (``forecast_cycle.store_from_params``): sin la clave no se escribe nada y
    nunca la particion compartida; un id invalido levanta en vez de degradar. La nota va
    siempre al reporte, y al resumen solo cuando alguien nombro una particion -- con la
    memoria apagada por configuracion, una linea en cada resumen seria ruido.
    """
    store = forecast_cycle.store_from_params(params)
    note = state_bridge.record_stock_snapshot(
        store, tool=tool, lines=lines, n_candidates=n_candidates,
    )
    textos = [note.text]
    if demand_points is not None or lead_times_by_sku is not None:
        # UN solo ciclo de `outcomes` con las dos metricas. `lead_time_drift_monitor`
        # compara los dos snapshots mas recientes del dominio: escribirlas en ciclos
        # separados dejaria arriba uno sin plazos y el monitor no podria disparar nunca.
        medido = state_bridge.record_outcomes(
            store, tool=tool, points_by_sku=demand_points,
            lead_times_by_sku=lead_times_by_sku,
        )
        # Una sola frase para el operador: las dos escrituras son del mismo ciclo de
        # sensado, y separarlas en dos lineas invita a leer una y no la otra.
        textos.append(medido.text)
    texto = " ".join(textos)
    report = replace(report, state_note=texto)
    if note.store_named:
        summary = f"{summary} {texto}"
    return report, summary


def _inventory_run(payload: object, params: dict) -> Produced:
    stock_position = payload.get("stock_position")
    on_hand_by_sku, on_order_by_sku = intake.position_maps(stock_position)
    position_note = (
        str(stock_position.attrs.get("position_note", "") or "")
        if stock_position is not None else ""
    )
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
        # La dispersion del plazo que el cliente declaro. Hasta el 2026-09-20 esta
        # linea NO existia: el job la aceptaba, el agente la tiraba, y no se avisaba.
        # Es la peor forma de perder un dato, porque INFLA la confianza -- sin
        # `sigma_L` el periodo de riesgo pierde el termino `sigma_L^2 * mu_d^2`, que
        # es el DOMINANTE cuando la demanda es grande frente a su propia dispersion.
        # Medido: con `lead_time_stds={"A": 3.0}` el buffer pasa de 11,9 a 238,4
        # llamando al job, y quedaba en 11,9 pasando por el orquestador, con el
        # entregable diciendo igual "95% service level".
        lead_time_stds=params.get("lead_time_stds"),
        observed_fill_rates=params.get("observed_fill_rates"),
        target_fill_rate=params.get("target_fill_rate", 0.95),
        intake_quality=payload["intake_quality"],
        intake_quality_threshold=params.get("intake_quality_threshold", 0.20),
        period_freq=payload.get("period"),
        order_lines=payload.get("order_lines"),
        stockout_days=params.get("stockout_days"),
        # La promesa que el cliente declaro (client_profile.delivery_promise_days,
        # via as_params). Sin ella, el OTIF de un ERP que no data sus lineas sale
        # "no medido"; con ella sale medido contra una vara dicha, y el entregable
        # lo declara. Nunca pisa una fecha del sistema.
        delivery_promise_days=params.get("delivery_promise_days"),
        # Per-SKU supplier terms typed into the request win over the file's columns.
        minimum_order_quantities=params.get("minimum_order_quantities"),
        order_multiples=params.get("order_multiples"),
        # Today's position from the file; explicit per-SKU maps in the request win.
        on_hand_by_sku=params.get("on_hand_by_sku", on_hand_by_sku),
        on_order_by_sku=params.get("on_order_by_sku", on_order_by_sku),
        position_note=position_note,
    )
    # El titular y la cola las escribe UNA sola funcion, en el productor
    # (`jobs/inventory_optimization.unsized_note`), porque el deck que lee el
    # cliente arma su propio encabezado y arreglar solo este dejaba al otro
    # diciendo "recommended $0 inventory investment" sobre una corrida en la que
    # no se decidio nada.
    titular, cola = unsized_note(report)
    if titular is not None:
        summary = titular
    else:
        summary = (
            f"Analyzed {report.n_skus} SKUs; recommended inventory investment "
            f"${report.final_investment:,.0f} at {report.params['service_level'] * 100:.0f}% service level."
            + cola
        )
    # La nota del servicio viaja en el resumen, que es lo que el operador lee ANTES de
    # abrir el entregable. Sin ella el fill rate aparece sin decir de que columna del
    # cliente salio, y un numero que no se puede reconciliar contra el ERP no se usa.
    order_lines = payload.get("order_lines")
    nota = getattr(order_lines, "attrs", {}).get("service_note") if order_lines is not None else None
    if nota:
        summary = f"{summary} {nota}"
    if report.n_positioned:
        summary = (
            f"{summary} {report.n_order_now} of {report.n_positioned} positioned SKU(s) are due "
            f"for an order now. {report.position_note}"
        )
    report, summary = _record_state(
        report, summary, params,
        tool="inventory_optimization",
        lines=state_bridge.stock_lines_from_inventory(report),
        n_candidates=len(report.recommendations),
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
        # Straight from jobs/intake.py's CANONICAL_REQUIRED / CANONICAL_OPTIONAL:
        # this tool does not sniff its own columns, it hands the file to the shared
        # normalizer. `revenue` is optional here for the same reason it is optional
        # there -- detected but never emitted, it exists only to recover a real unit
        # cost from a file that carries money but no cost column.
        required_columns=("date", "product_id", "quantity"),
        # `moq` / `order_multiple` are the supplier's terms: with them every (s,Q)
        # quantity is a lot the supplier accepts, with the runner-up lot on the
        # record (jobs/inventory_optimization.py, src/constraints.py).
        # `on_hand` / `on_order` are today's stock position: with them every row also
        # says whether an order is due NOW, sized against on hand plus stock already
        # in transit (jobs/intake.py::stock_position).
        optional_columns=(
            "unit_cost", "lead_time_days", "lead_time_std", "revenue", "moq", "order_multiple",
            "on_hand", "on_order",
        ),
        options=tool_options.inventory_options,
        prepare=_inventory_prepare,
        run=_inventory_run,
        qa=lambda report: qa.verify(report),
        deliver=lambda report, out_dir, client: deliverables.write_all(report, out_dir, client=client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            inventory_deliverable.build(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/pricing.py::_REQUIRED. `price` is the SELLING price, and it is the one
        # name here that is not already canonical elsewhere in the repo. Known edge:
        # jobs/intake.py lists "price" among unit_cost's aliases, so a header named
        # exactly `price` resolves here (exact names are claimed before aliases) but a
        # file whose only money column is `precio` or `unit_price` still resolves to
        # unit_cost, and this tool reads RED. Untangling cost-vs-price in that alias
        # table is a change of its own, not a side effect of declaring a contract.
        required_columns=("date", "product_id", "quantity", "price"),
        optional_columns=("unit_cost",),
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
    if not _has_cost_to_serve_signal(activities, request.params):
        return Prepared(status="needs_data", messages=[
            "cost to serve needs at least one activity cost driver: pass "
            "cost_per_order, cost_per_unit_shipped and/or return_handling_per_unit "
            "in params (or supply a freight column in the data). Without any of "
            "them fulfillment, returns and overhead are all zero, so the "
            "net-to-serve margin would just restate the gross margin."
        ])
    return Prepared(status="ok", payload=activities)


_CTS_RATE_KEYS = ("cost_per_order", "cost_per_unit_shipped", "return_handling_per_unit")


def _has_cost_to_serve_signal(activities, params: dict) -> bool:
    """True when *some* cost-to-serve driver exists, from params or from the data.

    Zero rates are legitimate when the file already carries the money (freight is
    sniffed into SegmentActivity.outbound_freight, overhead can be set by a
    caller building activities directly) -- so this only blocks the case where
    there is no cost signal anywhere.
    """
    if any(float(params.get(k, 0.0) or 0.0) > 0 for k in _CTS_RATE_KEYS):
        return True
    return any(
        float(getattr(a, "outbound_freight", 0.0) or 0.0) > 0
        or float(getattr(a, "overhead", 0.0) or 0.0) > 0
        for a in activities
    )


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
        # jobs/cost_to_serve_job.py:98 for segment/revenue, plus `freight`: without an
        # activity cost driver the tool refuses outright, and freight is the ONLY driver
        # that can arrive as a column -- the other three (cost_per_order,
        # cost_per_unit_shipped, return_handling_per_unit) are params. Under the default
        # params intake_scan speaks for, freight is therefore required, not optional.
        required_columns=("segment", "revenue", "freight"),
        optional_columns=("quantity", "order_id", "cogs"),
        options=tool_options.cost_to_serve_options,
        prepare=_cost_to_serve_prepare,
        run=_cost_to_serve_run,
        qa=lambda report: cost_to_serve_job.verify(report),
        deliver=lambda report, out_dir, client: cost_to_serve_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            cost_to_serve_deliverable.build(
                report.portfolio, working_cap=report.working_cap, cash_release=report.cash_release,
                client=client, citations=tuple(citations), confidence=confidence,
            ),
            options=tuple(options), readings=tuple(readings),
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
        promo_lift=sop_job.parse_promo_lift(params.get("promo_lift")),
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
        # jobs/sop_job.py sniffs _DATE_COLS and _DEMAND_COLS. Declared under the repo's
        # shared vocabulary (`date`, `quantity`) rather than that module's first alias
        # ("Order Date", "Quantity"): both are inside its own alias tuples, and asking a
        # client for three different names for one concept is how a data request becomes
        # unanswerable.
        required_columns=("date", "quantity"),
        options=lambda report: report.outcome,
        prepare=_sop_prepare,
        run=_sop_run,
        qa=lambda review: sop_job.verify(review),
        deliver=lambda review, out_dir, client: sop_job.write_operational(review, out_dir, client),
        deck=lambda review, out_dir, client, citations, confidence, options, readings=(): replace(
            sop_deliverable.build(review, client=client, citations=tuple(citations)),
            options=tuple(options), readings=tuple(readings),
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
        required_columns=("product_id", "quantity", "unit_cost"),
        optional_columns=(),
        options=tool_options.abc_xyz_options,
        prepare=_abc_xyz_prepare,
        run=_abc_xyz_run,
        qa=lambda report: abc_xyz_job.verify(report),
        deliver=lambda report, out_dir, client: abc_xyz_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            abc_xyz_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        notes=payload.get("notes"),
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
        # jobs/sourcing_job.py:396-449. This one does not use the shared sniffer idiom:
        # it DERIVES OTIF from alternative input shapes, refusing at five separate
        # points. Every column below is provably required UNDER DEFAULT PARAMS, which
        # is the only case intake_scan can speak for -- it never passes params. With
        # params the tool also accepts order/delivery date pairs instead of
        # `lead_time_days`, a `promised_date` instead of `on_time`, and
        # received-vs-ordered quantities instead of `in_full`.
        required_columns=(
            "supplier", "on_time", "in_full", "lead_time_days", "quantity", "defects",
        ),
        optional_columns=("unit_cost",),
        options=lambda report: report.outcome,
        prepare=_sourcing_prepare,
        run=_sourcing_run,
        qa=lambda report: sourcing_job.verify(report),
        deliver=lambda report, out_dir, client: sourcing_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, lang="en", readings=(): replace(
            sourcing_job.build_deck(report, client=client, citations=tuple(citations),
                                    confidence=confidence, lang=lang),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
    )


# ---- supplier_management (Kraljic segmentation / strategic SRM) ---------------

def _supplier_management_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(
            status="needs_data",
            messages=["a suppliers CSV (supplier, annual spend, plus risk-driver columns "
                      "like lead time / single-source / quality) is required"],
        )
    try:
        payload = supplier_management_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["suppliers"]:
        return Prepared(status="needs_data", messages=["no suppliers found in the data"])
    return Prepared(status="ok", payload=payload)


def _supplier_management_run(payload: object, params: dict) -> Produced:
    report = supplier_management_job.run(
        payload["suppliers"], payload["drivers"],
        impact_pareto=params.get("impact_pareto", 0.8),
        risk_threshold=params.get("risk_threshold", 0.5),
    )
    return Produced(report=report, summary=report.summary)


def supplier_management_tool() -> Tool:
    return Tool(
        key="supplier_management",
        title="Supplier Portfolio Segmentation (Kraljic)",
        description="Segment suppliers on the Kraljic matrix (profit impact x supply risk) into "
                    "strategic / bottleneck / leverage / non-critical quadrants and map each to a "
                    "strategic-SRM playbook.",
        intent_keywords=(
            "kraljic matrix", "kraljic", "supplier segmentation", "supplier portfolio",
            "purchasing portfolio", "strategic supplier relationship", "supplier relationship management",
            "srm", "supply risk segmentation", "supplier quadrant", "categorize suppliers",
        ),
        requires_data=True,
        # jobs/supplier_management_job.py:121. Only supplier and annual_value are hard.
        # The five risk drivers are a ONE-OF disjunction (the job refuses when none is
        # present), and a disjunction cannot live in `required_columns` without lying:
        # declaring one driver would tell a client carrying `defect_ppm` and no lead
        # time that their file is short two columns. They go in `optional_columns`,
        # which under-declares -- yellow instead of red -- and that is the safe
        # direction when the two errors are not symmetric.
        required_columns=("supplier", "annual_value"),
        optional_columns=(
            "lead_time_days", "single_source", "defect_ppm", "financial_risk", "geo_risk",
        ),
        options=lambda report: report.outcome,
        prepare=_supplier_management_prepare,
        run=_supplier_management_run,
        qa=lambda report: supplier_management_job.verify(report),
        deliver=lambda report, out_dir, client: supplier_management_job.write_operational(
            report, out_dir, client
        ),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            supplier_management_job.build_deck(
                report, client=client, citations=tuple(citations), confidence=confidence
            ),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/ddmrp_job.py:112. Its _PART_COLS carries BOTH `part_id` and
        # `product_id`; only one may be declared or dropping it would leave the other
        # standing in and the guard test would never fire. The shared canonical wins.
        required_columns=("product_id", "adu", "dlt"),
        optional_columns=(
            "ltf", "vf", "moq", "order_cycle_days", "on_hand", "on_order",
            "qualified_demand", "supplier",
        ),
        options=tool_options.ddmrp_options,
        prepare=_ddmrp_prepare,
        run=_ddmrp_run,
        qa=lambda report: ddmrp_job.verify(report),
        deliver=lambda report, out_dir, client: ddmrp_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            ddmrp_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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


def _import_case_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=[
            "an extracted-document folder or documents.json is required "
            "(commercial_invoice / packing_list / bill_of_lading JSON slices)",
        ])
    try:
        payload = import_case_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError, OSError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=payload)


def _import_case_run(payload: object, params: dict) -> Produced:
    report = import_case_job.run(payload, params)
    return Produced(report=report, summary=report.summary)


def import_case_tool() -> Tool:
    return Tool(
        key="import_case",
        title="Import Case / Document Pack",
        description="Unify extracted logistics documents (invoice, packing list, B/L or AWB) "
                    "into one shipment case, flag conflicts, and emit a Chile DIN field pack "
                    "for the licensed broker. Does not file a declaration or advise HS legally.",
        intent_keywords=(
            "import case", "expediente de importacion", "unify import documents",
            "unificar documentos de importacion", "document pack chile",
            "chile din pack", "cruzar factura packing", "packing vs invoice",
            "shipping document discrepancies", "discrepancias de documentos",
            "import document pack", "pack documental de importacion",
            "rellenar din", "campo din chile",
        ),
        requires_data=True,
        options=tool_options.import_case_options,
        prepare=_import_case_prepare,
        run=_import_case_run,
        qa=lambda report: import_case_job.verify(report),
        deliver=lambda report, out_dir, client: import_case_job.write_operational(
            report, out_dir, client,
        ),
        deck=lambda report, out_dir, client, citations, confidence, options, lang="en", readings=(): replace(
            import_case_job.build_deck(
                report, client=client, citations=tuple(citations),
                confidence=confidence, lang=lang,
            ),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
    )


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
        # jobs/landed_cost_job.py:63. That module spells them `sku` and `qty`; declared
        # under the shared canonical, which its own alias tuples already accept.
        required_columns=("product_id", "unit_cost", "quantity"),
        optional_columns=(
            "freight", "insurance", "duty_rate", "handling", "broker_fee", "incoterm",
        ),
        options=tool_options.landed_cost_options,
        prepare=_landed_cost_prepare,
        run=_landed_cost_run,
        qa=lambda report: landed_cost_job.verify(report),
        deliver=lambda report, out_dir, client: landed_cost_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, lang="en", readings=(): replace(
            landed_cost_job.build_deck(report, client=client, citations=tuple(citations),
                                       confidence=confidence, lang=lang),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/whatif_job.py:106. Note the `driver` column is not free text: its values
        # must name model inputs the job knows (annual_demand, holding_cost,
        # fixed_order_cost, demand_std, service_level, lead_time) or it refuses with
        # "unknown driver(s)". `required_columns` cannot express a value contract, only
        # a column one, so the scan can say the column is there and not that it is right.
        required_columns=("driver", "low", "high"),
        optional_columns=("base", "unit"),
        options=tool_options.whatif_options,
        prepare=_whatif_prepare,
        run=_whatif_run,
        qa=lambda report: whatif_job.verify(report),
        deliver=lambda report, out_dir, client: whatif_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            whatif_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # No default: DSO/DPO come from the AR/AP ledgers, not from inventory data.
        # A 0.0 stand-in made cash-to-cash == DIO and printed it as a measured cycle.
        payload, dso=params.get("dso"), dpo=params.get("dpo"),
    )
    # Any of these is None when this data could not support it (see src/financial_kpis).
    # verify() only gates `turns`, so the others still have to be printable as absent.
    return Produced(report=report, summary=(
        f"Inventory finance for {report.n_skus} SKU(s): "
        f"{financial_kpis_job.fmt(report.turns, '.1f')} turns, "
        f"GMROI {financial_kpis_job.fmt(report.gmroi, '.2f')}, "
        f"{financial_kpis_job.fmt(report.dio, '.0f')}-day DIO."
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
        required_columns=("product_id", "cogs", "avg_inventory_value"),
        optional_columns=("gross_margin", "units_sold", "units_on_hand", "net_sales"),
        options=tool_options.financial_kpis_options,
        prepare=_financial_kpis_prepare,
        run=_financial_kpis_run,
        qa=lambda report: financial_kpis_job.verify(report),
        deliver=lambda report, out_dir, client: financial_kpis_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, lang="en", readings=(): replace(
            financial_kpis_job.build_deck(report, client=client, citations=tuple(citations),
                                          confidence=confidence, lang=lang),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/reconciliation_job.py:118 -- the whole point of the tool is the gap
        # between what the system says and what was counted, so both sides are hard.
        required_columns=("product_id", "system_qty", "physical_qty"),
        optional_columns=("unit_cost",),
        options=tool_options.reconciliation_options,
        prepare=_reconciliation_prepare,
        run=_reconciliation_run,
        qa=lambda report: reconciliation_job.verify(report),
        deliver=lambda report, out_dir, client: reconciliation_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            reconciliation_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/returns_job.py:89. Declared as `returned_units`, not the shared
        # `quantity`: its _UNITS_COLS accepts both, but a returns file's unit count is
        # not the sales quantity, and collapsing them would let a plain sales export
        # read as a returns analysis.
        required_columns=("product_id", "returned_units", "unit_cost"),
        optional_columns=("reason", "resale_value", "sellable"),
        prepare=_returns_prepare,
        run=_returns_run,
        qa=lambda report: returns_job.verify(report),
        deliver=lambda report, out_dir, client: returns_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            returns_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/queuing_job.py:59. `station` and not its _STATION_COLS' `name`/`node`:
        # `name` already means product name, and `node` was left unclaimed when two
        # network tools wanted it.
        required_columns=("station", "arrival_rate", "service_rate"),
        optional_columns=("wait_cost", "server_cost"),
        options=tool_options.queuing_options,
        prepare=_queuing_prepare,
        run=_queuing_run,
        qa=lambda report: queuing_job.verify(report),
        deliver=lambda report, out_dir, client: queuing_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            queuing_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/scheduling_job.py:49. `due_date` is a NUMBER of days, not a date: the job
        # floats it, and a real date string makes the whole file refuse.
        required_columns=("job", "processing_time"),
        optional_columns=("due_date",),
        options=tool_options.scheduling_options,
        prepare=_scheduling_prepare,
        run=_scheduling_run,
        qa=lambda report: scheduling_job.verify(report),
        deliver=lambda report, out_dir, client: scheduling_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            scheduling_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
    )


# ---- launch_readiness (campaign launch date vs lead time & coverage) ---------

def _launch_readiness_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data",
                        messages=["a campaign CSV (product_id, launch_date) is required"])
    if not request.params.get("inventory_path"):
        return Prepared(status="needs_data",
                        messages=["params['inventory_path'] (the inventory/lead-time CSV) is required"])
    try:
        payload = launch_readiness_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=payload)


def _launch_readiness_run(payload: object, params: dict) -> Produced:
    report = launch_readiness_job.run(payload)
    return Produced(report=report, summary=report.summary)


def launch_readiness_tool() -> Tool:
    return Tool(
        key="launch_readiness",
        title="Launch Readiness",
        description="Cross a campaign launch-date list against real supplier lead time and "
                    "campaign-shaped stock coverage, returning a green/yellow/red readiness verdict "
                    "per SKU with ranked actions - a report a human forwards; no marketing-tool "
                    "integration.",
        intent_keywords=(
            "launch readiness", "campaign launch date", "marketing launch check",
            "will the sku be in stock for launch", "product ready for launch",
            "campaign stock coverage", "launch date lead time",
        ),
        requires_data=True,
        prepare=_launch_readiness_prepare,
        run=_launch_readiness_run,
        qa=lambda report: launch_readiness_job.verify(report),
        deliver=lambda report, out_dir, client: launch_readiness_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            launch_readiness_job.build_deck(report, client=client, citations=tuple(citations),
                                            confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
        # The verdict IS a set of ranked, executable choices -> surface them as the guided outcome.
        options=tool_options.launch_readiness_options,
    )


# ---- hats: N4 tension + N5 settlement (two tools, ONE shared job - D6) -------

def _hats_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    """Shared by both hat tools. Param problems (weights / wacc / sl_target) are
    needs_clarification; missing or unusable data is needs_data."""
    try:
        hats_job.config_from_params(request.params)
        hats_job.parse_request_weights(request.params)
    except ValueError as exc:
        return Prepared(status="needs_clarification", messages=[str(exc)])
    if not request.data_path:
        return Prepared(status="needs_data", messages=[
            "a weekly demand CSV (product_id, quantity, unit_cost, lead_time_days) is required"])
    try:
        payload = hats_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=payload)


def _hat_tension_run(payload: object, params: dict) -> Produced:
    report = hats_job.run_tension(payload)
    return Produced(report=report, summary=report.summary)


def _hat_settlement_run(payload: object, params: dict) -> Produced:
    report = hats_job.run_settlement(payload)
    return Produced(report=report, summary=report.summary)


def hat_tension_tool() -> Tool:
    return Tool(
        key="hat_tension",
        title="Decision Tension Map (Replenishment)",
        description="Score one replenishment decision (order quantity + service level) through "
                    "5 role hats (comprador / planner / cfo / comercial / transporte) and "
                    "render the disagreement as ranked options with the $ gaps. transporte "
                    "only joins when the file carries weight_kg AND a distance_km param is "
                    "given; otherwise it is named as off the table, never scored at zero. "
                    "The human resolves - nothing is reconciled or executed here.",
        intent_keywords=(
            "mapa de tension", "tension entre areas", "conflicto compras finanzas",
            "trade-off entre roles", "perspectivas de la decision", "decision tension map",
            "cuanto pedir segun cada area",
            # Multi-word on purpose: "transporte" alone would steal routing from the
            # `transportation` tool, which answers a different question (which MODE).
            "desacuerdo con transporte", "transporte vs finanzas",
            "cada area quiere otra cantidad",
        ),
        requires_data=True,
        # jobs/hats_job.py:123. Both hats share `_hats_prepare`, so both declare the
        # same contract. Note MIN_OBS = 5: a SKU with fewer weekly rows is skipped, and
        # a file where every SKU is skipped refuses -- the column contract is met and
        # the tool still cannot run. `required_columns` says nothing about row counts.
        required_columns=("product_id", "quantity", "unit_cost", "lead_time_days"),
        # weight_kg is the per-SKU half of the transporte hat; the lane half
        # (distance_km) is a param, not a column. Optional on purpose: without it
        # the council runs with four areas and declares the fifth as unmeasured.
        # moq / order_multiple are the supplier's terms: with them the grid holds
        # only lots the supplier accepts (jobs/hats_job.py module docstring).
        optional_columns=("weight_kg", "moq", "order_multiple"),
        prepare=_hats_prepare,
        run=_hat_tension_run,
        qa=lambda report: hats_job.verify_tension(report),
        deliver=lambda report, out_dir, client: hats_job.write_tension(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            hats_job.build_tension_deck(report, client=client, citations=tuple(citations),
                                        confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
        # The job already assembled the protected OPTIONS outcome (never EXECUTED).
        options=lambda report: report.outcome,
    )


def hat_settlement_tool() -> Tool:
    return Tool(
        key="hat_settlement",
        title="Reconciled Replenishment Plan",
        description="Reconcile the role hats into ONE replenishment plan (Q*, SL*) via the "
                    "operator's explicit weight policy, with a concession record (acta) and the "
                    "signed $ value vs today's baseline. Ends in a human HANDOFF - never applies "
                    "anything.",
        intent_keywords=(
            "plan reconciliado", "reconciliar la decision", "acta de concesiones",
            "decision unica ponderada", "reconciled order plan", "consenso ponderado de compra",
        ),
        requires_data=True,
        # Same contract as hat_tension by construction: one shared `_hats_prepare`.
        required_columns=("product_id", "quantity", "unit_cost", "lead_time_days"),
        # Same file, same prepare, same optional halves as hat_tension above.
        optional_columns=("weight_kg", "moq", "order_multiple"),
        prepare=_hats_prepare,
        run=_hat_settlement_run,
        qa=lambda report: hats_job.verify_settlement(report),
        deliver=lambda report, out_dir, client: hats_job.write_settlement(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            hats_job.build_settlement_deck(report, client=client, citations=tuple(citations),
                                           confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
        # The job already assembled the protected HANDOFF outcome (never EXECUTED).
        options=lambda report: report.outcome,
    )


# ---- cross_impact (cross-area impact of a decision already proposed) --------

def _cross_impact_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    """Param problems are needs_clarification; missing/unusable data is needs_data.

    The split matters: a missing 'current' is the OPERATOR's answer to give, not a
    data-file problem - and it is never defaulted to baseline_plan().
    """
    if not request.data_path:
        return Prepared(status="needs_data", messages=[
            "a weekly demand CSV (product_id, quantity, unit_cost, lead_time_days) is required"])
    try:
        payload = cross_impact_job.prepare(request.data_path, request.params)
    except FileNotFoundError as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    except ValueError as exc:
        return Prepared(status="needs_clarification", messages=[str(exc)])
    return Prepared(status="ok", payload=payload)


def _cross_impact_run(payload: object, params: dict) -> Produced:
    report = cross_impact_job.run(payload)
    return Produced(report=report, summary=report.summary)


def cross_impact_tool() -> Tool:
    return Tool(
        key="cross_impact",
        title="Cross-Area Impact of a Proposed Decision",
        description="Take a replenishment decision ALREADY proposed by one area "
                    "(order quantity + service level) and measure what it does to the "
                    "others, then judge whether each would object against the "
                    "threshold the operator declared. Zero objections means the "
                    "meeting does not need to happen. An area nobody could measure "
                    "(transporte without a unit weight and a lane distance) makes the "
                    "verdict 'indeterminado', never a clean zero. Nothing is executed.",
        intent_keywords=(
            "impacto cruzado", "impacto en las otras areas", "en las otras areas",
            "quien objeta", "costo de coordinacion", "cross-area impact",
            "objection threshold", "si cambio esto que pasa", "necesito convocar",
            "transporte objeta", "impacto en transporte", "que dice transporte",
        ),
        requires_data=True,
        prepare=_cross_impact_prepare,
        run=_cross_impact_run,
        qa=lambda report: cross_impact_job.verify(report),
        deliver=lambda report, out_dir, client: cross_impact_job.write(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            cross_impact_job.build_deck(report, client=client, citations=tuple(citations),
                                        confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
        options=lambda report: report.outcome,
        required_client_params=("objection_thresholds",),
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
        # jobs/risk_job.py:184. `risk_name`, not the module's first alias `name`: a
        # canonical called `name` would go green on any product master, which is the
        # false green this whole contract exists to prevent.
        required_columns=("risk_name", "likelihood", "impact_value"),
        optional_columns=(
            "risk_category", "exposure", "velocity_days", "detectability_days",
            "time_to_recover",
        ),
        prepare=_risk_prepare,
        run=_risk_run,
        qa=lambda report: risk_job.verify(report),
        deliver=lambda report, out_dir, client: risk_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            risk_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
        # The mitigation decision IS a set of ranked, executable choices -> surface them as
        # the guided OPTIONS outcome on success (recommended default flagged).
        options=lambda report: report.outcome,
    )


# ---- disruption_scan (GDELT supplier disruption exposure screen) -------------

def _disruption_scan_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(
            status="needs_data",
            messages=["a supplier-list CSV (supplier, optional country / annual_spend) is required"],
        )
    try:
        rows = disruption_scan_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not rows:
        return Prepared(status="needs_data", messages=["no suppliers found in the data"])
    return Prepared(status="ok", payload=rows)


def _disruption_scan_run(payload: object, params: dict) -> Produced:
    # fetcher/timespan are injectable seams: tests and the offline demo pass a
    # canned fetcher; production leaves fetcher=None so the throttled stdlib GET runs.
    report = disruption_scan_job.run(
        payload,
        fetcher=params.get("fetcher"),
        timespan=params.get("timespan", "3m"),
    )
    return Produced(report=report, summary=report.summary)


def disruption_scan_tool() -> Tool:
    return Tool(
        key="disruption_scan",
        title="Supplier Disruption Exposure Scan",
        description="Screen a supplier list against live GDELT disruption news (strikes, disasters, "
                    "port/logistics incidents, bankruptcies, shortages), score each supplier's "
                    "exposure, and route the signal through the risk engine into a ranked "
                    "'investigate first' plan. Read-only; needs only a supplier list, no client "
                    "system access. The signal is a screen, not a calibrated probability.",
        intent_keywords=(
            "supplier disruption", "disruption exposure", "disruption scan", "supplier risk scan",
            "disruption news", "supplier news monitoring", "supply chain disruption news",
            "supplier exposure scan", "escaneo de disrupcion", "monitoreo de proveedores",
            "exposicion a disrupcion", "disrupcion de proveedores",
        ),
        requires_data=True,
        # jobs/disruption_scan_job.py:85 -- a list of supplier names is genuinely
        # enough to run this: country and spend only sharpen the ranking. A single
        # required column would be a false-green risk if the name meant something
        # else, but `supplier` means exactly one thing across this registry.
        required_columns=("supplier",),
        optional_columns=("country", "annual_value"),
        prepare=_disruption_scan_prepare,
        run=_disruption_scan_run,
        qa=lambda report: disruption_scan_job.verify(report),
        deliver=lambda report, out_dir, client: disruption_scan_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            disruption_scan_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
        # The scan's recommendation IS a set of ranked, executable "investigate X" choices
        # (or an EXECUTED all-clear / HANDOFF retry) -> surface it as the guided outcome.
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
        # Cycle memory is opt-in and named: params['state_client_id'] selects
        # one client's partition. NOT request.client -- that is a caller-typed
        # display label this surface already refuses to trust as an identity
        # (webapp/app.py::_get_orchestrator). See forecast_cycle.store_from_params.
        store=forecast_cycle.store_from_params(params),
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
        # jobs/forecast_job.py:128 -- product and quantity are what it refuses over.
        # The period column is optional: with no date the series falls back to row
        # order, which still forecasts.
        required_columns=("product_id", "quantity"),
        optional_columns=("date",),
        prepare=_forecast_prepare,
        run=_forecast_run,
        qa=lambda report: forecast_job.verify(report),
        deliver=lambda report, out_dir, client: forecast_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            forecast_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        required_columns=("product_id",),
        optional_columns=("name", "gtin", "unit_cost"),
        prepare=_data_quality_prepare,
        run=_data_quality_run,
        qa=lambda report: data_quality_job.verify(report),
        deliver=lambda report, out_dir, client: data_quality_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            data_quality_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
        # The remediation decision IS a set of ranked, executable choices -> surface them as
        # the guided OPTIONS outcome on success (recommended default flagged).
        options=lambda report: report.outcome,
    )


# ---- flow_map (observed network + measured transit per lane) ------------------

def _flow_map_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=[
            "a movements CSV/Excel is required: one row per shipment leg with a departure "
            "date, an arrival date, an origin, a destination, a product and a quantity"
        ])
    try:
        movements = flow_map_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=movements)


def _flow_map_run(payload: object, params: dict) -> Produced:
    report = flow_map_job.run(payload, params)
    return Produced(report=report, summary=report.summary)


def flow_map_tool() -> Tool:
    return Tool(
        key="flow_map",
        title="Flow Map (Observed Network & Measured Transit)",
        description="Reconstruct the supplier-to-store network from recorded movements, "
                    "measure real transit time and its spread on every lane, and rank the "
                    "lanes whose variability locks up the most protective inventory. Lanes "
                    "with too few movements report no number at all.",
        intent_keywords=(
            "flow map", "map my flows", "end to end flow", "value stream", "network map",
            "map the network", "transit time", "transit variability",
            "lead time by lane", "sankey", "material flow", "where is the bottleneck",
        ),
        requires_data=True,
        # jobs/flow_map_job.py's _REQUIRED, restated in the shared vocabulary. That
        # module keeps its canonical names in Spanish (fecha_salida, origen, producto,
        # cantidad...) and its own alias table already accepts the English and the
        # product_id/quantity spellings, so declaring `producto` and `cantidad` here
        # would have created two rival canonicals for columns the rest of the registry
        # already owns -- a movements file would then feed flow_map and nothing else.
        required_columns=(
            "departure_date", "arrival_date", "origin", "destination",
            "product_id", "quantity",
        ),
        optional_columns=("unit_cost",),
        prepare=_flow_map_prepare,
        run=_flow_map_run,
        qa=lambda report: flow_map_job.verify(report),
        deliver=lambda report, out_dir, client: flow_map_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            flow_map_job.build_deck(
                report, client=client, citations=tuple(citations), confidence=confidence
            ),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
        options=lambda report: report.outcome,
    )


# ---- org_friction (organizational-friction audit: hidden factory, ratchet, ------
# ---- accountability vacuum, unclassified workarounds) ---------------------------

def _org_friction_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=[
            "a control points CSV/Excel is required: one row per KPI/checkpoint, with "
            "whichever of official_value, shadow_value, owner, workaround_present and "
            "workaround_classification you already track (an optional second file of "
            "target-vs-actual history can be passed via params['history_path'])"
        ])
    try:
        control_points = org_friction_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=control_points)


def _org_friction_run(payload: object, params: dict) -> Produced:
    report = org_friction_job.run(payload, params)
    return Produced(report=report, summary=report.summary)


def org_friction_tool() -> Tool:
    return Tool(
        key="org_friction_audit",
        title="Organizational Friction Audit (Hidden Factory & Ratchet Detector)",
        description="Audit a client's own control points (KPIs, data-load checkpoints) for "
                    "the political layer no data-quality tool sees: numbers that disagree "
                    "across channels, targets ratcheted up right after being met, control "
                    "points nobody owns, and informal workarounds nobody has classified. "
                    "Never renders a verdict on a person -- every finding is a ranked "
                    "question for a human to go confirm.",
        intent_keywords=(
            "organizational friction", "hidden factory", "goodhart", "ratchet effect",
            "gaming the metric", "shadow data", "accountability vacuum", "unclassified workaround",
            "friction audit", "metrics being gamed", "kpi being managed", "political layer",
        ),
        requires_data=True,
        prepare=_org_friction_prepare,
        run=_org_friction_run,
        qa=lambda report: org_friction_job.verify(report),
        deliver=lambda report, out_dir, client: org_friction_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            org_friction_job.build_deck(
                report, client=client, citations=tuple(citations), confidence=confidence
            ),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
        # Every finding is a triage choice, never an auto-verdict -> surface the ranked
        # OPTIONS outcome on success (or the HANDOFF asking for more evidence).
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
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            dea_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/acceptance_sampling_job.py:54. Declaring this contract is what surfaced
        # that its _PART_COLS did not accept `product_id` at all -- fixed in the same
        # change, with its own regression test.
        required_columns=("product_id", "aql", "ltpd"),
        options=tool_options.acceptance_sampling_options,
        prepare=_acceptance_sampling_prepare,
        run=_acceptance_sampling_run,
        qa=lambda report: acceptance_sampling_job.verify(report),
        deliver=lambda report, out_dir, client: acceptance_sampling_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            acceptance_sampling_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/earned_value_job.py:54. Declared as planned_value/earned_value/actual_cost
        # rather than that module's bare `planned`/`earned`/`actual`: those single words
        # mean nothing on their own in a client's folder, and all three long spellings
        # are inside its own alias tuples.
        required_columns=("task", "planned_value", "earned_value", "actual_cost"),
        options=tool_options.earned_value_options,
        prepare=_earned_value_prepare,
        run=_earned_value_run,
        qa=lambda report: earned_value_job.verify(report),
        deliver=lambda report, out_dir, client: earned_value_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            earned_value_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/learning_curve_job.py:56. Declaring this contract is what surfaced that
        # its _PRODUCT_COLS did not accept `product_id` -- the second tool with that
        # gap, fixed in the same change.
        required_columns=(
            "product_id", "first_unit_cost", "learning_rate", "planned_volume",
        ),
        options=tool_options.learning_curve_options,
        prepare=_learning_curve_prepare,
        run=_learning_curve_run,
        qa=lambda report: learning_curve_job.verify(report),
        deliver=lambda report, out_dir, client: learning_curve_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            learning_curve_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
    report, summary = _record_state(
        report, report.summary, params,
        tool="odoo_replenishment",
        lines=state_bridge.stock_lines_from_replenishment(report.lines),
        n_candidates=len(report.lines),
        demand_points=report.demand_points,
        # El tercer insumo del Tower, y el unico que cuesta una lectura aparte:
        # `receipts()` recorre stock.move, asi que se pide a proposito. Sin el,
        # `lead_time_drift` no puede disparar nunca en produccion -- y con el plazo
        # DECLARADO tampoco, porque una media sobre un campo fijo no deriva.
        lead_times_by_sku=(
            odoo_job.read_measured_lead_times(payload["connector"])[0]
            if params.get("state_record_lead_times") else None
        ),
    )
    return Produced(report=report, summary=summary)


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
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            odoo_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
    )


# ---- channel_replenishment (storefront demand -> ERP reorder point) ----------

def _channel_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    try:
        payload = channel_replenishment_job.prepare(request.data_path, request.params)
    except (ValueError, OdooError, ShopifyCommerceError, ShopifyInventoryError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["matched"]:
        return Prepared(
            status="needs_data",
            messages=["no SKU is present in both the storefront and the ERP: the catalogues do not "
                      "intersect, so the SKU codes probably do not agree between the two systems"],
        )
    return Prepared(status="ok", payload=payload)


def _channel_run(payload: object, params: dict) -> Produced:
    report = channel_replenishment_job.run(
        payload,
        cover_periods=params.get("cover_periods", 8.0),
        financial_threshold=params.get("financial_threshold", 50_000.0),
    )
    report, summary = _record_state(
        report, report.summary, params,
        tool="channel_replenishment",
        lines=state_bridge.stock_lines_from_channel(report),
        n_candidates=len(report.lines),
        demand_points=report.demand_points,
    )
    return Produced(report=report, summary=summary)


def channel_replenishment_tool() -> Tool:
    return Tool(
        key="channel_replenishment",
        title="Channel to ERP Replenishment",
        description="Forecast each SKU from a live sales channel's real orders (Shopify or "
                    "MercadoLibre, via params['channel']), compare against the ERP's stock and "
                    "lead times, and stage the restock as a reversible reorder point in the ERP "
                    "(Odoo or Unleashed, via params['erp']). Also reports where the two systems "
                    "disagree about on-hand - a divergence neither single-system tool can see.",
        intent_keywords=(
            "channel replenishment", "shopify to odoo", "storefront to erp", "omnichannel inventory",
            "cross system replenishment", "channel demand erp stock", "stock divergence",
            "reconcile shopify odoo", "dtc replenishment erp",
            # MercadoLibre routes here rather than to a tool of its own: like a storefront, its
            # writable quantity is physical stock, so the plan belongs in the ERP either way.
            # A separate meli_replenishment would be this job with different labels.
            "mercadolibre", "mercado libre", "meli to odoo", "mercadolibre replenishment",
            "reponer mercadolibre", "marketplace replenishment",
            # The ERP side is a parameter too. Unleashed qualifies because it holds a
            # reorder point (MinStockAlertLevel); Xero does not and is therefore absent.
            "unleashed", "shopify to unleashed", "unleashed replenishment", "unleashed reorder point",
        ),
        requires_data=False,
        options=lambda report: report.outcome,
        prepare=_channel_prepare,
        run=_channel_run,
        qa=lambda report: channel_replenishment_job.verify(report),
        deliver=lambda report, out_dir, client: channel_replenishment_job.write_operational(
            report, out_dir, client
        ),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            channel_replenishment_job.build_deck(
                report, client=client, citations=tuple(citations), confidence=confidence
            ),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
    )


# ---- shopify_replenishment (live storefront connector) -----------------------

def _shopify_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    try:
        payload = shopify_job.prepare(request.data_path, request.params)
    except (ValueError, ShopifyCommerceError, ShopifyInventoryError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if payload["n_products"] == 0:
        return Prepared(status="needs_data",
                        messages=["no product variants with a SKU found in Shopify"])
    return Prepared(status="ok", payload=payload)


def _shopify_run(payload: object, params: dict) -> Produced:
    report = shopify_job.run(
        payload,
        cover_periods=params.get("cover_periods", 8.0),
        financial_threshold=params.get("financial_threshold", 50_000.0),
    )
    report, summary = _record_state(
        report, report.summary, params,
        tool="shopify_replenishment",
        lines=state_bridge.stock_lines_from_replenishment(report.lines),
        n_candidates=len(report.lines),
        demand_points=report.demand_points,
    )
    return Produced(report=report, summary=summary)


def shopify_replenishment_tool() -> Tool:
    return Tool(
        key="shopify_replenishment",
        title="Shopify Replenishment (live storefront)",
        description="Connect to a live Shopify storefront (or an offline stand-in), read catalog / "
                    "on-hand / orders, forecast each SKU and recommend replenishment. Shopify raises "
                    "no purchase order, so the plan is handed off for purchasing; the only write "
                    "offered is reconciling on-hand against a physical count, staged reversibly.",
        intent_keywords=(
            "shopify", "shopify inventory", "shopify replenishment", "connect shopify",
            "connect to shopify", "sync shopify", "from shopify", "pull from shopify",
            "storefront connector", "dtc inventory", "ecommerce replenishment",
        ),
        requires_data=False,
        options=lambda report: report.outcome,
        prepare=_shopify_prepare,
        run=_shopify_run,
        qa=lambda report: shopify_job.verify(report),
        deliver=lambda report, out_dir, client: shopify_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            shopify_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        optimized_targets=params.get("optimized_targets"),
        prefer_optimized_policy=params.get("prefer_optimized_policy", True),
    )
    # La planilla es la unica de las cinco fuentes que puede quedarse sin escribir por
    # diseno: en modo punto-de-reorden no tiene columna de demanda, y el dominio exige
    # una tasa diaria. Las filas crudas traen esa columna cuando existe.
    report, summary = _record_state(
        report, report.summary, params,
        tool="excel_replenishment",
        lines=state_bridge.stock_lines_from_excel(report, payload["rows"]),
        n_candidates=len(report.lines),
    )
    return Produced(report=report, summary=summary)


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
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            excel_replenishment_job.build_deck(report, client=client, citations=tuple(citations),
                                               confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/newsvendor_job.py:76 -- product, mean demand, price and unit cost are
        # what it refuses over. `price` and `unit_cost` are separate concepts here and
        # their alias tuples do not overlap, so declaring both is safe.
        required_columns=("product_id", "mean_demand", "price", "unit_cost"),
        optional_columns=("std_demand", "salvage_value", "goodwill"),
        options=tool_options.newsvendor_options,
        prepare=_newsvendor_prepare,
        run=_newsvendor_run,
        qa=lambda report: newsvendor_job.verify(report),
        deliver=lambda report, out_dir, client: newsvendor_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            newsvendor_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            cycle_count_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/multi_echelon_job.py:81. Per-stage, not per-SKU: the key is the echelon.
        # Its _STAGE_COLS also accepts `name`, which is deliberately NOT declared --
        # `name` already means product name everywhere else, and declaring it would
        # turn any master file into a false green for a network model.
        # `mean_demand`/`std_demand` are REQUIRED, not optional: without params the job
        # refuses with "end-customer demand is required". They shipped as optional and
        # the required-only guard could not see it -- the complete fixture carries every
        # optional too. That is what the optional-is-really-optional check now closes.
        required_columns=(
            "stage", "lead_time_days", "holding_cost", "mean_demand", "std_demand",
        ),
        options=tool_options.multi_echelon_options,
        prepare=_multi_echelon_prepare,
        run=_multi_echelon_run,
        qa=lambda report: multi_echelon_job.verify(report),
        deliver=lambda report, out_dir, client: multi_echelon_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            multi_echelon_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/transportation_job.py:81 -- weight and distance are the whole cost model.
        # No product column at all: the unit here is a shipment, not a SKU.
        required_columns=("weight_kg", "distance_km"),
        optional_columns=("shipment_id", "lane", "quantity", "order_value"),
        options=tool_options.transportation_options,
        prepare=_transportation_prepare,
        run=_transportation_run,
        qa=lambda report: transportation_job.verify(report),
        deliver=lambda report, out_dir, client: transportation_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            transportation_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/fefo_job.py:156. `expiry_date` is deliberately NOT declared even as
        # optional: jobs/fefo_job.py:166 raises when it is present without
        # params.as_of, so declaring it would make the complete fixture refuse and
        # read as a broken declaration. Dates come in through params, not the contract.
        # `days_to_expiry` is REQUIRED, not optional: the job takes it OR `expiry_date`,
        # and `expiry_date` alone raises without params.as_of, so under the default
        # params intake_scan speaks for there is exactly one workable path.
        required_columns=("product_id", "lot_id", "quantity", "days_to_expiry"),
        optional_columns=("unit_cost", "daily_demand"),
        options=tool_options.fefo_options,
        prepare=_fefo_prepare,
        run=_fefo_run,
        qa=lambda report: fefo_job.verify(report),
        deliver=lambda report, out_dir, client: fefo_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            fefo_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/slotting_job.py:61 -- affinity is mined from order-product pairs, so the
        # basket id is as required as the product. `order_id` is its own canonical even
        # though jobs/intake.py lists that spelling among po_id's aliases: a picking
        # basket is not a purchase order, and exact names resolve before aliases.
        required_columns=("order_id", "product_id"),
        optional_columns=("unit_volume",),
        options=tool_options.slotting_options,
        prepare=_slotting_prepare,
        run=_slotting_run,
        qa=lambda report: slotting_job.verify(report),
        deliver=lambda report, out_dir, client: slotting_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            slotting_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/simulation_job.py:77. The lead time is declared as `lead_time`, not the
        # repo-wide `lead_time_days`, because that name is not in this job's _LEAD_COLS
        # -- declaring it would make the tool refuse a file it can actually read.
        required_columns=("product_id", "mean_demand", "std_demand", "lead_time_periods"),
        optional_columns=("holding_cost", "order_cost", "backorder_cost", "review_period"),
        options=tool_options.simulation_options,
        prepare=_simulation_prepare,
        run=_simulation_run,
        qa=lambda report: simulation_job.verify(report),
        deliver=lambda report, out_dir, client: simulation_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            simulation_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            digital_twin_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        required_columns=("product_id", "on_hand", "daily_demand"),
        optional_columns=("unit_cost", "days_since_last_sale"),
        options=tool_options.excess_obsolete_options,
        prepare=_excess_obsolete_prepare,
        run=_excess_obsolete_run,
        qa=lambda report: excess_obsolete_job.verify(report),
        deliver=lambda report, out_dir, client: excess_obsolete_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            excess_obsolete_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # Identical to excess_obsolete's by construction: this job imports that one's
        # prepare_records as its stock reader (jobs/markdown_liquidation_job.py:29). If
        # the two ever diverge, this declaration is what should break first.
        required_columns=("product_id", "on_hand", "daily_demand"),
        optional_columns=("unit_cost", "days_since_last_sale"),
        options=tool_options.markdown_liquidation_options,
        prepare=_markdown_liquidation_prepare,
        run=_markdown_liquidation_run,
        qa=lambda report: markdown_liquidation_job.verify(report),
        deliver=lambda report, out_dir, client: markdown_liquidation_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            markdown_liquidation_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/facility_location_job.py:61 -- only the coordinates are hard. Declared
        # as longitude/latitude rather than the job's own `x`/`y`: both spellings are
        # in its alias tuples, and a one-letter column name is not something to ask a
        # client for. `weight` is NOT declared even though it is a real optional --
        # transportation's `weight_kg` already accepts a header called `weight`, and
        # two canonicals wanting the same header is decided by iteration order, which
        # is no way to decide it.
        required_columns=("longitude", "latitude"),
        options=tool_options.facility_location_options,
        prepare=_facility_location_prepare,
        run=_facility_location_run,
        qa=lambda report: facility_location_job.verify(report),
        deliver=lambda report, out_dir, client: facility_location_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            facility_location_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
    )


# ---- network_design (multi-facility p-median MILP) ---------------------------

def _network_design_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(
            status="needs_data",
            messages=["a network-nodes CSV (x, y + optional name/weight/role/fixed_cost/capacity) "
                      "and a facility count (params.p) are required"],
        )
    try:
        payload = network_design_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["demands"]:
        return Prepared(status="needs_data", messages=["no demand points found in the data"])
    if not payload["sites"]:
        return Prepared(status="needs_data", messages=["no candidate sites found in the data"])
    return Prepared(status="ok", payload=payload)


def _network_design_run(payload: object, params: dict) -> Produced:
    report = network_design_job.run(payload)
    return Produced(report=report, summary=report.summary)


def network_design_tool() -> Tool:
    return Tool(
        key="network_design",
        title="Network Design (Multi-Facility p-Median)",
        description="Choose which p of several candidate sites to open and assign each demand point "
                    "to one, minimizing total weighted travel (with optional fixed costs and "
                    "capacities), via an exact MILP - the multi-facility counterpart to single-site "
                    "facility location. Offline, straight-line distance.",
        intent_keywords=(
            "p-median", "p median", "multi-facility", "multiple facilities",
            "how many warehouses", "how many distribution centers", "how many dcs",
            "number of dcs", "which sites to open", "network optimization",
            "open facilities", "consolidate distribution centers",
        ),
        requires_data=True,
        # jobs/network_design_job.py:100. Same coordinate contract as
        # facility_location; `role` splits the rows into demand points and candidate
        # sites, and with no role column every row is both, which is why it is optional.
        required_columns=("longitude", "latitude"),
        optional_columns=("role", "fixed_cost", "capacity"),
        options=tool_options.network_design_options,
        prepare=_network_design_prepare,
        run=_network_design_run,
        qa=lambda report: network_design_job.verify(report),
        deliver=lambda report, out_dir, client: network_design_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            network_design_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/drp_job.py:64. Declared as `quantity`, not the job's own first alias
        # `demand`: both are in its _DEMAND_COLS, and `quantity` is already the shared
        # canonical, so the client is asked for one name instead of two.
        required_columns=("branch", "period", "quantity"),
        # No lead-time column is declared here on purpose. This job spells it
        # `lead_time` and accepts no other spelling, and a canonical by that name
        # captures the generic header "Lead Time" ahead of `lead_time_days`, which is
        # what almost every other tool in the registry needs. Optional columns are not
        # worth breaking the shared vocabulary for.
        optional_columns=("on_hand", "safety_stock", "lot_size"),
        options=tool_options.drp_options,
        prepare=_drp_prepare,
        run=_drp_run,
        qa=lambda report: drp_job.verify(report),
        deliver=lambda report, out_dir, client: drp_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            drp_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            vehicle_routing_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
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

    def _deck(report, out_dir, client, citations, confidence, options, readings=()):
        # citations here are the orchestrator's ungated `ground_citations` (the
        # same convention every other tool's deck uses) -- but golden rule 7 +
        # the E5 gate are explicit requirements for THIS deliverable, so the deck
        # recomputes its own E5-gated citations via
        # `price_intelligence_job.gated_citations` (filter_citations, not a
        # parallel mechanism) instead of trusting the ungated list passed in.
        #
        # `readings` is threaded like everywhere else and is NOT affected by that
        # recomputation: a book passage is not a citation candidate at all (no
        # `node_id`), so it never enters the E5 gate in either direction. It rides
        # in its own `Deliverable.readings` section, which `write_report_md` renders
        # because it starts from `deliverable.to_markdown()`.
        deliverable = replace(
            price_intelligence_job.build_deck(
                report, client=client, citations=price_intelligence_job.gated_citations(""),
                confidence=confidence,
            ),
            options=tuple(options), readings=tuple(readings),
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
        # jobs/price_intelligence.py:162. The refs file is a SKU-to-competitor-URL map,
        # so the URL is as required as the SKU. Its own tuple also accepts bare `url`
        # and `link`; those are deliberately not aliases of the canonical, because in a
        # product master a `url` column is the client's own page, not a competitor's.
        required_columns=("product_id", "competitor_url"),
        optional_columns=("competitor_site", "our_price"),
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
    # Construct the owned resources one at a time: the owned SkuMap opens a
    # sqlite connection first, so if constructing the owned PriceLedger then
    # raises we must close that already-open sku_map before re-raising --
    # otherwise the main try/finally below (which never begins) leaks it to GC.
    # A caller-supplied handle is never touched here; its lifecycle stays the
    # caller's.
    sku_map = SkuMap() if owns_sku_map else params.get("sku_map")
    if owns_ledger:
        try:
            ledger = PriceLedger()
        except BaseException:
            if owns_sku_map:
                sku_map.close()
            raise
    else:
        ledger = params.get("ledger")
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


# ---- intake_scan (raw folder data-readiness audit) ---------------------------

def _intake_scan_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path or not Path(request.data_path).is_dir():
        return Prepared(status="needs_data", messages=[
            "a folder of raw client files (CSV/XLSX) is required: point this at the "
            "folder itself, not a single file"
        ])
    try:
        folder = intake_scan_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=folder)


def _intake_scan_run(payload: object, params: dict) -> Produced:
    report = intake_scan_job.run(payload, params)
    return Produced(report=report, summary=report.summary)



# ---- service_scan (servicio realizado, desde una cuenta viva) -----------------

def _service_scan_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    """Resuelve el backend. No pide archivo: esta tool lee una CUENTA.

    Un fallo de conexion sale como ``needs_data`` y no como ``error``: lo que falta es el
    acceso al sistema del cliente, que se arregla con credenciales, no con un parche.
    """
    try:
        payload = service_scan_job.prepare(request.data_path, request.params)
    except (ValueError, KeyError, RuntimeError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=payload)


def _service_scan_run(payload: object, params: dict) -> Produced:
    report = service_scan_job.run(
        payload, since=params.get("since"), stockout_days=params.get("stockout_days")
    )
    fill = report.portfolio.fill_rate
    otif = report.portfolio.otif
    resumen = (
        f"Read {report.n_orders:,} orders ({report.n_lines:,} lines) from "
        f"{report.source_name}. Fill rate "
        f"{'not measured' if fill is None else f'{fill * 100:.1f}%'}; OTIF "
        f"{'not measured' if otif is None else f'{otif * 100:.1f}%'}."
    )
    # Las notas de cobertura entran al resumen porque es lo que el operador lee ANTES de
    # abrir el entregable, y porque un OTIF sin decir de que campo salio -- ni si esa fecha
    # es un despacho o una llegada -- no se puede reconciliar contra el ERP del cliente.
    if report.notes:
        resumen = f"{resumen} {' '.join(report.notes)}"
    return Produced(report=report, summary=resumen)


def service_scan_tool() -> Tool:
    return Tool(
        key="service_scan",
        title="Realized Customer Service",
        description="Read a live commerce backend (Unleashed, Cin7 Core, or an offline "
                    "stand-in) and measure the service customers actually received: "
                    "order-line fill rate, outbound OTIF (SCOR RL.1.1) and lost sales -- "
                    "plus a field-by-field statement of what that backend CANNOT measure. "
                    "Never a policy's implied beta presented as service.",
        intent_keywords=(
            "service scan", "realized service", "servicio realizado", "fill rate",
            "otif", "on time in full", "perfect order", "pedido perfecto",
            "servicio al cliente", "customer service level", "cuanto entregue",
            "que servicio di", "lost sales", "venta perdida",
            # Frases multi-palabra y no verbos sueltos: "entregar" o "servicio" a secas
            # le robarian el ruteo a media docena de tools. Estas dos salieron de escribir
            # el brief como lo escribiria un cliente y ver que no matcheaba nada.
            "llegue a entregar", "que tan bien entregue",
        ),
        requires_data=False,
        prepare=_service_scan_prepare,
        run=_service_scan_run,
        qa=lambda report: service_scan_job.verify(report),
        options=lambda report: report.outcome,
        deliver=lambda report, out_dir, client: service_scan_deliverable.write_operational(
            report, out_dir, client
        ),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            service_scan_deliverable.build(report, client=client, citations=tuple(citations),
                                           confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
    )

def intake_scan_tool() -> Tool:
    return Tool(
        key="intake_scan",
        title="Data Readiness Scan",
        description="Walk a raw client folder, classify every readable file against the known "
                    "intake profiles (maestro/ventas/stock/finanzas), and report which registered "
                    "tools can run today -- green, yellow, red, or not-yet-declared -- plus the "
                    "single highest-value column to request or file to fix next.",
        intent_keywords=(
            "intake", "auditoria de datos", "que puedo correr", "what can i run",
            "escanear la carpeta", "scan the folder", "aptitud del dato", "data readiness",
            "que datos necesito", "what data do i need", "missing columns",
        ),
        requires_data=True,
        prepare=_intake_scan_prepare,
        run=_intake_scan_run,
        qa=lambda report: intake_scan_job.verify(report),
        deliver=lambda report, out_dir, client: intake_scan_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            intake_scan_job.build_deck(report, client=client, citations=tuple(citations),
                                        confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
        options=lambda report: report.outcome,
    )


# ---- procedure_audit (SOP document-control audit) ------------------------------
# ---- procedure_draft (bilingual SOP draft: Job Breakdown Sheet + control plan) --
#
# "sop" elsewhere in this file (see sop_tool() above) means Sales & Operations
# Planning -- a different discipline from a Standard Operating Procedure. Every
# keyword below was checked against scm_agent/intent.py's strict keyword win
# (top_score >= 1 AND top_score > second_score, scm_agent/intent.py:59): a tie with
# sop, cycle_count, org_friction_audit, inventory_optimization, acceptance_sampling
# or data_quality sends the brief to needs_clarification instead of either tool
# below. Do not add a bare "sop", "draft", "procedure" or "revision" -- each one
# ties against another registered tool (see tests/test_procedures_tools.py).

PROCEDURE_AUDIT_KEYWORDS = (
    "procedure register", "registro de procedimientos", "procedure audit", "auditoria de procedimientos",
    "auditar los procedimientos", "audit our procedures", "audit the procedures", "audit our sops",
    "audit the sops", "sop register", "registro de sops", "procedimientos operativos", "overdue sop",
    "sops overdue", "sop vencido", "sops vencidos", "procedimientos vencidos", "procedimiento vencido",
    "overdue for review", "revision vencida", "revisiones vencidas", "document control",
    "control de documentos", "control documental", "documented information", "informacion documentada",
    "iso 9001", "procedure owner", "dueno del procedimiento", "procedures without an owner",
    "sops without an owner", "procedimientos sin dueno", "sops sin dueno", "which sops", "que sops",
    "cuales sops", "who owns each procedure", "quien es dueno de cada procedimiento",
)
PROCEDURE_DRAFT_KEYWORDS = (
    "procedure draft", "sop draft", "draft a procedure", "draft the procedure", "draft a sop", "draft an sop",
    "draft the sop", "draft a standard operating procedure", "write a procedure", "write the procedure",
    "write a sop", "write an sop", "write the sop", "write a standard operating procedure",
    "borrador de procedimiento", "borrador de sop", "borrador del sop", "borrador del procedimiento",
    "redacta el procedimiento", "redactar el procedimiento", "redacta el sop", "redactar el sop",
    "redactame el sop", "escribe el sop", "escribir el sop", "arma el sop", "armar el sop", "genera el sop",
    "generar el sop", "sop template", "plantilla de sop", "plantilla de procedimiento", "procedure template",
    "job breakdown sheet", "hoja de desglose", "control plan", "plan de control", "work instruction",
    "instructivo de trabajo", "standard work", "trabajo estandar", "training timetable",
    "cronograma de entrenamiento", "document control header", "encabezado de control documental",
    "cadencia de revision", "review cadence",
)


def _procedure_audit_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=[
            "a procedure register CSV/Excel export is required: one row per procedure, with "
            "at least name/procedimiento and last_reviewed_at/ultima_revision as columns "
            "(pass params['as_of'] as an ISO date, e.g. '2026-09-03', to make the audit reproducible)"
        ])
    # Validate as_of/review_cadence_days/due_soon_days here, at the tool boundary,
    # before run() ever sees them -- request.params is the same merged dict run()
    # will receive (scm_agent/orchestrator.py replaces request.params before calling
    # prepare()). Without this, a malformed value (e.g. as_of='03/09/2026', a
    # non-numeric review_cadence_days) raised a ValueError inside tool.run(), which
    # is not guarded per-tool and so escaped to the orchestrator's blanket except
    # Exception -> "An internal error occurred.", losing the job's own clear message.
    try:
        procedure_audit_job.validate_params(request.params)
    except ValueError as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    try:
        prepared = procedure_audit_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=prepared)


def _procedure_audit_run(payload: object, params: dict) -> Produced:
    report = procedure_audit_job.run(payload, params)
    return Produced(report=report, summary=report.summary)


def procedure_audit_tool() -> Tool:
    return Tool(
        key="procedure_audit",
        title="Procedure Register Audit (SOP document control)",
        description="Audit a client's exported procedure register for overdue reviews, "
                    "missing owners, an owner who is also the approver, procedures marked "
                    "approved with no approver on file, procedures with no linked KPI, and "
                    "unversioned documents. Always ends in a HANDOFF naming the owner and the "
                    "due date -- and never stores the SOPs themselves.",
        intent_keywords=PROCEDURE_AUDIT_KEYWORDS,
        requires_data=True,
        required_columns=("name", "last_reviewed_at"),
        optional_columns=(
            "procedure_id", "process_area", "owner", "approver", "version", "approved_at",
            "review_cadence_days", "location", "linked_kpi", "reaction_plan", "status",
        ),
        prepare=_procedure_audit_prepare,
        run=_procedure_audit_run,
        qa=lambda r: procedure_audit_job.verify(r),
        deliver=lambda report, out_dir, client: procedure_audit_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            procedure_audit_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
        options=lambda report: report.outcome,
    )


def _procedure_draft_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    try:
        draft_request = procedure_draft_job.prepare(request.params)
    except ValueError as exc:
        return Prepared(status="needs_clarification", messages=[str(exc)])
    return Prepared(status="ok", payload=draft_request)


def _procedure_draft_run(payload: object, params: dict) -> Produced:
    draft = procedure_draft_job.run(payload, params)
    return Produced(report=draft, summary=draft.outcome.summary)


def procedure_draft_tool() -> Tool:
    return Tool(
        key="procedure_draft",
        title="SOP Draft with Document Control (Job Breakdown Sheet + Control Plan)",
        description="Draft a bilingual Standard Operating Procedure -- Job Breakdown Sheet, "
                    "control plan and training timetable -- from one of three templates "
                    "(replenishment, cycle count, or the S&OP cycle), stamped with a "
                    "document-control header. Always born unapproved: the outcome is a "
                    "HANDOFF asking a human to review, approve and publish it before use.",
        intent_keywords=PROCEDURE_DRAFT_KEYWORDS,
        requires_data=False,
        prepare=_procedure_draft_prepare,
        run=_procedure_draft_run,
        qa=lambda d: procedure_draft_job.verify(d),
        deliver=lambda draft, out_dir, client: procedure_draft_job.write_operational(draft, out_dir, client),
        deck=lambda draft, out_dir, client, citations, confidence, options, readings=(): replace(
            procedure_draft_job.build_deck(draft, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
        options=lambda draft: draft.outcome,
    )


# ---- iso_ops (plant ISO applicability + floor evidence; never a certificate) ----
#
# Distinctive multi-word phrases only. Do NOT add a bare "iso 9001": that keyword
# already belongs to procedure_audit (SOP document control) and a tie with it
# would send mixed briefs to needs_clarification instead of either tool.

ISO_OPS_KEYWORDS = (
    "catalogo iso", "catálogo iso", "iso operations catalog", "iso operations",
    "detectar normas iso", "detector de normas iso", "normas iso de operaciones",
    "normas iso de cadena", "evidencia de piso", "evidencia de piso iso",
    "iso floor evidence", "which iso standards apply", "que normas iso aplican",
    "qué normas iso aplican", "iso 14067", "iso 50001", "iso 14083", "iso 14001",
    "iatf 16949", "product carbon footprint", "huella de producto",
    "cobertura iso", "iso coverage kern",
)


def _iso_ops_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not iso_ops_job.perfil_presente(request.params, request.data_path):
        return Prepared(status="needs_clarification", messages=[
            "hace falta una ficha de planta (JSON/CSV) o params con sectores, "
            "clientePide, logistica, planta, kern, texto, y/o senales de orden "
            "ciclo/scrap/kg/tkm. Ficha vacia no inventa normas; sin ficha no se corre."
        ])
    try:
        prepared = iso_ops_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_clarification", messages=[str(exc)])
    return Prepared(status="ok", payload=prepared)


def _iso_ops_run(payload: object, params: dict) -> Produced:
    report = iso_ops_job.run(payload, params)
    return Produced(report=report, summary=report.summary)


def iso_ops_tool() -> Tool:
    return Tool(
        key="iso_ops",
        title="ISO Operations Floor Evidence (not a certificate)",
        description="Detect which operations/supply-chain ISO (and IATF) norms apply to "
                    "a plant profile, propose floor tasks already in the catalog, and "
                    "evaluate order evidence (cycle, scrap, kg, t·km). Always OPTIONS "
                    "or HANDOFF. Never says the plant is ISO-certified.",
        intent_keywords=ISO_OPS_KEYWORDS,
        requires_data=False,
        prepare=_iso_ops_prepare,
        run=_iso_ops_run,
        qa=lambda r: iso_ops_job.verify(r),
        deliver=lambda report, out_dir, client: iso_ops_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            iso_ops_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
        options=lambda report: report.outcome,
    )


# ---- audit_evidence (MUS + GL tie-out workpaper; never a signed opinion) ------
#
# Distinctive multi-word phrases. Do NOT add bare "reconciliation" / "reconcile"
# (IRA tool) or "acceptance sampling" / "aql" / "sampling plan".

AUDIT_EVIDENCE_KEYWORDS = (
    "audit sampling", "inventory audit sampling", "audit sample size",
    "monetary unit sampling", "mus sample", "mus workpaper",
    "gl reconciliation", "general ledger", "trial balance", "tie-out", "tie out",
    "audit workpaper", "audit evidence", "substantive testing", "test of details",
    "papel de trabajo mus", "evidencia de auditoria", "muestreo mus",
    "prepare a monetary unit sampling",
)


def _audit_evidence_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=[
            "a GL/inventory listing CSV is required (unit_id + book_value). "
            "Optional source=gl|subledger puts both ledgers in one table. "
            "params.tolerable_misstatement is auditor judgment and is required at run."
        ])
    try:
        audit_evidence_job.validate_params(request.params)
    except ValueError as exc:
        return Prepared(status="needs_clarification", messages=[str(exc)])
    try:
        prepared = audit_evidence_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=prepared)


def _audit_evidence_run(payload: object, params: dict) -> Produced:
    report = audit_evidence_job.run(payload, params)
    return Produced(report=report, summary=report.summary)


def audit_evidence_tool() -> Tool:
    return Tool(
        key="audit_evidence",
        title="Audit Evidence Workpaper (MUS + GL tie-out; not an opinion)",
        description="Prepare a re-performable inventory/COGS workpaper: MUS sample, optional "
                    "GL tie-out, IPE attestation, EvidenceRecord lineage. Always HANDOFF "
                    "(or ESCALATED if UML exceeds TM or the tie-out is off tolerance). "
                    "Never a signed audit opinion. Reviewed-by stays blank by design.",
        intent_keywords=AUDIT_EVIDENCE_KEYWORDS,
        requires_data=True,
        required_columns=("unit_id", "book_value"),
        optional_columns=("source", "audited_value"),
        required_client_params=("tolerable_misstatement",),
        prepare=_audit_evidence_prepare,
        run=_audit_evidence_run,
        qa=lambda r: audit_evidence_job.verify(r),
        deliver=lambda report, out_dir, client: audit_evidence_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            audit_evidence_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
        options=lambda report: report.outcome,
    )


# ---- scor_iso_audit (E4 SCOR + ISO checklist; dedicated job, not PackageSpec) -
#
# Do NOT add bare "iso 9001" (procedure_audit) or iso_ops catalog/floor phrases.

SCOR_ISO_AUDIT_KEYWORDS = (
    "scor iso audit", "scor scorecard", "iso 28000 checklist",
    "pack de preparacion para auditoria", "scor level 1 scorecard",
    "perfect order scor", "cash-to-cash scor",
    "preparacion para auditoria scor", "scor iso pack",
    "scor iso audit evidence", "registrar auditor scorecard",
)


def _scor_iso_audit_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=[
            "a tagged CSV is required: add a dataset column with values "
            "financial / cost_to_serve / sourcing / orders / procedures / risk"
        ])
    try:
        prepared = scor_audit_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=prepared)


def _scor_iso_audit_run(payload: object, params: dict) -> Produced:
    report = scor_audit_job.run(payload, params)
    return Produced(report=report, summary=report.summary)


def scor_iso_audit_tool() -> Tool:
    return Tool(
        key="scor_iso_audit",
        title="SCOR/ISO Evidence Pack (not a certificate)",
        description="Compose existing measurements (cash-to-cash, cost-to-serve, Perfect Order "
                    "RL.1.1, procedure register, TTR/TTS) into one EvidenceRecord plus a "
                    "Lineage sheet. Checklist statuses are observed / gap / no_evaluable. "
                    "Always HANDOFF. Kern prepares evidence for a registrar's auditor; it "
                    "does not certify ISO or SCOR.",
        intent_keywords=SCOR_ISO_AUDIT_KEYWORDS,
        requires_data=True,
        required_columns=("dataset",),
        optional_columns=(
            "product_id", "name", "cogs", "avg_inventory_value",
            "last_reviewed_at", "owner", "approver", "version",
        ),
        prepare=_scor_iso_audit_prepare,
        run=_scor_iso_audit_run,
        qa=lambda r: scor_audit_job.verify(r),
        deliver=lambda report, out_dir, client: scor_audit_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options, readings=(): replace(
            scor_audit_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options), readings=tuple(readings),
        ).write_all(out_dir),
        options=lambda report: report.outcome,
    )


REPRICING_KEYWORDS = (
    # Action-anchored and multi-word on purpose: a bare "precio"/"price" would
    # hijack every pricing brief away from the `pricing` and `price_intelligence`
    # tools. intent.classify needs a STRICT win, so these phrases name the ACT of
    # pushing a new price list to a channel, which neither of those two does.
    "reprice", "repricing", "repricing playbook", "push new prices",
    "update prices on", "stage price changes", "price changeset",
    "multichannel repricing", "reprice my channel", "reprice shopify",
    "repreciar", "repreciado", "cambiar precios en", "actualizar precios en",
    "subir precios al canal", "lista de precios nueva", "cambio de precios masivo",
)


def _repricing_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(
            status="needs_data",
            messages=["a price list is required: one row per SKU with current_price and proposed_price"],
        )
    try:
        plan = repricing_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=plan)


def _repricing_run(payload: object, params: dict) -> Produced:
    report = repricing_job.run(payload, max_move_pct=params.get("max_move_pct"))
    return Produced(report=report, summary=report.summary)


def repricing_tool() -> Tool:
    """The multichannel repricing playbook, as a STAGING-ONLY tool.

    ``jobs/repricing.py`` was fully built and tested in Linchpin 3.0 PR-18 and
    reachable by nobody but a test and a CLI (gap audit finding #6). This makes
    it routable -- without making a live price write routable.

    ``jobs.repricing_job`` never imports ``apply_repricing`` or
    ``run_channel_repricing``, stages against an in-memory copy of the client's
    own current prices, and always returns HANDOFF. There is no path from a
    brief to a channel write. Deliberately absent from
    ``webapp/mcp_tool_specs.py``: that surface is opt-in, and a tool about
    changing live prices does not belong on an anonymously reachable one.
    """
    return Tool(
        key="repricing",
        title="Multichannel Repricing (staged, never applied)",
        description="Take a proposed price list, run it through the central pricing guardrail "
                    "(max move per change, below-landed-cost block, a readable reason and L3 "
                    "citations), and stage the resulting per-SKU diff for a human to approve. "
                    "It never writes to Shopify/MercadoLibre/Odoo and never applies: the outcome "
                    "is always a handoff naming the exact steps the operator runs to apply it.",
        intent_keywords=REPRICING_KEYWORDS,
        requires_data=True,
        required_columns=repricing_job.REQUIRED_COLUMNS,
        optional_columns=repricing_job.OPTIONAL_COLUMNS,
        options=lambda report: report.outcome,
        prepare=_repricing_prepare,
        run=_repricing_run,
        qa=lambda report: repricing_job.verify(report),
        deliver=lambda report, out_dir, client: repricing_job.write_operational(report, out_dir, client),
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
    reg.register(supplier_management_tool())
    reg.register(ddmrp_tool())
    reg.register(landed_cost_tool())
    reg.register(import_case_tool())
    reg.register(whatif_tool())
    reg.register(financial_kpis_tool())
    reg.register(reconciliation_tool())
    reg.register(returns_tool())
    reg.register(warehouse_layout_tool())
    reg.register(queuing_tool())
    reg.register(scheduling_tool())
    reg.register(risk_tool())
    reg.register(disruption_scan_tool())
    reg.register(forecast_tool())
    reg.register(data_quality_tool())
    reg.register(dea_tool())
    reg.register(acceptance_sampling_tool())
    reg.register(earned_value_tool())
    reg.register(learning_curve_tool())
    reg.register(odoo_replenishment_tool())
    reg.register(shopify_replenishment_tool())
    reg.register(channel_replenishment_tool())
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
    reg.register(network_design_tool())
    reg.register(drp_tool())
    reg.register(vehicle_routing_tool())
    reg.register(price_intelligence_tool())
    reg.register(price_watch_tool())
    reg.register(launch_readiness_tool())
    reg.register(hat_tension_tool())
    reg.register(hat_settlement_tool())
    reg.register(flow_map_tool())
    reg.register(cross_impact_tool())
    reg.register(org_friction_tool())
    reg.register(intake_scan_tool())
    reg.register(service_scan_tool())
    reg.register(procedure_audit_tool())
    reg.register(procedure_draft_tool())
    reg.register(iso_ops_tool())
    reg.register(audit_evidence_tool())
    reg.register(scor_iso_audit_tool())
    reg.register(repricing_tool())
    return reg

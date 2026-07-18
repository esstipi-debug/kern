"""The seven sellable commercial packages (specs only; the runner lives in
``scm_agent/packages.py``).

Scope, price and cadence mirror the "Estructura de empaquetado comercial" in
``documentation/MONETIZATION_BRIEF.md`` (the commercial source of truth) and
the client-facing one-pagers in ``documentation/paquetes/``. If any of the
three diverge, fix them together in the same PR.

- **diagnostico** - Diagnostico de Arranque: one-time 2-week sprint, 4 tools.
- **starter** - Fundamentos de Inventario: variable monthly scope by SKU count
  (floor USD 900 at ~500 SKUs, +USD 40/mes per 250-SKU block, hard ceiling
  USD 1,500 = Growth's price), 15 tools. Includes 7 "universal" tools moved
  down from Growth 2026-07-18 (excess_obsolete, financial_kpis, pricing,
  reconciliation, landed_cost, returns, risk) - they apply to any business
  regardless of size, unlike the network/org-complexity tools that stay
  Growth-and-up. All 7 are wired ``required=False`` (skip gracefully if the
  client hasn't sent that file yet) except ``pricing``, which is why Starter's
  ``ventas`` input slot upgraded from ``_VENTAS_BASIC`` to ``_VENTAS_GROWTH``
  (adds the ``price`` column ``jobs/pricing.py`` hard-requires) instead of
  gating it - a present-but-malformed file blocks the whole package the same
  way a missing required file does, so a soft skip doesn't actually protect
  this one.
- **growth** - Operacion Completa de SC: monthly + quarterly QBR, 26 tools
  (everything in diagnostico + starter, plus 11 more).
- **scale** - Red, S&OP y Mando Ejecutivo: biweekly + monthly S&OP, the full
  35-tool catalog (everything in growth, plus 9 more).
- **retainer_ejecutivo** - Retainer Ejecutivo Fraccional: same 35 tools as
  scale - the brief is explicit that what changes is governance (weekly
  cadence, SLA escalation), not capability, so this spec reuses scale's step
  list verbatim under different commercial metadata.
- **proyecto_red_almacen** - one-off network/warehouse/ops project, 6 tools.
- **proyecto_sourcing** - one-off sourcing/landed-cost project, 3 tools
  (reuses growth's supplier/import/quality intake slots).
- **liquidacion** - Sprint de Liquidacion: one-off, contingent-fee pricing
  (10-20% of cash recovered, see ``src/contingent_fee.py``) instead of a
  fixed price, 3-4 tools (data_quality, excess_obsolete, markdown_liquidation,
  optional pricing).

Steps whose input file is a real burden to produce every month are optional:
they run when the file is present and are recorded as skipped (never blocking)
when it is not. ``cadence`` labels what the operator promises commercially -
the runner itself runs whatever it is given.
"""

from __future__ import annotations

import os

import pandas as pd

from .packages import PackageInput, PackageSpec, PackageStep

# ---- shared intake slots -----------------------------------------------------

_VENTAS_BASIC = PackageInput(
    slot="ventas", filename="ventas.csv",
    description="historial de ventas/demanda, una fila por venta o por periodo-SKU",
    columns="date, product_id, quantity, unit_cost",
)
_VENTAS_GROWTH = PackageInput(
    slot="ventas", filename="ventas.csv",
    description="historial de ventas/demanda con precio, una fila por venta o por periodo-SKU",
    columns="date, product_id, quantity, unit_cost, price",
)
_MAESTRO = PackageInput(
    slot="maestro", filename="maestro.csv",
    description="maestro de productos/SKUs",
    columns="sku (+ name, gtin, unit_cost opcionales)",
)
_STOCK = PackageInput(
    slot="stock", filename="stock.csv",
    description="stock a mano por SKU con su demanda diaria",
    columns="product_id, on_hand, daily_demand (+ unit_cost, days_since_last_sale opcionales)",
)
_FINANZAS = PackageInput(
    slot="finanzas", filename="finanzas.csv",
    description="finanzas de inventario por SKU (COGS e inventario promedio)",
    columns="product_id, cogs, avg_inventory_value (+ gross_margin, units_sold, net_sales opcionales)",
)
_PLANILLA = PackageInput(
    slot="planilla", filename="planilla.xlsx",
    description="tu planilla de reposicion tal como esta (Excel; se devuelve con el plan staged y reversible)",
    columns="una hoja con SKU + stock actual + (demanda o punto de reorden); se autodetectan",
)
_SUPUESTOS = PackageInput(
    slot="supuestos", filename="supuestos.csv",
    description="rangos de sensibilidad para el what-if (si falta, se corre una plantilla estandar)",
    columns="driver, low, high (+ base, unit); drivers validos: annual_demand, holding_cost, "
            "fixed_order_cost, demand_std, service_level, lead_time",
)
_ESTACIONAL = PackageInput(
    slot="estacional", filename="compra_estacional.csv",
    description="compras de una sola temporada (newsvendor) - solo cuando aplique",
    columns="product_id, mean_demand, price, unit_cost (+ std_demand, salvage_value, goodwill)",
)
_PEDIDOS = PackageInput(
    slot="pedidos", filename="pedidos.csv",
    description="lineas de pedido por segmento/canal para cost-to-serve",
    columns="segment, revenue (+ quantity, order_id, cogs, returns, freight opcionales)",
)
_CONTEOS = PackageInput(
    slot="conteos", filename="conteos.csv",
    description="conteos fisicos vs. sistema (exactitud de registros / IRA)",
    columns="product_id, system_qty, physical_qty (+ unit_cost)",
)
_LOTES = PackageInput(
    slot="lotes", filename="lotes.csv",
    description="lotes con vencimiento (FEFO) - solo si manejas perecederos/vencimientos",
    columns="product_id, lot_id, quantity, days_to_expiry (+ unit_cost, unit_price, daily_demand)",
)
_RED = PackageInput(
    slot="red", filename="red_echelon.csv",
    description="cadena serial (proveedor -> CD -> tienda) para ubicar el stock de seguridad",
    columns="stage, lead_time, holding_cost, mean_demand, demand_std (+ order)",
)
_DDMRP = PackageInput(
    slot="ddmrp", filename="buffers_ddmrp.csv",
    description="partes a bufferizar con DDMRP",
    columns="part_id, adu, dlt (+ ltf, vf, moq, on_hand, on_order, qualified_demand)",
)
_SIMULACION = PackageInput(
    slot="simulacion", filename="simulacion.csv",
    description="SKUs para optimizar la politica (R,S) por simulacion Monte Carlo",
    columns="product_id, mean_demand, std_demand, lead_time (+ holding_cost, order_cost, backorder_cost)",
)
_DRP = PackageInput(
    slot="drp", filename="sucursales_drp.csv",
    description="demanda por sucursal y periodo para DRP (plan de distribucion + CD central)",
    columns="branch, period, demand (+ on_hand, lead_time, safety_stock, lot_size; parametros dc_*)",
)
_PROVEEDORES = PackageInput(
    slot="proveedores", filename="proveedores.csv",
    description="registros de entrega por proveedor (scorecard + award TOPSIS)",
    columns="supplier (+ on_time, in_full, lead_time_days, units, defects, unit_price) - una fila por entrega",
)
_IMPORTACIONES = PackageInput(
    slot="importaciones", filename="importaciones.csv",
    description="lineas de importacion para costo total en destino (landed cost)",
    columns="sku, unit_cost, qty (+ freight, insurance, duty_rate, handling, broker_fee, incoterm)",
)
_CALIDAD = PackageInput(
    slot="calidad", filename="calidad_aql.csv",
    description="partes con niveles de calidad AQL/LTPD para muestreo de recepcion",
    columns="part, aql, ltpd",
)
_CURVA = PackageInput(
    slot="curva", filename="curva_aprendizaje.csv",
    description="productos para proyectar cost-down por curva de aprendizaje",
    columns="product, first_unit_cost, learning_rate, planned_volume",
)
_DEVOLUCIONES = PackageInput(
    slot="devoluciones", filename="devoluciones.csv",
    description="devoluciones por SKU para logistica inversa (mejor disposicion)",
    columns="product_id, returned_units, unit_cost (+ reason, resale_value, sellable)",
)
_RIESGOS = PackageInput(
    slot="riesgos", filename="riesgos.csv",
    description="registro de riesgos de la cadena (EMV, RPN, mapa de calor)",
    columns="name, likelihood, impact_value (+ category, exposure, velocity_days, detectability_days)",
)
_UNIDADES = PackageInput(
    slot="unidades", filename="unidades_dea.csv",
    description="unidades comparables (bodegas/proveedores/tiendas) para frontera de eficiencia DEA",
    columns="unit + columnas con prefijo input_* y output_*",
)
_UBICACIONES = PackageInput(
    slot="ubicaciones", filename="ubicaciones.csv",
    description="puntos de demanda (clientes/tiendas/CDs) para ubicar una nueva instalacion",
    columns="x, y - coordenadas locales o lat/lon (+ name, weight opcionales)",
)
_ENVIOS = PackageInput(
    slot="envios", filename="envios.csv",
    description="historial de envios para elegir el modo de transporte optimo por envio",
    columns="weight_kg, distance_km (+ shipment_id, lane, units, order_value opcionales)",
)
_LINEAS_PEDIDO = PackageInput(
    slot="lineas_pedido", filename="lineas_pedido.csv",
    description="lineas de pedido (pedido x SKU) para el slotting COI + afinidad",
    columns="order_id, product_id (+ unit_volume opcional)",
)
_ESTACIONES = PackageInput(
    slot="estaciones", filename="estaciones.csv",
    description="estaciones de servicio (mostrador/dock/call center) para dimensionar personal",
    columns="station, arrival_rate, service_rate (+ wait_cost, server_cost opcionales)",
)
_TRABAJOS = PackageInput(
    slot="trabajos", filename="trabajos.csv",
    description="trabajos a secuenciar en planta/taller (una maquina)",
    columns="job, processing_time (+ due_date opcional)",
)
_VALOR_GANADO = PackageInput(
    slot="valor_ganado", filename="valor_ganado.csv",
    description="control de proyectos activos por valor ganado (EVM)",
    columns="task, planned, earned, actual",
)
_LIDERAZGO = PackageInput(
    slot="liderazgo", filename="liderazgo.csv",
    description="autoevaluacion de liderazgo (modelo CHAIN) - relevala vos con el cliente, "
                "no se le manda un CSV a nadie",
    columns="C, H, A, I, N - una fila, cada valor entero 0-4",
)


# ---- derives / fallbacks / gates ----------------------------------------------

def _cycle_count_from_abc(reports: dict[str, object]) -> pd.DataFrame:
    """The count program consumes the ABC-XYZ classification computed two steps
    earlier - the client sends ONE sales file, not a second pre-classified list.
    (Feeding the raw sales long-format into cycle_count would mis-derive value:
    its value map keeps the last row per SKU, it does not aggregate.)"""
    report = reports["abc_xyz"]
    rows = [{"product_id": c.product_id, "abc": c.abc} for c in report.classifications]
    return pd.DataFrame(rows)


def _default_whatif_drivers() -> pd.DataFrame:
    """Standard sensitivity template (personalize via supuestos.csv when possible)."""
    rows = [
        {"driver": "annual_demand", "low": 9600, "base": 12000, "high": 14400, "unit": "units/yr"},
        {"driver": "holding_cost", "low": 2.4, "base": 3.0, "high": 3.6, "unit": "$/unit/yr"},
        {"driver": "fixed_order_cost", "low": 60.0, "base": 75.0, "high": 90.0, "unit": "$/order"},
        {"driver": "demand_std", "low": 32.0, "base": 40.0, "high": 48.0, "unit": "units"},
        {"driver": "service_level", "low": 0.90, "base": 0.95, "high": 0.98, "unit": "ratio"},
        {"driver": "lead_time", "low": 1.6, "base": 2.0, "high": 2.4, "unit": "periods"},
    ]
    return pd.DataFrame(rows)


def _odoo_gate(params: dict) -> str:
    """Run against Odoo only when the operator opted in (or credentials exist);
    the in-memory demo stand-in must never leak into a real client deliverable."""
    if params.get("use_odoo") or os.environ.get("ODOO_URL"):
        return ""
    return "sin credenciales Odoo (ODOO_URL) ni use_odoo=true; se omite"


def _leadership_scores_from_csv(path) -> dict:
    """leadership_chain takes params["scores"], not a data_path - liderazgo.csv is
    a one-row CHAIN self-assessment (C,H,A,I,N, each 0-4) the operator relevates
    with the client; this converts it into the params override the tool reads.
    Raises ValueError with an operator-actionable message (not a raw KeyError) so
    the package's error path surfaces something fixable, not just "'N'"."""
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError("liderazgo.csv esta vacio - debe tener una fila con C,H,A,I,N")
    row = df.iloc[0]
    order = ("C", "H", "A", "I", "N")
    missing = [code for code in order if code not in row.index]
    if missing:
        raise ValueError(
            f"liderazgo.csv no tiene columna(s) {missing} - se esperan exactamente C,H,A,I,N"
        )
    try:
        scores = [int(row[code]) for code in order]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"liderazgo.csv tiene un valor no entero en C,H,A,I,N: {exc}") from exc
    out_of_range = [f"{code}={v}" for code, v in zip(order, scores) if not 0 <= v <= 4]
    if out_of_range:
        raise ValueError(
            f"liderazgo.csv tiene valor(es) fuera de 0-4: {', '.join(out_of_range)}"
        )
    return {"scores": scores}


# A mid-size DC scenario for the parametric warehouse_layout tool - it has no CSV
# input at all (generative from a nested config dict, not client data), so this
# doubles as the step's default params. Client-specific dims come from the
# operator overriding these keys directly (they replace the whole nested dict,
# not a deep merge) when scoping the project.
_WAREHOUSE_PROJECT_PARAMS = {
    "site": {"width_m": 220.0, "depth_m": 160.0},
    "building": {"width_m": 90.0, "depth_m": 85.0, "height_m": 13.0, "levels": 4},
    "racks": {"modules": 8, "bays_per_rack": 22, "aisle_width_m": 3.2},
    "docks": {"count": 10, "face": "south"},
    "gates": {"count": 3},
}


# ---- the three packages --------------------------------------------------------

DIAGNOSTICO = PackageSpec(
    key="diagnostico",
    title="Diagnostico de Arranque",
    price="USD 1,500 - 2,500 (pago unico)",
    cadence="sprint unico de 2 semanas",
    audience="primer contacto - cuantifica el problema antes de comprometerse a un mensual",
    inputs=(_MAESTRO, _VENTAS_BASIC, _STOCK, _FINANZAS),
    steps=(
        PackageStep("data_quality", "maestro", cadence="sprint"),
        PackageStep("abc_xyz", "ventas", cadence="sprint"),
        PackageStep("excess_obsolete", "stock", cadence="sprint"),
        PackageStep("financial_kpis", "finanzas", cadence="sprint"),
    ),
)

_STARTER_STEPS = (
    PackageStep("data_quality", "maestro"),
    PackageStep("abc_xyz", "ventas"),
    PackageStep("forecast", "ventas"),
    PackageStep("inventory_optimization", "ventas"),
    PackageStep("whatif", "supuestos", fallback=_default_whatif_drivers,
                fallback_note="rangos estandar +/-20%; personalizar supuestos.csv"),
    PackageStep("newsvendor", "estacional", required=False, cadence="por temporada"),
    PackageStep("excel_replenishment", "planilla"),
    PackageStep("cycle_count", None, derive=_cycle_count_from_abc),
    # Moved down from Growth 2026-07-18 - "universal" tools that apply to any
    # business regardless of size/structure. pricing keeps required=True (see
    # module docstring: Starter's ventas slot now carries the price column it
    # needs); the other 6 are required=False since their files (stock/finanzas/
    # conteos/importaciones/devoluciones/riesgos) aren't part of Starter's
    # traditional intake - they run as a no-extra-charge bonus when sent.
    PackageStep("pricing", "ventas"),
    PackageStep("excess_obsolete", "stock", required=False),
    PackageStep("financial_kpis", "finanzas", required=False),
    PackageStep("reconciliation", "conteos", required=False),
    PackageStep("landed_cost", "importaciones", required=False, cadence="por disparador"),
    PackageStep("returns", "devoluciones", required=False),
    PackageStep("risk", "riesgos", required=False, cadence="QBR trimestral"),
)

STARTER = PackageSpec(
    key="starter",
    title="Starter - Fundamentos de Inventario",
    price="USD 900 / mes (variable: piso $900 hasta ~500 SKUs, +$40/mes cada "
          "bloque de 250 SKUs, techo $1,500 - subir de piso nunca es una "
          "sorpresa, siempre se aprueba antes)",
    cadence="mensual",
    audience="e-commerce o distribuidor mono-almacen (USD 1-10M) que hoy compra a ojo en Excel",
    # _VENTAS_GROWTH (not _VENTAS_BASIC) because the pricing step moved in here
    # needs the price column - see module docstring. Each PackageSpec's own
    # `inputs` tuple is authoritative for its own steps (PackageSpec.input_for
    # looks up slots on `self`, not on some shared registry), so the 6 other
    # moved-down slots (stock/finanzas/conteos/importaciones/devoluciones/
    # riesgos) must be declared here too, even though they're required=False -
    # otherwise resolving them raises KeyError before the "file absent" skip
    # path ever gets a chance to run.
    inputs=(_VENTAS_GROWTH, _MAESTRO, _PLANILLA, _SUPUESTOS, _ESTACIONAL,
            _STOCK, _FINANZAS, _CONTEOS, _IMPORTACIONES, _DEVOLUCIONES, _RIESGOS),
    steps=_STARTER_STEPS,
)

GROWTH = PackageSpec(
    key="growth",
    title="Growth - Operacion Completa de SC",
    price="USD 1,500 / mes (variable: piso $1,500 hasta ~2,000 SKUs, +$60/mes "
          "cada bloque de 500 SKUs, techo $3,200; mensual + QBR trimestral)",
    cadence="mensual + QBR trimestral",
    audience="empresa en crecimiento, multi-almacen/canal, con o migrando a un ERP (Odoo)",
    inputs=(
        _VENTAS_GROWTH, _MAESTRO, _PLANILLA, _SUPUESTOS, _ESTACIONAL,
        _STOCK, _FINANZAS, _PEDIDOS, _CONTEOS, _LOTES, _RED, _DDMRP,
        _SIMULACION, _DRP, _PROVEEDORES, _IMPORTACIONES, _CALIDAD, _CURVA,
        _DEVOLUCIONES, _RIESGOS, _UNIDADES,
    ),
    # excess_obsolete/financial_kpis/pricing/reconciliation/landed_cost/returns/
    # risk moved down into _STARTER_STEPS 2026-07-18 (see module docstring) -
    # Growth still gets all of them, just inherited from the shared base tuple
    # instead of listed here a second time.
    steps=_STARTER_STEPS + (
        PackageStep("cost_to_serve", "pedidos"),
        PackageStep("fefo", "lotes", required=False,
                    cadence="mensual (si hay perecederos)"),
        PackageStep("multi_echelon", "red", required=False,
                    cadence="mensual (si hay red multi-eslabon)"),
        PackageStep("ddmrp", "ddmrp", required=False),
        PackageStep("simulation", "simulacion", required=False),
        PackageStep("drp", "drp", required=False,
                    cadence="mensual (si hay sucursales)"),
        PackageStep("odoo_replenishment", None, required=False, gate=_odoo_gate,
                    cadence="mensual (si hay Odoo)"),
        PackageStep("sourcing", "proveedores", required=False, cadence="QBR trimestral"),
        PackageStep("acceptance_sampling", "calidad", required=False, cadence="por disparador"),
        PackageStep("learning_curve", "curva", required=False, cadence="QBR trimestral"),
        PackageStep("dea", "unidades", required=False, cadence="QBR trimestral"),
    ),
)

_SCALE_EXTRA_STEPS = (
    # Called out explicitly in the brief as Scale's defining monthly ritual
    # ("Quincenal + S&OP mensual") - required, but reuses ventas.csv, which
    # Growth already requires, so this adds no new required file.
    PackageStep("sop", "ventas", cadence="mensual (ciclo S&OP)"),
    PackageStep("facility_location", "ubicaciones", required=False,
                cadence="por disparador (rediseno de red)",
                params={"current_x": 40.0, "current_y": 25.0}),
    PackageStep("transportation", "envios", required=False,
                cadence="mensual (si hay flota/fletes propios)"),
    PackageStep("warehouse_layout", None, required=False,
                cadence="por disparador (rediseno de bodega)",
                params=_WAREHOUSE_PROJECT_PARAMS),
    PackageStep("slotting", "lineas_pedido", required=False,
                cadence="mensual (si hay bodega propia)"),
    PackageStep("queuing", "estaciones", required=False,
                cadence="mensual (si hay estaciones de servicio/mostrador)"),
    PackageStep("scheduling", "trabajos", required=False,
                cadence="mensual (si hay programacion de planta/taller)"),
    PackageStep("earned_value", "valor_ganado", required=False,
                cadence="QBR trimestral (si hay proyectos activos)"),
    PackageStep("leadership_chain", "liderazgo", required=False,
                cadence="QBR trimestral (coaching ejecutivo)",
                params_from_input=_leadership_scores_from_csv),
)
_SCALE_STEPS = GROWTH.steps + _SCALE_EXTRA_STEPS
_SCALE_INPUTS = GROWTH.inputs + (
    _UBICACIONES, _ENVIOS, _LINEAS_PEDIDO, _ESTACIONES, _TRABAJOS,
    _VALOR_GANADO, _LIDERAZGO,
)

SCALE = PackageSpec(
    key="scale",
    title="Scale - Red, S&OP y Mando Ejecutivo",
    price="USD 3,200 / mes (flat, sin variable - certidumbre de presupuesto sobre facturacion incremental)",
    cadence="quincenal + S&OP mensual",
    audience="mid-market con red real (2+ plantas/CDs)",
    inputs=_SCALE_INPUTS,
    steps=_SCALE_STEPS,
)

RETAINER_EJECUTIVO = PackageSpec(
    key="retainer_ejecutivo",
    title="Retainer Ejecutivo Fraccional",
    price="USD 4,500 / mes (flat = Scale x 1.4)",
    cadence="mensual + cadencia semanal + escalamiento con SLA",
    # Not a 4th tier a cold buyer picks from a menu - offered ONLY as an
    # upgrade to an existing Scale client (6-18 meses), never listed alongside
    # Starter/Growth/Scale up front. Same 35 tools as Scale, zero new
    # capability - the delta is governance (weekly cadence, SLA escalation,
    # autonomous writeback authority), not analysis.
    audience="upgrade ofrecido a un cliente Scale existente (6-18 meses en Scale, mandato de "
             "VP/COO fraccional) - nunca vendido en frio como opcion de entrada",
    # Deliberately the SAME tool set as Scale - the brief is explicit the
    # difference is governance (weekly cadence, SLA-routed escalation, see
    # RB-6), not analytical capability. Nothing to re-derive here.
    inputs=_SCALE_INPUTS,
    steps=_SCALE_STEPS,
)

PROYECTO_RED_ALMACEN = PackageSpec(
    key="proyecto_red_almacen",
    title="Proyecto de Red, Almacen y Operacion",
    price="USD 8,000 - 18,000 (pago unico)",
    cadence="proyecto unico, 4-8 semanas",
    audience="inflexion estructural: nueva bodega, rediseno de red/almacen",
    inputs=(_UBICACIONES, _ENVIOS, _LINEAS_PEDIDO, _ESTACIONES, _TRABAJOS),
    steps=(
        PackageStep("facility_location", "ubicaciones", cadence="proyecto",
                    params={"current_x": 40.0, "current_y": 25.0}),
        PackageStep("transportation", "envios", cadence="proyecto"),
        PackageStep("warehouse_layout", None, cadence="proyecto",
                    params=_WAREHOUSE_PROJECT_PARAMS),
        PackageStep("slotting", "lineas_pedido", cadence="proyecto"),
        PackageStep("queuing", "estaciones", cadence="proyecto"),
        PackageStep("scheduling", "trabajos", cadence="proyecto"),
    ),
)

PROYECTO_SOURCING = PackageSpec(
    key="proyecto_sourcing",
    title="Proyecto de Sourcing y Costo de Importacion",
    price="USD 5,000 - 10,000 (pago unico, recurrible trimestral/anual)",
    cadence="proyecto unico, recurrible trimestral/anual",
    audience="importadores / manufactura offshore",
    inputs=(_PROVEEDORES, _IMPORTACIONES, _CALIDAD),
    steps=(
        PackageStep("sourcing", "proveedores", cadence="proyecto"),
        PackageStep("landed_cost", "importaciones", cadence="proyecto"),
        PackageStep("acceptance_sampling", "calidad", cadence="proyecto"),
    ),
)

LIQUIDACION = PackageSpec(
    key="liquidacion",
    title="Sprint de Liquidacion",
    price="10-20% del cash recuperado (piso USD 1,500) - ver src/contingent_fee.py",
    cadence="sprint unico, 2-3 semanas",
    audience="stock muerto/excedente ya diagnosticado, decidido a liquidar - no quiere pagar "
             "un fee fijo por algo que todavia no se recupero",
    # Same intake as the Diagnostico (maestro + stock) plus the optional price
    # history the Growth package's pricing step already knows how to read -
    # a client who ran the Diagnostico first sends nothing new.
    inputs=(_MAESTRO, _STOCK, _VENTAS_GROWTH),
    steps=(
        PackageStep("data_quality", "maestro", cadence="sprint"),
        PackageStep("excess_obsolete", "stock", cadence="sprint"),
        # price_history_path comes from the SAME ventas.csv the (separate,
        # optional) pricing step below reads - when present, markdown_liquidation
        # fits a real elasticity curve instead of falling back to the default-
        # markdown/salvage heuristics (a >5x difference in recovered cash on the
        # demo intake - this is not cosmetic, the contingent fee is computed
        # directly off total_recovered).
        PackageStep("markdown_liquidation", "stock", cadence="sprint",
                    extra_input_params={"price_history_path": "ventas"}),
        PackageStep("pricing", "ventas", required=False, cadence="sprint (si hay historial de precios)"),
    ),
)

PACKAGES: dict[str, PackageSpec] = {
    spec.key: spec for spec in (
        DIAGNOSTICO, STARTER, GROWTH, SCALE, RETAINER_EJECUTIVO,
        PROYECTO_RED_ALMACEN, PROYECTO_SOURCING, LIQUIDACION,
    )
}


def get_package(key: str) -> PackageSpec:
    if key not in PACKAGES:
        raise KeyError(f"unknown package '{key}' (available: {', '.join(sorted(PACKAGES))})")
    return PACKAGES[key]

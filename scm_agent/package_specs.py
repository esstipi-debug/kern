"""The three sellable commercial packages (specs only; the runner lives in
``scm_agent/packages.py``).

Scope, price and cadence mirror the "Estructura de empaquetado comercial" in
``documentation/MONETIZATION_BRIEF.md`` (the commercial source of truth) and
the client-facing one-pagers in ``documentation/paquetes/``. If any of the
three diverge, fix them together in the same PR.

- **diagnostico** - Diagnostico de Arranque: one-time 2-week sprint, 4 tools.
- **starter** - Fundamentos de Inventario: fixed monthly scope, 8 tools.
- **growth** - Operacion Completa de SC: monthly + quarterly QBR, 26 tools
  (everything in diagnostico + starter, plus 16 more).

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
)

STARTER = PackageSpec(
    key="starter",
    title="Starter - Fundamentos de Inventario",
    price="USD 2,000 / mes (alcance fijo)",
    cadence="mensual",
    audience="e-commerce o distribuidor mono-almacen (USD 1-10M) que hoy compra a ojo en Excel",
    inputs=(_VENTAS_BASIC, _MAESTRO, _PLANILLA, _SUPUESTOS, _ESTACIONAL),
    steps=_STARTER_STEPS,
)

GROWTH = PackageSpec(
    key="growth",
    title="Growth - Operacion Completa de SC",
    price="USD 4,000 / mes (mensual + QBR trimestral)",
    cadence="mensual + QBR trimestral",
    audience="empresa en crecimiento, multi-almacen/canal, con o migrando a un ERP (Odoo)",
    inputs=(
        _VENTAS_GROWTH, _MAESTRO, _PLANILLA, _SUPUESTOS, _ESTACIONAL,
        _STOCK, _FINANZAS, _PEDIDOS, _CONTEOS, _LOTES, _RED, _DDMRP,
        _SIMULACION, _DRP, _PROVEEDORES, _IMPORTACIONES, _CALIDAD, _CURVA,
        _DEVOLUCIONES, _RIESGOS, _UNIDADES,
    ),
    steps=_STARTER_STEPS + (
        PackageStep("excess_obsolete", "stock"),
        PackageStep("financial_kpis", "finanzas"),
        PackageStep("pricing", "ventas"),
        PackageStep("cost_to_serve", "pedidos"),
        PackageStep("reconciliation", "conteos", required=False),
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
        PackageStep("landed_cost", "importaciones", required=False, cadence="por disparador"),
        PackageStep("acceptance_sampling", "calidad", required=False, cadence="por disparador"),
        PackageStep("learning_curve", "curva", required=False, cadence="QBR trimestral"),
        PackageStep("returns", "devoluciones", required=False),
        PackageStep("risk", "riesgos", required=False, cadence="QBR trimestral"),
        PackageStep("dea", "unidades", required=False, cadence="QBR trimestral"),
    ),
)

PACKAGES: dict[str, PackageSpec] = {
    spec.key: spec for spec in (DIAGNOSTICO, STARTER, GROWTH)
}


def get_package(key: str) -> PackageSpec:
    if key not in PACKAGES:
        raise KeyError(f"unknown package '{key}' (available: {', '.join(sorted(PACKAGES))})")
    return PACKAGES[key]

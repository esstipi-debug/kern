"""Run a commercial package end-to-end: several registry tools, one QA gate,
one consolidated deck + every tool's own deliverable.

    # what to ask the client for (the intake checklist)
    python examples/run_package.py --package starter --checklist

    # run on a client's intake folder
    python examples/run_package.py --package diagnostico --intake intake/acme \
        --client "ACME" --out deliverables/acme

    # full demo on synthetic data (no client files needed)
    python examples/run_package.py --package growth --demo

    # bilingual deck (see src/i18n.py) -- default is the client's profile, else "es"
    python examples/run_package.py --package diagnostico --demo --lang en

Packages (scope/price: documentation/MONETIZATION_BRIEF.md + documentation/paquetes/):
diagnostico (4 tools, sprint) | starter (8 tools, mensual) | growth (26 tools, mensual+QBR) |
scale (35 tools, quincenal+S&OP mensual) | retainer_ejecutivo (same 35, distinta cadencia) |
proyecto_red_almacen (6 tools, proyecto unico) | proyecto_sourcing (3 tools, proyecto unico) |
liquidacion (3-4 tools, sprint contingente -- ver --fee-pct/--fee-floor/--measure abajo).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from scm_agent.package_specs import PACKAGES, get_package
from scm_agent.packages import PackageSpec, missing_required_inputs, run_package
from scm_agent.types import STATUS_OK
from src import client_profile, contingent_fee

# ---------------------------------------------------------------------------
# Demo intake: deterministic synthetic files exercising every input slot.
# ---------------------------------------------------------------------------

_SKUS = ["SKU-001", "SKU-002", "SKU-003", "SKU-004", "SKU-005", "SKU-006"]


def _ean13(base12: str) -> str:
    digits = [int(c) for c in base12]
    check = (10 - sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits)) % 10) % 10
    return base12 + str(check)


def _demo_ventas(rng: np.random.Generator) -> pd.DataFrame:
    start = date(2025, 7, 7)
    rows = []
    base_qty = {s: 40 + 25 * i for i, s in enumerate(_SKUS)}
    base_price = {s: 12.0 + 6.0 * i for i, s in enumerate(_SKUS)}
    for week in range(52):
        d = (start + timedelta(weeks=week)).isoformat()
        for sku in _SKUS:
            price = base_price[sku] * float(rng.uniform(0.85, 1.15))
            # demand responds to price (elasticity ~ -1.5) so pricing can fit a curve
            qty = base_qty[sku] * (price / base_price[sku]) ** -1.5
            qty *= float(rng.uniform(0.8, 1.2))
            rows.append({
                "date": d, "product_id": sku, "quantity": round(max(qty, 0.0), 1),
                "unit_cost": round(base_price[sku] * 0.6, 2), "price": round(price, 2),
            })
    return pd.DataFrame(rows)


def _demo_maestro() -> pd.DataFrame:
    rows = []
    for i, sku in enumerate(_SKUS):
        rows.append({
            "sku": sku, "name": f"Producto {i + 1}",
            "gtin": _ean13(f"77912345000{i}"), "unit_cost": round(7.0 + 3.5 * i, 2),
        })
    # data-quality bait: a likely duplicate (same name) and an invalid GTIN
    rows.append({"sku": "SKU-001B", "name": "Producto 1", "gtin": _ean13("779123450001"),
                 "unit_cost": 7.1})
    rows.append({"sku": "SKU-007", "name": "Producto 7", "gtin": "7791234500999",
                 "unit_cost": 30.0})
    return pd.DataFrame(rows)


def _demo_stock() -> pd.DataFrame:
    rows = [
        {"product_id": "SKU-001", "on_hand": 320, "daily_demand": 6.0, "unit_cost": 7.0,
         "days_since_last_sale": 3},
        {"product_id": "SKU-002", "on_hand": 900, "daily_demand": 9.0, "unit_cost": 10.5,
         "days_since_last_sale": 5},
        {"product_id": "SKU-003", "on_hand": 2400, "daily_demand": 11.0, "unit_cost": 14.0,
         "days_since_last_sale": 12},   # excess
        {"product_id": "SKU-004", "on_hand": 640, "daily_demand": 0.05, "unit_cost": 17.5,
         "days_since_last_sale": 400},  # dead
        {"product_id": "SKU-005", "on_hand": 150, "daily_demand": 15.0, "unit_cost": 21.0,
         "days_since_last_sale": 1},
    ]
    return pd.DataFrame(rows)


def _demo_finanzas() -> pd.DataFrame:
    rows = []
    for i, sku in enumerate(_SKUS):
        cogs = 40_000 + 22_000 * i
        rows.append({
            "product_id": sku, "cogs": cogs,
            "avg_inventory_value": round(cogs / (2.0 + 0.8 * i), 2),
            "gross_margin": round(cogs * 0.45, 2), "units_sold": 2_000 + 700 * i,
            "units_on_hand": 400 + 90 * i, "net_sales": round(cogs * 1.45, 2),
        })
    return pd.DataFrame(rows)


def _demo_supuestos() -> pd.DataFrame:
    return pd.DataFrame([
        {"driver": "annual_demand", "low": 10_000, "base": 13_000, "high": 16_000,
         "unit": "units/yr"},
        {"driver": "holding_cost", "low": 2.5, "base": 3.2, "high": 4.0, "unit": "$/unit/yr"},
        {"driver": "service_level", "low": 0.90, "base": 0.95, "high": 0.98, "unit": "ratio"},
        {"driver": "lead_time", "low": 1.5, "base": 2.0, "high": 3.0, "unit": "periods"},
    ])


def _demo_estacional() -> pd.DataFrame:
    return pd.DataFrame([
        {"product_id": "TEMP-01", "mean_demand": 480, "std_demand": 130, "price": 35.0,
         "unit_cost": 19.0, "salvage_value": 8.0},
        {"product_id": "TEMP-02", "mean_demand": 260, "std_demand": 90, "price": 52.0,
         "unit_cost": 30.0, "salvage_value": 12.0},
    ])


def _demo_planilla(path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Reposicion"
    ws.append(["SKU", "Stock Actual", "Demanda Semanal", "Pedido"])
    for i, sku in enumerate(_SKUS):
        ws.append([sku, 40 + 30 * i, 35 + 20 * i, ""])
    wb.save(path)


def _demo_pedidos(rng: np.random.Generator) -> pd.DataFrame:
    segments = {"Retail": 1.00, "Mayorista": 0.72, "E-commerce": 1.20}
    rows = []
    for seg, factor in segments.items():
        for n in range(20):
            revenue = float(rng.uniform(300, 1_500)) * factor
            rows.append({
                "Segment": seg, "Order ID": f"{seg[:3].upper()}-{n:03d}",
                "Sales": round(revenue, 2), "Quantity": int(rng.integers(5, 60)),
                # Mayorista sells below cost => a loss-making segment to find
                "COGS": round(revenue * (0.95 if seg == "Mayorista" else 0.55), 2),
                "Returns": round(revenue * 0.03, 2),
                "Shipping Cost": round(float(rng.uniform(8, 45)), 2),
            })
    return pd.DataFrame(rows)


def _demo_conteos(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for i, sku in enumerate(_SKUS):
        system = 200 + 60 * i
        drift = int(rng.integers(-12, 13)) if i % 2 else 0
        rows.append({"product_id": sku, "system_qty": system,
                     "physical_qty": system + drift, "unit_cost": 7.0 + 3.5 * i})
    return pd.DataFrame(rows)


def _demo_lotes() -> pd.DataFrame:
    return pd.DataFrame([
        {"product_id": "PER-01", "lot_id": "L-101", "quantity": 120, "days_to_expiry": 9,
         "unit_cost": 4.0, "unit_price": 9.0, "daily_demand": 6.0},
        {"product_id": "PER-01", "lot_id": "L-102", "quantity": 200, "days_to_expiry": 40,
         "unit_cost": 4.0, "unit_price": 9.0, "daily_demand": 6.0},
        {"product_id": "PER-02", "lot_id": "L-201", "quantity": 80, "days_to_expiry": 4,
         "unit_cost": 6.5, "unit_price": 14.0, "daily_demand": 3.0},
    ])


def _demo_red() -> pd.DataFrame:
    return pd.DataFrame([
        {"stage": "proveedor", "lead_time": 3, "holding_cost": 1.0, "order": 1,
         "mean_demand": 90.0, "demand_std": 22.0},
        {"stage": "cd_central", "lead_time": 2, "holding_cost": 2.0, "order": 2,
         "mean_demand": 90.0, "demand_std": 22.0},
        {"stage": "tienda", "lead_time": 1, "holding_cost": 4.0, "order": 3,
         "mean_demand": 90.0, "demand_std": 22.0},
    ])


def _demo_ddmrp() -> pd.DataFrame:
    return pd.DataFrame([
        {"part_id": "P-100", "adu": 24.0, "dlt": 6, "ltf": 0.5, "vf": 0.4,
         "on_hand": 30, "on_order": 0, "qualified_demand": 40},   # deep red
        {"part_id": "P-200", "adu": 12.0, "dlt": 10, "ltf": 0.6, "vf": 0.5,
         "on_hand": 180, "on_order": 40, "qualified_demand": 10},
        {"part_id": "P-300", "adu": 40.0, "dlt": 4, "ltf": 0.4, "vf": 0.3,
         "on_hand": 600, "on_order": 0, "qualified_demand": 20},  # over green
    ])


def _demo_simulacion() -> pd.DataFrame:
    return pd.DataFrame([
        {"product_id": "SIM-01", "mean_demand": 50.0, "std_demand": 15.0, "lead_time": 2,
         "holding_cost": 1.0, "order_cost": 90.0, "backorder_cost": 6.0},
        {"product_id": "SIM-02", "mean_demand": 20.0, "std_demand": 9.0, "lead_time": 4,
         "holding_cost": 1.5, "order_cost": 120.0, "backorder_cost": 8.0},
    ])


def _demo_drp(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for branch, base in (("norte", 60), ("centro", 90), ("sur", 45)):
        for period in range(1, 9):
            rows.append({
                "branch": branch, "period": period,
                "demand": int(base * float(rng.uniform(0.8, 1.2))),
                "on_hand": base * 2, "lead_time": 1, "safety_stock": int(base * 0.5),
                "lot_size": base,
            })
    return pd.DataFrame(rows)


def _demo_proveedores(rng: np.random.Generator) -> pd.DataFrame:
    profiles = {"Proveedor A": (0.96, 3.0, 30), "Proveedor B": (0.82, 6.0, 220),
                "Proveedor C": (0.90, 4.5, 90)}
    rows = []
    for supplier, (otif, lead, ppm) in profiles.items():
        for _ in range(12):
            units = int(rng.integers(200, 900))
            rows.append({
                "supplier": supplier,
                "on_time": int(rng.random() < otif), "in_full": int(rng.random() < otif),
                "lead_time_days": round(lead * float(rng.uniform(0.8, 1.3)), 1),
                "units": units, "defects": int(units * ppm / 1_000_000) + int(rng.integers(0, 2)),
                "unit_price": round(10.0 * float(rng.uniform(0.9, 1.15)), 2),
            })
    return pd.DataFrame(rows)


def _demo_importaciones() -> pd.DataFrame:
    return pd.DataFrame([
        {"sku": "IMP-01", "unit_cost": 8.0, "qty": 1_200, "freight": 1_400.0,
         "insurance": 90.0, "duty_rate": 0.12, "handling": 240.0, "broker_fee": 180.0,
         "incoterm": "FOB"},
        {"sku": "IMP-02", "unit_cost": 22.0, "qty": 400, "freight": 900.0,
         "insurance": 60.0, "duty_rate": 0.16, "handling": 150.0, "broker_fee": 180.0,
         "incoterm": "CIF"},
    ])


def _demo_calidad() -> pd.DataFrame:
    return pd.DataFrame([
        {"part": "C-01", "aql": 0.01, "ltpd": 0.05},
        {"part": "C-02", "aql": 0.02, "ltpd": 0.08},
    ])


def _demo_curva() -> pd.DataFrame:
    return pd.DataFrame([
        {"product": "NUEVO-01", "first_unit_cost": 120.0, "learning_rate": 0.85,
         "planned_volume": 500},
        {"product": "NUEVO-02", "first_unit_cost": 60.0, "learning_rate": 0.90,
         "planned_volume": 1_500},
    ])


def _demo_devoluciones() -> pd.DataFrame:
    return pd.DataFrame([
        {"product_id": "SKU-001", "returned_units": 40, "unit_cost": 7.0,
         "reason": "no era lo esperado", "resale_value": 6.0, "sellable": True},
        {"product_id": "SKU-003", "returned_units": 25, "unit_cost": 14.0,
         "reason": "dano en transporte", "resale_value": 4.0, "sellable": False},
        {"product_id": "SKU-005", "returned_units": 60, "unit_cost": 21.0,
         "reason": "talle/variante", "resale_value": 19.0, "sellable": True},
    ])


def _demo_riesgos() -> pd.DataFrame:
    return pd.DataFrame([
        {"name": "proveedor unico componente critico", "likelihood": 0.25,
         "impact_value": 180_000, "category": "suministro", "velocity_days": 10,
         "detectability_days": 20, "time_to_recover": 45, "time_to_survive": 30},
        {"name": "quiebre en temporada alta", "likelihood": 0.35, "impact_value": 90_000,
         "category": "demanda", "velocity_days": 7, "detectability_days": 5,
         "time_to_recover": 15, "time_to_survive": 20},
        {"name": "aumento de flete internacional", "likelihood": 0.5, "impact_value": 40_000,
         "category": "logistica", "velocity_days": 30, "detectability_days": 10,
         "time_to_recover": 30, "time_to_survive": 60},
    ])


def _demo_unidades() -> pd.DataFrame:
    return pd.DataFrame([
        {"unit": "bodega_norte", "input_costo": 120_000, "input_horas": 4_200,
         "output_pedidos": 9_800, "output_lineas": 41_000},
        {"unit": "bodega_centro", "input_costo": 150_000, "input_horas": 5_100,
         "output_pedidos": 12_500, "output_lineas": 55_000},
        {"unit": "bodega_sur", "input_costo": 90_000, "input_horas": 3_600,
         "output_pedidos": 5_900, "output_lineas": 22_000},
        {"unit": "bodega_este", "input_costo": 110_000, "input_horas": 4_000,
         "output_pedidos": 10_100, "output_lineas": 47_000},
    ])


def _demo_ubicaciones() -> pd.DataFrame:
    return pd.DataFrame([
        {"name": "Tienda Norte", "x": 10.0, "y": 10.0, "weight": 120},
        {"name": "Tienda Este", "x": 70.0, "y": 15.0, "weight": 90},
        {"name": "Tienda Centro", "x": 45.0, "y": 60.0, "weight": 150},
        {"name": "Tienda Oeste", "x": 20.0, "y": 50.0, "weight": 80},
        {"name": "Tienda Sur", "x": 65.0, "y": 55.0, "weight": 110},
    ])


def _demo_envios(rng: np.random.Generator) -> pd.DataFrame:
    lanes = [
        ("Local", 12.0, 90.0), ("Local", 25.0, 110.0),
        ("Regional", 350.0, 220.0), ("Regional", 480.0, 260.0),
        ("Nacional", 8_500.0, 480.0), ("Nacional", 9_200.0, 520.0),
        ("Larga Distancia", 14_000.0, 1_900.0), ("Larga Distancia", 16_500.0, 2_100.0),
    ]
    rows = []
    for n, (lane, weight, distance) in enumerate(lanes):
        rows.append({
            "shipment_id": f"ENV-{n:03d}", "lane": lane,
            "weight_kg": round(weight * float(rng.uniform(0.9, 1.1)), 1),
            "distance_km": round(distance * float(rng.uniform(0.9, 1.1)), 1),
            "units": int(rng.integers(1, 40)),
            "order_value": round(weight * float(rng.uniform(2.0, 4.0)), 2),
        })
    return pd.DataFrame(rows)


def _demo_lineas_pedido(rng: np.random.Generator) -> pd.DataFrame:
    catalog = ["SLT-01", "SLT-02", "SLT-03", "SLT-04", "SLT-05", "SLT-06"]
    volumes = {sku: float(rng.uniform(0.01, 0.08)) for sku in catalog}
    rows = []
    for order_n in range(40):
        order_id = f"PED-{order_n:04d}"
        # SLT-01/SLT-02 co-occur often (affinity bait); the rest ride along randomly.
        # A list (not a set) so row order is reproducible across processes - a
        # set's iteration order depends on PYTHONHASHSEED, which is randomized
        # per interpreter by default even with a fixed numpy rng seed.
        basket: list[str] = ["SLT-01", "SLT-02"] if rng.random() < 0.6 else []
        target_size = int(rng.integers(1, 4))  # rolled ONCE per order, not re-rolled per pick
        while len(basket) < target_size:
            sku = catalog[int(rng.integers(0, len(catalog)))]
            if sku not in basket:
                basket.append(sku)
        for sku in basket:
            rows.append({"order_id": order_id, "product_id": sku, "unit_volume": volumes[sku]})
    return pd.DataFrame(rows)


def _demo_estaciones() -> pd.DataFrame:
    return pd.DataFrame([
        {"station": "caja_recepcion", "arrival_rate": 8.0, "service_rate": 3.0,
         "wait_cost": 12.0, "server_cost": 18.0},
        {"station": "mesa_ayuda", "arrival_rate": 4.0, "service_rate": 2.5,
         "wait_cost": 9.0, "server_cost": 15.0},
        {"station": "call_center", "arrival_rate": 20.0, "service_rate": 5.0,
         "wait_cost": 15.0, "server_cost": 20.0},
    ])


def _demo_trabajos() -> pd.DataFrame:
    return pd.DataFrame([
        {"job": "OP-101", "processing_time": 4.0, "due_date": 10.0},
        {"job": "OP-102", "processing_time": 2.0, "due_date": 6.0},
        {"job": "OP-103", "processing_time": 6.0, "due_date": 20.0},
        {"job": "OP-104", "processing_time": 1.0, "due_date": 4.0},
        {"job": "OP-105", "processing_time": 3.0, "due_date": 14.0},
    ])


def _demo_valor_ganado() -> pd.DataFrame:
    return pd.DataFrame([
        {"task": "Ingenieria", "planned": 100_000, "earned": 90_000, "actual": 95_000},
        {"task": "Instalacion", "planned": 50_000, "earned": 55_000, "actual": 48_000},
        {"task": "Pruebas", "planned": 30_000, "earned": 15_000, "actual": 25_000},
        {"task": "Puesta en marcha", "planned": 20_000, "earned": 20_000, "actual": 21_000},
    ])


def _demo_liderazgo() -> pd.DataFrame:
    # CHAIN model (Palamariu & Alicke): Comunicacion, Humildad, Adaptabilidad,
    # Integracion, Nutricion del talento - each 0-4. Relevar con el cliente,
    # nunca mandarle este CSV a llenar solo.
    return pd.DataFrame([{"C": 3, "H": 2, "A": 3, "I": 1, "N": 2}])


def build_demo_intake(intake_dir: str | Path) -> Path:
    """Write the full synthetic intake (every slot of every package) and return the dir."""
    intake = Path(intake_dir)
    intake.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    _demo_ventas(rng).to_csv(intake / "ventas.csv", index=False)
    _demo_maestro().to_csv(intake / "maestro.csv", index=False)
    _demo_stock().to_csv(intake / "stock.csv", index=False)
    _demo_finanzas().to_csv(intake / "finanzas.csv", index=False)
    _demo_supuestos().to_csv(intake / "supuestos.csv", index=False)
    _demo_estacional().to_csv(intake / "compra_estacional.csv", index=False)
    _demo_planilla(intake / "planilla.xlsx")
    _demo_pedidos(rng).to_csv(intake / "pedidos.csv", index=False)
    _demo_conteos(rng).to_csv(intake / "conteos.csv", index=False)
    _demo_lotes().to_csv(intake / "lotes.csv", index=False)
    _demo_red().to_csv(intake / "red_echelon.csv", index=False)
    _demo_ddmrp().to_csv(intake / "buffers_ddmrp.csv", index=False)
    _demo_simulacion().to_csv(intake / "simulacion.csv", index=False)
    _demo_drp(rng).to_csv(intake / "sucursales_drp.csv", index=False)
    _demo_proveedores(rng).to_csv(intake / "proveedores.csv", index=False)
    _demo_importaciones().to_csv(intake / "importaciones.csv", index=False)
    _demo_calidad().to_csv(intake / "calidad_aql.csv", index=False)
    _demo_curva().to_csv(intake / "curva_aprendizaje.csv", index=False)
    _demo_devoluciones().to_csv(intake / "devoluciones.csv", index=False)
    _demo_riesgos().to_csv(intake / "riesgos.csv", index=False)
    _demo_unidades().to_csv(intake / "unidades_dea.csv", index=False)
    _demo_ubicaciones().to_csv(intake / "ubicaciones.csv", index=False)
    _demo_envios(rng).to_csv(intake / "envios.csv", index=False)
    _demo_lineas_pedido(rng).to_csv(intake / "lineas_pedido.csv", index=False)
    _demo_estaciones().to_csv(intake / "estaciones.csv", index=False)
    _demo_trabajos().to_csv(intake / "trabajos.csv", index=False)
    _demo_valor_ganado().to_csv(intake / "valor_ganado.csv", index=False)
    _demo_liderazgo().to_csv(intake / "liderazgo.csv", index=False)
    return intake


DEMO_PARAMS = {
    "use_odoo": True,          # demo runs against the in-memory Odoo stand-in
    "holding_rate": 0.25, "service_level": 0.95,
    "dso": 45.0, "dpo": 30.0,  # working-capital lens for financial KPIs / cost-to-serve
    "periods": 600,            # keep the Monte Carlo simulation quick in demo mode
    "dc_on_hand": 400.0, "dc_lead_time": 2, "dc_safety_stock": 100.0, "dc_lot_size": 200.0,
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_checklist(spec: PackageSpec) -> None:
    print(f"== {spec.title} ==")
    print(f"Precio: {spec.price} | Cadencia: {spec.cadence}")
    print(f"Para: {spec.audience}")
    print()
    required_slots = {s.input_slot for s in spec.steps if s.required and s.input_slot and not s.fallback}
    print("Archivos a pedir al cliente (en una carpeta de intake):")
    for inp in spec.inputs:
        tag = "REQUERIDO" if inp.slot in required_slots else "opcional"
        print(f"  [{tag}] {inp.filename}: {inp.description}")
        print(f"      columnas: {inp.columns}")
    print()
    print("Parametros del cliente (una vez, quedan en su perfil):")
    print("  holding_rate (ej. 0.25), service_level (ej. 0.95), lead_time_days por defecto")
    print()
    print("Herramientas del paquete:")
    for step in spec.steps:
        req = "requerido" if step.required else "opcional"
        print(f"  - {step.tool_key} ({req}; {step.cadence})")


_ACTUAL_SALES_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "Product", "product")
_ACTUAL_SALES_QTY_COLS = ("quantity", "qty", "units", "units_sold", "Quantity")
_ACTUAL_SALES_PRICE_COLS = ("price", "unit_price", "sell_price", "Price")


def _resolve_fee_params(
    client: str, cli_pct: float | None, cli_floor: float | None,
    root: Path | str = client_profile.DEFAULT_CLIENTS_ROOT,
) -> tuple[float, float]:
    """CLI override > the client's negotiated contingent_fee_pct > the package default.

    Mirrors _resolve_lang's error-handling split (an adversarial review of E4
    caught the same anti-pattern there): an unslugifiable client label means
    "no profile", but a CORRUPT profile.json must fail loudly, not silently
    default the fee percentage and mask a real data-integrity problem.
    """
    pct = cli_pct
    if pct is None:
        try:
            slug = client_profile.slugify_client_id(client)
        except ValueError:
            slug = None
        if slug is not None:
            profile = client_profile.load_profile(slug, root=root)
            pct = profile.contingent_fee_pct if profile is not None else None
    if pct is None:
        pct = contingent_fee.DEFAULT_FEE_PCT
    floor = cli_floor if cli_floor is not None else contingent_fee.DEFAULT_FLOOR
    return pct, floor


def _pick(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    return next((c for c in candidates if c in df.columns), None)


def _actual_recovery_by_sku(path: str | Path) -> dict[str, float]:
    """Post-liquidation sales CSV -> {product_id: quantity * price summed}."""
    df = pd.read_csv(path)
    product = _pick(df, _ACTUAL_SALES_PRODUCT_COLS)
    qty = _pick(df, _ACTUAL_SALES_QTY_COLS)
    price = _pick(df, _ACTUAL_SALES_PRICE_COLS)
    missing = [n for n, c in (("product_id", product), ("quantity", qty), ("price", price)) if c is None]
    if missing:
        raise ValueError(
            f"--measure CSV: no se encontraron columnas {missing} (columnas vistas: {list(df.columns)[:10]})"
        )
    numeric_qty = pd.to_numeric(df[qty], errors="coerce")
    numeric_price = pd.to_numeric(df[price], errors="coerce")
    bad_rows = df.index[numeric_qty.isna() | numeric_price.isna()]
    if len(bad_rows):
        # A silently-dropped row would read as "sold for $0", indistinguishable
        # from a real zero-recovery outcome -- a garbled cell must be a loud
        # error, not a quiet zero baked into the client's real fee.
        bad_skus = sorted(df.loc[bad_rows, product].astype(str).unique())[:10]
        raise ValueError(
            f"--measure CSV: {len(bad_rows)} fila(s) con quantity/price no numerico "
            f"(SKUs afectados: {bad_skus}); corregi el archivo antes de medir el recupero"
        )
    df = df.assign(_cash=numeric_qty * numeric_price)
    return df.groupby(df[product].astype(str))["_cash"].sum().to_dict()


def _write_liquidation_annexes(spec: PackageSpec, result, args) -> None:
    """After a successful run that included markdown_liquidation: write the
    pre-engagement fee estimate, and (with --measure) the real closing annex."""
    liq_outcome = next(
        (s for s in result.steps if s.tool_key == "markdown_liquidation" and s.status == STATUS_OK), None,
    )
    if liq_outcome is None:
        if args.measure:
            print("\n[honorarios] --measure se omite: este paquete no corrio markdown_liquidation.")
        return

    fee_pct, floor = _resolve_fee_params(args.client, args.fee_pct, args.fee_floor)
    out_root = Path(args.out) / spec.key
    estimate = contingent_fee.calculate_contingent_fee(
        liq_outcome.report.total_recovered, fee_pct, floor,
    )
    estimate_path = out_root / "estimacion_honorarios.md"
    estimate_path.write_text(contingent_fee.render_fee_estimate(estimate, client=args.client), encoding="utf-8")
    print(f"\n[honorarios] estimacion escrita en {estimate_path}")

    if args.measure:
        estimated_by_sku = {line.product_id: line.recovered_value for line in liq_outcome.report.lines}
        actual_by_sku = _actual_recovery_by_sku(args.measure)
        measured = contingent_fee.measure_recovery(estimated_by_sku, actual_by_sku, fee_pct=fee_pct, floor=floor)
        closing_path = out_root / "anexo_cierre.md"
        closing_path.write_text(
            contingent_fee.render_measurement_annex(measured, client=args.client), encoding="utf-8",
        )
        print(f"[medicion] anexo de cierre escrito en {closing_path} "
              f"(real {measured.total_actual:,.0f} vs. estimado {measured.total_estimated:,.0f})")


def _resolve_lang(
    client: str, cli_lang: str | None, root: Path | str = client_profile.DEFAULT_CLIENTS_ROOT,
) -> str:
    """CLI override > the client's stored profile.lang > the package default ("es").

    Mirrors scm_agent/packages.py::_load_profile's error-handling split: an
    unslugifiable client label legitimately means "no profile" (degrade to
    the default), but a CORRUPT profile.json is a real data-integrity problem
    and must fail loudly, not silently default the language and mask it.
    """
    if cli_lang is not None:
        return cli_lang
    try:
        slug = client_profile.slugify_client_id(client)
    except ValueError:
        return "es"
    profile = client_profile.load_profile(slug, root=root)
    return profile.lang if profile is not None else "es"


def _print_result(result) -> None:
    print(f"status: {result.status}")
    print(result.summary)
    if result.missing_inputs:
        print("\nFalta pedirle al cliente:")
        for line in result.missing_inputs:
            print(f"  - {line}")
    if result.qa_issues:
        print("\nQA issues:")
        for issue in result.qa_issues:
            print(f"  - {issue}")
    if result.steps:
        print("\nCobertura:")
        for s in result.steps:
            extra = f" <- {s.skip_reason}" if s.skip_reason else ""
            print(f"  [{s.status:>8}] {s.tool_key}{extra}")
    if result.deliverables:
        print("\nEntregables:")
        for name, path in sorted(result.deliverables.items()):
            print(f"  {name}: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Linchpin commercial package")
    parser.add_argument("--package", required=True, choices=sorted(PACKAGES))
    parser.add_argument("--intake", default=None, help="folder with the client's intake files")
    parser.add_argument("--client", default="Demo Client")
    parser.add_argument("--out", default="deliverables/paquetes")
    parser.add_argument("--params", default=None, help="JSON file with client parameters")
    parser.add_argument("--demo", action="store_true",
                        help="generate synthetic intake data and run on it")
    parser.add_argument("--checklist", action="store_true",
                        help="print the client intake checklist and exit")
    parser.add_argument("--strict-params", action="store_true")
    parser.add_argument("--fee-pct", type=float, default=None,
                        help="Sprint de Liquidacion: honorario contingente 0.10-0.20 "
                             "(default: el perfil del cliente, o 0.15)")
    parser.add_argument("--fee-floor", type=float, default=None,
                        help="Sprint de Liquidacion: piso del honorario (default 1500)")
    parser.add_argument("--measure", default=None,
                        help="Sprint de Liquidacion: CSV de ventas post-liquidacion "
                             "(product_id, quantity, price) -> anexo de cierre real-vs-estimado")
    parser.add_argument("--lang", choices=("es", "en"), default=None,
                        help="deck language (default: the client's profile, or 'es')")
    parser.add_argument("--verbose", action="store_true",
                        help="show INFO-level logs, incl. omitted-citation reasons "
                             "from scm_agent.citation_gate")
    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    spec = get_package(args.package)
    if args.checklist:
        print_checklist(spec)
        return

    spec = dataclasses.replace(spec, lang=_resolve_lang(args.client, args.lang))

    params: dict = {}
    if args.params:
        params.update(json.loads(Path(args.params).read_text(encoding="utf-8")))

    intake = args.intake
    if args.demo:
        intake = build_demo_intake(Path(args.out) / "demo_intake")
        params = {**DEMO_PARAMS, **params}
        print(f"[demo] intake sintetico en {intake}")
    elif intake is None:
        missing = missing_required_inputs(spec, None)
        print("Sin --intake ni --demo. Este paquete requiere:")
        for line in missing:
            print(f"  - {line}")
        return

    result = run_package(
        spec, intake, client=args.client, params=params, out_dir=args.out,
        strict_params=args.strict_params, prepared=date.today().isoformat(),
    )
    _print_result(result)
    if result.status == STATUS_OK:
        try:
            _write_liquidation_annexes(spec, result, args)
        except (ValueError, OSError) as exc:
            # The package's own deliverables (per-tool decks, consolidated
            # report) are already written by this point -- a bad --fee-pct,
            # an out-of-range --fee-floor, or a malformed --measure CSV must
            # not read as the whole run having failed, and must never surface
            # as a raw traceback after "status: ok" already printed.
            print(f"\n[honorarios] no se pudo escribir el anexo: {exc}")


if __name__ == "__main__":
    main()

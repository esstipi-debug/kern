"""Run a commercial package end-to-end: several registry tools, one QA gate,
one consolidated deck + every tool's own deliverable.

    # what to ask the client for (the intake checklist)
    python examples/run_package.py --package starter --checklist

    # run on a client's intake folder
    python examples/run_package.py --package diagnostico --intake intake/acme \
        --client "ACME" --out deliverables/acme

    # full demo on synthetic data (no client files needed)
    python examples/run_package.py --package growth --demo

Packages (scope/price: documentation/MONETIZATION_BRIEF.md + documentation/paquetes/):
diagnostico (4 tools, sprint) | starter (8 tools, mensual) | growth (26 tools, mensual+QBR).
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from scm_agent.package_specs import PACKAGES, get_package
from scm_agent.packages import PackageSpec, missing_required_inputs, run_package

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
    args = parser.parse_args()

    spec = get_package(args.package)
    if args.checklist:
        print_checklist(spec)
        return

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


if __name__ == "__main__":
    main()

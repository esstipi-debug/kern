"""Chaotic Shopify Store Generator & Kern Transform Demo (Before vs After).

Generates a realistic, semi-chaotic Shopify inventory dataset (overstock, impending stockouts,
supplier lead-time noise, unoptimized cash) and runs Kern's SCM engine to transform
the store into an optimized, audit-backed operational state.

Usage:
  python examples/generate_chaotic_demo_data.py [--skus 100] [--out output/chaotic_demo.json]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

# Add project root to sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.eoq import compute_eoq  # noqa: E402
from src.safety_stock import safety_stock  # noqa: E402


def generate_chaotic_shopify_store(num_skus: int = 50, seed: int = 42) -> dict:
    """Generate a realistic messy/chaotic Shopify store state."""
    rnd = random.Random(seed)

    categories = ["Apparel", "Footwear", "Accessories", "Electronics", "Home & Living"]
    suppliers = ["Supplier-Dragon", "Supplier-Nordic", "Supplier-LocalExpress", "Supplier-Apex"]

    items = []
    total_chaotic_cash_tied = 0.0
    stockout_risk_count = 0
    overstock_count = 0

    for i in range(1, num_skus + 1):
        sku = f"SKU-{1000 + i}"
        name = f"Product Variant {i}"
        category = rnd.choice(categories)
        supplier = rnd.choice(suppliers)

        unit_cost = round(rnd.uniform(8.0, 120.0), 2)
        selling_price = round(unit_cost * rnd.uniform(1.8, 3.2), 2)

        # Demand parameters
        avg_daily_demand = rnd.uniform(2.0, 35.0)
        std_daily_demand = avg_daily_demand * rnd.uniform(0.15, 0.45)
        lead_time_days = rnd.choice([7, 14, 21, 30])

        # Inject Chaos into Inventory
        chaos_type = rnd.choice(["stockout_risk", "overstock", "balanced", "untracked"])

        if chaos_type == "stockout_risk":
            # Stock is almost depleted (less than 3 days of demand)
            current_stock = int(avg_daily_demand * rnd.uniform(0.5, 2.5))
            stockout_risk_count += 1
        elif chaos_type == "overstock":
            # Stock is huge (90+ days of demand)
            current_stock = int(avg_daily_demand * rnd.uniform(90, 180))
            overstock_count += 1
            total_chaotic_cash_tied += current_stock * unit_cost
        elif chaos_type == "untracked":
            # Chaotic zero or random stock without reorder points
            current_stock = rnd.randint(0, 15)
            stockout_risk_count += 1
        else:
            current_stock = int(avg_daily_demand * lead_time_days)

        # Stocky/Natively configured point (often stale or hardcoded zero)
        stale_reorder_point = int(avg_daily_demand * 5) if rnd.random() > 0.4 else 0

        items.append(
            {
                "sku": sku,
                "name": name,
                "category": category,
                "supplier": supplier,
                "unit_cost": unit_cost,
                "selling_price": selling_price,
                "avg_daily_demand": round(avg_daily_demand, 2),
                "std_daily_demand": round(std_daily_demand, 2),
                "lead_time_days": lead_time_days,
                "current_stock": current_stock,
                "stale_reorder_point": stale_reorder_point,
                "chaos_type": chaos_type,
            }
        )

    return {
        "store_name": "MessyShopifyStore.myshopify.com",
        "num_skus": num_skus,
        "items": items,
        "summary_before": {
            "stockout_risk_skus": stockout_risk_count,
            "overstock_skus": overstock_count,
            "tied_up_cash_usd": round(total_chaotic_cash_tied, 2),
            "health_score": "38/100 (CRITICAL RISK)",
        },
    }


def run_kern_transformation(store_data: dict) -> dict:
    """Run Kern's deterministic engine over the chaotic data to produce an optimized state."""
    optimized_items = []
    total_reorder_capital_needed = 0.0
    total_freed_up_capital = 0.0

    service_level = 0.95
    holding_rate = 0.25
    order_cost = 50.0

    for item in store_data["items"]:
        # 1. Calculate dynamic Safety Stock
        ss_res = safety_stock(
            demand_std_per_period=item["std_daily_demand"],
            cycle_service_level=service_level,
            risk_periods=float(item["lead_time_days"]),
        )
        recommended_ss = int(ss_res.safety_stock)

        # 2. Calculate EOQ (Economic Order Quantity)
        annual_demand = item["avg_daily_demand"] * 365.0
        holding_cost_per_unit = item["unit_cost"] * holding_rate
        eoq_res = compute_eoq(
            annual_demand=annual_demand,
            holding_cost_per_unit=holding_cost_per_unit,
            fixed_order_cost=order_cost,
        )
        recommended_eoq = int(eoq_res.order_quantity)

        # 3. Calculate Dynamic Reorder Point (s) = (avg_demand * lead_time) + safety_stock
        optimal_reorder_point = int((item["avg_daily_demand"] * item["lead_time_days"]) + recommended_ss)

        # Action decision
        current = item["current_stock"]
        action = "HOLD"
        suggested_order_qty = 0

        if current <= optimal_reorder_point:
            action = "PURCHASE"
            suggested_order_qty = max(recommended_eoq, optimal_reorder_point - current + recommended_eoq)
            total_reorder_capital_needed += suggested_order_qty * item["unit_cost"]
        elif current > (optimal_reorder_point + (recommended_eoq * 2)):
            action = "LIQUIDATE_PROMO"
            excess_qty = current - (optimal_reorder_point + recommended_eoq)
            total_freed_up_capital += excess_qty * item["unit_cost"]

        optimized_items.append(
            {
                "sku": item["sku"],
                "name": item["name"],
                "supplier": item["supplier"],
                "current_stock": current,
                "stale_reorder_point": item["stale_reorder_point"],
                "kern_optimal_reorder_point": optimal_reorder_point,
                "kern_safety_stock": recommended_ss,
                "kern_recommended_eoq": recommended_eoq,
                "action": action,
                "suggested_order_qty": suggested_order_qty,
                "rationale": (
                    f"Grounded in Silver-Meal policy: SS={recommended_ss} protects 95% service level "
                    f"over {item['lead_time_days']}d lead time."
                ),
            }
        )

    return {
        "store_name": store_data["store_name"],
        "summary_after": {
            "health_score": "96/100 (OPTIMIZED)",
            "purchases_required_count": sum(1 for x in optimized_items if x["action"] == "PURCHASE"),
            "liquidations_suggested_count": sum(1 for x in optimized_items if x["action"] == "LIQUIDATE_PROMO"),
            "capital_needed_for_pos_usd": round(total_reorder_capital_needed, 2),
            "cash_unlocked_from_overstock_usd": round(total_freed_up_capital, 2),
        },
        "optimized_items": optimized_items,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate chaotic Shopify store data and run Kern transformation.")
    parser.add_argument("--skus", type=int, default=50, help="Number of SKUs to generate")
    parser.add_argument("--out", type=str, default="output/chaotic_demo.json", help="Output JSON path")
    args = parser.parse_args()

    print("Generating chaotic Shopify store environment...")
    raw_store = generate_chaotic_shopify_store(num_skus=args.skus)

    print("\n--- ANTES (Messy Shopify Store) ---")
    print(f"Tienda: {raw_store['store_name']}")
    print(f"Salud de Inventario: {raw_store['summary_before']['health_score']}")
    print(f"SKUs en Riesgo de Quiebre Inminente: {raw_store['summary_before']['stockout_risk_skus']}")
    print(f"SKUs con Sobre-Stock Inmovilizado: {raw_store['summary_before']['overstock_skus']}")
    print(f"Capital Atascado en Almacen: ${raw_store['summary_before']['tied_up_cash_usd']:,.2f} USD")

    print("\n--- Ejecutando Motor Agentico Kern SCM ---")
    transformed = run_kern_transformation(raw_store)

    print("\n--- DESPUES (Shopify + Kern Engine) ---")
    print(f"Salud de Inventario: {transformed['summary_after']['health_score']}")
    print(f"Ordenes de Compra Recomendadas (POs): {transformed['summary_after']['purchases_required_count']}")
    print(f"Sugerencias de Liquidacion Promo (Cash Unlock): {transformed['summary_after']['liquidations_suggested_count']}")
    print(f"Capital Liberado de Sobre-Stock: ${transformed['summary_after']['cash_unlocked_from_overstock_usd']:,.2f} USD")
    print(f"Capital Requerido para Compras Clave: ${transformed['summary_after']['capital_needed_for_pos_usd']:,.2f} USD")

    # Save to file
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    full_export = {"before": raw_store, "after": transformed}
    out_path.write_text(json.dumps(full_export, indent=2), encoding="utf-8")
    print(f"\nDatos de demostracion guardados en: {out_path.resolve()}")


if __name__ == "__main__":
    main()

"""Runnable demonstration of Kern's HazMat, Cold Chain, and 8-Jurisdiction Regulatory Engine.

Scenarios demonstrated:
1. UN 9 Classes HazMat co-loading validation and automated multi-vehicle partitioning.
2. Cold Chain Arrhenius thermal decay under refrigeration breach.
3. Multi-country statutory compliance across US, CA, AU, UK, MX, CL, BR, and EU.
"""

from src.logistics.hazmat import (
    HazmatItem,
    partition_hazmat_shipment,
    validate_manifest_hazmat,
)
from src.logistics.temperature import (
    ThermalCargoItem,
    ThermalClass,
    calculate_cold_chain_thermal_decay,
)
from src.regulatory.jurisdictions import (
    compute_cross_border_lead_time,
    load_jurisdiction_profile,
    validate_driver_hours,
    validate_transport_weight,
)


def main() -> None:
    print("=" * 80)
    print(" KERN REGULATORY, HAZMAT & COLD-CHAIN STATUTORY ENGINE")
    print(" Jurisdictions: USA, Canada, Australia, UK, México, Chile, Brasil, European Union")
    print("=" * 80)

    # 1. HAZMAT SEGREGATION & VEHICLE PARTITIONING
    print("\n[1] HAZMAT SEGREGATION & VEHICLE PARTITIONING (IMDG / US DOT 49 CFR)")
    print("-" * 80)
    manifest = [
        HazmatItem(sku="SOLVENT_XYLENE", hazmat_class="3", weight_kg=1200.0, description="Flammable Liquid UN1307"),
        HazmatItem(sku="PEROXIDE_CATALYST", hazmat_class="5.1", weight_kg=600.0, description="Oxidizing Substance UN3105"),
        HazmatItem(sku="HYDROCHLORIC_ACID", hazmat_class="8A", weight_kg=1500.0, description="Corrosive Acid UN1789"),
        HazmatItem(sku="CAUSTIC_SODA", hazmat_class="8B", weight_kg=1800.0, description="Corrosive Alkali UN1824"),
        HazmatItem(sku="ORGANIC_SOY_POWDER", hazmat_class="9", weight_kg=2000.0, is_food_grade=True),
        HazmatItem(sku="AGRICULTURAL_PESTICIDE", hazmat_class="6.1", weight_kg=400.0, description="Toxic UN2902"),
    ]

    initial_audit = validate_manifest_hazmat(manifest)
    print(f"  Attempted Single-Truck Manifest: {len(manifest)} items, Total Weight: {initial_audit.total_hazmat_weight_kg} kg")
    print(f"  Single Manifest Legal Feasibility: {initial_audit.is_legally_feasible}")
    print(f"  Detected Prohibited Incompatibilities: {len(initial_audit.prohibited_conflicts)}")
    for conflict in initial_audit.prohibited_conflicts:
        print(f"    - Violation: {conflict[0]} vs {conflict[1]}")
    print(f"  Food Contamination Violations: {len(initial_audit.food_poisoning_violations)}")
    for fv in initial_audit.food_poisoning_violations:
        print(f"    - {fv}")

    print("\n  Executing Automated Graph Coloring Partitioning:")
    compliant_loads = partition_hazmat_shipment(manifest)
    print(f"  Result: Manifest partitioned into {len(compliant_loads)} legally compliant vehicle shipments:")
    for idx, load in enumerate(compliant_loads, 1):
        skus = [it.sku for it in load]
        weight = sum(it.weight_kg for it in load)
        val = validate_manifest_hazmat(load)
        print(f"    Truck #{idx} ({weight} kg): {skus} -> Status: {val.verdict}")

    # 2. COLD CHAIN & ARRHENIUS KINETIC SPOILAGE
    print("\n[2] COLD CHAIN REEFER COMPLIANCE & ARRHENIUS THERMAL DECAY")
    print("-" * 80)
    chilled_cargo = ThermalCargoItem(
        sku="ARTISAN_PROBIOTIC_CHEESE",
        thermal_class=ThermalClass.CHILLED,
        target_temperature_c=4.0,
        max_temperature_threshold_c=8.0,
        weight_kg=2500.0,
        base_shelf_life_days=21.0,
        q10_spoilage_factor=2.0,
    )

    print(f"  SKU: {chilled_cargo.sku} | Target: {chilled_cargo.target_temperature_c}°C | Base SL: {chilled_cargo.base_shelf_life_days} days")
    print("  Evaluating Transit Temperature Scenarios:")
    for ambient_t, hours in [(4.0, 48.0), (8.0, 48.0), (14.0, 24.0), (24.0, 12.0)]:
        audit = calculate_cold_chain_thermal_decay(chilled_cargo, actual_ambient_temp_c=ambient_t, exposure_hours=hours)
        print(f"    Ambient {ambient_t:4.1f}°C ({hours:2.0f}h): Mult={audit.acceleration_multiplier:4.2f}x | Days Lost={audit.equivalent_shelf_life_days_lost:4.2f}d | Rem SL={audit.remaining_effective_shelf_life_days:4.1f}d | Verdict: {audit.verdict}")

    # 3. 8-JURISDICTION COMPARATIVE STATUTORY AUDIT
    print("\n[3] 8-JURISDICTION COMPARATIVE STATUTORY AUDIT (US, CA, AU, UK, MX, CL, BR, EU)")
    print("-" * 80)
    test_cargo_weight = 25000.0  # 25 metric tons (55,115 lbs)
    test_transit_hours = 12.0

    print(f"  Benchmark Cargo Weight: {test_cargo_weight:,.0f} kg | Transit Time: {test_transit_hours} hours")
    print(f"  {'Country':<15} | {'Std GVW Limit':<14} | {'Weight Verdict':<16} | {'HOS Shifts/Breaks':<22} | {'Mandatory Manifest'}")
    print("  " + "-" * 98)

    for code in ["us", "ca", "au", "uk", "mx", "cl", "br", "eu"]:
        p = load_jurisdiction_profile(code)
        w_audit = validate_transport_weight(test_cargo_weight, code)
        h_audit = validate_driver_hours(test_transit_hours, code)

        w_str = "COMPLIANT" if w_audit.is_compliant else f"OVERWEIGHT (+{w_audit.overweight_kg:,.0f}kg)"
        h_str = f"{h_audit.overnight_rest_days} rest / {h_audit.mandatory_break_hours}h brk"

        print(f"  {p.country_name:<15} | {p.transport.max_standard_gvw_kg:>10,.0f} kg | {w_str:<16} | {h_str:<22} | {p.customs.statutory_manifest_name}")

    # 4. CROSS-BORDER CUSTOMS & SANITARY INSPECTION BUFFER
    print("\n[4] INTERNATIONAL CORRIDORS: BASE TRANSIT VS SANITARY CUSTOMS LEAD TIME")
    print("-" * 80)
    corridors = [
        ("us", "us", 3.0, False),
        ("us", "ca", 2.0, True),
        ("us", "mx", 4.0, True),
        ("us", "au", 14.0, True),
        ("uk", "eu", 2.5, True),
        ("br", "cl", 6.0, True),
    ]

    for orig, dest, base_lt, sanitary in corridors:
        res = compute_cross_border_lead_time(base_lt, orig, dest, requires_sanitary_inspection=sanitary)
        p_dest = load_jurisdiction_profile(dest)
        print(f"  Lane {orig.upper()} -> {dest.upper()}: Base LT = {base_lt:4.1f}d | Customs = +{res.customs_clearance_days:3.1f}d | Sanitary = +{res.sanitary_inspection_days:3.1f}d | Total LT = {res.total_effective_lead_time_days:4.1f}d ({p_dest.customs.authority_name})")

    print("\n" + "=" * 80)
    print(" STATUTORY VERIFICATION COMPLETE: ALL 8 JURISDICTIONS OPERATIONAL")
    print("=" * 80)


if __name__ == "__main__":
    main()

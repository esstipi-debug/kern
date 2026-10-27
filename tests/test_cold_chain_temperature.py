"""Tests for src/logistics/temperature.py (Cold Chain & Arrhenius Thermal Degradation)."""


from src.logistics.temperature import (
    ReeferEquipment,
    ThermalCargoItem,
    ThermalClass,
    calculate_cold_chain_thermal_decay,
    normalize_thermal_class,
    validate_thermal_loading,
)


def test_thermal_class_normalization():
    assert normalize_thermal_class("CHILLED") == ThermalClass.CHILLED
    assert normalize_thermal_class("refrigerated") == ThermalClass.CHILLED
    assert normalize_thermal_class("FROZEN") == ThermalClass.FROZEN
    assert normalize_thermal_class("congelado") == ThermalClass.FROZEN
    assert normalize_thermal_class("ambient") == ThermalClass.DRY
    assert normalize_thermal_class("cool") == ThermalClass.COOL


def test_arrhenius_temperature_decay_normal_conditions():
    item = ThermalCargoItem(
        sku="ARTISAN_YOGURT",
        thermal_class=ThermalClass.CHILLED,
        target_temperature_c=4.0,
        max_temperature_threshold_c=8.0,
        weight_kg=100.0,
        base_shelf_life_days=30.0,
        q10_spoilage_factor=2.0,
    )

    # Maintained at 4°C for 24 hours -> 1 day consumed, 29 days remaining
    audit = calculate_cold_chain_thermal_decay(item, actual_ambient_temp_c=4.0, exposure_hours=24.0)
    assert audit.acceleration_multiplier == 1.0
    assert audit.is_safe_for_consumption
    assert audit.remaining_effective_shelf_life_days == 29.0
    assert audit.verdict == "IN_SPECIFICATION"


def test_arrhenius_temperature_decay_temperature_abuse():
    item = ThermalCargoItem(
        sku="ARTISAN_YOGURT",
        thermal_class=ThermalClass.CHILLED,
        target_temperature_c=4.0,
        max_temperature_threshold_c=8.0,
        weight_kg=100.0,
        base_shelf_life_days=30.0,
        q10_spoilage_factor=2.0,
    )

    # Exposed to 14°C (+10°C delta) for 24 hours. With Q10=2.0, spoilage rate is 2.0x!
    # Equivalent to 2.0 days consumed in 24 hours.
    audit = calculate_cold_chain_thermal_decay(item, actual_ambient_temp_c=14.0, exposure_hours=24.0)
    assert audit.acceleration_multiplier == 2.0
    assert audit.remaining_effective_shelf_life_days == 28.0
    assert audit.equivalent_shelf_life_days_lost == 1.0  # 1 extra day lost due to thermal abuse
    # Exposed to 14°C (> 8°C max threshold) for 24h -> Critical spoilage
    assert not audit.is_safe_for_consumption
    assert audit.verdict == "CRITICAL_SPOILAGE_DISPOSAL_REQUIRED"


def test_single_compartment_reefer_cannot_mix_chilled_and_frozen():
    items = [
        ThermalCargoItem(
            sku="FRESH_MILK",
            thermal_class=ThermalClass.CHILLED,
            target_temperature_c=4.0,
            max_temperature_threshold_c=6.0,
            weight_kg=500.0,
            base_shelf_life_days=14.0,
        ),
        ThermalCargoItem(
            sku="ICE_CREAM_TUB",
            thermal_class=ThermalClass.FROZEN,
            target_temperature_c=-20.0,
            max_temperature_threshold_c=-15.0,
            weight_kg=300.0,
            base_shelf_life_days=180.0,
        ),
    ]

    single_comp_truck = ReeferEquipment(
        equipment_id="REEFER_01",
        supported_thermal_classes=(ThermalClass.CHILLED, ThermalClass.FROZEN),
        multi_compartment=False,  # Single bulkhead
        max_payload_weight_kg=10000.0,
    )

    result = validate_thermal_loading(items, single_comp_truck)
    assert not result.is_compatible
    assert "CANNOT_MIX_MULTIPLE_THERMAL_REGIMES" in result.verdict


def test_multi_compartment_reefer_allows_chilled_and_frozen():
    items = [
        ThermalCargoItem(
            sku="FRESH_MILK",
            thermal_class=ThermalClass.CHILLED,
            target_temperature_c=4.0,
            max_temperature_threshold_c=6.0,
            weight_kg=500.0,
            base_shelf_life_days=14.0,
        ),
        ThermalCargoItem(
            sku="ICE_CREAM_TUB",
            thermal_class=ThermalClass.FROZEN,
            target_temperature_c=-20.0,
            max_temperature_threshold_c=-15.0,
            weight_kg=300.0,
            base_shelf_life_days=180.0,
        ),
    ]

    multi_comp_truck = ReeferEquipment(
        equipment_id="REEFER_DUAL_02",
        supported_thermal_classes=(ThermalClass.CHILLED, ThermalClass.FROZEN),
        multi_compartment=True,  # Dual evaporator / isolated bulkheads
        max_payload_weight_kg=10000.0,
    )

    result = validate_thermal_loading(items, multi_comp_truck)
    assert result.is_compatible
    assert result.verdict == "COMPLIANT"


def test_odor_contamination_in_single_compartment():
    items = [
        ThermalCargoItem(
            sku="FRESH_SEAFOOD_SALMON",
            thermal_class=ThermalClass.CHILLED,
            target_temperature_c=2.0,
            max_temperature_threshold_c=4.0,
            weight_kg=200.0,
            base_shelf_life_days=7.0,
            emits_strong_odors=True,
        ),
        ThermalCargoItem(
            sku="ORGANIC_BUTTER",
            thermal_class=ThermalClass.CHILLED,
            target_temperature_c=4.0,
            max_temperature_threshold_c=6.0,
            weight_kg=150.0,
            base_shelf_life_days=45.0,
            absorbs_odors=True,
        ),
    ]

    single_comp_truck = ReeferEquipment(
        equipment_id="REEFER_03",
        supported_thermal_classes=(ThermalClass.CHILLED,),
        multi_compartment=False,
    )

    result = validate_thermal_loading(items, single_comp_truck)
    assert not result.is_compatible
    assert len(result.odor_contamination_violations) >= 1
    assert "Odor contamination" in result.odor_contamination_violations[0]

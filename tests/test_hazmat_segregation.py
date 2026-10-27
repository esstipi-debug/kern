"""Tests for src/logistics/hazmat.py (UN 9 Classes Segregation & Mixed Loading)."""


from src.logistics.hazmat import (
    HazmatItem,
    SegregationLevel,
    check_hazmat_compatibility,
    normalize_hazmat_class,
    partition_hazmat_shipment,
    validate_manifest_hazmat,
)


def test_hazmat_class_normalization():
    assert normalize_hazmat_class("8a") == "8A"
    assert normalize_hazmat_class("acid") == "8A"
    assert normalize_hazmat_class("base") == "8B"
    assert normalize_hazmat_class("3") == "3"
    assert normalize_hazmat_class("5.1") == "5.1"
    assert normalize_hazmat_class("2.1") == "2.1"


def test_prohibited_flammable_liquids_and_oxidizers():
    # Class 3 (Flammables) + Class 5.1 (Oxidizers) -> Strictly PROHIBITED
    result = check_hazmat_compatibility("3", "5.1")
    assert result.segregation_level == SegregationLevel.PROHIBITED
    assert not result.is_allowed_on_same_vehicle
    assert "strictly prohibited" in result.reason.lower()


def test_prohibited_acid_and_base_neutralization():
    # Class 8A (Acids) + Class 8B (Alkalis) -> Strictly PROHIBITED
    result = check_hazmat_compatibility("8A", "8B")
    assert result.segregation_level == SegregationLevel.PROHIBITED
    assert not result.is_allowed_on_same_vehicle


def test_prohibited_explosives_with_flammable_liquids():
    # Class 1.1 (Mass Explosives) + Class 3 (Flammable Liquids) -> Strictly PROHIBITED
    result = check_hazmat_compatibility("1.1", "3")
    assert result.segregation_level == SegregationLevel.PROHIBITED
    assert not result.is_allowed_on_same_vehicle


def test_separated_compartment_for_flammables_and_corrosives():
    # Class 3 + Class 8A -> SEPARATE_COMPARTMENT (minimum 3m separation)
    result = check_hazmat_compatibility("3", "8A")
    assert result.segregation_level == SegregationLevel.SEPARATE_COMPARTMENT
    assert result.is_allowed_on_same_vehicle
    assert result.requires_separation_distance_m == 3.0


def test_compatible_same_class():
    # Two Class 3 items are compatible
    result = check_hazmat_compatibility("3", "3")
    assert result.segregation_level == SegregationLevel.COMPATIBLE
    assert result.is_allowed_on_same_vehicle
    assert result.requires_separation_distance_m == 0.0


def test_manifest_validation_detects_prohibited_and_food_violations():
    items = [
        HazmatItem(sku="GASOLINE_DRUM", hazmat_class="3", weight_kg=200.0, description="Gasoline UN1203"),
        HazmatItem(sku="HYDROGEN_PEROXIDE", hazmat_class="5.1", weight_kg=150.0, description="Oxidizer UN2014"),
        HazmatItem(sku="INFANT_FORMULA", hazmat_class="9", weight_kg=50.0, is_food_grade=True),
        HazmatItem(sku="PESTICIDE_TOXIC", hazmat_class="6.1", weight_kg=80.0, description="Toxic UN2902"),
    ]

    validation = validate_manifest_hazmat(items)
    assert not validation.is_legally_feasible
    assert validation.verdict == "ILLEGAL_HAZMAT_MANIFEST_CO_LOADING_PROHIBITED"
    # Should flag 3 + 5.1 conflict
    assert any("GASOLINE_DRUM" in pair[0] and "HYDROGEN_PEROXIDE" in pair[1] for pair in validation.prohibited_conflicts)
    # Should flag food contamination with Class 6.1
    assert len(validation.food_poisoning_violations) >= 1
    assert any("PESTICIDE_TOXIC" in v for v in validation.food_poisoning_violations)


def test_partition_hazmat_shipment_splits_into_legal_truckloads():
    # 4 mutually or partially incompatible items:
    # Item A: Class 3 (Flammable)
    # Item B: Class 5.1 (Oxidizer) - Incompatible with A
    # Item C: Class 8A (Acid) - Incompatible with B
    # Item D: Class 8B (Base) - Incompatible with B and C
    items = [
        HazmatItem(sku="SKU_FLAMMABLE", hazmat_class="3", weight_kg=500.0),
        HazmatItem(sku="SKU_OXIDIZER", hazmat_class="5.1", weight_kg=400.0),
        HazmatItem(sku="SKU_ACID", hazmat_class="8A", weight_kg=300.0),
        HazmatItem(sku="SKU_BASE", hazmat_class="8B", weight_kg=300.0),
    ]

    partitions = partition_hazmat_shipment(items)

    # Must be split into at least 2 or 3 separate vehicle shipments
    assert len(partitions) >= 2

    # Verify that in EVERY partitioned truckload, zero prohibited pairs exist
    for vehicle_load in partitions:
        val = validate_manifest_hazmat(vehicle_load)
        assert len(val.prohibited_conflicts) == 0, f"Vehicle load has prohibited conflicts: {val.prohibited_conflicts}"

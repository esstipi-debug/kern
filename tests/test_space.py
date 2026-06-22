"""Tests for warehouse space (m3) and COI slotting (capability M7)."""

import math

import pytest

from src.space import (
    SkuSlot,
    cube_per_order_index,
    required_space,
    sku_volume,
    slot_skus,
    warehouse_utilization,
)


def test_sku_volume():
    assert sku_volume(2.0, 1.0, 0.5) == pytest.approx(1.0)


def test_required_space_scales_with_units():
    assert required_space(unit_volume=1.0, target_units=100) == pytest.approx(100.0)


def test_cube_per_order_index():
    assert cube_per_order_index(required_space=100.0, pick_frequency=50.0) == pytest.approx(2.0)


def test_coi_with_no_picks_is_inf():
    assert math.isinf(cube_per_order_index(100.0, 0.0))


def test_warehouse_utilization():
    assert warehouse_utilization(used_volume=800.0, available_volume=1000.0) == pytest.approx(0.8)


def test_slot_skus_lowest_coi_goes_to_zone_a():
    skus = [
        {"product_id": "fast", "required_space": 10.0, "pick_frequency": 100.0},   # COI 0.1
        {"product_id": "mid", "required_space": 50.0, "pick_frequency": 50.0},      # COI 1.0
        {"product_id": "slow", "required_space": 100.0, "pick_frequency": 5.0},     # COI 20
    ]
    slots = slot_skus(skus, zone_cuts=(0.34, 0.67))
    by_id = {s.product_id: s for s in slots}
    assert isinstance(slots[0], SkuSlot)
    assert by_id["fast"].zone == "A"   # most accessible
    assert by_id["slow"].zone == "C"


def test_slot_skus_empty():
    assert slot_skus([]) == []

"""Tests for src/atp_allocation.py (Multi-Level BOM & Capacity Rationing)."""

import pytest
from src.atp_allocation import (
    BomGraph,
    Tier1Contract,
    quote_atp_order,
)


def test_bom_cycle_detection():
    """Verify that circular dependencies in BOM raise ValueError."""
    bom = BomGraph()
    bom.add_item("A", lead_time_days=2, is_assembly=True)
    bom.add_item("B", lead_time_days=3, is_assembly=True)
    bom.add_dependency("A", "B", quantity_per_parent=1.0)
    
    with pytest.raises(ValueError, match="Circular dependency"):
        bom.add_dependency("B", "A", quantity_per_parent=1.0)


def test_cumulative_lead_time_critical_path():
    """Verify critical path CLT computation across multi-level DAG.
    
    Structure:
    Assembly 'Kit_Final' (assembly LT = 2 days)
      ├── SubAssembly 'Sub_A' (assembly LT = 3 days)
      │     ├── Part 'Raw_1' (LT = 10 days)
      │     └── Part 'Raw_2' (LT = 15 days)  <-- Critical path for Sub_A (15 + 3 = 18)
      └── Part 'Raw_3' (LT = 12 days)
    
    CLT('Kit_Final') = 2 + max(18, 12) = 20 days.
    """
    bom = BomGraph()
    bom.add_item("Raw_1", lead_time_days=10)
    bom.add_item("Raw_2", lead_time_days=15)
    bom.add_item("Raw_3", lead_time_days=12)
    bom.add_item("Sub_A", lead_time_days=3, is_assembly=True)
    bom.add_item("Kit_Final", lead_time_days=2, is_assembly=True)

    bom.add_dependency("Sub_A", "Raw_1", 2.0)
    bom.add_dependency("Sub_A", "Raw_2", 1.0)
    bom.add_dependency("Kit_Final", "Sub_A", 1.0)
    bom.add_dependency("Kit_Final", "Raw_3", 4.0)

    assert bom.cumulative_lead_time("Raw_1") == 10
    assert bom.cumulative_lead_time("Raw_2") == 15
    assert bom.cumulative_lead_time("Sub_A") == 18  # 3 + 15
    assert bom.cumulative_lead_time("Kit_Final") == 20  # 2 + 18


def test_capacity_rationing_tier1_protection():
    """Ensure Tier-1 contractual stock is ring-fenced from spot orders."""
    bom = BomGraph()
    bom.add_item("SKU_PROD", lead_time_days=5)

    on_hand = {"SKU_PROD": 100.0}

    # Tier-1 contract commits 80 units
    contracts = [
        Tier1Contract(
            customer_id="TIER1_RETAIL_CORP",
            sku="SKU_PROD",
            committed_units=80.0,
            unit_price=50.0,
            penalty_per_unit=25.0,
        )
    ]

    # Spot request asks for 40 units (Total stock is 100, but only 20 is spot-available)
    quote = quote_atp_order(
        bom=bom,
        sku="SKU_PROD",
        requested_units=40.0,
        requested_lead_time_days=2,
        inventory_on_hand=on_hand,
        tier1_contracts=contracts,
    )

    assert quote.tier1_reserved_units == 80.0
    assert quote.immediate_available_units == 20.0
    assert quote.shortage_units == 20.0
    assert quote.recommendation == "SPLIT_SHIPMENT"
    assert quote.is_fulfilled_within_requested_time is False  # 2 days requested < 5 days CLT


def test_bottleneck_detection_in_pack():
    """Identify the exact component that limits immediate assembly.
    
    Pack requires:
      - 2 units of Comp_A (on hand: 100 -> can build 50)
      - 1 unit of Comp_B (on hand: 20 -> can build 20)  <-- Bottleneck!
    """
    bom = BomGraph()
    bom.add_item("Comp_A", lead_time_days=5)
    bom.add_item("Comp_B", lead_time_days=14)
    bom.add_item("Pack_X", lead_time_days=1, is_assembly=True)

    bom.add_dependency("Pack_X", "Comp_A", 2.0)
    bom.add_dependency("Pack_X", "Comp_B", 1.0)

    on_hand = {
        "Comp_A": 100.0,
        "Comp_B": 20.0,
    }

    quote = quote_atp_order(
        bom=bom,
        sku="Pack_X",
        requested_units=30.0,
        requested_lead_time_days=15,
        inventory_on_hand=on_hand,
    )

    assert quote.immediate_available_units == 20.0
    assert quote.shortage_units == 10.0
    assert quote.bottleneck_component == "Comp_B"
    assert quote.cumulative_lead_time_days == 15  # 1 + 14
    assert quote.is_fulfilled_within_requested_time is True  # 15 >= 15
    assert quote.recommendation == "SPLIT_SHIPMENT"


def test_atp_invariance_monotonicity():
    """Invariance test: Increasing order size never increases immediate availability."""
    bom = BomGraph()
    bom.add_item("Item_Z", lead_time_days=7)
    on_hand = {"Item_Z": 50.0}

    q1 = quote_atp_order(bom, "Item_Z", 30.0, 10, on_hand)
    q2 = quote_atp_order(bom, "Item_Z", 60.0, 10, on_hand)
    q3 = quote_atp_order(bom, "Item_Z", 100.0, 10, on_hand)

    # Immediate availability capped at 50
    assert q1.immediate_available_units == 30.0
    assert q2.immediate_available_units == 50.0
    assert q3.immediate_available_units == 50.0

    # Shortage strictly non-decreasing
    assert q1.shortage_units <= q2.shortage_units <= q3.shortage_units

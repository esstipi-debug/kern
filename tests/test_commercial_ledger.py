"""Tests for src/commercial_ledger.py (Double-Entry Ledger & Margin Conservation)."""

import pytest
from src.commercial_ledger import (
    CommercialLedger,
    OverrideTransaction,
    reconcile_with_cost_to_serve,
)
from src.cost_to_serve import SegmentActivity, ServiceCostRates


def test_double_entry_ledger_balancing():
    """Verify that every override transaction posts balanced debits and credits."""
    ledger = CommercialLedger()

    tx1 = OverrideTransaction(
        transaction_id="TX_001",
        timestamp_iso="2026-10-27T10:00:00Z",
        sku="SKU_PROMO_X",
        units=500.0,
        requester_cost_center="MKTG_FLASH_SALE",
        operational_credit_center="OPS_FREIGHT_EXPEDITE",
        standard_freight_cost=1000.0,
        expedited_freight_cost=3500.0,  # delta = 2500
        overtime_labor_cost=400.0,
        supplier_rush_fee=200.0,
        justification="Flash sale announced before container arrival",
    )

    ledger.record_override(tx1)

    assert tx1.net_surcharge == 3100.0  # 2500 + 400 + 200
    assert ledger.is_balanced() is True
    assert ledger.total_debits() == 3100.0
    assert ledger.total_credits() == 3100.0

    # Requester has a debit balance (+3100), operations has credit balance (-3100)
    assert ledger.get_cost_center_balance("MKTG_FLASH_SALE") == 3100.0
    assert ledger.get_cost_center_balance("OPS_FREIGHT_EXPEDITE") == -3100.0


def test_margin_conservation_reconciliation():
    """Verify that audited net margin reconciles down to zero penny error."""
    ledger = CommercialLedger()

    # Commercial override costing 1,500 in expedites
    tx = OverrideTransaction(
        transaction_id="TX_002",
        timestamp_iso="2026-10-27T11:00:00Z",
        sku="SKU_VIP_ORDER",
        units=200.0,
        requester_cost_center="ACCOUNT_RETAIL_KEY",
        operational_credit_center="OPS_EXPEDITE",
        standard_freight_cost=500.0,
        expedited_freight_cost=2000.0,  # surcharge = 1500
        overtime_labor_cost=0.0,
        supplier_rush_fee=0.0,
        justification="Client contract penalty avoidance",
    )
    ledger.record_override(tx)

    # Segment activity for ACCOUNT_RETAIL_KEY
    activity = SegmentActivity(
        segment="ACCOUNT_RETAIL_KEY",
        revenue=50000.0,
        units=2000.0,
        orders=20.0,
        cogs=30000.0,
        returns_units=10.0,
        outbound_freight=2000.0,
        overhead=1000.0,
    )

    rates = ServiceCostRates(
        cost_per_order=15.0,        # 20 * 15 = 300
        cost_per_unit_shipped=1.0,  # 2000 * 1 = 2000
        return_handling_per_unit=5.0 # 10 * 5 = 50
    )

    # Standard CTS:
    # Product: 30000
    # Fulfillment: 300 + 2000 + 2000 = 4300
    # Returns: 50
    # Overhead: 1000
    # Total CTS: 35350
    # Standard Net: 50000 - 35350 = 14650

    reconciled = reconcile_with_cost_to_serve(
        activity=activity,
        rates=rates,
        ledger=ledger,
        commercial_cost_center="ACCOUNT_RETAIL_KEY",
    )

    assert reconciled.standard_net_to_serve == 14650.0
    assert reconciled.total_commercial_surcharges == 1500.0
    assert reconciled.audited_net_to_serve == 13150.0  # 14650 - 1500
    assert reconciled.is_margin_conserved is True
    assert reconciled.reconciliation_error == 0.0


def test_invalid_override_rejected():
    """Verify that negative units or cheaper expedite than standard are rejected."""
    ledger = CommercialLedger()

    with pytest.raises(ValueError, match="units must be positive"):
        ledger.record_override(
            OverrideTransaction(
                transaction_id="TX_ERR",
                timestamp_iso="2026-10-27T12:00:00Z",
                sku="SKU_A",
                units=-10.0,
                requester_cost_center="CC_A",
                operational_credit_center="CC_B",
                standard_freight_cost=100.0,
                expedited_freight_cost=200.0,
            )
        )

    with pytest.raises(ValueError, match="Expedited freight cost cannot be less"):
        ledger.record_override(
            OverrideTransaction(
                transaction_id="TX_ERR2",
                timestamp_iso="2026-10-27T12:00:00Z",
                sku="SKU_A",
                units=10.0,
                requester_cost_center="CC_A",
                operational_credit_center="CC_B",
                standard_freight_cost=200.0,
                expedited_freight_cost=100.0,
            )
        )

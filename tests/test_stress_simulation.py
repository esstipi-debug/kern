"""Tests for src/stress_simulation.py (180-Day Counterfactual Stress Engine)."""

from src.stress_simulation import (
    StressSimulationConfig,
    run_stress_simulation,
)


def test_stress_simulation_determinism():
    """Verify that identical configurations and seeds yield bit-exact identical financial results."""
    cfg = StressSimulationConfig(horizon_days=90, random_seed=123)
    res1 = run_stress_simulation(cfg)
    res2 = run_stress_simulation(cfg)

    assert res1.ungoverned.gross_revenue == res2.ungoverned.gross_revenue
    assert res1.ungoverned.net_operating_profit == res2.ungoverned.net_operating_profit
    assert res1.governed.gross_revenue == res2.governed.gross_revenue
    assert res1.governed.net_operating_profit == res2.governed.net_operating_profit
    assert res1.profit_delta == res2.profit_delta


def test_governed_outperforms_ungoverned_financially():
    """Verify that Kern governance generates strictly superior net operating margin under volatility."""
    res = run_stress_simulation(StressSimulationConfig(horizon_days=180, random_seed=42))

    # Net operating profit under governance must exceed traditional ungoverned operation
    assert res.profit_delta > 0
    assert res.governed.net_operating_profit > res.ungoverned.net_operating_profit
    assert res.governed.operating_margin_pct > res.ungoverned.operating_margin_pct


def test_tier1_contract_sla_protection():
    """Verify that Capacity Rationing and ATP protect Tier-1 customers from penalties."""
    res = run_stress_simulation(StressSimulationConfig(horizon_days=180, random_seed=42))

    # Ungoverned incurs heavy contractual penalties because spot demand drains inventory
    assert res.ungoverned.tier1_penalties > 0
    assert res.governed.tier1_penalties == 0.0 or res.governed.tier1_penalties < res.ungoverned.tier1_penalties
    assert res.penalty_avoided > 0

    # Service level on Tier-1 must be higher in Governed
    assert res.governed.tier1_sla_service_level > res.ungoverned.tier1_sla_service_level


def test_push_radar_reduces_perishable_spoilage():
    """Verify that proactive Push Radar clearance markdown avoids perishable waste."""
    res = run_stress_simulation(StressSimulationConfig(horizon_days=180, random_seed=42))

    # Ungoverned suffers significant spoilage write-offs
    assert res.ungoverned.perishable_spoilage_units > 0
    assert res.ungoverned.perishable_spoilage_losses > 0

    # Governed eliminates or sharply reduces spoilage
    assert res.spoilage_saved > 0
    assert res.governed.perishable_spoilage_losses < res.ungoverned.perishable_spoilage_losses


def test_expedite_freight_governance():
    """Verify that unbudgeted panic rush freight in ungoverned exceeds LP-planned governed expedites."""
    res = run_stress_simulation(StressSimulationConfig(horizon_days=180, random_seed=42))

    # Ungoverned incurs additional rush handling fees on top of air freight
    assert res.ungoverned.expedite_freight_surcharges > res.governed.expedite_freight_surcharges
    assert res.freight_savings > 0

"""Tests for src/digital_twin.py - the multi-echelon network digital twin.

The twin generates synthetic-but-realistic network operations (demand, orders,
inventory, service) so the rest of the engine has complex scenarios to chew on.
These tests pin the mechanics: demand generation shapes, policy behavior,
disruption ripple effects, capacity limits, and topology validation.
"""

import numpy as np
import pytest

from src.digital_twin import (
    DemandProfile,
    Disruption,
    NodeSpec,
    generate_demand,
    simulate_network,
)


def _linear_network(
    *,
    order_up_to: float = 0.0,
    store_lead: int = 2,
    dc_lead: int = 3,
    capacity: float | None = None,
) -> list[NodeSpec]:
    return [
        NodeSpec(name="SUP", kind="supplier"),
        NodeSpec(name="DC", kind="dc", supplier="SUP", lead_time=dc_lead,
                 review_period=1, order_up_to=order_up_to * 2, capacity=capacity),
        NodeSpec(name="S1", kind="store", supplier="DC", lead_time=store_lead,
                 review_period=1, order_up_to=order_up_to, capacity=capacity),
        NodeSpec(name="S2", kind="store", supplier="DC", lead_time=store_lead,
                 review_period=1, order_up_to=order_up_to),
    ]


# -- demand generation ----------------------------------------------------------


def test_flat_profile_is_constant_without_noise():
    rng = np.random.default_rng(1)
    d = generate_demand(DemandProfile(base=50.0), 100, rng)
    assert d.shape == (100,)
    assert np.allclose(d, 50.0)


def test_trend_raises_later_demand():
    rng = np.random.default_rng(1)
    d = generate_demand(DemandProfile(base=100.0, trend=1.0), 200, rng)
    assert d[100:].mean() > d[:100].mean()


def test_seasonality_oscillates_around_base():
    rng = np.random.default_rng(1)
    d = generate_demand(
        DemandProfile(base=100.0, season_amplitude=0.5, season_period=52), 104, rng
    )
    assert d.max() > 130.0 and d.min() < 70.0
    assert abs(d.mean() - 100.0) < 5.0


def test_promotions_lift_promo_periods_only():
    rng = np.random.default_rng(1)
    profile = DemandProfile(base=100.0, promo_every=10, promo_length=2, promo_uplift=1.0)
    d = generate_demand(profile, 40, rng)
    assert d[0] == pytest.approx(200.0)   # promo window opens at each cycle start
    assert d[1] == pytest.approx(200.0)
    assert d[2] == pytest.approx(100.0)


def test_intermittency_zeroes_demand():
    rng = np.random.default_rng(1)
    d = generate_demand(DemandProfile(base=100.0, zero_prob=1.0), 50, rng)
    assert np.allclose(d, 0.0)


def test_demand_never_negative_under_noise():
    rng = np.random.default_rng(1)
    d = generate_demand(DemandProfile(base=5.0, noise_std=20.0), 500, rng)
    assert (d >= 0.0).all()


def test_invalid_profile_rejected():
    rng = np.random.default_rng(1)
    with pytest.raises(ValueError):
        generate_demand(DemandProfile(base=-1.0), 10, rng)
    with pytest.raises(ValueError):
        generate_demand(DemandProfile(zero_prob=1.5), 10, rng)


# -- network simulation ---------------------------------------------------------


def test_same_seed_reproduces_result():
    nodes = _linear_network()
    a = simulate_network(nodes, DemandProfile(base=20.0, noise_std=5.0), periods=200, seed=7)
    b = simulate_network(nodes, DemandProfile(base=20.0, noise_std=5.0), periods=200, seed=7)
    assert a.network_fill_rate == b.network_fill_rate
    assert np.array_equal(a.demand["S1"], b.demand["S1"])


def test_ample_stock_serves_everything():
    nodes = _linear_network(order_up_to=10_000.0)
    result = simulate_network(nodes, DemandProfile(base=20.0), periods=200, seed=7)
    assert result.network_fill_rate == pytest.approx(1.0)
    for stats in result.nodes:
        if stats.kind == "store":
            assert stats.fill_rate == pytest.approx(1.0)
            assert stats.stockout_periods == 0


def test_auto_sizing_reaches_reasonable_service():
    nodes = _linear_network()  # order_up_to=0 -> auto-sized from demand
    result = simulate_network(nodes, DemandProfile(base=20.0, noise_std=4.0), periods=400, seed=7)
    assert result.network_fill_rate > 0.80


def test_served_never_exceeds_demand():
    nodes = _linear_network()
    result = simulate_network(nodes, DemandProfile(base=20.0, noise_std=6.0), periods=300, seed=7)
    for store in ("S1", "S2"):
        assert (result.served[store] <= result.demand[store] + 1e-9).all()


def test_supplier_outage_degrades_service():
    nodes = _linear_network()
    demand = DemandProfile(base=20.0, noise_std=4.0)
    base = simulate_network(nodes, demand, periods=300, seed=7)
    hit = simulate_network(
        nodes, demand, periods=300, seed=7,
        disruptions=(Disruption(kind="supplier_outage", target="SUP", start=100, duration=60),),
    )
    assert hit.network_fill_rate < base.network_fill_rate


def test_lead_time_spike_degrades_service():
    nodes = _linear_network()
    demand = DemandProfile(base=20.0, noise_std=4.0)
    base = simulate_network(nodes, demand, periods=300, seed=7)
    hit = simulate_network(
        nodes, demand, periods=300, seed=7,
        disruptions=(Disruption(kind="lead_time_spike", target="S1", start=50,
                                duration=100, magnitude=6.0),),
    )
    s1_base = next(s for s in base.nodes if s.name == "S1")
    s1_hit = next(s for s in hit.nodes if s.name == "S1")
    assert s1_hit.fill_rate < s1_base.fill_rate


def test_demand_surge_raises_demand_in_window():
    nodes = _linear_network(order_up_to=10_000.0)
    result = simulate_network(
        nodes, DemandProfile(base=20.0), periods=100, seed=7,
        disruptions=(Disruption(kind="demand_surge", target="S1", start=40,
                                duration=10, magnitude=3.0),),
    )
    assert result.demand["S1"][40] == pytest.approx(60.0)
    assert result.demand["S1"][39] == pytest.approx(20.0)
    assert result.demand["S2"][40] == pytest.approx(20.0)


def test_capacity_caps_on_hand():
    nodes = _linear_network(order_up_to=10_000.0, capacity=150.0)
    result = simulate_network(nodes, DemandProfile(base=20.0), periods=200, seed=7)
    assert result.on_hand["S1"].max() <= 150.0 + 1e-9
    assert result.on_hand["DC"].max() <= 150.0 + 1e-9


def test_orders_are_recorded_for_review_periods():
    nodes = _linear_network()
    result = simulate_network(nodes, DemandProfile(base=20.0), periods=50, seed=7)
    assert len(result.orders["S1"]) > 0
    period, qty = result.orders["S1"][0]
    assert 0 <= period < 50 and qty > 0


# -- topology validation ----------------------------------------------------------


def test_unknown_supplier_rejected():
    nodes = [
        NodeSpec(name="SUP", kind="supplier"),
        NodeSpec(name="S1", kind="store", supplier="GHOST"),
    ]
    with pytest.raises(ValueError, match="GHOST"):
        simulate_network(nodes, DemandProfile(), periods=10)


def test_duplicate_names_rejected():
    nodes = [
        NodeSpec(name="SUP", kind="supplier"),
        NodeSpec(name="X", kind="dc", supplier="SUP"),
        NodeSpec(name="X", kind="store", supplier="SUP"),
    ]
    with pytest.raises(ValueError, match="duplicate"):
        simulate_network(nodes, DemandProfile(), periods=10)


def test_store_cannot_supply_anyone():
    nodes = [
        NodeSpec(name="SUP", kind="supplier"),
        NodeSpec(name="S1", kind="store", supplier="SUP"),
        NodeSpec(name="S2", kind="store", supplier="S1"),
    ]
    with pytest.raises(ValueError, match="store"):
        simulate_network(nodes, DemandProfile(), periods=10)


def test_supplier_with_upstream_rejected():
    nodes = [
        NodeSpec(name="A", kind="supplier"),
        NodeSpec(name="B", kind="supplier", supplier="A"),
        NodeSpec(name="S1", kind="store", supplier="B"),
    ]
    with pytest.raises(ValueError, match="supplier"):
        simulate_network(nodes, DemandProfile(), periods=10)


def test_no_stores_rejected():
    nodes = [
        NodeSpec(name="SUP", kind="supplier"),
        NodeSpec(name="DC", kind="dc", supplier="SUP"),
    ]
    with pytest.raises(ValueError, match="store"):
        simulate_network(nodes, DemandProfile(), periods=10)


def test_invalid_disruption_rejected():
    nodes = _linear_network()
    with pytest.raises(ValueError, match="kind"):
        simulate_network(
            nodes, DemandProfile(), periods=10,
            disruptions=(Disruption(kind="alien_invasion", target="SUP", start=0, duration=5),),
        )
    with pytest.raises(ValueError, match="target"):
        simulate_network(
            nodes, DemandProfile(), periods=10,
            disruptions=(Disruption(kind="supplier_outage", target="GHOST", start=0, duration=5),),
        )
    with pytest.raises(ValueError, match="demand_surge"):
        simulate_network(
            nodes, DemandProfile(), periods=10,
            disruptions=(Disruption(kind="demand_surge", target="DC", start=0, duration=5),),
        )

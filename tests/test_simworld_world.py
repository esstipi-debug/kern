import numpy as np
import pytest

from src.simworld.world import generate_world, initial_state


def test_same_seed_gives_identical_world():
    a = generate_world(seed=7, n_skus=10, n_suppliers=3, weeks=20, regime="seasonal_trend")
    b = generate_world(seed=7, n_skus=10, n_suppliers=3, weeks=20, regime="seasonal_trend")
    assert np.array_equal(a.demand, b.demand)
    assert np.array_equal(a.real_lead_weeks, b.real_lead_weeks)
    assert [s.sku for s in a.skus] == [s.sku for s in b.skus]


def test_different_seed_gives_different_world():
    a = generate_world(seed=7, n_skus=10, n_suppliers=3, weeks=20, regime="seasonal_trend")
    b = generate_world(seed=8, n_skus=10, n_suppliers=3, weeks=20, regime="seasonal_trend")
    assert not np.array_equal(a.demand, b.demand)


def test_demand_shape_and_non_negative():
    w = generate_world(seed=1, n_skus=6, n_suppliers=2, weeks=30, regime="seasonal_trend")
    assert w.demand.shape == (6, 30)
    assert (w.demand >= 0).all()


def test_promised_lead_time_differs_from_real_at_least_once():
    w = generate_world(seed=3, n_skus=12, n_suppliers=4, weeks=52, regime="seasonal_trend")
    promised = np.array([w.suppliers[s.supplier_idx].promised_lead_weeks for s in w.skus])
    assert (w.real_lead_weeks != promised[:, None]).any()


def test_unknown_regime_raises():
    with pytest.raises(ValueError, match="regime"):
        generate_world(seed=1, n_skus=2, n_suppliers=1, weeks=5, regime="nope")


def test_initial_state_is_week_zero_with_nothing_in_transit():
    w = generate_world(seed=1, n_skus=4, n_suppliers=2, weeks=10, regime="seasonal_trend")
    st = initial_state(w)
    assert st.week == 0
    assert st.in_transit == ()
    assert st.on_hand.shape == (4,)

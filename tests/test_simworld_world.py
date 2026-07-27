import numpy as np
import pytest

from src.simworld.world import advance, generate_world, initial_state


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


def _tiny():
    return generate_world(seed=11, n_skus=3, n_suppliers=1, weeks=12, regime="seasonal_trend")


def test_advance_conserves_mass():
    w = _tiny()
    st = initial_state(w)
    before = st.on_hand.copy()
    st2, out = advance(w, st, {w.skus[0].sku: 25.0})
    # lo que habia + lo recibido = lo vendido + lo que queda
    left = before + out.received
    right = out.sold + out.on_hand_end
    assert np.allclose(left, right)


def test_lost_sales_are_unmet_demand_never_negative():
    w = _tiny()
    st = initial_state(w)
    _, out = advance(w, st, {})
    assert np.allclose(out.lost, np.maximum(out.demand - out.sold, 0.0))
    assert (out.lost >= 0).all()


def test_order_arrives_after_the_real_lead_time_not_the_promised_one():
    w = _tiny()
    st = initial_state(w)
    sku0 = w.skus[0].sku
    lead = int(w.real_lead_weeks[0, 0])
    st, _ = advance(w, st, {sku0: 100.0})
    for _ in range(lead - 1):
        st, out = advance(w, st, {})
        assert out.received[0] == 0.0
    st, out = advance(w, st, {})
    assert out.received[0] == 100.0


def test_unknown_sku_in_orders_raises():
    w = _tiny()
    st = initial_state(w)
    with pytest.raises(KeyError, match="SKU-9999"):
        advance(w, st, {"SKU-9999": 5.0})


def test_negative_order_raises():
    w = _tiny()
    st = initial_state(w)
    with pytest.raises(ValueError, match="negativa"):
        advance(w, st, {w.skus[0].sku: -3.0})


def test_advance_past_the_horizon_raises():
    w = generate_world(seed=2, n_skus=2, n_suppliers=1, weeks=2, regime="seasonal_trend")
    st = initial_state(w)
    st, _ = advance(w, st, {})
    st, _ = advance(w, st, {})
    with pytest.raises(IndexError, match="horizonte"):
        advance(w, st, {})

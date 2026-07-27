import numpy as np
import pytest

from src.simworld.world import WorldState, advance, generate_world, initial_state


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
    st, out = advance(w, st, {w.skus[0].sku: 25.0})
    # lo que habia + lo recibido = lo vendido + lo que queda
    left = before + out.received
    right = out.sold + out.on_hand_end
    assert np.allclose(left, right)

    # La orden recien puesta todavia no llego -- received es 0 por construccion
    # en esta primera llamada, asi que el balance de arriba nunca ejercito el
    # camino de credito de llegada. Seguimos avanzando hasta que realmente
    # llegue (received > 0 para algun SKU) y re-verificamos el balance ahi.
    saw_arrival = False
    while st.week < w.weeks:
        before = st.on_hand.copy()
        st, out = advance(w, st, {})
        left = before + out.received
        right = out.sold + out.on_hand_end
        assert np.allclose(left, right)
        if out.received[0] > 0:
            saw_arrival = True
            break
    assert saw_arrival, "la orden nunca llego dentro del horizonte de la semilla de prueba"


def test_lost_sales_are_unmet_demand_never_negative():
    w = _tiny()
    st = initial_state(w)
    # initial_state siembra 4 semanas de demanda media en mano, asi que la
    # semana 0 real nunca puede quiebrar por construccion. Forzamos un
    # quiebre real con on_hand deliberadamente chico (WorldState es frozen:
    # construimos uno nuevo, no mutamos el de initial_state).
    stockout_state = WorldState(
        week=st.week,
        on_hand=np.zeros_like(st.on_hand),
        in_transit=st.in_transit,
    )
    next_state, out = advance(w, stockout_state, {})
    assert np.allclose(out.lost, np.maximum(out.demand - out.sold, 0.0))
    assert (out.lost >= 0).all()
    assert (out.lost > 0).any(), "el escenario debe forzar un quiebre real de al menos un SKU"

    # Pin del ledger de inventario: con on_hand=0 no llega nada, asi que
    # available=0 y sold=0. La forma CORRECTA (venta perdida) deja
    # on_hand_end = available - sold = 0. Una implementacion de backorder
    # (on_hand_end = available - demand) dejaria esto en -demand, negativo.
    # Sin esta asercion, ambas formas pasan igual de bien el resto de la
    # prueba porque ninguna otra asercion aqui lee on_hand_end.
    assert np.allclose(out.on_hand_end, 0.0), (
        "on_hand_end no debe quedar negativo: eso seria backorder, no venta perdida"
    )

    # La prueba de que es venta PERDIDA y no backorder: la demanda reportada
    # la semana siguiente debe ser exactamente la demanda cruda del mundo para
    # esa semana, sin inflarse con el faltante de la semana anterior.
    _, out2 = advance(w, next_state, {})
    assert np.array_equal(out2.demand, w.demand[:, next_state.week])


def test_order_arrives_after_the_real_lead_time_not_the_promised_one():
    w = _tiny()
    sku0 = w.skus[0].sku
    sup = w.suppliers[w.skus[0].supplier_idx]
    promised = sup.promised_lead_weeks
    real_row = w.real_lead_weeks[0]

    # Buscamos, en los datos (no hardcodeado), una semana t donde el lead real
    # diverge del prometido. Si el fixture alguna vez dejara de producir esa
    # divergencia, esta asercion falla ruidosamente en vez de dejar el resto
    # de la prueba vacuo.
    t = int(np.argmax(real_row > promised))
    real = int(real_row[t])
    assert real > promised, "la semilla de prueba debe divergir real vs prometido en algun t"
    assert t + real < w.weeks, (
        "la semana derivada t+real debe caer dentro del horizonte; si no, un "
        "cambio de fixture rompe esto con un IndexError de horizonte en vez "
        "de la falla real que se queria probar"
    )

    st = initial_state(w)
    for _ in range(t):
        st, _ = advance(w, st, {})

    st, out_order = advance(w, st, {sku0: 100.0})
    assert out_order.week == t

    # Avanzamos desde t+1 hasta t+real inclusive. En cada semana intermedia
    # -incluida t+promised, el lead PROMETIDO- no debe haber llegado nada.
    # Solo en t+real (el lead REAL) debe acreditarse la cantidad completa.
    received_at_promised = None
    for week_offset in range(1, real + 1):
        st, out = advance(w, st, {})
        if week_offset == promised:
            received_at_promised = out.received[0]
        if week_offset < real:
            assert out.received[0] == 0.0
        else:
            assert out.received[0] == 100.0

    assert received_at_promised == 0.0, "no debe llegar en el lead prometido, solo en el real"


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


def test_nan_order_raises():
    # nan < 0 es False, asi que una guarda escrita como `if qty < 0` deja
    # pasar un NaN silenciosamente y envenena on_hand para siempre desde su
    # semana de llegada. La guarda debe rechazar NaN igual que un negativo.
    # El mensaje debe decir "no finita", no "negativa" -- un NaN no es negativo
    # y el traceback no debe mentirle a quien lo lea.
    w = _tiny()
    st = initial_state(w)
    with pytest.raises(ValueError, match="finita"):
        advance(w, st, {w.skus[0].sku: float("nan")})


def test_inf_order_raises():
    # `not float("inf") >= 0` es False (inf >= 0 es True), asi que una guarda
    # escrita como `if not qty >= 0` deja pasar +inf silenciosamente: entra a
    # `ordered`, y al llegar deja on_hand y on_hand_end en +inf para siempre.
    # Mismo envenenamiento silencioso que el caso NaN.
    w = _tiny()
    st = initial_state(w)
    with pytest.raises(ValueError, match="finita"):
        advance(w, st, {w.skus[0].sku: float("inf")})


def test_outcome_on_hand_end_is_independent_from_next_state_on_hand():
    # Ambos dataclasses son frozen=True, pero frozen no vuelve inmutable un
    # campo numpy: si next_state.on_hand y outcome.on_hand_end son el MISMO
    # ndarray, un consumidor que mute uno corrompe el otro en silencio.
    w = _tiny()
    st = initial_state(w)
    next_state, out = advance(w, st, {})

    assert next_state.on_hand is not out.on_hand_end

    before_mutation = next_state.on_hand.copy()
    out.on_hand_end[:] = -999.0
    assert np.array_equal(next_state.on_hand, before_mutation)


def test_advance_past_the_horizon_raises():
    w = generate_world(seed=2, n_skus=2, n_suppliers=1, weeks=2, regime="seasonal_trend")
    st = initial_state(w)
    st, _ = advance(w, st, {})
    st, _ = advance(w, st, {})
    with pytest.raises(IndexError, match="horizonte"):
        advance(w, st, {})

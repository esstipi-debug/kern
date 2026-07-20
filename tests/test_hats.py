"""Substrate tests for the 4-hat decision engine (src/hats.py) -- spec 2026-07-20 D1-D8."""

import pytest

from src.hats import (
    DEFAULT_WEIGHTS,
    HAT_COMERCIAL,
    HAT_COMPRADOR,
    HAT_KEYS,
    HATS,
    SL_GRID,
    Candidate,
    HatConfig,
    HatInputs,
    parse_weights,
)

# -- HatConfig validation (D5) ------------------------------------------------


def test_config_defaults_match_repo_generics():
    cfg = HatConfig()
    assert cfg.order_cost == 75.0
    assert cfg.holding_rate == 0.25
    assert cfg.wacc == 0.12
    assert cfg.sl_target == 0.95
    assert cfg.gross_margin_rate == 0.30
    assert cfg.h_oop == pytest.approx(0.13)


def test_config_rejects_wacc_not_below_holding_rate():
    with pytest.raises(ValueError, match="wacc"):
        HatConfig(wacc=0.25)          # == h_total
    with pytest.raises(ValueError, match="wacc"):
        HatConfig(wacc=0.30)          # > h_total
    with pytest.raises(ValueError, match="wacc"):
        HatConfig(wacc=0.0)


def test_config_rejects_bad_scalars():
    with pytest.raises(ValueError):
        HatConfig(order_cost=0.0)
    with pytest.raises(ValueError):
        HatConfig(holding_rate=-0.1)
    with pytest.raises(ValueError):
        HatConfig(sl_target=1.0)
    with pytest.raises(ValueError):
        HatConfig(gross_margin_rate=1.0)
    with pytest.raises(ValueError):
        HatConfig(gross_margin_rate=-0.05)


# -- weights = explicit POLICY (D4) -------------------------------------------


def test_default_weights_are_equal_and_cover_all_hats():
    assert set(DEFAULT_WEIGHTS) == set(HAT_KEYS)
    assert all(w == pytest.approx(0.25) for w in DEFAULT_WEIGHTS.values())


def test_parse_weights_none_gives_default():
    assert parse_weights(None) == DEFAULT_WEIGHTS


def test_parse_weights_string_renormalizes():
    w = parse_weights("cfo=0.4,planner=0.3,comprador=0.2,comercial=0.1")
    assert w["cfo"] == pytest.approx(0.4)
    assert sum(w.values()) == pytest.approx(1.0)


def test_parse_weights_missing_keys_default_to_zero():
    w = parse_weights("cfo=2")
    assert w["cfo"] == pytest.approx(1.0)
    assert w[HAT_COMPRADOR] == 0.0 and w[HAT_COMERCIAL] == 0.0


@pytest.mark.parametrize("raw", [
    "cfo=-1",                                        # negative
    "cfo=0,planner=0,comprador=0,comercial=0",       # sum 0
    "gerente=1",                                     # unknown key
    "cfo=abc",                                       # malformed number
    "cfo",                                           # malformed pair
])
def test_parse_weights_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        parse_weights(raw)


def test_parse_weights_accepts_dict_and_renormalizes():
    assert parse_weights({"cfo": 1, "planner": 1})["cfo"] == pytest.approx(0.5)


# -- contracts ----------------------------------------------------------------


def test_hats_registry_has_the_four_hats_in_order():
    assert HAT_KEYS == ("comprador", "planner", "cfo", "comercial")
    assert set(HATS) == set(HAT_KEYS)
    for hat in HATS.values():
        assert hat.kpis and hat.objetivo and hat.label
        assert hat.mode_key in ("inventory", "scm", None)


def test_sl_grid_is_the_spec_grid():
    assert SL_GRID == (0.90, 0.925, 0.95, 0.975, 0.99)


def test_inputs_validate():
    cfg = HatConfig()
    with pytest.raises(ValueError):
        HatInputs(sku="A", annual_demand=0.0, mean_weekly=1.0, std_weekly=0.0,
                  lead_time_weeks=1.0, unit_cost=10.0, price_breaks=(),
                  price_breaks_assumed=False, config=cfg)
    with pytest.raises(ValueError):
        HatInputs(sku="A", annual_demand=52.0, mean_weekly=1.0, std_weekly=-1.0,
                  lead_time_weeks=1.0, unit_cost=10.0, price_breaks=(),
                  price_breaks_assumed=False, config=cfg)
    with pytest.raises(ValueError):
        Candidate(order_quantity=0.0, service_level=0.95)
    with pytest.raises(ValueError):
        Candidate(order_quantity=10.0, service_level=1.0)


# -- price breaks (D8) + grid (D1) --------------------------------------------

from src.eoq import PriceBreak, compute_eoq  # noqa: E402
from src.hats import (  # noqa: E402
    anchor_quantities,
    build_inputs,
    candidate_grid,
    ss_units,
    unit_cost_at,
)


def _inputs(**kw):
    """Representative smooth SKU: D=5200, mu_w=100, sigma_w=30, L=1w, c=10."""
    base = dict(sku="SKU-T", annual_demand=5200.0, mean_weekly=100.0, std_weekly=30.0,
                lead_time_weeks=1.0, unit_cost=10.0, config=HatConfig())
    base.update(kw)
    return build_inputs(**base)


def test_default_breaks_are_labeled_assumed_and_deterministic():
    inp = _inputs()
    assert inp.price_breaks_assumed is True
    q_eoq = compute_eoq(5200.0, 0.25 * 10.0, 75.0).order_quantity
    assert inp.price_breaks == (
        PriceBreak(0.0, 10.0),
        PriceBreak(2.0 * q_eoq, 9.8),
        PriceBreak(4.0 * q_eoq, 9.6),
    )


def test_injected_breaks_are_used_as_given_not_assumed():
    breaks = (PriceBreak(0.0, 10.0), PriceBreak(500.0, 9.0))
    inp = _inputs(price_breaks=breaks)
    assert inp.price_breaks_assumed is False
    assert inp.price_breaks == breaks


def test_unit_cost_at_is_piecewise_with_base_fallback():
    inp = _inputs(price_breaks=(PriceBreak(100.0, 9.5),))  # no base tier injected
    assert unit_cost_at(inp, 50.0) == 10.0        # below every tier -> base unit_cost
    assert unit_cost_at(inp, 100.0) == 9.5
    assert unit_cost_at(inp, 5000.0) == 9.5


def test_anchor_quantities_are_closed_form_and_precede_the_grid():
    inp = _inputs()
    q_eoq, q_disc = anchor_quantities(inp)
    assert q_eoq == pytest.approx(compute_eoq(5200.0, 2.5, 75.0).order_quantity)
    assert q_disc > q_eoq  # the -2%/-4% synthetic breaks make a bigger Q win


def test_grid_is_deterministic_ordered_and_contains_the_anchors():
    inp = _inputs()
    grid = candidate_grid(inp)
    assert grid == candidate_grid(inp)                       # bytes-identical rerun
    sls = [c.service_level for c in grid]
    assert sls == sorted(sls)                                # SL asc outer
    q_eoq, q_disc = anchor_quantities(inp)
    qs_at_95 = [c.order_quantity for c in grid if c.service_level == 0.95]
    assert qs_at_95 == sorted(qs_at_95)                      # Q asc inner
    for q in (q_eoq, q_disc):                                # mandatory candidates (+ baseline=q_eoq)
        assert any(abs(c.order_quantity - q) < 1e-12 for c in grid)
    assert len(grid) >= 125 and len(grid) % len(SL_GRID) == 0
    lo = 0.5 * min(q_eoq, q_disc)
    hi = 1.25 * max(q_eoq, q_disc)
    assert min(qs_at_95) == pytest.approx(lo) and max(qs_at_95) == pytest.approx(hi)


def test_ss_units_matches_engine_safety_stock():
    from src.safety_stock import safety_stock
    inp = _inputs()
    assert ss_units(inp, 0.95) == pytest.approx(
        safety_stock(30.0, 0.95, risk_periods=1.0).safety_stock)

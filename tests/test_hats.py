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

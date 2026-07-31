"""Cross-area impact measurement between two decision points (spec sec 6.2)."""

import pytest

from src.cross_impact import CrossImpact, ImpactRow, measure
from src.hats import (
    HAT_COMPRADOR,
    HAT_KEYS,
    Candidate,
    HatConfig,
    build_inputs,
    decision_cost,
    hat_kpis,
    headline_kpi,
)


@pytest.fixture
def inputs():
    return build_inputs(
        sku="SKU-1",
        annual_demand=5_200.0,
        mean_weekly=100.0,
        std_weekly=20.0,
        lead_time_weeks=2.0,
        unit_cost=10.0,
        config=HatConfig(),
    )


def test_one_row_per_hat_in_hat_keys_order(inputs):
    ci = measure(inputs, Candidate(200.0, 0.95), Candidate(400.0, 0.95), HAT_COMPRADOR)
    assert isinstance(ci, CrossImpact)
    assert tuple(r.hat_key for r in ci.rows) == HAT_KEYS
    assert all(isinstance(r, ImpactRow) for r in ci.rows)


def test_values_come_straight_from_hat_kpis(inputs):
    cur, prop = Candidate(200.0, 0.95), Candidate(400.0, 0.975)
    ci = measure(inputs, cur, prop, HAT_COMPRADOR)
    for row in ci.rows:
        name = headline_kpi(row.hat_key)
        assert row.kpi_name == name
        assert row.value_current == pytest.approx(hat_kpis(inputs, row.hat_key, cur)[name])
        assert row.value_proposed == pytest.approx(hat_kpis(inputs, row.hat_key, prop)[name])


def test_delta_is_raw_and_signed(inputs):
    cur, prop = Candidate(200.0, 0.95), Candidate(400.0, 0.95)
    ci = measure(inputs, cur, prop, HAT_COMPRADOR)
    for row in ci.rows:
        assert row.delta == pytest.approx(row.value_proposed - row.value_current)


def test_identical_points_give_zero_deltas(inputs):
    cand = Candidate(300.0, 0.95)
    ci = measure(inputs, cand, cand, HAT_COMPRADOR)
    assert all(r.delta == 0.0 for r in ci.rows)
    assert ci.judge_cost_current == pytest.approx(ci.judge_cost_proposed)


def test_judge_costs_match_decision_cost(inputs):
    cur, prop = Candidate(200.0, 0.95), Candidate(400.0, 0.99)
    ci = measure(inputs, cur, prop, HAT_COMPRADOR)
    assert ci.judge_cost_current == pytest.approx(decision_cost(inputs, cur))
    assert ci.judge_cost_proposed == pytest.approx(decision_cost(inputs, prop))


def test_unknown_proposer_raises(inputs):
    with pytest.raises(ValueError):
        measure(inputs, Candidate(200.0, 0.95), Candidate(400.0, 0.95), "almacen")


def test_sku_and_proposer_are_carried(inputs):
    ci = measure(inputs, Candidate(200.0, 0.95), Candidate(400.0, 0.95), HAT_COMPRADOR)
    assert ci.sku == "SKU-1"
    assert ci.proposed_by == HAT_COMPRADOR


def test_missing_kpi_propagates_keyerror_instead_of_filling_in(inputs, monkeypatch):
    # spec sec 10: measure() indexes hat_kpis(...)[name] with '[]', not .get(), so
    # a hat that stops carrying its headline KPI must blow up loudly rather than
    # silently being treated as a zero/None impact.
    import src.cross_impact as cross_impact_module

    def kpis_missing_headline(inputs, hat_key, cand):
        return {k: v for k, v in hat_kpis(inputs, hat_key, cand).items()
                if k != headline_kpi(hat_key)}

    monkeypatch.setattr(cross_impact_module, "hat_kpis", kpis_missing_headline)
    with pytest.raises(KeyError):
        measure(inputs, Candidate(200.0, 0.95), Candidate(400.0, 0.95), HAT_COMPRADOR)

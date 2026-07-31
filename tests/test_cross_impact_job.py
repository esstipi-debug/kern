"""prepare()'s validation contract (spec sec 5.1). Every gap is fatal."""

import pandas as pd
import pytest

from jobs import cross_impact_job
from src.hats import HAT_COMPRADOR, Candidate


@pytest.fixture
def demand_csv(tmp_path):
    path = tmp_path / "demanda.csv"
    pd.DataFrame({
        "product_id": ["SKU-1"] * 30 + ["SKU-2"] * 30,
        "quantity": ([100, 110, 95, 105, 120] * 6)[:30] * 2,
        "unit_cost": [10.0] * 60,
        "lead_time_days": [14.0] * 60,
    }).to_csv(path, index=False)
    return str(path)


def _params(**over):
    base = {
        "sku": "SKU-1",
        "current": {"q": 200.0, "sl": 0.95},
        "proposed": {"q": 400.0, "sl": 0.95},
        "proposed_by": HAT_COMPRADOR,
        "objection_thresholds": {"comprador": 0.2, "planner": 5_000.0,
                                 "cfo": 3_000.0, "comercial": 0.03},
    }
    base.update(over)
    return base


def test_happy_path_builds_the_payload(demand_csv):
    p = cross_impact_job.prepare(demand_csv, _params())
    assert p["inputs"].sku == "SKU-1"
    assert p["current"] == Candidate(200.0, 0.95)
    assert p["proposed"] == Candidate(400.0, 0.95)
    assert p["proposed_by"] == HAT_COMPRADOR
    assert p["thresholds"]["cfo"] == 3_000.0


@pytest.mark.parametrize("missing", ["sku", "current", "proposed", "proposed_by"])
def test_each_required_param_is_fatal_and_named(demand_csv, missing):
    params = _params()
    del params[missing]
    with pytest.raises(ValueError, match=missing):
        cross_impact_job.prepare(demand_csv, params)


def test_missing_current_does_not_fall_back_to_baseline(demand_csv):
    params = _params()
    del params["current"]
    with pytest.raises(ValueError) as exc:
        cross_impact_job.prepare(demand_csv, params)
    msg = str(exc.value).lower()
    assert "baseline" not in msg          # never silently assumes the theoretical (s,Q)
    assert "current" in msg


def test_unknown_proposer_is_fatal(demand_csv):
    with pytest.raises(ValueError, match="proposed_by"):
        cross_impact_job.prepare(demand_csv, _params(proposed_by="almacen"))


@pytest.mark.parametrize("bad", [{"q": 0.0, "sl": 0.95}, {"q": 200.0, "sl": 1.0},
                                 {"q": 200.0, "sl": 0.0}, {"q": -5.0, "sl": 0.95}])
def test_out_of_range_points_are_fatal(demand_csv, bad):
    with pytest.raises(ValueError):
        cross_impact_job.prepare(demand_csv, _params(proposed=bad))


def test_incomplete_point_is_fatal(demand_csv):
    with pytest.raises(ValueError, match="proposed"):
        cross_impact_job.prepare(demand_csv, _params(proposed={"q": 400.0}))


def test_identical_points_are_fatal(demand_csv):
    with pytest.raises(ValueError, match="cambio"):
        cross_impact_job.prepare(demand_csv, _params(proposed={"q": 200.0, "sl": 0.95}))


def test_sku_not_in_csv_is_fatal(demand_csv):
    with pytest.raises(ValueError):
        cross_impact_job.prepare(demand_csv, _params(sku="SKU-404"))


def test_absent_thresholds_are_not_fatal(demand_csv):
    # honesty does not depend on strict_params: the run proceeds and the
    # verdict comes out indeterminado (Task 5).
    params = _params()
    del params["objection_thresholds"]
    p = cross_impact_job.prepare(demand_csv, params)
    assert p["thresholds"] == {}


def test_partial_thresholds_survive(demand_csv):
    p = cross_impact_job.prepare(demand_csv, _params(objection_thresholds={"cfo": 1.0}))
    assert p["thresholds"] == {"cfo": 1.0}


def test_bad_threshold_key_is_fatal(demand_csv):
    with pytest.raises(ValueError, match="objection_thresholds"):
        cross_impact_job.prepare(demand_csv, _params(objection_thresholds={"almacen": 1.0}))

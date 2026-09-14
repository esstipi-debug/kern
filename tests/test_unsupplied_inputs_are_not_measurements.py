"""A default nobody supplied is not a measurement, and a rate over nothing is not zero.

Two follow-ups an adversarial review confirmed against the earlier commits:

- ``financial_kpis`` defaulted ``dso``/``dpo`` to ``0.0``. Since ``_measured(0.0)`` is True,
  ``cash_to_cash`` happily returned ``DIO + 0 - 0 == DIO`` and the deck printed
  "Cash-to-cash 61 days (DIO 61 + DSO 0 - DPO 0)" as a KPI. DSO and DPO come from the AR/AP
  ledgers, which the inventory data never contains, so that figure was always a fabrication
  wearing the shape of a measurement.
- ``landed_cost`` reported ``per_unit = 0.00`` for a genuinely zero-quantity line while
  ``total`` carried real freight and duty. The finiteness guard added earlier only closed
  the NaN door; zero units is not a free import, it is no per-unit cost at all.
"""

import pandas as pd
import pytest

from jobs import financial_kpis_job as fkj
from jobs import landed_cost_job as lcj
from src.deliverable import not_measured
from src.landed_cost import landed_cost

_ROWS = pd.DataFrame({
    "product_id": ["A", "B"],
    "cogs": [1000.0, 2000.0],
    "avg_inventory_value": [250.0, 500.0],
    "gross_margin": [400.0, 700.0],
    "units_sold": [100.0, 200.0],
    "units_on_hand": [25.0, 50.0],
    "net_sales": [1400.0, 2700.0],
})


# ---- DSO / DPO nobody supplied ----------------------------------------------

def test_an_unsupplied_dso_and_dpo_leave_cash_to_cash_unmeasurable():
    report = fkj.run(fkj.prepare_records(_ROWS))

    assert report.dso is None
    assert report.dpo is None
    assert report.cash_to_cash is None


def test_cash_to_cash_is_measured_once_the_controller_supplies_the_legs():
    report = fkj.run(fkj.prepare_records(_ROWS), dso=45.0, dpo=30.0)

    assert report.cash_to_cash == pytest.approx(report.dio + 45.0 - 30.0)


def test_a_genuinely_zero_dso_is_still_a_measurement():
    """Cash on delivery is a real DSO of 0. Only the *default* was ever the lie."""
    report = fkj.run(fkj.prepare_records(_ROWS), dso=0.0, dpo=0.0)

    assert report.dso == pytest.approx(0.0)
    assert report.cash_to_cash == pytest.approx(report.dio)


def test_the_deck_does_not_print_a_cash_to_cash_nobody_could_compute():
    report = fkj.run(fkj.prepare_records(_ROWS))

    md = fkj.build_deck(report, client="Acme").to_markdown()

    assert "DSO 0" not in md
    assert "DPO 0" not in md
    assert not_measured() in md


def test_the_agent_surface_survives_an_unsupplied_cash_to_cash(tmp_path):
    from nosilo_core import llm
    from scm_agent import tools
    from scm_agent.orchestrator import Orchestrator

    csv = tmp_path / "fin.csv"
    _ROWS.to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("inventory financial KPIs: turns, DIO, GMROI, cash-to-cash",
                   data_path=str(csv), client="Acme", out_dir=tmp_path)

    assert res.status == "ok", res.summary


def test_the_tool_still_honours_dso_and_dpo_passed_as_params(tmp_path):
    from nosilo_core import llm
    from scm_agent import tools
    from scm_agent.orchestrator import Orchestrator

    csv = tmp_path / "fin.csv"
    _ROWS.to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("inventory financial KPIs: turns, DIO, GMROI, cash-to-cash",
                   data_path=str(csv), client="Acme", out_dir=tmp_path,
                   overrides={"dso": 45.0, "dpo": 30.0})

    assert res.status == "ok"
    assert not_measured() not in res.summary


# ---- a zero-quantity shipment has no per-unit cost ---------------------------

def test_a_zero_quantity_line_has_no_per_unit_cost_rather_than_a_free_one():
    result = landed_cost(unit_cost=10.0, qty=0.0, freight=500.0, duty_rate=0.05)

    assert result.total == pytest.approx(500.0)
    assert result.per_unit is None


def test_a_real_quantity_still_costs_what_it_costs():
    result = landed_cost(unit_cost=10.0, qty=100.0, freight=500.0)

    assert result.per_unit == pytest.approx(15.0)


def test_the_landed_cost_csv_leaves_an_uncomputable_per_unit_blank(tmp_path):
    csv = tmp_path / "shipments.csv"
    pd.DataFrame({
        "sku": ["A", "B"],
        "unit_cost": [10.0, 20.0],
        "qty": [100.0, 0.0],          # B: a shipment line with no units
        "freight": [500.0, 300.0],
    }).to_csv(csv, index=False)
    report = lcj.run(lcj.prepare(str(csv), {}))

    paths = lcj.write_operational(report, tmp_path)
    rows = pd.read_csv(paths["csv"], keep_default_na=False).set_index("sku")

    assert rows.loc["B", "per_unit_landed"] == ""
    assert float(rows.loc["A", "per_unit_landed"]) == pytest.approx(15.0)


def test_the_landed_cost_deck_renders_an_uncomputable_per_unit_as_not_measured(tmp_path):
    csv = tmp_path / "shipments.csv"
    pd.DataFrame({
        "sku": ["A"],
        "unit_cost": [10.0],
        "qty": [0.0],
        "freight": [5000.0],
    }).to_csv(csv, index=False)
    report = lcj.run(lcj.prepare(str(csv), {}))

    md = lcj.build_deck(report, client="Acme").to_markdown()

    assert "0.00/unit" not in md
    assert not_measured() in md

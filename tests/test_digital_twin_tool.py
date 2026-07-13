"""Tests for the digital twin agent tool (jobs/digital_twin_job.py + registration).

The twin is the engine's scenario factory: params -> a simulated supplier->DC->
store network -> CSV datasets shaped like a client export, so the OTHER tools
can be exercised on complex, known-ground-truth scenarios. The integration test
below is the point: the twin's demand CSV must feed forecast_job unchanged.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import digital_twin_job as dt
from jobs import forecast_job
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS

_FAST = {"periods": 60, "n_products": 2, "n_stores": 2}


# -- prepare ----------------------------------------------------------------------


def test_prepare_defaults_build_a_network():
    payload = dt.prepare({})
    kinds = [n["kind"] for n in payload["nodes"]]
    assert kinds.count("supplier") == 1
    assert kinds.count("dc") == 1
    assert kinds.count("store") == 3
    assert payload["periods"] == 364
    assert len(payload["products"]) == 5


def test_prepare_respects_params():
    payload = dt.prepare({"n_stores": 5, "n_dcs": 2, "n_products": 3, "periods": 100,
                          "base_demand": 40.0, "seed": 9})
    kinds = [n["kind"] for n in payload["nodes"]]
    assert kinds.count("dc") == 2 and kinds.count("store") == 5
    assert payload["periods"] == 100
    bases = [p["base"] for p in payload["products"]]
    assert len(set(bases)) > 1  # products get distinct demand levels


def test_prepare_rejects_bad_params():
    with pytest.raises(ValueError):
        dt.prepare({"n_stores": 0})
    with pytest.raises(ValueError):
        dt.prepare({"periods": 5})
    with pytest.raises(ValueError):
        dt.prepare({"n_products": 200, "n_stores": 20, "periods": 5000})  # too big
    with pytest.raises(ValueError):
        dt.prepare({"disruption": "alien_invasion"})


def test_prepare_wires_disruption():
    payload = dt.prepare({"disruption": "supplier_outage", **_FAST})
    assert len(payload["disruptions"]) == 1
    d = payload["disruptions"][0]
    assert d["kind"] == "supplier_outage" and d["target"] == "SUPPLIER-1"
    assert 0 < d["start"] < 60


# -- run + verify -------------------------------------------------------------------


def test_run_produces_report_and_passes_qa():
    report = dt.run(dt.prepare(_FAST))
    assert report.n_products == 2
    assert report.periods == 60
    assert 0.0 <= report.network_fill_rate <= 1.0
    assert report.weakest_store_fill <= report.network_fill_rate + 1e-9
    assert dt.verify(report) == []
    assert "network" in report.summary.lower() or "fill" in report.summary.lower()


def test_disruption_degrades_fill_vs_baseline():
    base = dt.run(dt.prepare(_FAST))
    hit = dt.run(dt.prepare({**_FAST, "disruption": "supplier_outage",
                             "disruption_duration": 20}))
    assert hit.network_fill_rate < base.network_fill_rate


def test_run_is_deterministic():
    a = dt.run(dt.prepare({**_FAST, "seed": 5}))
    b = dt.run(dt.prepare({**_FAST, "seed": 5}))
    assert a.network_fill_rate == b.network_fill_rate


# -- deliverables ------------------------------------------------------------------


def test_write_operational_emits_dataset_csvs(tmp_path):
    report = dt.run(dt.prepare(_FAST))
    paths = dt.write_operational(report, tmp_path)
    for key in ("demand_history", "inventory", "orders", "node_kpis"):
        assert key in paths and Path(paths[key]).exists()

    demand = pd.read_csv(paths["demand_history"])
    assert list(demand.columns) == ["date", "product_id", "location", "units"]
    assert not demand.isna().any().any()
    assert len(demand) == 60 * 2 * 2  # periods * stores * products
    assert (demand["units"] >= 0).all()

    kpis = pd.read_csv(paths["node_kpis"])
    assert {"location", "kind", "fill_rate"} <= set(kpis.columns)


def test_twin_demand_feeds_the_forecast_tool(tmp_path):
    """The whole point: generated data must flow into the analysis suite unchanged."""
    report = dt.run(dt.prepare(_FAST))
    paths = dt.write_operational(report, tmp_path)
    payload = forecast_job.prepare(str(paths["demand_history"]), {})
    assert payload  # forecast intake accepted the twin's dataset without adaptation


def test_build_deck_is_ascii_deliverable():
    report = dt.run(dt.prepare(_FAST))
    deck = dt.build_deck(report, client="Acme", citations=("Vandeput (2020) ch.5",),
                         confidence=0.8)
    assert isinstance(deck, Deliverable)
    text = deck.summary + "".join(f.title + f.detail for f in deck.findings)
    assert text.isascii()


# -- registration + routing ---------------------------------------------------------


def test_registry_exposes_digital_twin():
    reg = tools.build_default_registry()
    tool = reg.get("digital_twin")
    assert tool.requires_data is False
    assert "digital twin" in tool.intent_keywords


def test_intent_routes_twin_briefs_and_leaves_simulation_alone():
    reg = tools.build_default_registry()
    provider = llm.RulesFallback()
    twin = intent.classify("arma un digital twin de mi red de suministro con disrupciones",
                           reg, provider)
    assert twin.job_type == "digital_twin"
    sim = intent.classify("monte carlo simulation optimization of the inventory policy",
                          reg, provider)
    assert sim.job_type == "simulation"


def test_orchestrator_runs_twin_end_to_end(tmp_path):
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run("necesito un gemelo digital de la red para generar escenarios",
                   overrides=dict(_FAST), client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "digital_twin"
    assert Path(res.deliverables["demand_history"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1

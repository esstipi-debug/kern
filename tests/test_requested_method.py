"""A brief that names EOQ gets told which SKUs did not get one, and why.

The defect: ``inventory_optimization`` already puts intermittent SKUs on a periodic
(R, S) policy with no EOQ quantity -- the right answer to the problem behind the
request. But a brief that said "calculate the EOQ" got a summary that never
mentioned it, so the requester got no EOQ quantity for part of the catalog and no
explanation.
These tests pin the sentence, its silence, and that it moves no number.
"""
from __future__ import annotations

import pandas as pd
import pytest

from nosilo_core import llm
from nosilo_core.types import JobRequest
from scm_agent import intent, tools
from scm_agent.orchestrator import Orchestrator
from src.forecasting import INTERMITTENT_ADI_THRESHOLD
from src.requested_method import MAX_NAMED, EoqReframe, eoq_reframe, names_eoq


@pytest.mark.parametrize("brief", [
    "calculate the EOQ for my SKUs",
    "calcula el eoq de mis productos",
    "what is the Economic Order Quantity per item",
    "calcula el lote económico de compra",
    "Lote Economico por SKU",
    "necesito la cantidad económica de pedido",
    "aplica la fórmula de Wilson a los repuestos",
    "run the Wilson formula",
])
def test_names_eoq_recognises_the_method_by_name(brief):
    assert names_eoq(brief)


@pytest.mark.parametrize("brief", [
    "set up reorder points and safety stock",
    # These name the PROBLEM, not the method: nothing to reframe.
    "what order quantity should I buy",
    "cuanto pedir de cada repuesto",
    # Substrings and the surname are not the method.
    "geoquality audit of the warehouse",
    "Wilson Supply is our main vendor",
    "",
])
def test_names_eoq_ignores_briefs_that_do_not_name_the_method(brief):
    assert not names_eoq(brief)


def test_no_reframe_when_every_priced_sku_got_eoq():
    assert eoq_reframe([("A", False), ("B", False)]) is None


def test_no_reframe_on_an_empty_portfolio():
    assert eoq_reframe([]) is None


def test_reframe_counts_the_intermittent_skus_in_order():
    reframe = eoq_reframe([("A", False), ("P-2", True), ("B", False), ("P-1", True)])

    assert reframe == EoqReframe(n_priced=4, intermittent=("P-2", "P-1"))
    assert reframe.adi_threshold == INTERMITTENT_ADI_THRESHOLD


def test_sentence_names_the_skus_the_threshold_and_the_policy_used_instead():
    sentence = eoq_reframe([("A", False), ("P-2", True), ("P-1", True)]).sentence()

    assert sentence.startswith("You asked for EOQ: 2 of 3 SKUs have intermittent demand")
    assert f"ADI >= {INTERMITTENT_ADI_THRESHOLD:g}" in sentence
    assert "(R, S)" in sentence and "no EOQ quantity" in sentence
    assert sentence.endswith(": P-2, P-1.")


def test_sentence_is_singular_for_one_sku():
    sentence = eoq_reframe([("A", False), ("P-1", True)]).sentence()

    assert "1 of 2 SKUs has intermittent demand" in sentence
    assert "so it got a periodic" in sentence


def test_sentence_caps_the_named_ids():
    ids = [f"P-{i}" for i in range(MAX_NAMED + 2)]
    sentence = eoq_reframe([(pid, True) for pid in ids]).sentence()

    assert "P-0" in sentence and f"P-{MAX_NAMED - 1}" in sentence
    assert f"P-{MAX_NAMED}," not in sentence
    assert sentence.endswith("(+2 more).")


def test_sentence_is_ascii():
    # It rides in the run summary, which run_agent.py prints to a cp1252 console.
    eoq_reframe([("P-1", True)]).sentence().encode("ascii")


# ---- wired into inventory_optimization -----------------------------------------

def _mixed_csv(tmp_path):
    """One smooth SKU sold every week, one spare part sold one week in four."""
    rows = []
    for i, d in enumerate(pd.date_range("2024-01-07", periods=52, freq="W")):
        rows.append({"date": d, "product_id": "SMOOTH-1", "quantity": [10, 12, 9, 11][i % 4],
                     "unit_cost": 5.0})
        if i % 4 == 0:
            rows.append({"date": d, "product_id": "SPARE-9", "quantity": [3, 5, 2][i % 3],
                         "unit_cost": 40.0})
    path = tmp_path / "mixed.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def _run_tool(brief, data_path):
    tool = tools.inventory_tool()
    prepared = tool.prepare(JobRequest(brief=brief, data_path=data_path), llm.RulesFallback())
    assert prepared.status == "ok"
    return tool.run(prepared.payload, {})


def test_an_eoq_brief_is_told_which_skus_got_r_s_instead(tmp_path):
    produced = _run_tool("calculate the EOQ for my SKUs", _mixed_csv(tmp_path))

    assert "You asked for EOQ: 1 of 2 SKUs has intermittent demand" in produced.summary
    assert produced.summary.endswith(": SPARE-9.")


def test_a_brief_that_did_not_name_eoq_gets_no_reframe(tmp_path):
    produced = _run_tool("set up reorder points and safety stock", _mixed_csv(tmp_path))

    assert "You asked for EOQ" not in produced.summary


def test_the_reframe_moves_no_number(tmp_path):
    data = _mixed_csv(tmp_path)
    asked = _run_tool("calculate the EOQ for my SKUs", data).report
    plain = _run_tool("set up reorder points and safety stock", data).report

    assert asked.recommendations == plain.recommendations
    assert asked.final_investment == plain.final_investment


def test_a_payload_built_without_the_flag_still_runs(tmp_path):
    # Package runs and hand-built payloads predate the key; absence means "not asked".
    tool = tools.inventory_tool()
    prepared = tool.prepare(JobRequest(brief="EOQ", data_path=_mixed_csv(tmp_path)),
                            llm.RulesFallback())
    payload = {k: v for k, v in prepared.payload.items() if k != "eoq_requested"}

    assert "You asked for EOQ" not in tool.run(payload, {}).summary


def test_orchestrator_routes_an_eoq_brief_and_carries_the_reframe(tmp_path):
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback(),
                        clients_root=None)
    res = orch.run("calculate the EOQ for my SKUs", data_path=_mixed_csv(tmp_path),
                   out_dir=tmp_path / "out")

    assert res.status == "ok" and res.tool == "inventory_optimization"
    assert "SPARE-9" in res.summary and "(R, S)" in res.summary


# ---- the Spanish names reach the tool without an LLM ---------------------------

@pytest.mark.parametrize("brief", [
    "calcula el lote economico de compra",
    "calcula el lote económico de mis SKU",
    "quiero la formula de wilson para mis repuestos",
    "aplica la Fórmula de Wilson",
])
def test_spanish_eoq_names_route_to_inventory_without_an_llm(brief):
    result = intent.classify(brief, tools.build_default_registry(), llm.RulesFallback())

    assert result.job_type == "inventory_optimization"


@pytest.mark.parametrize("brief", [
    # "lote" alone is a batch (FEFO territory), and Wilson is also a surname.
    "el lote de produccion llega vencido",
    "Wilson Supply es nuestro proveedor",
])
def test_lote_alone_or_wilson_the_surname_does_not_route_to_inventory(brief):
    result = intent.classify(brief, tools.build_default_registry(), llm.RulesFallback())

    assert result.job_type != "inventory_optimization"


def test_a_spanish_eoq_brief_gets_the_reframe_end_to_end(tmp_path):
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback(),
                        clients_root=None)
    res = orch.run("calcula el lote económico de mis productos", data_path=_mixed_csv(tmp_path),
                   out_dir=tmp_path / "out")

    assert res.status == "ok" and res.tool == "inventory_optimization"
    assert "You asked for EOQ" in res.summary and "SPARE-9" in res.summary

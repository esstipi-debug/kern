"""Tests for the Odoo replenishment agent tool (the 24th registered tool), fully offline.

Wires the Odoo connector into the orchestrator: ``prepare`` reads Odoo (the ``InMemoryOdoo``
stand-in when no ODOO_* creds are set), ``run`` forecasts + plans the restock and presents
>=2 ranked executable options, and the tool routes + runs end-to-end through the orchestrator
with no network or API key.
"""

from pathlib import Path

from jobs import odoo_job
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.connectors.odoo import InMemoryOdoo, demo_odoo
from src.guided import ESCALATED, OPTIONS, passed_guided


def _deep_odoo() -> InMemoryOdoo:
    """An Odoo where the single product is far above cover -> no restock needed."""
    return InMemoryOdoo(
        {
            "product.product": {1: {"default_code": "SKU-1", "name": "W", "list_price": 10.0, "standard_price": 6.0}},
            "stock.location": {10: {"usage": "internal"}},
            "stock.quant": {100: {"product_id": [1, "W"], "location_id": [10, "S"], "quantity": 9999.0}},
            "sale.order": {200: {"name": "S1", "date_order": "2026-01-05 10:00:00", "state": "sale", "order_line": [300]}},
            "sale.order.line": {300: {"product_id": [1, "W"], "product_uom_qty": 1.0, "price_unit": 10.0}},
            "product.supplierinfo": {},
            "stock.warehouse.orderpoint": {},
        }
    )


# -- prepare ------------------------------------------------------------------


def test_prepare_uses_the_standin_without_credentials():
    payload = odoo_job.prepare(None, {})

    assert payload["n_products"] == 3
    assert "stand-in" in payload["source"]


def test_prepare_accepts_an_injected_rpc():
    payload = odoo_job.prepare(None, {"odoo_rpc": demo_odoo()})

    assert payload["n_products"] == 3


# -- run ----------------------------------------------------------------------


def test_run_plans_restock_for_thin_skus():
    report = odoo_job.run(odoo_job.prepare(None, {}), cover_periods=8.0)

    # demo: SKU-1 (12 on-hand, ~10/period) and SKU-3 (20, ~5/period) are thin; SKU-2 is deep.
    assert report.n_restock == 2
    assert set(report.restock) == {"SKU-1", "SKU-3"}
    assert report.total_restock > 0
    assert odoo_job.verify(report) == []


def test_run_offers_ranked_executable_options():
    report = odoo_job.run(odoo_job.prepare(None, {}), cover_periods=8.0)

    assert report.outcome.status == OPTIONS
    assert len(report.outcome.options) >= 2
    assert sum(1 for o in report.outcome.options if o.recommended) == 1
    assert all(o.action for o in report.outcome.options)  # every option is executable
    assert passed_guided(report.outcome)
    assert report.outcome.options[0].label.startswith("Apply reorder")


def test_well_stocked_odoo_holds_but_stays_protected():
    report = odoo_job.run(odoo_job.prepare(None, {"odoo_rpc": _deep_odoo()}), cover_periods=8.0)

    assert report.n_restock == 0
    assert report.outcome.status == OPTIONS
    assert passed_guided(report.outcome)
    assert report.outcome.options[0].label.startswith("Hold")


# -- financial-threshold escalation: a big-$ restock needs finance sign-off ---


def test_default_threshold_leaves_the_small_demo_restock_as_plain_options():
    """Demo restock is ~$916 - well under the $50k default, so the default
    behavior (freely-actionable options) is unchanged."""
    report = odoo_job.run(odoo_job.prepare(None, {}), cover_periods=8.0)

    assert report.outcome.status == OPTIONS


def test_restock_value_above_threshold_escalates_to_finance():
    payload = odoo_job.prepare(None, {})

    report = odoo_job.run(payload, cover_periods=8.0, financial_threshold=500.0)

    assert report.outcome.status == ESCALATED
    assert report.outcome.escalation.route_to
    assert report.outcome.escalation.sla
    # the ranked options are NOT lost - they travel inside the escalation packet
    assert len(report.outcome.escalation.options) >= 2
    assert odoo_job.verify(report) == []


def test_escalated_deck_states_the_requirement_in_words():
    """The data model being correct (outcome.status == ESCALATED) is not the same
    guarantee as a human reading the ACTUAL rendered document ever seeing it -
    the deck must say so, not just carry it silently in a field nobody reads."""
    report = odoo_job.run(odoo_job.prepare(None, {}), cover_periods=8.0, financial_threshold=500.0)
    assert report.outcome.status == ESCALATED  # sanity: this run is really escalated

    md = odoo_job.build_deck(report, client="Acme", confidence=0.85).to_markdown()

    assert "ESCALATED" in md
    assert "finance" in md.lower()
    assert report.outcome.escalation.sla in md


def test_escalated_run_still_reaches_the_orchestrator_deck_with_visible_options(tmp_path):
    """End-to-end through Orchestrator.run(): the ranked options must reach the
    written deck's 'Options to act' section even when the outcome is escalated -
    not just live in report.outcome.escalation.options, unread by anything."""
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run(
        "connect odoo and plan replenishment from odoo",
        client="Acme", out_dir=tmp_path,
        overrides={"financial_threshold": 500.0},
    )

    assert res.status == "ok"
    assert res.guided is not None and res.guided.status == ESCALATED
    assert len(res.guided.options) >= 2          # visible at the top level, not just inside escalation
    deck_path = Path(res.deliverables["deck_report"])
    md = deck_path.read_text(encoding="utf-8")
    assert "ESCALATED" in md
    assert "## Options to act" in md             # the options actually rendered, not just returned


def test_well_stocked_odoo_never_escalates_even_with_a_tiny_threshold():
    """Nothing to restock -> no dollar value at risk -> the 'hold' outcome must
    never be gated behind finance, no matter how low the threshold is."""
    report = odoo_job.run(odoo_job.prepare(None, {"odoo_rpc": _deep_odoo()}), cover_periods=8.0,
                           financial_threshold=0.01)

    assert report.outcome.status == OPTIONS
    assert report.outcome.options[0].label.startswith("Hold")


def test_lead_times_are_carried_through():
    report = odoo_job.run(odoo_job.prepare(None, {}), cover_periods=8.0)

    # demo supplierinfo: SKU-1 7d, SKU-3 21d
    assert report.lead_times["SKU-1"] == 7.0
    assert report.lead_times["SKU-3"] == 21.0


# -- deck ---------------------------------------------------------------------


def test_build_deck_is_ascii_and_complete():
    report = odoo_job.run(odoo_job.prepare(None, {}), cover_periods=8.0)

    md = odoo_job.build_deck(report, client="Acme", citations=("Odoo XML-RPC",), confidence=0.85).to_markdown()

    assert md.isascii()
    assert "Odoo Replenishment" in md
    assert "## Coverage & handoff" in md


# -- registration + routing ---------------------------------------------------


def test_tool_is_registered_with_an_options_hook():
    reg = tools.build_default_registry()
    tool = reg.get("odoo_replenishment")

    assert tool.options is not None
    assert tool.requires_data is False


def test_routes_to_odoo_when_the_brief_names_it():
    reg = tools.build_default_registry()

    classified = intent.classify(
        "connect to my odoo erp and pull from odoo to plan replenishment", reg, llm.RulesFallback()
    )

    assert classified.job_type == "odoo_replenishment"


def test_runs_end_to_end_through_the_orchestrator(tmp_path):
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("connect odoo and plan replenishment from odoo", client="Acme", out_dir=tmp_path)

    assert res.status == "ok" and res.tool == "odoo_replenishment"
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
    assert Path(res.deliverables["csv"]).exists()
    assert Path(res.deliverables["deck_report"]).exists()

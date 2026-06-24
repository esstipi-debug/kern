"""Every tool delivers >=2 ranked, executable options on success (not just a dashboard).

Unit-tests the per-tool option builders (fed lightweight report stand-ins) plus the
system-wide invariant that every registered tool wires a Tool.options hook.
"""

from types import SimpleNamespace as NS

from scm_agent import tool_options as to
from scm_agent import tools
from src.guided import OPTIONS, passed_guided, recommend


def _assert_ranked(outcome, *, min_options=2):
    assert outcome.status == OPTIONS
    assert len(outcome.options) >= min_options
    assert sum(1 for o in outcome.options if o.recommended) == 1
    assert all(o.action for o in outcome.options)        # every option is executable
    assert passed_guided(outcome)                        # honours the never-unprotected contract


# -- the system-wide guarantee ------------------------------------------------


def test_every_registered_tool_delivers_ranked_options():
    reg = tools.build_default_registry()
    missing = [t.key for t in reg.list() if t.options is None]
    assert missing == []


# -- per-tool builders --------------------------------------------------------


def test_inventory_options():
    r = NS(params={"service_level": 0.95},
           recommendations=[NS(status="ok"), NS(status="review")], final_investment=120_000.0)
    _assert_ranked(to.inventory_options(r), min_options=3)


def test_pricing_options_recommends_apply_when_actionable():
    out = to.pricing_options(NS(n_actionable=4, n_inelastic=3, n_skus=10))
    _assert_ranked(out, min_options=3)
    assert recommend(out.options).label.startswith("Apply")


def test_pricing_options_recommends_pilot_when_nothing_actionable():
    out = to.pricing_options(NS(n_actionable=0, n_inelastic=3, n_skus=10))
    assert recommend(out.options).label.startswith("Pilot")


def test_leadership_options():
    out = to.leadership_options(NS(lever_name="Adaptable", lever_code="A", archetype="Builder", average=2.4))
    _assert_ranked(out, min_options=3)
    assert "Adaptable" in recommend(out.options).label


def test_cost_to_serve_options_with_losers():
    r = NS(portfolio=NS(segments=(NS(net_to_serve=-5.0, segment="Retail"),
                                  NS(net_to_serve=50.0, segment="Wholesale"))),
           cash_release=NS(total_cash_released=8_000.0))
    out = to.cost_to_serve_options(r)
    _assert_ranked(out, min_options=3)
    assert "loss-making" in recommend(out.options).label.lower()


def test_cost_to_serve_options_without_losers_or_cash():
    r = NS(portfolio=NS(segments=(NS(net_to_serve=10.0, segment="A"),)), cash_release=None)
    _assert_ranked(to.cost_to_serve_options(r))


def test_abc_xyz_options():
    _assert_ranked(to.abc_xyz_options(NS(n_a=5, n_cz=3, a_value_share=0.72, n_skus=40)), min_options=3)


def test_ddmrp_options_release_when_orders_due():
    out = to.ddmrp_options(NS(total_order_qty=500.0, n_order=2, n_red=1, n_parts=20))
    _assert_ranked(out, min_options=3)
    assert "Release" in recommend(out.options).label


def test_ddmrp_options_hold_when_all_green():
    out = to.ddmrp_options(NS(total_order_qty=0.0, n_order=0, n_red=0, n_parts=20))
    assert recommend(out.options).label.startswith("Hold")


def test_landed_cost_options():
    r = NS(lines=[NS(sku="SKU-A", landed=NS(total=1300.0))], total_freight=300.0, total_duty=110.0, n_lines=2)
    _assert_ranked(to.landed_cost_options(r), min_options=3)


def test_financial_kpis_options_markdown_when_weak():
    out = to.financial_kpis_options(NS(worst=[NS(product_id="SKU-B")], gmroi=0.6, turns=2.0, dio=120.0))
    _assert_ranked(out, min_options=3)
    assert recommend(out.options).label.startswith("Markdown")


def test_reconciliation_options_root_cause_below_target():
    out = to.reconciliation_options(NS(worst=[NS(product_id="SKU-B")], ira=0.33))
    _assert_ranked(out)
    assert "Root-cause" in recommend(out.options).label


def test_reconciliation_options_accept_above_target():
    out = to.reconciliation_options(NS(worst=[], ira=0.99))
    assert recommend(out.options).label.startswith("Accept")


def test_whatif_options():
    r = NS(top_driver="holding_cost", breakeven_found=True, breakeven_value=110.0, pessimistic_value=800.0)
    out = to.whatif_options(r)
    _assert_ranked(out, min_options=3)
    assert "holding_cost" in recommend(out.options).label


def test_warehouse_options():
    from warehouse.generator import generate_layout

    _assert_ranked(to.warehouse_options(generate_layout({})), min_options=3)

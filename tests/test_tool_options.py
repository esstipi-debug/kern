"""Every tool delivers >=2 ranked, executable options on success (not just a dashboard).

Unit-tests the per-tool option builders (fed lightweight report stand-ins) plus the
system-wide invariant that every registered tool wires a Tool.options hook.
"""

from types import SimpleNamespace as NS

from nosilo_core.guided import ESCALATED, OPTIONS, passed_guided, recommend

from jobs.intake import IntakeQuality
from scm_agent import tool_options as to
from scm_agent import tools


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


def _quality(n_raw: int, *, bad_date: int = 0, bad_qty: int = 0, negative_qty: int = 0) -> IntakeQuality:
    return IntakeQuality(
        n_raw=n_raw, n_dropped_bad_date=bad_date, n_dropped_bad_quantity=bad_qty,
        n_dropped_negative_quantity=negative_qty,
    )


def _inventory_report(*, intake_quality=None, intake_quality_threshold=0.20,
                       n_skipped_zero_demand=0, n_skipped_zero_cost=0):
    return NS(
        params={"service_level": 0.95},
        recommendations=[NS(status="ok"), NS(status="review")], final_investment=120_000.0,
        intake_quality=intake_quality, intake_quality_threshold=intake_quality_threshold,
        n_skipped_zero_demand=n_skipped_zero_demand, n_skipped_zero_cost=n_skipped_zero_cost,
    )


def test_inventory_options():
    _assert_ranked(to.inventory_options(_inventory_report()), min_options=3)


# -- zero-demand skips (jobs/inventory_optimization.py's n_skipped_zero_demand): a
# residual (never just silently folded into n_review), the never-unprotected contract
# applied to the same "loud crash became a silent success" failure class -----------


def test_inventory_options_no_skips_has_no_zero_demand_residual():
    out = to.inventory_options(_inventory_report())
    assert out.residuals == []


def test_inventory_options_zero_demand_skip_adds_residual():
    out = to.inventory_options(_inventory_report(n_skipped_zero_demand=2))
    assert out.status == OPTIONS
    assert len(out.residuals) == 1
    desc = out.residuals[0].description.lower()
    assert "2" in desc and "zero" in desc and "demand" in desc
    assert out.residuals[0].risk_if_skipped.strip()  # never a bare/empty risk statement


def test_inventory_options_defer_item_excludes_skipped_zero_demand_from_count():
    """The 'Free capital - defer low-value SKUs' option must not conflate a zero-demand
    skip (a data gap - no policy could be computed) with a genuinely flagged, deferrable
    SKU (e.g. high forecast bias)."""
    recs = [
        NS(status="ok", policy_kind="(s, Q)"),
        NS(status="review", policy_kind="(s, Q)"),  # genuinely flagged (e.g. high bias)
        NS(status="review", policy_kind="skipped_zero_demand"),
    ]
    report = NS(
        params={"service_level": 0.95}, recommendations=recs, final_investment=100_000.0,
        intake_quality=None, intake_quality_threshold=0.20, n_skipped_zero_demand=1,
    )
    out = to.inventory_options(report)
    defer_item = next(o for o in out.options if o.label.startswith("Free capital"))
    assert "1 flagged-for-review" in defer_item.summary
    assert "data gap" in defer_item.summary.lower()


# -- zero-cost skips (jobs/inventory_optimization.py's n_skipped_zero_cost): the
# EOQ-explosion bug found via the simworld harness's inject_zero_cost injector --
# same "loud crash became a silent success" failure class as zero-demand, but on
# the cost axis (mean_unit_cost <= 0 explodes compute_eoq via the holding-cost
# floor instead of raising a ValueError). --------------------------------------


def test_inventory_options_no_skips_has_no_zero_cost_residual():
    out = to.inventory_options(_inventory_report())
    assert out.residuals == []


def test_inventory_options_zero_cost_skip_adds_residual():
    out = to.inventory_options(_inventory_report(n_skipped_zero_cost=3))
    assert out.status == OPTIONS
    assert len(out.residuals) == 1
    desc = out.residuals[0].description.lower()
    assert "3" in desc and "cost" in desc
    assert out.residuals[0].risk_if_skipped.strip()  # never a bare/empty risk statement


def test_inventory_options_both_skip_kinds_add_two_separate_residuals():
    """Zero-demand and zero-cost are different data-quality findings; a report
    hitting both must surface both, not collapse into one generic residual."""
    out = to.inventory_options(_inventory_report(n_skipped_zero_demand=1, n_skipped_zero_cost=2))
    assert len(out.residuals) == 2
    descriptions = " ".join(r.description.lower() for r in out.residuals)
    assert "demand" in descriptions and "cost" in descriptions


def test_inventory_options_defer_item_excludes_skipped_zero_cost_from_count():
    recs = [
        NS(status="ok", policy_kind="(s, Q)"),
        NS(status="review", policy_kind="(s, Q)"),  # genuinely flagged (e.g. high bias)
        NS(status="review", policy_kind="skipped_zero_cost"),
    ]
    report = NS(
        params={"service_level": 0.95}, recommendations=recs, final_investment=100_000.0,
        intake_quality=None, intake_quality_threshold=0.20, n_skipped_zero_cost=1,
    )
    out = to.inventory_options(report)
    defer_item = next(o for o in out.options if o.label.startswith("Free capital"))
    assert "1 flagged-for-review" in defer_item.summary
    assert "data gap" in defer_item.summary.lower()


# -- intake plausibility: a residual (always, once anything was dropped) or an
# escalation (dropped_fraction over threshold) - nosilo_core/escalation.py wired in --


def test_inventory_options_clean_intake_has_no_residual():
    out = to.inventory_options(_inventory_report(intake_quality=_quality(100)))
    assert out.status == OPTIONS
    assert out.residuals == []


def test_inventory_options_moderate_drop_attaches_residual_but_does_not_escalate():
    out = to.inventory_options(_inventory_report(intake_quality=_quality(100, bad_date=5)))
    assert out.status == OPTIONS
    assert len(out.residuals) == 1
    assert "intake" in out.residuals[0].description.lower()
    assert out.residuals[0].risk_if_skipped.strip()  # never a bare/empty risk statement


def test_inventory_options_severe_drop_escalates_and_keeps_options_ranked():
    out = to.inventory_options(_inventory_report(intake_quality=_quality(100, bad_date=30, negative_qty=20)))
    assert out.status == ESCALATED
    assert out.escalation.route_to and out.escalation.sla
    assert len(out.escalation.options) >= 3
    assert out.options == out.escalation.options  # visible at the top level too


def test_inventory_options_respects_a_custom_threshold():
    # 10% dropped: escalates under a tight 5% threshold, stays plain options at 20%.
    tight = to.inventory_options(
        _inventory_report(intake_quality=_quality(100, bad_date=10), intake_quality_threshold=0.05)
    )
    loose = to.inventory_options(
        _inventory_report(intake_quality=_quality(100, bad_date=10), intake_quality_threshold=0.20)
    )
    assert tight.status == ESCALATED
    assert loose.status == OPTIONS


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


def _seo_report(*, robots: bool, sitemap: bool, ranked=None):
    return NS(
        domain="shop.example.com", pages_crawled=12,
        findings=[NS(issue_type="missing_title", url="u", detail="d")],
        ranked_issues=list(ranked if ranked is not None else [
            NS(issue_type="missing_title", severity_weight=9, affected_count=4, score=36,
               example_urls=("https://shop.example.com/a", "https://shop.example.com/b")),
        ]),
        robots_txt=NS(present=robots), sitemap=NS(present=sitemap),
    )


def test_seo_audit_options_leads_with_the_top_issue_when_the_site_gates_are_fine():
    outcome = to.seo_audit_options(_seo_report(robots=True, sitemap=True))
    _assert_ranked(outcome, min_options=3)
    assert "missing_title" in outcome.options[0].label


def test_seo_audit_options_promotes_a_missing_site_gate_above_the_top_issue():
    """robots.txt/sitemap.xml are site-level gates: while they are broken, fixing
    per-page tags moves nothing, so they must outrank the top page issue."""
    outcome = to.seo_audit_options(_seo_report(robots=False, sitemap=True))
    _assert_ranked(outcome, min_options=3)
    assert outcome.options[0].recommended
    assert "robots.txt" in outcome.options[0].label


def test_seo_audit_options_survives_a_clean_crawl_with_no_ranked_issues():
    """A clean site still has to hand back ranked, executable choices -- an empty
    ranked_issues list must never index out of range."""
    outcome = to.seo_audit_options(_seo_report(robots=True, sitemap=True, ranked=[]))
    _assert_ranked(outcome, min_options=3)

"""Minimum-history gate: a SKU the engine barely knows must not receive a number.

The defect this pins (audit finding A4): a SKU whose entire history was ONE sale
came back with a confident purchase recommendation -- order quantity 35.3, safety
stock 0.00, status "ok", $4,416 of recommended investment -- and passed QA clean,
while a SKU with 24 real observations in the same portfolio was flagged
"high_bias". The confidence signal was inverted: the less the engine knew about a
SKU, the cleaner the verdict it shipped.

Root cause: jobs/inventory_optimization.py guarded only degenerate DEMAND
(annual_demand <= 0) and degenerate COST (mean_unit_cost <= 0), never degenerate
HISTORY LENGTH. With one observation src/forecasting.py's _error_stats returns
error_std 0.0 and _std returns demand_std 0.0, both finite, so
ForecastResult.to_engine_inputs takes its "a genuinely flat history: measured,
not missing" branch and hands the engine sigma = 0.0. z * 0 * sqrt(tau) = 0.

These tests are the regression net for both entry points into the money path:
the CSV/intake path (jobs/inventory_optimization.run) and the connector path
(src/connectors/replenish.plan_replenishment), which forecasts straight from a
live store's order history.

One thing shifted underneath this gate after it was written, and the tests below
were rewritten to match: src/sources.py now builds each SKU's series on a
complete period grid, so "how much history" is counted in PERIODS OBSERVED, not
in sales made. That splits the original headline case cleanly in two, and both
halves are pinned here -- a SKU whose single sale was 24 weeks ago has real
evidence (a slow mover) and must NOT be refused, while a SKU that first sold last
week has no window to measure and must be. Before the grid these two were the
same one-element series, which is precisely why the engine could not tell them
apart.
"""

from __future__ import annotations

import pandas as pd
import pytest

from jobs import qa
from jobs.inventory_optimization import run
from src.connectors import InventoryLevel, Order, OrderLine, Product
from src.connectors.replenish import plan_replenishment
from src.forecasting import MIN_PERIODS_FOR_POLICY

# ---------------------------------------------------------------------------
# CSV / intake path
# ---------------------------------------------------------------------------


_PERIOD = "W"


def _portfolio(thin_periods: int, rich_periods: int = 24) -> pd.DataFrame:
    """RICH spans the whole window; THIN's FIRST sale is `thin_periods` from the end.

    History length is measured in PERIODS OBSERVED, not sales made -- see
    src/sources.py's period grid. A SKU whose only sale was 24 weeks ago has 24 weeks
    of evidence (23 of them zero) and the engine is entitled to an opinion: it sells
    rarely. What it cannot have an opinion about is a SKU that only STARTED selling
    `thin_periods` ago, because there is no window to measure. That is the case this
    gate exists for, and it is produced by placing THIN's first sale near the END.

    Both SKUs carry positive cost and positive demand, so neither trips the
    pre-existing zero-demand / zero-cost guards -- the only thing separating them is
    how long the engine has been watching.
    """
    rows = []
    dates = pd.date_range("2026-01-04", periods=rich_periods, freq=_PERIOD)
    for i in range(rich_periods):
        rows.append({"date": dates[i], "product_id": "RICH", "quantity": 8.0 + (i % 5), "unit_cost": 40.0})
    for i in range(thin_periods):
        idx = rich_periods - thin_periods + i
        rows.append({"date": dates[idx], "product_id": "THIN", "quantity": 10.0, "unit_cost": 250.0})
    return pd.DataFrame(rows)


def _run(df: pd.DataFrame):
    return run(df, period_freq=_PERIOD)


def test_brand_new_sku_gets_no_quantity():
    """The headline case: a SKU whose first sale was one period ago."""
    report = _run(_portfolio(thin_periods=1))
    thin = {r.product_id: r for r in report.recommendations}["THIN"]

    assert thin.policy_kind == "insufficient_history"
    assert thin.order_quantity is None
    assert thin.order_up_to is None
    # `None`, no 0.0, desde el 2026-09-19: un cero es un numero sobre el que un
    # operador -- y `src/simworld/runners.py`, y el puente al Tower -- actua. La
    # negativa tiene que estar en el CAMPO, no solo en `policy_kind` y en un
    # residual. Ver tests/test_negativa_no_es_cero.py.
    assert thin.forecast is None
    assert thin.reorder_point is None
    assert thin.safety_stock is None
    assert thin.z_factor is None
    # La inversion SI es cero, y medida: no se estaciona plata en este SKU.
    assert thin.investment == 0.0
    assert thin.status == "review"
    assert thin.n_periods == 1


def test_one_old_sale_is_evidence_and_is_NOT_refused():
    """The case the period grid changed, and the reason the fixture above places THIN
    at the END of the window rather than the start.

    Before the grid, a single sale 24 weeks ago arrived as a one-element series and was
    indistinguishable from a SKU first seen last week. It is not the same thing at all:
    24 weeks of watching, 23 of them empty, is a real measurement of a very slow mover.
    Refusing it would be its own kind of dishonesty -- and the quantity that comes out
    must be small, not the ~10/period the collapsed series used to imply.
    """
    dates = pd.date_range("2026-01-04", periods=24, freq=_PERIOD)
    rows = [{"date": dates[i], "product_id": "RICH", "quantity": 8.0 + (i % 5), "unit_cost": 40.0}
            for i in range(24)]
    rows.append({"date": dates[0], "product_id": "SLOW", "quantity": 10.0, "unit_cost": 250.0})

    slow = {r.product_id: r for r in _run(pd.DataFrame(rows)).recommendations}["SLOW"]

    assert slow.policy_kind != "insufficient_history"
    assert slow.n_periods == 24
    assert slow.forecast < 2.0, "10 units across 24 weeks is not 10 units per week"


def test_thin_sku_no_longer_looks_cleaner_than_the_well_known_one():
    """The inversion itself. Before the gate, THIN came back status='ok' while
    RICH came back 'high_bias' -- the SKU with no data carried the cleaner label."""
    report = _run(_portfolio(thin_periods=1))
    by = {r.product_id: r for r in report.recommendations}

    assert by["THIN"].status == "review"
    assert by["RICH"].status in {"ok", "high_bias"}
    assert by["RICH"].order_quantity is not None and by["RICH"].order_quantity > 0


@pytest.mark.parametrize("n", range(1, MIN_PERIODS_FOR_POLICY))
def test_every_length_below_the_threshold_is_refused(n):
    report = _run(_portfolio(thin_periods=n))
    thin = {r.product_id: r for r in report.recommendations}["THIN"]
    assert thin.policy_kind == "insufficient_history", f"{n} period(s) produced a policy"
    assert thin.order_quantity is None


def test_at_the_threshold_a_policy_is_produced():
    """The gate must not swallow SKUs that DO have enough history -- otherwise the
    fix trades a false-confidence bug for a silently-empty-portfolio bug."""
    report = _run(_portfolio(thin_periods=MIN_PERIODS_FOR_POLICY))
    thin = {r.product_id: r for r in report.recommendations}["THIN"]
    assert thin.policy_kind in {"(s, Q)", "(R, S)"}
    assert thin.n_periods == MIN_PERIODS_FOR_POLICY


def test_refusal_does_not_contaminate_the_rest_of_the_portfolio():
    """Same invariant the zero-demand skip already holds: isolate, never abort."""
    baseline = _run(_portfolio(thin_periods=MIN_PERIODS_FOR_POLICY))
    gated = _run(_portfolio(thin_periods=1))

    base_rich = {r.product_id: r for r in baseline.recommendations}["RICH"]
    gated_rich = {r.product_id: r for r in gated.recommendations}["RICH"]

    assert gated_rich.order_quantity == pytest.approx(base_rich.order_quantity)
    assert gated_rich.safety_stock == pytest.approx(base_rich.safety_stock)
    assert gated_rich.investment == pytest.approx(base_rich.investment)


def test_refused_sku_is_excluded_from_the_investment_total():
    """A refused SKU must not inflate requested_investment -- the operator is being
    asked to approve a number, and a SKU we declined to size cannot be inside it."""
    report = _run(_portfolio(thin_periods=1))
    by = {r.product_id: r for r in report.recommendations}
    assert report.requested_investment == pytest.approx(by["RICH"].investment)


def test_report_counts_the_refusals():
    assert _run(_portfolio(thin_periods=1)).n_insufficient_history == 1
    assert _run(_portfolio(thin_periods=MIN_PERIODS_FOR_POLICY)).n_insufficient_history == 0


def test_qa_passes_on_a_report_containing_a_refusal():
    """Mirrors test_qa_passes_on_report_with_skipped_sku: a refusal is a valid
    deliverable state, not a QA failure. Picking a policy_kind QA does not know
    about would reintroduce the bug in QA-failure form."""
    assert qa.verify(_run(_portfolio(thin_periods=1))) == []


# ---------------------------------------------------------------------------
# QA structural invariants -- the belt to the gate's braces
# ---------------------------------------------------------------------------


def _rec(**overrides):
    from jobs.inventory_optimization import SkuRecommendation

    base = dict(
        product_id="X", method="ses", intermittent=False, forecast=10.0,
        error_std=2.0, bias=0.0, mae=1.0, policy_kind="(s, Q)", order_quantity=30.0,
        order_up_to=None, reorder_point=25.0, safety_stock=5.0, z_factor=1.645,
        service_level=0.95, unit_cost=10.0, lead_periods=2.0,
        cycle_investment=200.0, ss_investment=50.0, investment=250.0, status="ok",
        n_periods=20,
    )
    base.update(overrides)
    return SkuRecommendation(**base)


def _report(recs):
    from jobs.inventory_optimization import JobReport

    total = sum(r.investment for r in recs)
    return JobReport(
        recommendations=recs, params={"service_level": 0.95}, requested_investment=total,
        cycle_floor=total, final_investment=total, safety_stock_scale=1.0, feasible=True,
        budget=None, n_skus=len(recs), n_at_risk=0, n_intermittent=0,
    )


def test_qa_rejects_a_priced_policy_built_on_too_little_history():
    """If the gate is ever removed or bypassed, QA is the second line: a numeric
    recommendation whose n_periods is below the threshold must not ship."""
    issues = qa.verify(_report([_rec(n_periods=2)]))
    assert any("history" in i.lower() for i in issues), issues


def test_qa_rejects_a_buffer_conjured_from_no_dispersion():
    """sigma_source 'unavailable' means no dispersion was estimable. A positive
    safety stock cannot come from that -- it would be a fabricated number."""
    issues = qa.verify(_report([_rec(sigma_source="unavailable", safety_stock=5.0)]))
    assert any("dispersion" in i.lower() or "sigma" in i.lower() for i in issues), issues


def test_qa_accepts_a_measured_flat_history():
    """The legitimate 'unavailable' case that must keep passing: a long, genuinely
    flat history really does warrant a zero buffer (src/forecasting.py's
    'measured, not missing' branch). Rejecting this would be a false positive."""
    assert qa.verify(_report([_rec(sigma_source="unavailable", safety_stock=0.0)])) == []


# ---------------------------------------------------------------------------
# Connector path -- the one that would run unattended
# ---------------------------------------------------------------------------


class _StubSource:
    """Minimal InventorySource: a SKU whose order history is `n_order_days` long."""

    def __init__(self, n_order_days: int) -> None:
        self._n = n_order_days

    def list_products(self):
        return [Product(sku="SPARE", title="Repuesto", price=500.0, cost=300.0)]

    def inventory_levels(self):
        return [InventoryLevel("SPARE", 2.0, "loc-1")]

    def orders(self, *, since=None):
        return [
            Order(order_id=f"o{i}", created_at=f"2026-01-{i + 1:02d}",
                  lines=(OrderLine(sku="SPARE", quantity=5.0, price=500.0),))
            for i in range(self._n)
        ]

    def lead_times(self):
        return {}


def test_connector_replenishment_refuses_a_sku_with_too_few_order_days():
    """plan_replenishment forecast * cover_periods straight off the order history,
    with no safety stock and no history check. Three order-days in two years came
    out as 'demanda regular' and drove a restock target."""
    plan = plan_replenishment(_StubSource(n_order_days=3), cover_periods=8.0)
    line = plan.lines[0]

    assert line.status == "insufficient_history"
    assert line.restock_qty == 0.0
    assert "SPARE" not in plan.restock


def test_connector_replenishment_still_plans_with_enough_history():
    plan = plan_replenishment(_StubSource(n_order_days=MIN_PERIODS_FOR_POLICY), cover_periods=8.0)
    line = plan.lines[0]

    assert line.status == "ok"
    assert line.restock_qty > 0
    assert plan.restock["SPARE"] == line.restock_qty


def test_connector_refusal_is_stated_as_a_residual_not_swallowed():
    """A SKU dropped from the restock must leave a trace on the guided outcome --
    otherwise 'no restock needed' and 'I refused to size this' look identical."""
    plan = plan_replenishment(_StubSource(n_order_days=3), cover_periods=8.0)
    blob = " ".join(r.description for r in plan.outcome.residuals).lower()
    assert "history" in blob or "historial" in blob, plan.outcome.residuals

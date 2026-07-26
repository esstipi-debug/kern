"""Acceptance test for the cost-to-serve rate wiring (audit fix 4d).

``_cost_to_serve_run`` built ``ServiceCostRates`` from ``params.get(..., 0.0)`` for
all three activity rates. With none supplied, fulfillment + returns + overhead is
identically zero, so ``net_margin_pct`` equals the gross margin — and the deck
still calls it the "net-to-serve margin" and lists 0 loss-making segments.

Zero rates are legitimate when the file itself carries the cost (the freight
column is sniffed into ``SegmentActivity.outbound_freight``), so the guard fires
only when there is no cost signal at all.
"""
from __future__ import annotations

import pandas as pd
import pytest

from scm_agent import llm, tools
from scm_agent.types import JobRequest
from src.cost_to_serve import SegmentActivity, ServiceCostRates, analyze_portfolio


def _orderlines(with_freight: bool = False) -> pd.DataFrame:
    df = pd.DataFrame({
        "Segment": ["Retail", "Retail", "Wholesale", "Wholesale"],
        "Sales": [1000.0, 500.0, 2000.0, 800.0],
        "Quantity": [100, 50, 200, 80],
        "Order ID": ["o1", "o2", "o3", "o4"],
        "COGS": [600.0, 300.0, 1200.0, 480.0],
    })
    if with_freight:
        df["Shipping Cost"] = [40.0, 20.0, 90.0, 35.0]
    return df


def _prepare(df: pd.DataFrame, tmp_path, params: dict | None = None):
    csv = tmp_path / "orders.csv"
    df.to_csv(csv, index=False)
    tool = tools.cost_to_serve_tool()
    request = JobRequest(
        brief="cost to serve by segment", data_path=str(csv), params=params or {}
    )
    return tool.prepare(request, llm.RulesFallback())


def test_engine_delta_is_material():
    """The audit's headline: zero rates overstate the net-to-serve margin."""
    segs = [
        SegmentActivity("Consumer", revenue=1_161_401.0, units=19_521, orders=5_191,
                        cogs=800_000.0, returns_units=390.0),
        SegmentActivity("Corporate", revenue=706_146.0, units=11_608, orders=3_020,
                        cogs=490_000.0, returns_units=230.0),
        SegmentActivity("Home Office", revenue=429_653.0, units=6_744, orders=1_783,
                        cogs=300_000.0, returns_units=135.0),
    ]
    zero = analyze_portfolio(segs, ServiceCostRates())
    real = analyze_portfolio(
        segs,
        ServiceCostRates(cost_per_order=7.50, cost_per_unit_shipped=1.20,
                         return_handling_per_unit=9.00),
    )
    assert zero.overall_net_margin * 100 == pytest.approx(30.79, abs=0.01)
    assert real.overall_net_margin * 100 == pytest.approx(25.25, abs=0.01)


def test_tool_gates_when_no_cost_signal_at_all(tmp_path):
    """No rates and no freight/overhead -> ask, do not report gross as net."""
    prepared = _prepare(_orderlines(), tmp_path)
    assert prepared.status == "needs_data"
    joined = " ".join(prepared.messages)
    assert "cost_per_order" in joined
    assert "cost_per_unit_shipped" in joined
    assert "return_handling_per_unit" in joined


def test_tool_runs_when_rates_are_supplied(tmp_path):
    prepared = _prepare(_orderlines(), tmp_path, {"cost_per_order": 7.5})
    assert prepared.status == "ok"


def test_tool_runs_when_the_data_carries_freight(tmp_path):
    """A file with a freight column has a real cost signal; must not be gated."""
    prepared = _prepare(_orderlines(with_freight=True), tmp_path)
    assert prepared.status == "ok"

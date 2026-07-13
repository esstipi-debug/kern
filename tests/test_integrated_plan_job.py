"""Tests for jobs/integrated_plan.py (Linchpin 3.0 PR-20, A5 v1).

End-to-end: real CSVs -> forecast -> P2 demand shaping / P4 liquidation
demand shaping -> purchase plan -> >=3 coherence checks -> QA -> ONE
deliverable. Numbers below were computed by actually running this module
(see the module's own docstring for the formulas) and are asserted with
``pytest.approx`` where floating-point OLS/elasticity math is involved.
"""

from __future__ import annotations

import pandas as pd
import pytest

from jobs import integrated_plan as ip
from jobs.qa import integrated_plan_passed, verify_integrated_plan
from src.deliverable import Deliverable
from src.sop_engine.coherence import CHECK_BUDGET_FEASIBILITY, CHECK_PROMO_COVERAGE, CHECK_SERVICE_LEVEL
from src.sop_engine.demand_plan import LIQUIDATION_SOURCE, NO_SHIFT_SOURCE, PRICE_OPTIMIZER_SOURCE


def _write_demand_csv(path, series: dict[str, list[float]]) -> str:
    rows = [
        {"sku": sku, "period": t, "demand": q}
        for sku, vals in series.items()
        for t, q in enumerate(vals)
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def _write_stock_csv(path, rows: list[dict]) -> str:
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def _write_price_csv(path, rows: list[dict]) -> str:
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


# ---- scenario A: a P2 price-cut promo with NO covering purchase order ----
# (the plan's literal coherence-check example, sourced from the price
# optimizer rather than the liquidation report -- see
# tests/test_sop_engine_pipeline.py for the same example sourced from a
# LiquidationLine directly at the pure-function level.)

def _promo_scenario(tmp_path):
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {
        "SKU-A": [10.0] * 12,
        "SKU-PROMO": [20.0] * 12,
    })
    # price 4 -> 2 (halved), quantity 10 -> 40 (quadrupled): log-log OLS
    # elasticity = ln(40/10) / ln(2/4) = ln(4) / ln(0.5) = -2.0 exactly.
    price_csv = _write_price_csv(tmp_path / "prices.csv", [
        {"date": "2026-01-05", "product_id": "SKU-PROMO", "price": 4.0, "quantity": 10},
        {"date": "2026-01-12", "product_id": "SKU-PROMO", "price": 4.0, "quantity": 10},
        {"date": "2026-01-19", "product_id": "SKU-PROMO", "price": 2.0, "quantity": 40},
        {"date": "2026-01-26", "product_id": "SKU-PROMO", "price": 2.0, "quantity": 40},
    ])
    stock_csv = _write_stock_csv(tmp_path / "stock.csv", [
        {"product_id": "SKU-A", "on_hand": 200, "unit_cost": 5.0, "reorder_point": 20.0, "incoming_po": 0.0},
        {"product_id": "SKU-PROMO", "on_hand": 5.0, "unit_cost": 1.0, "reorder_point": 2.0, "incoming_po": 0.0},
    ])
    params = {"stock_path": stock_csv, "price_history_path": price_csv}
    payload = ip.prepare(demand_csv, params)
    bundle = ip.run(payload)
    return bundle


def test_prepare_requires_stock_path(tmp_path):
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {"A": [1.0] * 6})
    with pytest.raises(ValueError, match="stock_path"):
        ip.prepare(demand_csv, {})


def test_promo_scenario_end_to_end(tmp_path):
    bundle = _promo_scenario(tmp_path)

    by_id = {line.product_id: line for line in bundle.plan.demand_plan}
    assert by_id["SKU-A"].source == NO_SHIFT_SOURCE
    assert by_id["SKU-A"].shaped_demand == pytest.approx(10.0)

    promo = by_id["SKU-PROMO"]
    assert promo.source == PRICE_OPTIMIZER_SOURCE
    # elasticity -2.0, price 3.0 (median of 4,4,2,2) -> 2.0 (p* = c*e/(e+1), c=1.0):
    # ratio = (2/3)**-2 - 1 = 1.25 -> +125% -> shaped = 20 * 2.25 = 45.0
    assert promo.demand_shift_pct == pytest.approx(125.0, abs=0.01)
    assert promo.shaped_demand == pytest.approx(45.0, abs=0.01)

    purchase_by_id = {line.product_id: line for line in bundle.plan.purchase_plan}
    promo_purchase = purchase_by_id["SKU-PROMO"]
    assert promo_purchase.recommended_order == pytest.approx(40.0, abs=0.01)  # 45 - 5 - 0

    checks_by_kind = {}
    for c in bundle.plan.checks:
        checks_by_kind.setdefault(c.check, []).append(c)
    assert set(checks_by_kind) == {CHECK_PROMO_COVERAGE, CHECK_BUDGET_FEASIBILITY, CHECK_SERVICE_LEVEL}

    promo_coverage_results = checks_by_kind[CHECK_PROMO_COVERAGE]
    failed_promo = next(c for c in promo_coverage_results if c.product_id == "SKU-PROMO")
    assert failed_promo.passed is False
    assert "SKU-PROMO" in failed_promo.message
    assert "45.0" in failed_promo.message  # shaped demand, citable

    service_results = checks_by_kind[CHECK_SERVICE_LEVEL]
    assert any(c.product_id == "SKU-A" and c.passed for c in service_results)
    assert any(c.product_id == "SKU-PROMO" and not c.passed for c in service_results)

    assert bundle.plan.n_checks_failed >= 1
    assert "FAILED" in bundle.plan.summary


def test_promo_scenario_qa_passes_despite_failed_coherence_checks(tmp_path):
    """A FAILED coherence check is a reported FINDING, not a QA blocker --
    the deliverable still ships (mirrors every other job's QA gate: it
    checks structural soundness, not that the business outcome is good)."""
    bundle = _promo_scenario(tmp_path)
    assert any(not c.passed for c in bundle.plan.checks)
    assert verify_integrated_plan(bundle) == []
    assert integrated_plan_passed(bundle) is True


def test_promo_scenario_write_deliverable_produces_one_integrated_report(tmp_path):
    bundle = _promo_scenario(tmp_path)
    out_dir = tmp_path / "out"
    written = ip.write_deliverable(bundle, out_dir=out_dir, client="Acme Co", brief="integrated plan")

    assert set(written) == {"demand_plan", "purchase_plan", "coherence_checks", "report", "workbook"}
    for path in written.values():
        assert path.exists()

    report_text = written["report"].read_text(encoding="utf-8")
    assert "Acme Co" in report_text
    assert "SKU-PROMO" in report_text
    # ONE integrated narrative, not four stapled reports: the single deck
    # mentions both the forecast step and the failed coherence check.
    assert "coherence check" in report_text.lower() or "Coherence check" in report_text


def test_write_operational_produces_three_named_csvs(tmp_path):
    bundle = _promo_scenario(tmp_path)
    out_dir = tmp_path / "csv_out"
    written = ip.write_operational(bundle, out_dir, client="Acme")
    assert set(written) == {"demand_plan", "purchase_plan", "coherence_checks"}
    demand_df = pd.read_csv(written["demand_plan"])
    assert set(demand_df["product_id"]) == {"SKU-A", "SKU-PROMO"}
    checks_df = pd.read_csv(written["coherence_checks"])
    assert set(checks_df["check"]) == {CHECK_PROMO_COVERAGE, CHECK_BUDGET_FEASIBILITY, CHECK_SERVICE_LEVEL}


def test_build_deck_returns_a_deliverable(tmp_path):
    bundle = _promo_scenario(tmp_path)
    deck = ip.build_deck(bundle, client="Acme", citations=("cite one", "cite two"))
    assert isinstance(deck, Deliverable)
    assert deck.title.startswith("Integrated Plan")
    assert len(deck.findings) >= 3  # forecast + demand-shaping + at least one failed check


# ---- scenario B: P4 liquidation demand-shaping wiring (the SAME stock CSV
# doubles as the E&O input when it carries daily_demand) ----

def test_liquidation_demand_shaping_wires_through_when_stock_csv_has_daily_demand(tmp_path):
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {"SKU-CLR": [1.0] * 12})
    # flat price (no variation) -> P2 reports needs_data; P4's default-discount
    # heuristic still fires because the SKU is excess (on_hand >> target cover).
    price_csv = _write_price_csv(tmp_path / "prices.csv", [
        {"date": "2026-01-05", "product_id": "SKU-CLR", "price": 10.0, "quantity": 5},
        {"date": "2026-01-12", "product_id": "SKU-CLR", "price": 10.0, "quantity": 6},
        {"date": "2026-01-19", "product_id": "SKU-CLR", "price": 10.0, "quantity": 4},
        {"date": "2026-01-26", "product_id": "SKU-CLR", "price": 10.0, "quantity": 5},
    ])
    stock_csv = _write_stock_csv(tmp_path / "stock.csv", [
        {"product_id": "SKU-CLR", "on_hand": 2000.0, "unit_cost": 5.0, "daily_demand": 1.0,
         "reorder_point": 5.0, "incoming_po": 0.0},
    ])
    payload = ip.prepare(demand_csv, {"stock_path": stock_csv, "price_history_path": price_csv})
    assert payload["liquidation_lines"]["SKU-CLR"].method == "default_discount"
    assert payload["price_shifts"]["SKU-CLR"].status == "needs_data"

    bundle = ip.run(payload)
    line = bundle.plan.demand_plan[0]
    assert line.source == LIQUIDATION_SOURCE
    # excess_units = on_hand - target_cover_days(90) * daily_demand = 2000 - 90 = 1910
    # shaped_demand = base_forecast(1.0) + units_to_clear(1910.0)
    assert line.shaped_demand == pytest.approx(1911.0)
    assert verify_integrated_plan(bundle) == []


def test_include_liquidation_false_skips_p4_even_with_a_qualifying_sku(tmp_path):
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {"SKU-CLR": [1.0] * 12})
    stock_csv = _write_stock_csv(tmp_path / "stock.csv", [
        {"product_id": "SKU-CLR", "on_hand": 2000.0, "unit_cost": 5.0, "daily_demand": 1.0},
    ])
    payload = ip.prepare(demand_csv, {"stock_path": stock_csv, "include_liquidation": False})
    assert payload["liquidation_lines"] == {}


# ---- budget scenario ----

def test_budget_exceeded_scenario_end_to_end(tmp_path):
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {"A": [200.0] * 6, "B": [200.0] * 6})
    stock_csv = _write_stock_csv(tmp_path / "stock.csv", [
        {"product_id": "A", "on_hand": 0.0, "unit_cost": 1.0, "reorder_point": 100.0},
        {"product_id": "B", "on_hand": 0.0, "unit_cost": 1.0, "reorder_point": 100.0},
    ])
    payload = ip.prepare(demand_csv, {"stock_path": stock_csv, "budget": 50.0})
    bundle = ip.run(payload)
    budget_result = next(c for c in bundle.plan.checks if c.check == CHECK_BUDGET_FEASIBILITY)
    assert budget_result.passed is False
    assert bundle.plan.allocation is not None
    assert bundle.plan.allocation.feasible is False
    assert verify_integrated_plan(bundle) == []  # a failed check is a finding, not a QA blocker


# ---- SKUs missing purchase data are excluded, never silently ----

def test_forecast_only_skus_are_dropped_with_a_named_reason(tmp_path):
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {"A": [10.0] * 6, "ORPHAN": [5.0] * 6})
    stock_csv = _write_stock_csv(tmp_path / "stock.csv", [
        {"product_id": "A", "on_hand": 50.0, "unit_cost": 2.0},
    ])
    payload = ip.prepare(demand_csv, {"stock_path": stock_csv})
    bundle = ip.run(payload)
    assert bundle.dropped_no_purchase_data == ("ORPHAN",)
    assert "ORPHAN" in bundle.summary
    assert bundle.plan.n_skus == 1

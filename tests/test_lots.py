"""Tests for the offline lots engine: FEFO issuance, expiry risk, aging + disposition."""

from src.lots.expiry import aging_report, markdown_vs_scrap
from src.lots.fefo import (
    AtRiskLot,
    Lot,
    at_risk_quantities,
    fefo_allocate,
    fefo_order,
)


def _lots() -> list[Lot]:
    return [
        Lot("L1", "P", quantity=10, days_to_expiry=5, unit_cost=5, unit_price=12),
        Lot("L2", "P", quantity=10, days_to_expiry=10, unit_cost=5, unit_price=12),
    ]


def test_fefo_order_soonest_expiry_first():
    lots = [Lot("a", "P", 1, 5), Lot("b", "P", 1, 1), Lot("c", "P", 1, 10)]
    assert [lot.lot_id for lot in fefo_order(lots)] == ["b", "a", "c"]


def test_fefo_allocate_consumes_earliest_first():
    picks = fefo_allocate(_lots(), {"P": 15})
    assert [(p.lot_id, p.quantity) for p in picks] == [("L1", 10.0), ("L2", 5.0)]


def test_at_risk_with_demand_rate_flags_unsellable():
    # rate 1/day: L1 sells 5 of 10 by day 5, L2 sells 5 of 10 by day 10 -> 10 units at risk
    risk = {r.lot_id: r for r in at_risk_quantities(_lots(), {"P": 1.0})}
    assert risk["L1"].at_risk_quantity == 5.0
    assert risk["L2"].at_risk_quantity == 5.0
    assert risk["L1"].at_risk_value == 25.0          # 5 * unit_cost 5
    assert sum(r.at_risk_quantity for r in risk.values()) == 10.0


def test_at_risk_none_when_demand_absorbs_all():
    assert at_risk_quantities(_lots(), {"P": 2.0}) == []   # rate 2/day clears both lots


def test_at_risk_without_rate_flags_only_expired():
    lots = [Lot("ok", "P", 5, 8, unit_cost=2), Lot("dead", "P", 4, -1, unit_cost=2)]
    risk = at_risk_quantities(lots, {})
    assert [r.lot_id for r in risk] == ["dead"]
    assert risk[0].at_risk_quantity == 4.0


def test_aging_report_buckets_by_shelf_life():
    lots = [Lot(f"L{d}", "P", 1, d, unit_cost=10) for d in (-1, 3, 20, 100)]
    by = {b.label: b for b in aging_report(lots)}
    assert by["expired"].quantity == 1 and by["expiring"].quantity == 1
    assert by["aging"].quantity == 1 and by["fresh"].quantity == 1
    assert by["expired"].value == 10.0


def test_markdown_recommended_when_it_beats_scrap():
    risk = [AtRiskLot("L", "P", 10, 3, at_risk_value=50, potential_revenue=120)]
    plan = markdown_vs_scrap(risk, scrap_value_pct=0.2, markdown_price_pct=0.5)
    assert plan.recommended == "markdown"
    assert plan.markdown_recovery == 60.0            # 0.5 * 120
    assert plan.recovered_value == 60.0


def test_scrap_recommended_when_markdown_is_worse():
    risk = [AtRiskLot("L", "P", 10, 1, at_risk_value=50, potential_revenue=120)]
    plan = markdown_vs_scrap(risk, scrap_value_pct=0.5, markdown_price_pct=0.1)
    assert plan.recommended == "scrap"
    assert plan.scrap_recovery == 25.0               # 0.5 * 50
    assert plan.recovered_value == 25.0

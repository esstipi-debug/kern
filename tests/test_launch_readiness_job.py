"""Tests for the launch_readiness agent job (Kern tool #41)."""

from dataclasses import replace as _replace
from datetime import date
from pathlib import Path as _Path

import pandas as pd
import pytest

from jobs import launch_readiness_job as lrj
from src.deliverable import Deliverable as _Deliverable
from src.guided import ESCALATED, EXECUTED, OPTIONS
from src.guided import as_executed as _as_executed

AS_OF = date(2026, 7, 1)


def _run_one(inp: "lrj.LaunchInput", *, service_level: float = 0.95):
    report = lrj.run({"records": [inp], "as_of_date": AS_OF, "target_service_level": service_level})
    assert len(report.lines) == 1
    return report.lines[0]


def _covered(**kw) -> "lrj.LaunchInput":
    base = dict(product_id="sku", launch_date=date(2026, 7, 31), lift_pct=0.0, has_coverage=True,
                on_hand=200.0, daily_demand=10.0, lead_time_days=7.0, demand_std=0.0, lead_time_std=0.0)
    base.update(kw)
    return lrj.LaunchInput(**base)


# -- Task 1: verdict engine ---------------------------------------------------


def test_green_when_on_hand_covers_to_launch():
    line = _run_one(_covered(on_hand=1000.0))  # 100 days cover vs 30 to launch
    assert line.verdict == lrj.VERDICT_GREEN
    assert line.outcome.status == EXECUTED
    assert line.days_of_cover == 100.0


def test_yellow_order_now_when_on_hand_above_reorder_point():
    line = _run_one(_covered(on_hand=200.0))  # cover 20 < 30; lead 7 fits; reorder = 10*7 = 70
    assert line.verdict == lrj.VERDICT_YELLOW
    assert line.outcome.status == OPTIONS
    assert line.reorder_point == 70.0
    rec = next(o for o in line.outcome.options if o.recommended)
    assert "order" in rec.label.lower()


def test_yellow_limited_allocation_when_below_reorder_point():
    line = _run_one(_covered(on_hand=50.0))  # cover 5 < 30; reorder 70; on_hand 50 < 70
    assert line.verdict == lrj.VERDICT_YELLOW
    rec = next(o for o in line.outcome.options if o.recommended)
    assert "limited" in rec.label.lower()
    assert line.outcome.confidence < 0.8


def test_red_when_lead_time_exceeds_days_to_launch():
    line = _run_one(_covered(launch_date=date(2026, 7, 4), on_hand=20.0, lead_time_days=14.0))
    # 3 days to launch, cover 2 (< 3), lead 14 -> exposure_gap 11
    assert line.verdict == lrj.VERDICT_RED
    assert line.outcome.status == ESCALATED
    assert line.exposure_gap_days == 11.0
    assert line.outcome.escalation.route_to == "marketing campaign owner"
    assert line.outcome.escalation.sla
    assert len(line.outcome.escalation.options) >= 2


def test_red_when_missing_coverage_data():
    inp = lrj.LaunchInput(product_id="ghost", launch_date=date(2026, 7, 31), lift_pct=0.0, has_coverage=False)
    line = _run_one(inp)
    assert line.verdict == lrj.VERDICT_RED
    assert line.outcome.status == ESCALATED
    assert line.days_of_cover is None and line.lead_time_days is None
    assert len(line.outcome.escalation.options) >= 2


def test_red_when_lift_wipes_out_demand_is_not_green():
    line = _run_one(_covered(on_hand=1000.0, lift_pct=-1.0))  # shaped = 0 -> degenerate
    assert line.verdict == lrj.VERDICT_RED
    assert line.outcome.status == ESCALATED


def test_lead_time_variability_raises_the_reorder_point():
    low = _run_one(_covered(demand_std=2.0, lead_time_std=0.0))
    high = _run_one(_covered(demand_std=2.0, lead_time_std=3.0))
    assert high.reorder_point > low.reorder_point


# -- Task 2: ingestion --------------------------------------------------------


def _campaigns_df(**over) -> pd.DataFrame:
    d = {"product_id": ["a", "b"], "launch_date": ["2026-07-31", "2026-07-31"]}
    d.update(over)
    return pd.DataFrame(d)


def _inventory_df() -> pd.DataFrame:
    return pd.DataFrame({
        "product_id": ["a", "b"],
        "on_hand": [1000, 50],
        "daily_demand": [10, 10],
        "lead_time_days": [7, 7],
    })


def test_prepare_records_joins_and_defaults_lift_to_zero():
    payload = lrj.prepare_records(_campaigns_df(), _inventory_df(), {"as_of_date": "2026-07-01"})
    assert {r.product_id for r in payload["records"]} == {"a", "b"}
    assert all(r.lift_pct == 0.0 and r.has_coverage for r in payload["records"])
    assert payload["target_service_level"] == 0.95


def test_prepare_records_direct_lift_is_a_fraction_not_a_percent():
    df = _campaigns_df(expected_lift_pct=[0.20, 0.0])
    payload = lrj.prepare_records(df, _inventory_df(), {"as_of_date": "2026-07-01"})
    a = next(r for r in payload["records"] if r.product_id == "a")
    assert a.lift_pct == 0.20  # 1.2x, NOT 21x


def test_prepare_records_derives_lift_from_discount_trio():
    df = _campaigns_df(current_price=[100.0, 0.0], proposed_price=[80.0, 0.0], elasticity=[-2.0, 0.0])
    payload = lrj.prepare_records(df, _inventory_df(), {"as_of_date": "2026-07-01"})
    a = next(r for r in payload["records"] if r.product_id == "a")
    assert a.lift_pct == pytest.approx((80.0 / 100.0) ** -2.0 - 1.0)  # 0.5625


def test_prepare_records_rejects_percent_typo_lift():
    df = _campaigns_df(expected_lift_pct=[20.0, 0.0])  # 20 meaning 20% -> nonsense as a fraction
    with pytest.raises(ValueError, match="fraction"):
        lrj.prepare_records(df, _inventory_df(), {"as_of_date": "2026-07-01"})


def test_prepare_records_keeps_a_campaign_sku_with_no_inventory_row():
    inv = _inventory_df().iloc[[0]]  # only "a" has an inventory row
    payload = lrj.prepare_records(_campaigns_df(), inv, {"as_of_date": "2026-07-01"})
    b = next(r for r in payload["records"] if r.product_id == "b")
    assert b.has_coverage is False


def test_prepare_records_errors_on_missing_required_columns():
    with pytest.raises(ValueError, match="launch_date"):
        lrj.prepare_records(pd.DataFrame({"product_id": ["a"]}), _inventory_df(), {})


def test_prepare_reads_two_csvs(tmp_path):
    camp = tmp_path / "campanas.csv"
    _campaigns_df().to_csv(camp, index=False)
    inv = tmp_path / "inv.csv"
    _inventory_df().to_csv(inv, index=False)
    payload = lrj.prepare(str(camp), {"inventory_path": str(inv), "as_of_date": "2026-07-01"})
    assert len(payload["records"]) == 2


def test_prepare_requires_inventory_path(tmp_path):
    camp = tmp_path / "campanas.csv"
    _campaigns_df().to_csv(camp, index=False)
    with pytest.raises(ValueError, match="inventory_path"):
        lrj.prepare(str(camp), {})


# -- Task 3: verify -----------------------------------------------------------


def _healthy_report():
    return lrj.run({"records": [_covered(on_hand=1000.0)], "as_of_date": AS_OF})


def test_verify_passes_a_healthy_report():
    assert lrj.verify(_healthy_report()) == []


def test_verify_flags_a_red_line_mislabelled_executed():
    rep = lrj.run({"records": [_covered(launch_date=date(2026, 7, 4), on_hand=20.0, lead_time_days=14.0)],
                   "as_of_date": AS_OF})
    broken = _replace(rep.lines[0], outcome=_as_executed("nope"))
    rep = _replace(rep, lines=(broken,))
    assert any("EXECUTED" in m for m in lrj.verify(rep))


def test_verify_flags_an_empty_reason():
    rep = _healthy_report()
    rep = _replace(rep, lines=(_replace(rep.lines[0], reason="  "),))
    assert any("reason" in m for m in lrj.verify(rep))


def test_verify_flags_an_invalid_verdict():
    rep = _healthy_report()
    rep = _replace(rep, lines=(_replace(rep.lines[0], verdict="purple"),))
    assert any("verdict" in m for m in lrj.verify(rep))


# -- Task 4: deliverables -----------------------------------------------------


def test_write_operational_renders_na_for_missing_coverage(tmp_path):
    inp = lrj.LaunchInput(product_id="ghost", launch_date=date(2026, 7, 31), lift_pct=0.0, has_coverage=False)
    rep = lrj.run({"records": [inp], "as_of_date": AS_OF})
    out = lrj.write_operational(rep, tmp_path, client="Acme")
    text = _Path(out["csv"]).read_text(encoding="utf-8")
    assert "ghost" in text and "N/A" in text and "red" in text


def test_build_deck_is_an_ascii_deliverable():
    rep = lrj.run({"records": [_covered(on_hand=1000.0)], "as_of_date": AS_OF})
    deck = lrj.build_deck(rep, client="Acme", citations=("Chopra & Meindl, Ch.7",), confidence=0.8)
    assert isinstance(deck, _Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Launch Readiness" in md and "## Coverage & handoff" in md
    assert "does NOT communicate" in md

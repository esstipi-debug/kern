"""Tests for src/liquidation_calendar.py (Linchpin 3.0 PR-19, plan section 7
P4 ``promo_liquidation`` v2).

Hand-verified reference scenarios (see individual test docstrings for the
worked numbers, all derived from the SAME ``_mixed_stocks``/``_mixed_history``
fixture ``tests/test_liquidation.py`` already hand-verifies for
``plan_liquidation`` itself -- this module never recomputes plan_liquidation's
own per-SKU numbers, only sequences and gates them):

- a realistic multi-week cash-recovery calendar, staggered by launch week,
  hand-verified against LiquidationReport's own recovered_value/weeks_to_clear;
- a SKU whose stated discount, calculated against this module's own price
  reference, does NOT match the amount calculated against the OFFICIALLY
  tracked src.state 30-day-lowest price is EXCLUDED (EU hard gate) while a
  SKU whose numbers agree ships normally;
- a SKU with no price history supplied at all (so no reference can be
  computed) is excluded with a clear, non-silent reason;
- a SKU with a confirmed CHEAPER competitor price is flagged (excluded
  pending a human decision) unless a documented undercut reason is supplied,
  in which case it ships with that reason recorded;
- a SKU with no pricing_intel match at all still works (v1 fallback, no
  competitor_check at all) -- this PR is additive.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest

from src.excess_obsolete import SkuStock
from src.liquidation import plan_liquidation
from src.liquidation_calendar import (
    DEFAULT_STEPS_PER_WEEK,
    build_liquidation_calendar,
)
from src.price_optimizer import CompetitorPriceContext
from src.pricing_guardrails import MarketNotConfiguredError
from src.state.store import StateStore
from src.state.system_state import snapshot

MARKETS_DIR = "config/markets"
AS_OF = date(2026, 7, 12)

# Same fixture tests/test_liquidation.py hand-verifies for plan_liquidation:
#   A: excess, elasticity path   -> at_risk=4550.0, recovered=price*910, weeks_to_clear=13.0
#   B: excess, default markdown  -> at_risk=1640.0, recovered=2460.0,   weeks_to_clear=410/7
#   C: excess, salvage           -> at_risk=330.0,  recovered=99.0,     weeks_to_clear=inf
#   D: dead,   salvage           -> at_risk=200.0,  recovered=60.0,     weeks_to_clear=inf
# ranked (at_risk desc): A, B, C, D.
_ELASTIC_HISTORY = ([4.0, 2.0, 4.0, 2.0], [10.0, 40.0, 10.0, 40.0])


def _mixed_stocks() -> list[SkuStock]:
    return [
        SkuStock(product_id="A", on_hand=1000.0, daily_demand=1.0, unit_cost=5.0),
        SkuStock(product_id="B", on_hand=500.0, daily_demand=1.0, unit_cost=4.0),
        SkuStock(product_id="C", on_hand=200.0, daily_demand=1.0, unit_cost=3.0),
        SkuStock(product_id="D", on_hand=100.0, daily_demand=0.0, unit_cost=2.0),
    ]


def _mixed_history() -> dict[str, tuple[list[float], list[float]]]:
    return {
        "A": _ELASTIC_HISTORY,
        "B": ([10.0, 10.0, 10.0, 10.0], [5.0, 6.0, 4.0, 5.0]),  # flat price -> median 10.0
    }


def _report():
    return plan_liquidation(_mixed_stocks(), _mixed_history(), horizon_weeks=13.0)


def _price_a() -> float:
    # mirrors tests/test_liquidation.py's own worked example
    return math.sqrt(160.0 / (910.0 / 13.0))


def _store(tmp_path) -> StateStore:
    return StateStore(tmp_path / "state")


def _seed(store: StateStore, cycle: str, product_id: str, price: float, when: datetime) -> None:
    df = pd.DataFrame({"product_id": [product_id], "price": [price], "currency": ["USD"]})
    snapshot("prices_own", df, cycle, store=store, now=when)


def _step(calendar, product_id: str):
    return next(s for s in calendar.steps if s.product_id == product_id)


# -- calendar sequencing + cash-recovery accrual --------------------------------


def test_calendar_sequences_steps_and_accrues_cash_by_week(tmp_path) -> None:
    """steps_per_week=1 -> A, B, C, D each launch on their own week (rank
    order, matching the report's own at-risk-desc ranking). market='us' has
    discount_reference_gate='none', so Omnibus never blocks here (no state
    seeding needed) -- this test isolates the calendar/accrual math itself.
    """
    report = _report()
    calendar = build_liquidation_calendar(
        report, market="us", as_of=AS_OF, store=_store(tmp_path),
        price_history=_mixed_history(), markets_dir=MARKETS_DIR, steps_per_week=1,
    )

    assert calendar.n_steps == 4
    assert calendar.n_included == 4
    assert calendar.n_excluded == 0
    assert calendar.n_omnibus_blocked == 0
    assert calendar.n_competitor_flagged == 0

    a, b, c, d = (_step(calendar, pid) for pid in "ABCD")
    assert (a.launch_week, b.launch_week, c.launch_week, d.launch_week) == (0, 1, 2, 3)
    assert a.duration_weeks == 13  # ceil(13.0)
    assert b.duration_weeks == 59  # ceil(410/7) == ceil(58.571...)
    assert c.duration_weeks == 1  # inf -> one-week lump sum, never divided by infinity
    assert d.duration_weeks == 1
    for step in (a, b, c, d):
        assert step.included is True
        assert step.exclusion_reason is None
        assert step.competitor_check is None  # no pricing_intel signal supplied at all

    price_a = _price_a()
    recovered_a = price_a * 910.0
    recovered_b = 2460.0

    # week 0: only A has launched (A's range covers weeks 0..12).
    assert calendar.weekly_schedule[0].recovered_this_week == pytest.approx(recovered_a / 13.0)
    assert calendar.weekly_schedule[0].steps_launched == 1
    # week 1: A still active + B launches.
    assert calendar.weekly_schedule[1].recovered_this_week == pytest.approx(
        recovered_a / 13.0 + recovered_b / 59.0
    )
    # week 2: A + B active, C's lump sum lands here (C launches week 2).
    assert calendar.weekly_schedule[2].recovered_this_week == pytest.approx(
        recovered_a / 13.0 + recovered_b / 59.0 + 99.0
    )
    # week 3: A + B active, D's lump sum lands here.
    assert calendar.weekly_schedule[3].recovered_this_week == pytest.approx(
        recovered_a / 13.0 + recovered_b / 59.0 + 60.0
    )
    # week 13: A has finished (its range was weeks 0..12) -> only B remains.
    assert calendar.weekly_schedule[13].recovered_this_week == pytest.approx(recovered_b / 59.0)
    # the calendar's last active week is B's (launch week 1 + 59 weeks - 1 = week 59).
    assert calendar.weekly_schedule[-1].week == 59
    assert len(calendar.weekly_schedule) == 60

    total_planned = recovered_a + recovered_b + 99.0 + 60.0
    assert calendar.total_recovered_planned == pytest.approx(total_planned)
    assert calendar.total_recovered_planned == pytest.approx(report.total_recovered)
    assert calendar.weekly_schedule[-1].cumulative_recovered == pytest.approx(total_planned)
    assert calendar.total_at_risk == pytest.approx(report.total_at_risk)
    assert calendar.total_at_risk_excluded == 0.0
    assert calendar.total_recovered_excluded == 0.0


def test_default_steps_per_week_batches_multiple_launches_in_one_week(tmp_path) -> None:
    report = _report()
    calendar = build_liquidation_calendar(
        report, market="us", as_of=AS_OF, store=_store(tmp_path),
        price_history=_mixed_history(), markets_dir=MARKETS_DIR,
    )
    assert calendar.steps_per_week == DEFAULT_STEPS_PER_WEEK
    assert all(step.launch_week == 0 for step in calendar.steps)  # 4 steps < default batch size
    assert calendar.weekly_schedule[0].steps_launched == 4


def test_steps_per_week_must_be_positive(tmp_path) -> None:
    report = _report()
    with pytest.raises(ValueError):
        build_liquidation_calendar(
            report, market="us", as_of=AS_OF, store=_store(tmp_path),
            markets_dir=MARKETS_DIR, steps_per_week=0,
        )


def test_unconfigured_market_fails_fast(tmp_path) -> None:
    report = _report()
    with pytest.raises(MarketNotConfiguredError):
        build_liquidation_calendar(
            report, market="zz", as_of=AS_OF, store=_store(tmp_path), markets_dir=MARKETS_DIR,
        )


# -- Omnibus gate (PR-17) --------------------------------------------------------


def test_eu_hard_gate_blocks_a_mismatched_step_but_ships_a_matching_one(tmp_path) -> None:
    """A's LOCAL reference price (median of its own price history, 3.0) does
    NOT match the OFFICIALLY tracked src.state 30-day-lowest price (seeded
    here at 4.0) -- the stated discount (49.60%, off 3.0) and the computed
    discount (62.20%, off 4.0) disagree by far more than the EU profile's
    0.5-point tolerance, so A is EXCLUDED under the hard gate. B's local
    reference (10.0, its own flat price) is seeded IDENTICALLY into state, so
    its stated (40.00%) and computed (40.00%) discounts agree exactly -- B
    ships normally. C/D are salvage (no price announced) -- Omnibus does not
    apply to them at all.
    """
    report = _report()
    store = _store(tmp_path)
    _seed(store, "1", "A", 4.0, datetime(2026, 6, 20, tzinfo=timezone.utc))
    _seed(store, "2", "B", 10.0, datetime(2026, 6, 25, tzinfo=timezone.utc))

    calendar = build_liquidation_calendar(
        report, market="eu", as_of=AS_OF, store=store,
        price_history=_mixed_history(), markets_dir=MARKETS_DIR, steps_per_week=10,
    )

    a, b, c, d = (_step(calendar, pid) for pid in "ABCD")
    assert a.included is False
    assert "Omnibus compliance check failed" in a.exclusion_reason
    assert a.compliance is not None
    assert a.compliance.gate == "hard"
    assert a.compliance.passed is False
    assert a.compliance.prior_price_30d_low == Decimal("4.0")
    assert a.compliance.stated_discount_pct == Decimal("49.6")
    assert a.compliance.computed_discount_pct == Decimal("62.20")

    assert b.included is True
    assert b.exclusion_reason is None
    assert b.compliance.passed is True
    assert b.compliance.prior_price_30d_low == Decimal("10.0")

    assert c.included is True and c.compliance is None  # no price announced -> N/A
    assert d.included is True and d.compliance is None

    assert calendar.n_omnibus_blocked == 1
    assert calendar.n_excluded == 1
    assert calendar.total_at_risk_excluded == pytest.approx(4550.0)
    price_a = _price_a()
    assert calendar.total_recovered_excluded == pytest.approx(price_a * 910.0)
    # excluded cash never silently vanishes from the total either.
    assert calendar.total_recovered_planned == pytest.approx(report.total_recovered - price_a * 910.0)


def test_missing_price_history_excludes_priced_lines_with_a_clear_reason(tmp_path) -> None:
    """No price_history means this module cannot compute ANY reference price
    to state a discount against -- Golden Rule 14 forbids assuming
    compliance, so A and B (both priced) are excluded; C and D (salvage, no
    price announced) are unaffected."""
    report = _report()
    calendar = build_liquidation_calendar(
        report, market="us", as_of=AS_OF, store=_store(tmp_path), markets_dir=MARKETS_DIR,
    )
    a, b, c, d = (_step(calendar, pid) for pid in "ABCD")
    assert a.included is False and "no price history" in a.exclusion_reason
    assert b.included is False and "no price history" in b.exclusion_reason
    assert a.compliance is None and b.compliance is None
    assert c.included is True and d.included is True
    assert calendar.n_omnibus_blocked == 2


# -- competitive floor (PR-10/PR-14) ---------------------------------------------


def test_competitor_undercut_without_a_reason_is_flagged_not_silently_shipped(tmp_path) -> None:
    """B's clearance price is 6.0; a confirmed competitor is observed at 7.0
    (our price undercuts it). With no documented reason, B is excluded
    pending a human decision -- never a silent undercut."""
    report = _report()
    observed_at = datetime(2026, 7, 10, tzinfo=timezone.utc)
    contexts = {
        "B": CompetitorPriceContext(
            site="rival.example", competitor_price=7.0, acquisition_tier="L1", observed_at=observed_at,
        )
    }
    calendar = build_liquidation_calendar(
        report, market="us", as_of=AS_OF, store=_store(tmp_path),
        price_history=_mixed_history(), competitor_contexts=contexts, markets_dir=MARKETS_DIR,
    )
    b = _step(calendar, "B")
    assert b.competitor_check is not None
    assert b.competitor_check.at_or_above_floor is False
    assert b.competitor_check.reason is None
    assert b.included is False
    assert "needs a human decision" in b.exclusion_reason
    assert calendar.n_competitor_flagged == 1


def test_competitor_undercut_with_a_documented_reason_ships(tmp_path) -> None:
    report = _report()
    observed_at = datetime(2026, 7, 10, tzinfo=timezone.utc)
    contexts = {
        "B": CompetitorPriceContext(
            site="rival.example", competitor_price=7.0, acquisition_tier="L1", observed_at=observed_at,
        )
    }
    reasons = {"B": "clearing dead stock regardless of competitor pricing"}
    calendar = build_liquidation_calendar(
        report, market="us", as_of=AS_OF, store=_store(tmp_path),
        price_history=_mixed_history(), competitor_contexts=contexts,
        undercut_reasons=reasons, markets_dir=MARKETS_DIR,
    )
    b = _step(calendar, "B")
    assert b.competitor_check.at_or_above_floor is False
    assert b.competitor_check.reason == "clearing dead stock regardless of competitor pricing"
    assert b.included is True
    assert b.exclusion_reason is None
    assert calendar.n_competitor_flagged == 0


def test_clearance_price_at_or_above_competitor_floor_confirms_automatically(tmp_path) -> None:
    report = _report()
    observed_at = datetime(2026, 7, 10, tzinfo=timezone.utc)
    contexts = {
        "B": CompetitorPriceContext(
            site="rival.example", competitor_price=3.0, acquisition_tier="L1", observed_at=observed_at,
        )
    }
    calendar = build_liquidation_calendar(
        report, market="us", as_of=AS_OF, store=_store(tmp_path),
        price_history=_mixed_history(), competitor_contexts=contexts, markets_dir=MARKETS_DIR,
    )
    b = _step(calendar, "B")
    assert b.competitor_check.at_or_above_floor is True
    assert b.competitor_check.reason is None
    assert b.included is True


def test_no_pricing_intel_match_falls_back_to_v1_behavior(tmp_path) -> None:
    """A SKU absent from competitor_contexts gets NO competitive check at
    all (not penalized for a match pricing_intel never made) -- this PR is
    additive over the existing v1 plan."""
    report = _report()
    calendar = build_liquidation_calendar(
        report, market="us", as_of=AS_OF, store=_store(tmp_path),
        price_history=_mixed_history(), competitor_contexts={}, markets_dir=MARKETS_DIR,
    )
    for step in calendar.steps:
        assert step.competitor_check is None
    assert calendar.n_competitor_flagged == 0
    assert calendar.n_included == 4

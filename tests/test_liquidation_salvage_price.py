"""A salvage line announces no discount, and that is a legal invariant - not a
consequence of it having no price.

This file exists because of a near-miss worth recording, and it is on its SECOND
version. Both versions defend the same rule; they disagree about where the rule
lives.

**The near-miss (version 1).** Task T12 of ``src/bateria`` was PARCIAL for one
reason: ``LiquidationLine.clearance_price`` was ``None`` on salvage lines, so the
deliverable did not offer "a proposed price per SKU". The obvious fix - and the one
a synthesis pass recommended after verifying that it moved the score - was one line
in ``_plan_sku``::

    clearance_price = recovered / units      # salvage already knows what it recovers

That one-liner moves T12 from PARCIAL to CUMPLE. It also breaks an EU compliance
gate, which the score never notices. Measured: five calendar steps flip to
``included=False`` with "cannot compute a discount reference", because
``src/liquidation_calendar.py`` decided whether Omnibus applied by asking
``line.clearance_price is None``. Populate the price and every salvage step becomes,
to that gate, an announced discount with no computable reference - so it is excluded
from the shippable calendar. Version 1 of this file pinned ``clearance_price is
None`` so that fix would fail loudly. It did its job.

**Why the rule moved (version 2).** ``clearance_price is None`` was never the
invariant; it was the ENCODING of one. The invariant is: *a salvage recovery is a
disposal - return-to-vendor, a jobber lot, a write-down - not an advertised markdown,
so EU/UK Omnibus does not apply, no competitor's shelf price binds it, and no shopper
reacts to it with extra demand.* Nothing in that sentence is about a price being
absent. It is about what the line IS, which is exactly what ``method`` says.

So the gates now read ``src.liquidation.announces_discount(line.method)``, and the
salvage line quotes the per-unit recovery it was already booking anyway
(``salvage_recovery_pct * unit_cost`` - the same money, divided by the units, not a
new heuristic and not an invented elasticity). The naive one-liner is STILL wrong,
for the same reason it always was: it publishes a price while leaving three gates
reading the old encoding. What makes this version different is not the price - it is
that the invariant was moved before the data changed.

These tests pin the invariant in its new home. They are deliberately stronger than
version 1's: a salvage line WITH a price must still skip the Omnibus gate, still
escape the competitive floor, and still shape no demand. Version 1 could not test
any of that, because under version 1 no such line could exist.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

import pytest

from src.excess_obsolete import SkuStock
from src.liquidation import (
    DEFAULT_DISCOUNT,
    ELASTICITY,
    SALVAGE,
    announces_discount,
    plan_liquidation,
)
from src.liquidation_calendar import build_liquidation_calendar
from src.price_optimizer import CompetitorPriceContext
from src.sop_engine.demand_plan import NO_SHIFT_SOURCE, build_demand_plan
from src.state.store import StateStore

_SALVAGE_PCT = 0.30
_MARKETS_DIR = "config/markets"
_AS_OF = date(2026, 7, 12)


def _dead(units: float = 100.0, unit_cost: float = 8.0) -> SkuStock:
    """Dead stock: no movement, so no markdown clears it - it falls through to salvage."""
    return SkuStock(
        product_id="SKU-DEAD",
        on_hand=units,
        daily_demand=0.0,
        unit_cost=unit_cost,
        days_since_last_sale=400.0,
    )


def _line(stocks, **kwargs):
    kwargs.setdefault("salvage_recovery_pct", _SALVAGE_PCT)
    return plan_liquidation(stocks, **kwargs).lines[0]


def _report(stocks=None, **kwargs):
    kwargs.setdefault("salvage_recovery_pct", _SALVAGE_PCT)
    return plan_liquidation(stocks if stocks is not None else [_dead()], **kwargs)


# -- the invariant, stated where it actually lives --------------------------------


def test_salvage_announces_no_discount_even_though_it_quotes_a_price():
    """The rule the three downstream gates read. Do NOT re-anchor this to
    ``clearance_price is None``: that was the old encoding, and it silently changes
    meaning the moment a salvage line carries a number."""
    line = _line([_dead()])

    assert line.method == SALVAGE
    assert announces_discount(line.method) is False
    assert line.clearance_price == pytest.approx(_SALVAGE_PCT * 8.0)
    assert line.discount_pct is None, "a disposal is not a reduction off anything"


def test_the_quoted_salvage_price_is_the_recovery_it_already_booked():
    """The price is not a new number: ``at_risk_value`` is ``units * unit_cost``, so
    ``salvage_recovery_pct * at_risk / units`` IS ``salvage_recovery_pct * unit_cost``.
    Nothing was estimated, fitted, or assumed to produce it."""
    line = _line([_dead(units=100.0, unit_cost=8.0)])

    assert line.recovered_value == pytest.approx(_SALVAGE_PCT * line.at_risk_value)
    assert line.recovered_value == pytest.approx(line.clearance_price * line.units_to_clear)
    assert line.recovery_pct == pytest.approx(_SALVAGE_PCT)
    assert line.units_to_clear == 100.0


def test_the_two_announced_methods_do_carry_a_price():
    """Elasticity and default-discount ARE advertised markdowns, so they price off
    an observed reference and the Omnibus gate applies to them. That contrast is the
    whole point of the distinction - all three price, only two announce."""
    excess = SkuStock(
        product_id="SKU-EXCESS", on_hand=100.0, daily_demand=0.5,
        unit_cost=5.0, days_since_last_sale=10.0,
    )
    history = {"SKU-EXCESS": ([10.0, 9.0, 8.0, 7.0], [5.0, 8.0, 14.0, 25.0])}

    with_curve = _line([excess], price_history=history)
    flat = _line([excess], price_history={"SKU-EXCESS": ([10.0] * 4, [5.0] * 4)})

    assert with_curve.method == ELASTICITY
    assert with_curve.clearance_price == pytest.approx(
        with_curve.recovered_value / with_curve.units_to_clear)
    assert flat.method == DEFAULT_DISCOUNT
    assert flat.clearance_price == pytest.approx(flat.recovered_value / flat.units_to_clear)
    # Both announce against an observed reference, which is what Omnibus needs.
    assert announces_discount(with_curve.method) and announces_discount(flat.method)
    assert with_curve.current_price is not None and with_curve.discount_pct is not None
    assert flat.current_price is not None and flat.discount_pct is not None


def test_salvage_has_no_reference_price_to_discount_from():
    """Without price history there is still no observed reference - so even if
    somebody decided a disposal WERE an announcement, there would be nothing to
    state it against. Two independent reasons, not one."""
    line = _line([_dead()])

    assert line.current_price is None


def test_a_salvage_line_is_still_a_complete_line():
    """Quoting the price must not leave the rest of the line half-built."""
    line = _line([_dead()])

    assert line.gross_margin_value == pytest.approx(
        line.recovered_value - line.units_to_clear * 8.0)
    assert math.isinf(line.weeks_to_clear), "dead stock never clears on demand alone"
    assert line.classification in ("dead", "excess")


# -- the three gates that must NOT fire on a priced salvage line -------------------


def test_a_priced_salvage_step_still_skips_the_omnibus_gate(tmp_path):
    """The regression version 1 of this file was written to prevent, now pinned at
    the gate instead of at the data. ``market='eu'`` is the HARD discount-reference
    gate and no price history is supplied, so ANY line the gate considers announced
    would be excluded with "cannot compute a discount reference". The salvage line
    ships anyway, because the gate asks what it IS."""
    calendar = build_liquidation_calendar(
        _report(), market="eu", as_of=_AS_OF, store=StateStore(tmp_path / "state"),
        markets_dir=_MARKETS_DIR, price_history=None,
    )
    step = calendar.steps[0]

    assert step.method == SALVAGE
    assert step.clearance_price is not None, "the price IS published now"
    assert step.compliance is None, "Omnibus does not apply to a disposal"
    assert step.included is True
    assert step.exclusion_reason is None
    assert calendar.n_omnibus_blocked == 0
    assert calendar.total_recovered_excluded == 0.0


def test_a_priced_salvage_step_is_not_bound_by_the_competitive_floor(tmp_path):
    """A jobber lot at 2.40/unit is not sold against a retailer's shelf at 7.00, so
    "we undercut a competitor" is not a decision anyone has to sign off here. Under
    the old encoding this was true by accident (no price to compare); now it is true
    on purpose."""
    contexts = {
        "SKU-DEAD": CompetitorPriceContext(
            site="rival.example", competitor_price=7.0, acquisition_tier="L1",
            observed_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        )
    }
    calendar = build_liquidation_calendar(
        _report(), market="us", as_of=_AS_OF, store=StateStore(tmp_path / "state"),
        markets_dir=_MARKETS_DIR, competitor_contexts=contexts,
    )
    step = calendar.steps[0]

    assert step.clearance_price == pytest.approx(_SALVAGE_PCT * 8.0)  # 2.40, well under 7.00
    assert step.competitor_check is None
    assert calendar.n_competitor_flagged == 0
    assert step.included is True


def test_a_priced_salvage_line_shapes_no_demand():
    """A disposal price moves no shopper, so it implies no lift. Booking one would
    push write-down volume into the S&OP demand plan and inflate the purchase plan
    downstream - the one failure mode here that no gate would ever report."""
    line = _line([_dead(units=999.0)])
    assert line.clearance_price is not None and line.units_to_clear == 999.0

    plan = build_demand_plan({"SKU-DEAD": 40.0}, liquidation_lines={"SKU-DEAD": line})

    assert plan[0].source == NO_SHIFT_SOURCE
    assert plan[0].demand_shift_pct == 0.0
    assert plan[0].shaped_demand == 40.0

"""A salvage line announces no price, and that is a legal invariant - not a gap to fill.

This file exists because of a near-miss worth recording. Task T12 of ``src/bateria`` is
PARCIAL for one reason: ``LiquidationLine.clearance_price`` is ``None`` on salvage lines,
so the deliverable does not offer "a proposed price per SKU". The obvious fix - and the
one a synthesis pass recommended after verifying that it moved the score - is one line in
``_plan_sku``::

    clearance_price = recovered / units      # salvage already knows what it recovers

It moves T12 from PARCIAL to CUMPLE. It also breaks eight preexisting tests and an EU
compliance gate, which the score never noticed.

**Why it is wrong.** ``src/liquidation_calendar.py:185-197`` states the invariant in its
own docstring: the Omnibus discount-reference check "does not apply at all (a salvage
line announces no price -- ``clearance_price`` is ``None``)". EU/UK Omnibus requires a
prior-price reference for any *announced discount*. A salvage recovery is a disposal, not
an advertised markdown: there is nothing to reference it against. Give it a
``clearance_price`` and every salvage step becomes an announced discount whose reference
cannot be computed, so ``_omnibus_gate`` excludes it - measured: five calendar tests flip
to ``included=False`` with "cannot compute a discount reference".

So the module is right and the battery task is describing something the module
deliberately does not do. Winning that half point means building the *other* thing: a
per-unit salvage recovery carried as its own field, distinct from an advertised clearance
price, and a T12 gate that can tell the two apart. Populating ``clearance_price`` is
scoring against the harness while breaking the product.

These tests pin the invariant so the one-line "fix" fails loudly next time.
"""

from __future__ import annotations

import math

import pytest

from src.liquidation import DEFAULT_DISCOUNT, ELASTICITY, SALVAGE, SkuStock, plan_liquidation

_SALVAGE_PCT = 0.30


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


def test_salvage_announces_no_clearance_price():
    """The invariant the Omnibus gate reads. Do not "fix" this to move T12."""
    line = _line([_dead()])

    assert line.method == SALVAGE
    assert line.clearance_price is None
    assert line.discount_pct is None, "no announced price means no announced discount"


def test_salvage_still_states_what_it_recovers():
    """The absence is narrow on purpose: the recovery IS reported, only the per-unit
    announced price is withheld. This is what makes the gap a product decision rather
    than a silent zero - nothing measurable is being hidden."""
    line = _line([_dead(units=100.0, unit_cost=8.0)])

    assert line.recovered_value == pytest.approx(_SALVAGE_PCT * line.at_risk_value)
    assert line.recovery_pct == pytest.approx(_SALVAGE_PCT)
    assert line.units_to_clear == 100.0


def test_the_two_announced_methods_do_carry_a_price():
    """Elasticity and default-discount ARE advertised markdowns, so they price - and the
    Omnibus gate applies to them. That contrast is the whole point of the distinction."""
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
    assert with_curve.current_price is not None and with_curve.discount_pct is not None
    assert flat.current_price is not None and flat.discount_pct is not None


def test_salvage_has_no_reference_price_to_discount_from():
    """The mechanical reason the gate cannot run: no observed price, so no reference."""
    line = _line([_dead()])

    assert line.current_price is None


def test_a_salvage_line_is_still_a_complete_line():
    """Withholding the price must not leave the rest of the line half-built."""
    line = _line([_dead()])

    assert line.gross_margin_value == pytest.approx(
        line.recovered_value - line.units_to_clear * 8.0)
    assert math.isinf(line.weeks_to_clear), "dead stock never clears on demand alone"
    assert line.classification in ("dead", "excess")

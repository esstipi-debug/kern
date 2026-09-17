"""Cross-area impact of ONE proposed decision change (spec 2026-07-31, sec 6.2).

Measures, never judges: no import of ``src.objection``. Every number comes from
``src.hats`` (``hat_kpis`` / ``decision_cost``) - this module does no arithmetic
of its own beyond the subtraction, so a KPI it cannot find propagates a KeyError
instead of being filled in.

Unlike ``src.hat_council``, which computes each hat's IDEAL from the grid, this
takes two points the caller already has: what the client runs today and what an
area proposes. Neither has to lie on the ``src.hats`` grid.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.hats import (
    HAT_KEYS,
    Candidate,
    HatInputs,
    decision_cost,
    evaluable_hats,
    hat_kpis,
    headline_kpi,
)


@dataclass(frozen=True)
class ImpactRow:
    """One area's headline KPI at both points. ``delta`` is raw and signed
    (proposed - current); interpreting its direction is src.objection's job."""

    hat_key: str
    kpi_name: str
    value_current: float
    value_proposed: float
    delta: float


@dataclass(frozen=True)
class CrossImpact:
    """The measured effect of moving from ``current`` to ``proposed``."""

    sku: str
    proposed_by: str
    current: Candidate
    proposed: Candidate
    #: One per EVALUABLE hat, in HAT_KEYS order -- 4 rows or 5. The areas that
    #: produced no row are in `excluded_hats`; measuring fewer areas than exist
    #: is only honest if the count of the missing ones travels with the result.
    rows: tuple[ImpactRow, ...]
    judge_cost_current: float
    judge_cost_proposed: float
    excluded_hats: tuple[str, ...] = ()


def measure(
    inputs: HatInputs,
    current: Candidate,
    proposed: Candidate,
    proposed_by: str,
) -> CrossImpact:
    """Every evaluable hat's headline KPI at both points, plus the judge cost."""
    if proposed_by not in HAT_KEYS:
        raise ValueError(
            f"proposed_by must be one of {HAT_KEYS}, got {proposed_by!r}")
    hats = evaluable_hats(inputs)
    if proposed_by not in hats:
        # An area we cannot score cannot be credited with a proposal either: the
        # report would name it as the proposer and then have no row for it.
        raise ValueError(
            f"proposed_by={proposed_by!r} is not evaluable for {inputs.sku}: the areas "
            f"on the table are {list(hats)}. Declare its inputs (transporte needs a "
            "FreightProfile) before attributing a proposal to it.")

    rows = []
    for hat_key in hats:
        name = headline_kpi(hat_key)
        # KeyError here is deliberate: a missing KPI is a contract break with
        # src.hats, not something to paper over with a default.
        v_cur = hat_kpis(inputs, hat_key, current)[name]
        v_prop = hat_kpis(inputs, hat_key, proposed)[name]
        rows.append(ImpactRow(
            hat_key=hat_key, kpi_name=name,
            value_current=v_cur, value_proposed=v_prop,
            delta=v_prop - v_cur,
        ))

    return CrossImpact(
        sku=inputs.sku,
        proposed_by=proposed_by,
        current=current,
        proposed=proposed,
        rows=tuple(rows),
        judge_cost_current=decision_cost(inputs, current),
        judge_cost_proposed=decision_cost(inputs, proposed),
        excluded_hats=tuple(k for k in HAT_KEYS if k not in hats),
    )

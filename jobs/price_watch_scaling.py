"""R5 bounded auto-scaling -- the per-SKU scaling decision for the price-watch
cycle (Discovery-Assisted Price Intel plan, Task 9 / PR-9).

Extracted from ``jobs/price_watch.py`` as its own cohesive unit (keeping that
module under the repo's 800-line cap; coding-style.md "extract utilities from
large modules"): the scaling I/O shapes plus the SINGLE place a cadence/tier
change is decided. That decision is delegated ENTIRELY to
``watch_policy.plan_watch_escalation`` (PR-8) -- nothing here mutates a
``SiteConfig`` or writes ``config/sites/*.yaml``.

``jobs.price_watch.run_price_watch_cycle`` is the call site: it resolves each
approved pair's ``SiteConfig``, then calls :func:`_scale_one` BEFORE that pair is
acquired. The call chain (cycle -> _scale_one -> guard) is the physical location
of the R5 guarantee.

R5 (CONFIRMED with the user, plan 2026-07-13): a faster re-check CADENCE within a
human-approved tier is safe to apply in-process; a higher acquisition TIER than
the approved ceiling changes the compliance envelope, and the engine NEVER does
that alone -- it surfaces a pending-approval ``GuidedOutcome`` and applies
nothing. There is deliberately no "auto-escalate without asking" branch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.guided import GuidedOutcome
from src.pricing_intel import watch_policy
from src.pricing_intel.match.sku_map import SkuMapEntry
from src.pricing_intel.models import SiteConfig


@dataclass(frozen=True)
class SkuScalingRequest:
    """A caller-supplied desire to watch one SKU harder -- a faster
    ``desired_cadence_hours`` and/or higher ``desired_tier``, tagged with the
    SKU's ``sku_value_rank`` for the operator rationale. This job never decides
    WHICH SKUs deserve this (that value model -- ABC-XYZ, margin, velocity -- is
    the caller's); it only routes the desire through the R5 guard."""

    desired_tier: str
    desired_cadence_hours: float
    sku_value_rank: str


@dataclass(frozen=True)
class ScaledWatch:
    """One SKU whose cadence this cycle tightened -- ONLY an
    ``approved_within_ceiling`` result (a ``needs_ceiling_raise`` SKU surfaces in
    ``PriceWatchCycleReport.pending_escalations`` instead). ``applied_cadence_hours``
    is the guard's rate-limit-floored cadence, applied in-process for this cycle
    only (never a write to ``config/sites/*.yaml`` or the scheduler)."""

    site: str
    competitor_sku_ref: str
    matched_product_id: str
    applied_cadence_hours: float


def _apply_scaled_cadence(entry: SkuMapEntry, decision: watch_policy.WatchEscalationDecision) -> float:
    """Apply an ``approved_within_ceiling`` cadence tightening for one SKU,
    in-process. Reached ONLY on that branch of :func:`_scale_one` -- NEVER on
    ``needs_ceiling_raise`` (the guard short-circuits first). A named, separate
    function so "the apply path is unreachable when a ceiling raise is needed" is
    a spyable, provable fact (``test_escalation_checked_before_apply``)."""
    _ = entry  # applied per-SKU; the (floored) cadence is the applied value
    return decision.applied_cadence_hours


def _scale_one(
    entry: SkuMapEntry,
    site_config: SiteConfig,
    request: SkuScalingRequest | None,
    *,
    current_cadence_hours: float,
    now: datetime,
) -> tuple[float | None, GuidedOutcome | None]:
    """The SOLE place a cadence/tier scaling decision is made, delegating it
    ENTIRELY to :func:`watch_policy.plan_watch_escalation` (PR-8, R5) -- no other
    path changes a cadence or tier. Returns ``(applied_cadence_hours, guided)``:

    * ``(cadence, None)`` -- ``approved_within_ceiling``: cadence applied (the
      ONLY branch reaching :func:`_apply_scaled_cadence`).
    * ``(None, guided)`` -- ``needs_ceiling_raise``: NOTHING applied; the
      pending-approval ``GuidedOutcome`` is returned. RETURNS before
      :func:`_apply_scaled_cadence` -- the R5 "checked before apply" guarantee.
    * ``(None, None)`` -- no request, or ``no_change``.
    """
    if request is None:
        return None, None
    decision = watch_policy.plan_watch_escalation(
        site_config=site_config,
        current_cadence_hours=current_cadence_hours,
        desired_cadence_hours=request.desired_cadence_hours,
        desired_tier=request.desired_tier,
        sku_value_rank=request.sku_value_rank,
        now=now,
    )
    # SAFETY-CRITICAL (R5): a tier beyond the ceiling is NEVER applied here --
    # return the pending-approval outcome; _apply_scaled_cadence is unreachable.
    if decision.kind == watch_policy.NEEDS_CEILING_RAISE:
        return None, decision.guided
    if decision.kind == watch_policy.APPROVED_WITHIN_CEILING:
        return _apply_scaled_cadence(entry, decision), None
    return None, None

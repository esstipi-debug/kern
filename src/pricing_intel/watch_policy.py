"""R5 bounded auto-scaling guard -- the pure decision behind "never crawl a site
harder than a human already approved, without asking first."

Context (Linchpin/Kern 3.0, discovery-assisted competitor price intelligence,
PR-8). The recurring watch cycle (PR-6/9) may decide a high-value SKU deserves a
more aggressive watch: a faster re-check *cadence* and/or a higher *acquisition
tier*. Those two moves are NOT equal in risk:

* A faster cadence within the tier a human already approved
  (``SiteConfig.max_tier_allowed``) is a scheduling change against a compliance
  envelope that already exists -- safe to apply, bounded only by the site's own
  ``rate_limit_seconds`` floor (never re-check faster than that implies).
* A higher acquisition tier than the approved ceiling changes the compliance
  envelope itself (crawl this domain harder than anyone cleared). Per the R5
  decision CONFIRMED with the user (plan 2026-07-13), the engine NEVER does this
  alone. It ALWAYS returns a ``GuidedOutcome`` (``OPTIONS``/``HANDOFF``, never
  ``EXECUTED``) asking a human to review the domain's robots.txt/ToS and raise
  the ceiling -- and applies nothing. There is deliberately no "auto-escalate
  without asking" branch, no matter how valuable the SKU.

This module is a pure decision (frozen dataclass + pure functions, the
analytical-core style of ``src/guided.py``). It NEVER mutates the ``SiteConfig``
it is given and NEVER writes a ``config/sites/*.yaml`` file -- surfacing the
request is the whole job; applying it is a human's. PR-9 wires this into
``jobs/price_watch.py``'s scaling step, where the code that would apply a tier
change is only reached on the ``approved_within_ceiling`` branch; on
``needs_ceiling_raise`` the caller applies nothing and collects the
``GuidedOutcome`` for the operator.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.guided import GuidedOutcome, HandoffPacket, Residual, as_handoff
from src.pricing_intel.models import ACQUISITION_TIERS, SiteConfig

# Decision kinds -- the three ways a scaling request can legitimately resolve.
APPROVED_WITHIN_CEILING = "approved_within_ceiling"
NEEDS_CEILING_RAISE = "needs_ceiling_raise"
NO_CHANGE = "no_change"

_SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True)
class WatchEscalationDecision:
    """The outcome of one scaling request against a site's approved envelope.

    ``applied_cadence_hours`` is the (possibly rate-limit-clamped) cadence the
    caller should adopt, or ``None`` when nothing is applied -- which is ALWAYS
    the case for a ``needs_ceiling_raise`` (surfaced via ``guided``) and for a
    ``no_change``. ``guided`` is a real ``GuidedOutcome`` ONLY on the
    ``needs_ceiling_raise`` branch, and it is never ``EXECUTED`` (see module
    docstring): a tier raise beyond the approved ceiling is always a
    pending-approval outcome.
    """

    kind: str
    applied_cadence_hours: float | None
    guided: GuidedOutcome | None
    reason: str


def _require_positive(name: str, value: float) -> None:
    """Fail fast at the boundary: cadences must be finite and strictly positive."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite value > 0, got {value!r}")


def _floor_cadence_hours(site_config: SiteConfig) -> float:
    """The fastest cadence this site's approved rate limit permits, in hours.

    A watch may never re-check faster than the site's own ``rate_limit_seconds``
    implies; that per-request delay expressed in hours is the hard floor a desired
    cadence is clamped up to.
    """
    return site_config.rate_limit_seconds / _SECONDS_PER_HOUR


def _ceiling_raise_outcome(
    site_config: SiteConfig,
    *,
    desired_tier: str,
    current_ceiling: str,
    sku_value_rank: str,
    now: object,
) -> GuidedOutcome:
    """Prepare the human-executable ceiling-raise request.

    Nothing here touches disk or the ``SiteConfig``; it only *describes* the step a
    human must take. The result is a ``HANDOFF`` outcome (an executable path exists),
    so ``verify_guided``/``passed_guided`` accept it.
    """
    domain = site_config.domain
    config_path = f"config/sites/{domain}.yaml"
    packet = HandoffPacket(
        title=f"Raise acquisition ceiling for {domain}: {current_ceiling} -> {desired_tier}",
        steps=[
            f"Review {domain}'s robots.txt and Terms of Service (ToS) for acquisition "
            f"tier {desired_tier}, a level above the approved ceiling {current_ceiling}.",
            f"If -- and only if -- that tier is cleared, raise max_tier_allowed to "
            f"{desired_tier} in {config_path}.",
            "Re-run the watch cycle so the approved higher tier takes effect for this SKU.",
        ],
        data={
            "domain": domain,
            "sku_value_rank": sku_value_rank,
            "current_ceiling": current_ceiling,
            "requested_tier": desired_tier,
            "config_path": config_path,
            "requested_at": now,
        },
        risk_if_skipped=(
            f"A {sku_value_rank}-ranked SKU keeps getting staler competitor reads at "
            f"tier {current_ceiling} until the ceiling is raised."
        ),
    )
    residual = Residual(
        description=(
            f"A human must review {domain}'s ToS/robots and raise max_tier_allowed to "
            f"{desired_tier}; the engine never raises a ceiling on its own."
        ),
        risk_if_skipped=(
            f"Without the raise, the {sku_value_rank}-ranked SKU stays monitored at the "
            f"lower tier {current_ceiling}, so competitor price moves are detected later "
            "than desired."
        ),
    )
    return as_handoff(
        f"A {sku_value_rank}-ranked SKU on {domain} wants acquisition tier "
        f"{desired_tier}, above the approved ceiling {current_ceiling}. Prepared a "
        "human step to review compliance and raise the ceiling; nothing was applied.",
        [packet],
        residuals=[residual],
    )


def plan_watch_escalation(
    *,
    site_config: SiteConfig,
    current_cadence_hours: float,
    desired_cadence_hours: float,
    desired_tier: str,
    sku_value_rank: str,
    now: object = None,
) -> WatchEscalationDecision:
    """Decide how (or whether) to scale up watching one SKU on one site.

    PURE: never mutates ``site_config`` and never writes to disk.

    * ``desired_tier`` above ``site_config.max_tier_allowed`` (by
      ``ACQUISITION_TIERS`` ordinal position) -> ``needs_ceiling_raise`` with a
      real ``GuidedOutcome`` and ``applied_cadence_hours=None``. NOTHING is
      applied; the outcome is never ``EXECUTED``.
    * ``desired_tier`` within the ceiling and a faster cadence requested ->
      ``approved_within_ceiling`` with the (rate-limit-floored) cadence applied.
    * Otherwise -> ``no_change``.

    ``now`` is optional provenance recorded in the handoff packet; it is never read
    from the wall clock here (keeping the function pure and deterministic).
    """
    _require_positive("current_cadence_hours", current_cadence_hours)
    _require_positive("desired_cadence_hours", desired_cadence_hours)
    if desired_tier not in ACQUISITION_TIERS:
        raise ValueError(
            f"desired_tier must be one of {ACQUISITION_TIERS}, got {desired_tier!r}"
        )
    if not isinstance(sku_value_rank, str) or not sku_value_rank.strip():
        raise ValueError(
            f"sku_value_rank must be a non-empty string, got {sku_value_rank!r}"
        )

    ceiling = site_config.max_tier_allowed
    # SAFETY-CRITICAL, checked FIRST and unconditionally: a tier beyond the
    # approved ceiling ALWAYS returns a GuidedOutcome and applies nothing. No
    # cadence math below can ever be reached for such a request, so there is no
    # path that raises a tier and then asks.
    if ACQUISITION_TIERS.index(desired_tier) > ACQUISITION_TIERS.index(ceiling):
        guided = _ceiling_raise_outcome(
            site_config,
            desired_tier=desired_tier,
            current_ceiling=ceiling,
            sku_value_rank=sku_value_rank,
            now=now,
        )
        return WatchEscalationDecision(
            kind=NEEDS_CEILING_RAISE,
            applied_cadence_hours=None,
            guided=guided,
            reason=(
                f"Desired tier {desired_tier} exceeds approved ceiling {ceiling} for "
                f"{site_config.domain}; surfaced for human approval, nothing applied."
            ),
        )

    # Within the approved ceiling: a cadence change is safe to apply, bounded by the
    # site's own rate-limit floor (never re-check faster than that implies).
    floor = _floor_cadence_hours(site_config)
    clamped = max(desired_cadence_hours, floor)
    if clamped < current_cadence_hours:
        clamp_note = (
            ""
            if clamped == desired_cadence_hours
            else f" (clamped up from {desired_cadence_hours:g}h to the rate-limit floor)"
        )
        return WatchEscalationDecision(
            kind=APPROVED_WITHIN_CEILING,
            applied_cadence_hours=clamped,
            guided=None,
            reason=(
                f"Tightened cadence to {clamped:g}h within approved tier {ceiling}"
                f"{clamp_note}."
            ),
        )

    return WatchEscalationDecision(
        kind=NO_CHANGE,
        applied_cadence_hours=None,
        guided=None,
        reason=(
            f"No tightening applied: desired cadence {desired_cadence_hours:g}h "
            f"(floored to {clamped:g}h) is not faster than current "
            f"{current_cadence_hours:g}h."
        ),
    )

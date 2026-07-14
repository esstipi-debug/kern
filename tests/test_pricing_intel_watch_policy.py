"""Tests for the R5 bounded auto-scaling guard (``src/pricing_intel/watch_policy.py``).

The single safety-critical guarantee under test: a desired acquisition tier
*above* a site's already-approved ``SiteConfig.max_tier_allowed`` NEVER auto-applies.
It always returns a real ``GuidedOutcome`` (``OPTIONS``/``HANDOFF``, never
``EXECUTED``) asking a human to raise the ceiling, and applies NOTHING. The guard
is a pure function: it never mutates the ``SiteConfig`` and never writes a config
file to disk.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from src.guided import EXECUTED, HANDOFF, OPTIONS, passed_guided, verify_guided
from src.pricing_intel.models import SiteConfig
from src.pricing_intel.watch_policy import (
    APPROVED_WITHIN_CEILING,
    NEEDS_CEILING_RAISE,
    NO_CHANGE,
    WatchEscalationDecision,
    plan_watch_escalation,
)


def _site_config(
    *,
    domain: str = "example-retailer.test",
    max_tier_allowed: str = "L1",
    rate_limit_seconds: float = 5.0,
    tos_decision: str = "limited",
) -> SiteConfig:
    """A valid, frozen SiteConfig satisfying every ``__post_init__`` invariant."""
    return SiteConfig(
        domain=domain,
        robots_txt_respected=True,
        robots_checked_at="2026-07-13",
        tos_summary="Auto-onboarded via robots.txt only; ToS not human-reviewed.",
        tos_decision=tos_decision,
        rate_limit_seconds=rate_limit_seconds,
        max_tier_allowed=max_tier_allowed,
    )


# -- (a) cadence tightening within the approved ceiling ------------------------


def test_cadence_tightening_within_ceiling_is_applied():
    decision = plan_watch_escalation(
        site_config=_site_config(max_tier_allowed="L1", rate_limit_seconds=2.0),
        current_cadence_hours=24.0,
        desired_cadence_hours=6.0,
        desired_tier="L1",  # == ceiling, so no raise
        sku_value_rank="top",
    )
    assert decision.kind == APPROVED_WITHIN_CEILING == "approved_within_ceiling"
    assert decision.applied_cadence_hours == 6.0
    assert decision.guided is None


def test_tier_below_ceiling_still_within_ceiling():
    # desired tier strictly below the ceiling is also "within" -- no raise needed.
    decision = plan_watch_escalation(
        site_config=_site_config(max_tier_allowed="L2", rate_limit_seconds=2.0),
        current_cadence_hours=24.0,
        desired_cadence_hours=6.0,
        desired_tier="L1",
        sku_value_rank="top",
    )
    assert decision.kind == APPROVED_WITHIN_CEILING
    assert decision.guided is None


# -- (b) tier raise beyond the ceiling: guided, never applied ------------------


def test_tier_raise_beyond_ceiling_returns_guided_never_applies():
    decision = plan_watch_escalation(
        site_config=_site_config(max_tier_allowed="L1"),
        current_cadence_hours=24.0,
        desired_cadence_hours=1.0,
        desired_tier="L2",  # one above the L1 ceiling
        sku_value_rank="top",
    )
    assert decision.kind == NEEDS_CEILING_RAISE == "needs_ceiling_raise"
    assert decision.applied_cadence_hours is None  # NOTHING applied, ever
    assert decision.guided is not None
    assert decision.guided.status in (OPTIONS, HANDOFF)
    assert passed_guided(decision.guided) is True


def test_tier_raise_far_beyond_ceiling_still_only_guided():
    # L0 ceiling, desired L3 -- the largest possible jump still only asks.
    decision = plan_watch_escalation(
        site_config=_site_config(max_tier_allowed="L0"),
        current_cadence_hours=24.0,
        desired_cadence_hours=0.5,
        desired_tier="L3",
        sku_value_rank="top",
    )
    assert decision.kind == NEEDS_CEILING_RAISE
    assert decision.applied_cadence_hours is None
    assert passed_guided(decision.guided) is True


def test_guided_outcome_is_never_executed_for_a_raise():
    decision = plan_watch_escalation(
        site_config=_site_config(max_tier_allowed="L1"),
        current_cadence_hours=24.0,
        desired_cadence_hours=1.0,
        desired_tier="L2",
        sku_value_rank="critical",
    )
    # Assert the NEGATIVE explicitly: a future third status value must not slip
    # through as "executed" for a tier raise.
    assert decision.guided is not None
    assert decision.guided.status != EXECUTED


def test_ceiling_raise_guided_passes_verify_guided_contract():
    # Prove it against the REAL guided-layer QA gate, not a hand-rolled check.
    decision = plan_watch_escalation(
        site_config=_site_config(max_tier_allowed="L1", domain="shop.example.test"),
        current_cadence_hours=24.0,
        desired_cadence_hours=2.0,
        desired_tier="L2",
        sku_value_rank="top",
    )
    assert verify_guided(decision.guided) == []


def test_ceiling_raise_handoff_carries_domain_tier_and_config_path():
    domain = "shop.example.test"
    decision = plan_watch_escalation(
        site_config=_site_config(max_tier_allowed="L1", domain=domain),
        current_cadence_hours=24.0,
        desired_cadence_hours=2.0,
        desired_tier="L2",
        sku_value_rank="premium",
    )
    guided = decision.guided
    assert guided.status == HANDOFF
    packet = guided.handoffs[0]
    steps_text = " ".join(packet.steps).lower()
    # Steps mention reviewing ToS/robots and raising the ceiling in the yaml.
    assert "robots" in steps_text
    assert "terms of service" in steps_text or "tos" in steps_text
    assert f"config/sites/{domain}.yaml" in " ".join(packet.steps)
    assert "L2" in packet.title and "L1" in packet.title
    # The requested tier and current ceiling travel in the packet data.
    assert packet.data["requested_tier"] == "L2"
    assert packet.data["current_ceiling"] == "L1"
    assert packet.data["sku_value_rank"] == "premium"
    # The residual states the risk of NOT raising.
    assert decision.guided.residuals
    assert decision.guided.residuals[0].risk_if_skipped.strip()


# -- (c) purity: no mutation, no file written ---------------------------------


def test_never_mutates_site_config_and_writes_no_config_file():
    config = _site_config(
        domain="purity-probe.test", max_tier_allowed="L1", rate_limit_seconds=5.0
    )
    before = dataclasses.asdict(config)

    sites_dir = Path("config/sites")
    listing_before = (
        sorted(p.name for p in sites_dir.iterdir()) if sites_dir.exists() else []
    )

    # Exercise BOTH branches; the ceiling-raise handoff even names a
    # config/sites/<domain>.yaml path -- prove that path is never actually written.
    plan_watch_escalation(
        site_config=config,
        current_cadence_hours=24.0,
        desired_cadence_hours=6.0,
        desired_tier="L1",
        sku_value_rank="top",
    )
    plan_watch_escalation(
        site_config=config,
        current_cadence_hours=24.0,
        desired_cadence_hours=6.0,
        desired_tier="L2",
        sku_value_rank="top",
    )

    assert dataclasses.asdict(config) == before  # byte-for-byte field state unchanged
    assert config.max_tier_allowed == "L1"  # ceiling explicitly untouched
    listing_after = (
        sorted(p.name for p in sites_dir.iterdir()) if sites_dir.exists() else []
    )
    assert listing_after == listing_before
    assert not (sites_dir / "purity-probe.test.yaml").exists()


def test_pure_function_is_deterministic():
    kwargs = dict(
        site_config=_site_config(max_tier_allowed="L1"),
        current_cadence_hours=24.0,
        desired_cadence_hours=3.0,
        desired_tier="L2",
        sku_value_rank="top",
    )
    first = plan_watch_escalation(**kwargs)
    second = plan_watch_escalation(**kwargs)
    assert first == second  # same inputs, byte-identical decision (incl. guided)


# -- (d) rate-limit floor is respected ----------------------------------------


def test_floor_cadence_respected():
    # rate_limit_seconds=7200s => a 2.0h floor. A 0.5h desire is clamped UP to 2.0h,
    # not honored as-is.
    decision = plan_watch_escalation(
        site_config=_site_config(max_tier_allowed="L1", rate_limit_seconds=7200.0),
        current_cadence_hours=6.0,
        desired_cadence_hours=0.5,  # faster than the floor
        desired_tier="L1",
        sku_value_rank="top",
    )
    assert decision.kind == APPROVED_WITHIN_CEILING
    assert decision.applied_cadence_hours == 2.0  # the floor
    assert decision.applied_cadence_hours != 0.5  # NOT the too-fast desire


def test_desired_cadence_within_floor_is_honored_unchanged():
    # A desire slower than the floor is not clamped.
    decision = plan_watch_escalation(
        site_config=_site_config(max_tier_allowed="L1", rate_limit_seconds=3600.0),  # 1.0h floor
        current_cadence_hours=24.0,
        desired_cadence_hours=6.0,  # slower than the 1.0h floor, so honored
        desired_tier="L1",
        sku_value_rank="top",
    )
    assert decision.kind == APPROVED_WITHIN_CEILING
    assert decision.applied_cadence_hours == 6.0


# -- (e) no_change branch ------------------------------------------------------


def test_no_change_when_desired_not_faster_than_current():
    decision = plan_watch_escalation(
        site_config=_site_config(max_tier_allowed="L1", rate_limit_seconds=2.0),
        current_cadence_hours=6.0,
        desired_cadence_hours=12.0,  # slower than current -> nothing to tighten
        desired_tier="L1",
        sku_value_rank="mid",
    )
    assert decision.kind == NO_CHANGE == "no_change"
    assert decision.applied_cadence_hours is None
    assert decision.guided is None


def test_no_change_when_floor_makes_desire_not_faster():
    # A too-fast desire that clamps to the floor, but the floor is not faster than
    # current -> no tightening happens.
    decision = plan_watch_escalation(
        site_config=_site_config(max_tier_allowed="L1", rate_limit_seconds=7200.0),  # 2.0h floor
        current_cadence_hours=2.0,
        desired_cadence_hours=0.25,
        desired_tier="L1",
        sku_value_rank="mid",
    )
    assert decision.kind == NO_CHANGE
    assert decision.applied_cadence_hours is None


# -- (f) input validation (fail fast at the boundary) -------------------------


def test_invalid_desired_tier_raises():
    with pytest.raises(ValueError):
        plan_watch_escalation(
            site_config=_site_config(),
            current_cadence_hours=24.0,
            desired_cadence_hours=6.0,
            desired_tier="L9",  # not a real tier
            sku_value_rank="top",
        )


@pytest.mark.parametrize("bad", [0.0, -1.0, float("inf"), float("nan")])
def test_non_positive_or_non_finite_cadence_raises(bad):
    with pytest.raises((ValueError, TypeError)):
        plan_watch_escalation(
            site_config=_site_config(),
            current_cadence_hours=bad,
            desired_cadence_hours=6.0,
            desired_tier="L1",
            sku_value_rank="top",
        )


def test_empty_sku_value_rank_raises():
    with pytest.raises(ValueError):
        plan_watch_escalation(
            site_config=_site_config(),
            current_cadence_hours=24.0,
            desired_cadence_hours=6.0,
            desired_tier="L1",
            sku_value_rank="   ",
        )


def test_decision_is_frozen():
    decision = plan_watch_escalation(
        site_config=_site_config(),
        current_cadence_hours=24.0,
        desired_cadence_hours=6.0,
        desired_tier="L1",
        sku_value_rank="top",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.kind = "tampered"  # type: ignore[misc]
    assert isinstance(decision, WatchEscalationDecision)

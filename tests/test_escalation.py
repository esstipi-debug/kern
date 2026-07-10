"""Tests for the escalation-packet builder (Guided Execution Layer, plan §2.14).

Bundles context + options + recommended response + citations and routes to the right
human with an SLA, filling sensible route/SLA defaults per trigger (dispute, legal,
financial threshold). Pure - no external deps.
"""

import pytest

from src.escalation import (
    DISPUTE,
    FINANCIAL,
    LEGAL,
    build_escalation,
    escalate,
    escalation_banner,
    maybe_escalate_financial,
)
from src.guided import (
    ESCALATED,
    OPTIONS,
    EscalationPacket,
    ExecutionOption,
    Residual,
    as_executed,
    as_options,
    passed_guided,
)


def test_build_escalation_fills_route_and_sla_by_trigger():
    packet = build_escalation(LEGAL, "HTS classification dispute on entry 7501")

    assert isinstance(packet, EscalationPacket)
    assert packet.route_to                       # a named human/role
    assert packet.sla                            # an SLA
    assert "broker" in packet.route_to.lower() or "legal" in packet.route_to.lower()


def test_explicit_route_and_sla_override_defaults():
    packet = build_escalation(DISPUTE, "damaged pallet", route_to="VP Ops", sla="within 1h")

    assert packet.route_to == "VP Ops"
    assert packet.sla == "within 1h"


def test_triggers_have_distinct_default_routes():
    routes = {
        build_escalation(DISPUTE, "x").route_to,
        build_escalation(LEGAL, "x").route_to,
        build_escalation(FINANCIAL, "x").route_to,
    }
    assert len(routes) == 3                       # each trigger routes somewhere distinct


def test_unknown_trigger_still_routes_and_has_sla():
    packet = build_escalation("mystery", "something odd")

    assert packet.route_to
    assert packet.sla


def test_build_escalation_requires_a_reason():
    with pytest.raises(ValueError):
        build_escalation(DISPUTE, "   ")


def test_escalate_returns_protected_escalation_outcome():
    options = [ExecutionOption("settle", "settle the claim", score=0.4)]
    outcome = escalate(
        "OS&D claim needs a human decision",
        FINANCIAL,
        "claim value exceeds the $5k auto-approve threshold",
        recommendation="counter at 60%",
        options=options,
        citations=["BOL #123", "photos x3"],
    )

    assert outcome.status == ESCALATED
    assert passed_guided(outcome)
    assert outcome.escalation.recommendation == "counter at 60%"
    assert outcome.escalation.options == options
    assert outcome.escalation.citations == ["BOL #123", "photos x3"]


# -- maybe_escalate_financial: gate a writeback tool's options on $ size ------


def _apply_options() -> list[ExecutionOption]:
    return [
        ExecutionOption("apply", "apply the staged changeset", score=3.0, recommended=True),
        ExecutionOption("export", "export the plan only", score=1.0),
    ]


def test_maybe_escalate_financial_passes_through_under_threshold():
    outcome = as_options("500 units short", _apply_options())

    result = maybe_escalate_financial(outcome, value=500.0, threshold=50_000.0)

    assert result is outcome


def test_maybe_escalate_financial_passes_through_at_exactly_the_threshold():
    outcome = as_options("at the line", _apply_options())

    result = maybe_escalate_financial(outcome, value=50_000.0, threshold=50_000.0)

    assert result is outcome


def test_maybe_escalate_financial_escalates_over_threshold_preserving_options():
    outcome = as_options("340000 restock", _apply_options(), confidence=0.9)

    result = maybe_escalate_financial(outcome, value=340_000.0, threshold=50_000.0)

    assert result.status == ESCALATED
    assert result.confidence == 0.9                      # routing confidence carried through
    assert result.escalation.route_to                    # routed to a named human/role
    assert result.escalation.sla                          # with an SLA
    assert result.escalation.options == outcome.options    # the ranked choices are NOT lost
    assert "340,000" in result.escalation.reason or "340000" in result.escalation.reason
    assert "50,000" in result.escalation.reason or "50000" in result.escalation.reason
    assert passed_guided(result)


def test_maybe_escalate_financial_preserves_existing_residuals():
    outcome = as_options(
        "big plan", _apply_options(),
        residuals=[Residual("approve and apply", risk_if_skipped="stockouts")],
    )

    result = maybe_escalate_financial(outcome, value=100_000.0, threshold=50_000.0)

    assert result.residuals == outcome.residuals


def test_maybe_escalate_financial_only_intercepts_options_outcomes():
    """A non-OPTIONS outcome (e.g. already EXECUTED) is never re-routed - this
    helper only gates the freely-actionable 'options' outcome writeback tools emit."""
    outcome = as_executed("nothing to do")

    result = maybe_escalate_financial(outcome, value=1_000_000.0, threshold=1.0)

    assert result is outcome
    assert result.status != OPTIONS


def test_maybe_escalate_financial_keeps_options_visible_at_the_top_level():
    """Every existing consumer that builds the deck's action menu reads the
    TOP-LEVEL `outcome.options` (scm_agent/orchestrator.py, scm_agent/packages.py),
    not `outcome.escalation.options` - so the escalated outcome must carry both,
    or the ranked choices silently vanish from every rendered deliverable even
    though the data model still technically "has" them."""
    outcome = as_options("340000 restock", _apply_options())

    result = maybe_escalate_financial(outcome, value=340_000.0, threshold=50_000.0)

    assert result.options == outcome.options
    assert result.options == result.escalation.options


# -- escalation_banner: makes an ESCALATED outcome unmissable in a rendered deck --


def test_escalation_banner_is_none_for_a_non_escalated_outcome():
    assert escalation_banner(as_options("fine", _apply_options())) is None
    assert escalation_banner(as_executed("done")) is None


def test_escalation_banner_states_reason_route_and_sla():
    outcome = maybe_escalate_financial(
        as_options("340000 restock", _apply_options()), value=340_000.0, threshold=50_000.0,
    )

    banner = escalation_banner(outcome)

    assert banner is not None
    assert "340,000" in banner or "340000" in banner
    assert outcome.escalation.route_to in banner
    assert outcome.escalation.sla in banner

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
)
from src.guided import ESCALATED, EscalationPacket, ExecutionOption, passed_guided


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

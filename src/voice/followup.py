"""linchpin-voice-followup orchestration (capability M16) - the credential-free loop.

Wires the voice brain end to end with no phone network and no credentials:

    compliance gate -> build_agent_config -> place call -> capture CallOutcome -> sync

Every branch returns a ``VoiceFollowupResult`` carrying a never-unprotected
``GuidedOutcome`` (Guided Execution Layer, nosilo_core/guided.py): the call is either executed
and its result safely staged/applied to the system of record, or the human is handed a
ranked escalation / prepared manual follow-up. The dial itself is injected as a
``VoiceCaller``; with ``DryRunCaller`` the whole flow is testable offline.
"""

from __future__ import annotations

from dataclasses import dataclass

from nosilo_core.guided import (
    EscalationPacket,
    GuidedOutcome,
    HandoffPacket,
    Residual,
    as_escalation,
    as_executed,
    as_handoff,
)
from nosilo_core.writeback import (
    TIER_REVERSIBLE,
    ApplyResult,
    Changeset,
    InMemoryStore,
    apply,
    stage,
)

from .agent_config import build_agent_config
from .caller import CallOutcome, CallRequest, VoiceCaller
from .compliance import ComplianceDecision, can_dial
from .playbooks import get_playbook

# Where the captured shipment facts are written. Reversible: a status/ETA field set
# can be cleanly rolled back via the writeback audit trail.
_SYNC_TARGET = "tms"
_SYNC_TIER = TIER_REVERSIBLE


@dataclass(frozen=True)
class Contact:
    """The party being called and the consent/DNC state the compliance gate needs."""

    name: str
    phone: str
    consent: bool = False
    on_dnc: bool = False
    all_party_state: bool = False


@dataclass(frozen=True)
class VoiceFollowupResult:
    """The full record of one follow-up attempt, with its protected outcome."""

    guided: GuidedOutcome
    decision: ComplianceDecision
    outcome: CallOutcome | None = None
    request: CallRequest | None = None
    changeset: Changeset | None = None
    apply_result: ApplyResult | None = None


def run_voice_followup(
    *,
    playbook_key: str,
    shipper: str,
    contact: Contact,
    caller: VoiceCaller,
    store: InMemoryStore,
    now_local_hour: int,
    dynamic_vars: dict | None = None,
    attempts: int = 0,
    sync_entity_id: str | None = None,
    auto_apply_reversible: bool = True,
) -> VoiceFollowupResult:
    """Run one compliance-gated, safely-synced voice follow-up. Never dead-ends."""
    playbook = get_playbook(playbook_key)  # validates the key up front
    dynamic_vars = dict(dynamic_vars or {})
    entity_id = sync_entity_id or dynamic_vars.get("entity_id")

    # 1. Compliance gate - must pass before any dial.
    decision = can_dial(
        consent=contact.consent,
        on_dnc=contact.on_dnc,
        now_local_hour=now_local_hour,
        attempts=attempts,
        all_party_state=contact.all_party_state,
    )
    if not decision.allowed:
        return VoiceFollowupResult(
            guided=_blocked_guided(contact, playbook.name, decision),
            decision=decision,
        )

    # 2. Build the deployable agent config for this call type.
    cfg = build_agent_config(playbook, shipper=shipper)

    # 3. Place the call (injected caller; dry-run keeps it credential-free).
    request = CallRequest(
        to=contact.phone,
        agent_config=cfg,
        dynamic_vars=dynamic_vars,
        playbook_key=playbook_key,
        requires_ai_disclosure=decision.requires_ai_disclosure,
        requires_recording_consent=decision.requires_recording_consent,
    )
    outcome = caller.place(request)

    # 4. A call that surfaced an escalate_when condition routes to a human; we do NOT
    #    auto-write disputed/sensitive facts.
    if outcome.needs_human:
        return VoiceFollowupResult(
            guided=_escalation_guided(playbook, outcome),
            decision=decision,
            outcome=outcome,
            request=request,
        )

    # 5. A call that did not connect (dry-run / no answer / voicemail) becomes a
    #    prepared manual follow-up.
    if not outcome.placed:
        return VoiceFollowupResult(
            guided=_handoff_guided(contact, playbook, outcome),
            decision=decision,
            outcome=outcome,
            request=request,
        )

    # 6. Connected call: safely stage + apply the captured facts to the store.
    changeset, apply_result = _sync_capture(
        store, outcome, entity_id, auto_apply_reversible=auto_apply_reversible
    )
    return VoiceFollowupResult(
        guided=_executed_guided(playbook, outcome, apply_result),
        decision=decision,
        outcome=outcome,
        request=request,
        changeset=changeset,
        apply_result=apply_result,
    )


# -- sync -----------------------------------------------------------------------------


def _sync_capture(
    store: InMemoryStore,
    outcome: CallOutcome,
    entity_id: str | None,
    *,
    auto_apply_reversible: bool,
) -> tuple[Changeset | None, ApplyResult | None]:
    """Stage the non-null captured fields into the store and apply them idempotently."""
    fields = {k: v for k, v in outcome.captured.items() if v is not None}
    if not fields or not entity_id:
        return None, None

    key = outcome.call_id or f"voice:{outcome.status}:{entity_id}"
    changeset = stage(
        store,
        target=_SYNC_TARGET,
        edits={entity_id: fields},
        risk_tier=_SYNC_TIER,
        idempotency_key=key,
        reason="voice follow-up capture sync",
    )
    result = apply(store, changeset, auto_apply_reversible=auto_apply_reversible)
    return changeset, result


# -- guided outcomes (one per branch; all protected by construction) ------------------


def _blocked_guided(
    contact: Contact, playbook_name: str, decision: ComplianceDecision
) -> GuidedOutcome:
    packet = EscalationPacket(
        reason="outbound call blocked by compliance: " + "; ".join(decision.reasons),
        route_to="account owner / compliance",
        recommendation=(
            "resolve the blocking reason(s) before re-attempting: document consent, "
            "remove the number from the DNC list, or wait for the permitted calling window"
        ),
        citations=list(decision.reasons),
        sla="before next attempt",
    )
    return as_escalation(
        f"Call to {contact.name} not placed - blocked by compliance",
        packet,
        residuals=[
            Residual(
                description=f"the {playbook_name} follow-up is still open",
                risk_if_skipped="the shipment exception stays unresolved until contact is made",
            )
        ],
    )


def _escalation_guided(playbook, outcome: CallOutcome) -> GuidedOutcome:
    packet = EscalationPacket(
        reason=f"call surfaced a condition requiring a human: {playbook.escalate_when}",
        route_to=f"human owner for {playbook.name}",
        recommendation="review the captured facts and take the liability/commercial decision",
        citations=[f"{k}={v}" for k, v in outcome.captured.items()],
        sla="same business day",
    )
    return as_escalation(f"Call completed; escalating per playbook - {playbook.name}", packet)


def _handoff_guided(contact: Contact, playbook, outcome: CallOutcome) -> GuidedOutcome:
    packet = HandoffPacket(
        title=f"Complete the {playbook.name} follow-up manually",
        steps=[
            f"The automated call did not connect (status: {outcome.status}).",
            f"Reach {contact.name} at {contact.phone} and run the playbook.",
            *[f"Ask: {q}" for q in playbook.questions],
            f"Record: {', '.join(playbook.capture)}.",
        ],
        risk_if_skipped="the shipment exception this call targets stays unresolved",
    )
    return as_handoff(
        f"Call not completed ({outcome.status}); prepared a manual follow-up", [packet]
    )


def _executed_guided(playbook, outcome: CallOutcome, apply_result: ApplyResult | None) -> GuidedOutcome:
    synced = sorted(k for k, v in outcome.captured.items() if v is not None)
    if apply_result is None:
        summary = f"{playbook.name}: call completed; no fields to sync"
    elif apply_result.idempotent_skip:
        summary = f"{playbook.name}: call completed; capture already synced (idempotent)"
    else:
        summary = f"{playbook.name}: call completed; synced {len(synced)} field(s) to system of record"
    return as_executed(summary)

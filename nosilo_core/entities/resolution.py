"""Deciding which source wins a field, reproducibly.

Two systems will disagree about the same attribute - constantly, and usually for
boring reasons (one is stale, one normalises differently, one was edited by
hand). The merge rule therefore has to satisfy three properties at once:

**Deterministic.** Replaying the same observations in the same order must produce
byte-identical results, or the audit trail is fiction and a backfill silently
rewrites history. Every tie is broken by an explicit, total ordering - never by
"whichever arrived first" or by dict iteration order.

**Explainable.** When someone asks why the ERP's price lost to the portal's, the
answer has to be a rule with a name, not a scoring heuristic nobody can
reconstruct. Hence :data:`REASON_OWNER` and friends.

**Safe by default on the fields that get people robbed.** Bank details, credit
limits and contract prices are never overwritten by an automated feed. They
become a :class:`FieldConflict` for a human instead. Supplier bank-account
takeover is among the cheapest frauds in existence and it arrives looking
exactly like a routine master-data update.

The precedence order, highest first:

1. **Declared owner** - the field's system of record always wins; nobody else
   may overwrite what the owner said.
2. **Guarded** - never auto-overwritten once set; a differing claim raises a
   conflict.
3. **Trust**, then **recorded_at**, then **source** lexicographically - the
   deterministic tail. The last step is what makes the whole thing total.

Everything in this module is a pure function of its arguments. No clock, no
storage, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .model import ABSENT, EntityType, FieldProvenance, FieldValue

#: The incoming source is the field's declared system of record.
REASON_OWNER = "owner"
#: The field has an owner and the incoming source is not it.
REASON_NOT_OWNER = "not_owner"
#: First value this field has ever had.
REASON_FIRST = "first_value"
#: Incoming trust strictly exceeded the current value's.
REASON_TRUST = "higher_trust"
#: Trust tied; the more recently recorded observation won.
REASON_RECENCY = "more_recent"
#: Trust and recency both tied; broken on source ordering so replay is stable.
REASON_TIEBREAK = "source_order"
#: Guarded field with an existing, different value - held for a human.
REASON_GUARDED = "guarded"
#: Incoming value equals the current one; nothing changes.
REASON_UNCHANGED = "unchanged"
#: The same source revised its own earlier claim.
REASON_RESTATED = "restated_by_same_source"


@dataclass(frozen=True)
class FieldConflict:
    """A claim that was *not* applied, and why.

    Conflicts are returned rather than raised: one guarded field must not abort
    an observation that also carries twenty perfectly ordinary ones. The caller
    persists the accepted fields and routes these to whoever arbitrates.
    """

    field: str
    kept: Any
    kept_source: str
    rejected: Any
    rejected_source: str
    reason: str

    def describe(self) -> str:
        return (
            f"{self.field}: kept {self.kept!r} from {self.kept_source}, "
            f"rejected {self.rejected!r} from {self.rejected_source} ({self.reason})"
        )


@dataclass(frozen=True)
class Resolution:
    """The outcome of merging one observation into an entity's current fields."""

    fields: Mapping[str, FieldValue]
    conflicts: tuple[FieldConflict, ...]
    accepted: frozenset[str]

    @property
    def changed(self) -> bool:
        """Whether anything actually moved.

        Drives the store's "no-op observations create no version" rule: a monitor
        re-reading an unchanged catalogue every hour must not append a version per
        cycle, or the history becomes unreadable and the table grows without
        carrying information.
        """
        return bool(self.accepted)


def _wins_on_tail(incoming: FieldProvenance, current: FieldProvenance) -> tuple[bool, str]:
    """The deterministic tail: trust, then recency, then source ordering.

    Returns ``(incoming_wins, reason)``. The final comparison is on the fully
    ordered :class:`~nosilo_core.entities.model.SourceRef`, so two distinct
    sources can never tie and the result never depends on arrival order.
    """
    # A source is always authoritative about its own previous claim: if the ERP
    # said 5 and now says 7, the ERP wins, full stop. This has to come before the
    # trust/recency comparison rather than after it, because those can tie - two
    # observations from one poller land inside the same clock tick on a platform
    # with coarse timer resolution (~15 ms on Windows), which would otherwise
    # make a genuine update look like a duplicate and silently drop it.
    if incoming.source == current.source:
        return True, REASON_RESTATED
    if incoming.trust != current.trust:
        return incoming.trust > current.trust, REASON_TRUST
    if incoming.recorded_at != current.recorded_at:
        return incoming.recorded_at > current.recorded_at, REASON_RECENCY
    return incoming.source > current.source, REASON_TIEBREAK


def resolve(
    entity_type: EntityType,
    current: Mapping[str, FieldValue],
    incoming: Mapping[str, Any],
    provenance: FieldProvenance,
) -> Resolution:
    """Merge one source's observation into an entity's current field set.

    ``incoming`` maps attribute name to claimed value. A value of
    :data:`~nosilo_core.entities.model.ABSENT` means "this source did not report
    this field", which is materially different from reporting ``None`` ("this
    source says there is no value") - the first must never clear a value another
    system owns, the second is a real claim.
    """
    resolved = dict(current)
    conflicts: list[FieldConflict] = []
    accepted: set[str] = set()

    incoming_system = provenance.source.system

    for name in sorted(incoming):  # sorted: conflict order is reproducible too
        claimed = incoming[name]
        if claimed is ABSENT:
            continue

        held = resolved.get(name)
        owner = entity_type.field_owners.get(name)

        if held is None:
            # Nothing to conflict with. Guarded fields are guarded against being
            # *changed*, not against ever being populated - the initial value has
            # to arrive somehow, and the audit trail records where it came from.
            resolved[name] = FieldValue(claimed, provenance)
            accepted.add(name)
            continue

        if held.value == claimed:
            continue  # REASON_UNCHANGED: no version churn, no conflict

        held_system = held.provenance.source.system

        if owner is not None:
            if incoming_system == owner:
                resolved[name] = FieldValue(claimed, provenance)
                accepted.add(name)
            elif held_system == owner:
                conflicts.append(
                    FieldConflict(
                        field=name,
                        kept=held.value,
                        kept_source=str(held.provenance.source),
                        rejected=claimed,
                        rejected_source=str(provenance.source),
                        reason=REASON_NOT_OWNER,
                    )
                )
            else:
                # The owner has not spoken yet; two non-owners are competing.
                wins, reason = _wins_on_tail(provenance, held.provenance)
                if wins:
                    resolved[name] = FieldValue(claimed, provenance)
                    accepted.add(name)
                else:
                    conflicts.append(
                        FieldConflict(
                            field=name,
                            kept=held.value,
                            kept_source=str(held.provenance.source),
                            rejected=claimed,
                            rejected_source=str(provenance.source),
                            reason=reason,
                        )
                    )
            continue

        if name in entity_type.guarded_fields:
            conflicts.append(
                FieldConflict(
                    field=name,
                    kept=held.value,
                    kept_source=str(held.provenance.source),
                    rejected=claimed,
                    rejected_source=str(provenance.source),
                    reason=REASON_GUARDED,
                )
            )
            continue

        wins, reason = _wins_on_tail(provenance, held.provenance)
        if wins:
            resolved[name] = FieldValue(claimed, provenance)
            accepted.add(name)
        else:
            conflicts.append(
                FieldConflict(
                    field=name,
                    kept=held.value,
                    kept_source=str(held.provenance.source),
                    rejected=claimed,
                    rejected_source=str(provenance.source),
                    reason=reason,
                )
            )

    return Resolution(fields=resolved, conflicts=tuple(conflicts), accepted=frozenset(accepted))

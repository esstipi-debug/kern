"""Identity, provenance and bitemporal versioning for canonical entities.

Everything here is a frozen dataclass and clock-free: timestamps and identities
enter as arguments (the convention ``src/audit_evidence.py`` already sets), so a
backfill or a replay produces byte-identical results to the original run.

Three ideas carry the whole design:

**Identity is minted once and never reused.** A ``nosilo_id`` is a time-ordered
UUID that belongs to the entity forever. Source systems keep their own ids; the
mapping between them lives in :class:`Xref`, never in the entity itself. That is
what lets the same physical SKU exist as ``product.product/42`` in Odoo and
``variant/99`` in Shopify without either system's id leaking into the canonical
model.

**Provenance is per field, not per record.** A supplier's legal name may come
from the ERP while its bank account comes from a vetted portal and its risk
score from a third party. Recording one ``source`` for the whole row throws that
away, and it is exactly the information you need when two systems disagree. Each
attribute therefore carries its own :class:`FieldProvenance`.

**Time is two-dimensional.** ``valid_from``/``valid_to`` is *business* time -
when the fact was true in the world. ``recorded_at``/``superseded_at`` is
*system* time - when this store learned it. Auditors ask both questions, and
only the second one can answer "what did we know on the day that decision was
made". Keeping just one of them cannot be repaired later, because the
information was never captured.

Timestamps are ISO-8601 UTC strings rather than ``datetime`` objects: they
round-trip through JSON and SQLite unchanged, sort lexically, and cannot silently
acquire a local timezone in the middle of a pipeline. :func:`utc_now` is the one
place a clock is read.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

#: Marker for "this source did not report this field at all", which is different
#: from reporting it as ``None`` (an explicit "no value"). Mirrors the
#: ``ABSENT`` sentinel ``nosilo_core.writeback`` uses for the same distinction.
ABSENT: Any = object()


def utc_now() -> str:
    """Current UTC instant as an ISO-8601 string with explicit offset."""
    return datetime.now(timezone.utc).isoformat()


def new_entity_id() -> str:
    """A fresh, time-ordered entity id.

    UUIDv7 where the runtime offers it (Python 3.14+), UUIDv4 otherwise. Both are
    valid ``nosilo_id`` values and nothing in the store depends on the version -
    the ordering is a convenience for index locality and for eyeballing which of
    two entities was created first, not a correctness property.
    """
    uuid7 = getattr(uuid, "uuid7", None)
    return str(uuid7() if uuid7 is not None else uuid.uuid4())


def to_decimal(value: Decimal | int | float | str) -> Decimal:
    """Money/quantity coercion that never routes through binary float.

    ``Decimal(0.1)`` is 0.1000000000000000055511151231257827..., ``Decimal("0.1")``
    is exactly 0.1. Going through ``str`` first is the difference, and it is the
    reason this helper exists rather than callers constructing ``Decimal`` inline.
    Matches ``src/pricing_guardrails.py::_to_decimal``.
    """
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True, order=True)
class SourceRef:
    """A record's identity *in the system it came from*.

    ``entity`` is that system's own object name (``product.product``,
    ``InventoryItem``, a sheet name) - kept verbatim rather than normalised, so
    the reference stays resolvable by someone looking at the source system.

    Ordered so resolution can break ties lexicographically and reproducibly.
    """

    system: str
    entity: str
    id: str

    def __str__(self) -> str:
        return f"{self.system}:{self.entity}:{self.id}"


@dataclass(frozen=True)
class FieldProvenance:
    """Where one attribute's current value came from, and how much it is trusted.

    ``trust`` is only ever used to break ties between sources that both claim a
    field with no declared owner; it is deliberately not a probability and
    nothing interprets its absolute magnitude.
    """

    source: SourceRef
    recorded_at: str
    trust: float = 1.0


@dataclass(frozen=True)
class FieldValue:
    """One attribute: its value plus the provenance of *that* value."""

    value: Any
    provenance: FieldProvenance


@dataclass(frozen=True)
class Xref:
    """A link between a canonical entity and one source-system record.

    ``confidence`` and ``method`` are carried from the moment the link is made so
    a later audit can tell an exact key match from a human decision from a fuzzy
    guess - which matters enormously when a link turns out to be wrong.
    """

    nosilo_id: str
    source: SourceRef
    confidence: float
    method: str
    recorded_at: str


#: How a link between a source record and a canonical entity was established.
LINK_EXACT_KEY = "exact_key"  # matched on a declared natural key
LINK_MANUAL = "manual"  # a human said so
LINK_MINTED = "minted"  # no match existed; this record created the entity


@dataclass(frozen=True)
class EntityVersion:
    """An immutable snapshot of one entity's attributes, in both time axes.

    Versions are appended, never updated. The current version of an entity is the
    one with ``superseded_at is None``; the version that was current at some past
    instant is found by filtering on the two time ranges rather than by walking
    back through diffs.
    """

    nosilo_id: str
    entity_type: str
    version: int
    fields: Mapping[str, FieldValue]
    valid_from: str
    recorded_at: str
    valid_to: str | None = None
    superseded_at: str | None = None
    change_reason: str = ""

    @property
    def attributes(self) -> dict[str, Any]:
        """Just the values, for callers that do not care where they came from."""
        return {name: fv.value for name, fv in self.fields.items()}

    @property
    def is_current(self) -> bool:
        return self.superseded_at is None

    def provenance_of(self, name: str) -> FieldProvenance | None:
        found = self.fields.get(name)
        return found.provenance if found is not None else None

    def sources(self) -> tuple[SourceRef, ...]:
        """Every distinct source that owns at least one field, in a stable order."""
        return tuple(sorted({fv.provenance.source for fv in self.fields.values()}))


@dataclass(frozen=True)
class EntityType:
    """The declaration a domain pack registers to define one kind of entity.

    The core stores entities without knowing what any of them mean; this is the
    entire vocabulary a domain has to supply. Keeping it this small is the point:
    a source-to-pay pack registers ``Invoice`` the same way the SCM pack
    registers ``Sku``, and neither one needs a change in ``nosilo_core``.

    ``natural_keys``
        Attributes that identify the entity across systems (a SKU code, a tax
        id). Used for *exact* matching only - probabilistic entity resolution is
        deliberately not part of this layer.
    ``field_owners``
        ``field -> source system`` for attributes with a declared system of
        record. A claim from the owning system always wins; a claim from any
        other system never overwrites the owner's value.
    ``guarded_fields``
        Attributes that are never silently overwritten by an automated
        observation, whatever the trust scores say - bank details, credit limits,
        contract prices. A competing claim is surfaced as a conflict for a human
        instead of being applied. Supplier bank-account takeover is one of the
        cheapest and most common frauds there is; making it structurally
        impossible to do by data feed costs nothing here.
    """

    name: str
    natural_keys: tuple[str, ...] = ()
    field_owners: Mapping[str, str] = field(default_factory=dict)
    guarded_fields: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("EntityType.name must be a non-empty string")
        overlap = set(self.field_owners) & set(self.guarded_fields)
        if overlap:
            raise ValueError(
                f"EntityType {self.name!r}: {sorted(overlap)} declared both owned and guarded. "
                "An owned field auto-applies from its owner; a guarded field never auto-applies. "
                "Pick one - the combination has no coherent meaning."
            )


class EntityTypeRegistry:
    """The registered entity types, by name.

    A plain instance rather than a module-level global so a test (or a second
    domain pack in the same process) can build its own without leaking into
    anyone else's - the same instance-based choice ``ToolRegistry`` makes.
    """

    def __init__(self) -> None:
        self._types: dict[str, EntityType] = {}

    def register(self, entity_type: EntityType) -> EntityType:
        existing = self._types.get(entity_type.name)
        if existing is not None and existing != entity_type:
            raise ValueError(
                f"entity type {entity_type.name!r} is already registered with a different "
                "definition; re-registering under the same name would silently change how "
                "already-stored entities resolve"
            )
        self._types[entity_type.name] = entity_type
        return entity_type

    def get(self, name: str) -> EntityType:
        try:
            return self._types[name]
        except KeyError:
            known = ", ".join(sorted(self._types)) or "(none)"
            raise KeyError(f"unknown entity type {name!r}; registered: {known}") from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._types))

    def __contains__(self, name: object) -> bool:
        return name in self._types

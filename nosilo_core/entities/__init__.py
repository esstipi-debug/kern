"""Canonical entities: stable identity, per-field provenance, bitemporal history.

The seed of the canonical model. Kern up to now has been job-centric - a file
goes in, a deliverable comes out, nothing is remembered between runs. That is
fine for producing a plan and useless for answering "what did we know about this
supplier on the day we approved that PO", for reconciling two systems that
disagree, or for letting a second domain pack build on facts the first one
already established.

This package stores *entities*, not jobs. Three pieces:

``model``
    Identity (:func:`~.model.new_entity_id`), source references, per-field
    provenance, bitemporal versions, and the :class:`~.model.EntityType`
    declaration a domain pack registers.
``resolution``
    The pure, deterministic rule for which source wins a contested field -
    declared owner, then guarded-field hold, then trust/recency/source ordering.
``store``
    An append-only SQLite store with cross-references to source-system ids.

The core knows nothing about what a SKU or a supplier *is*: entity types are
declared by the domain (see ``src/entities.py`` for the supply-chain ones). That
is the whole point - a source-to-pay pack registers ``Invoice`` the same way,
with no change here.

Deliberately **not** in this layer yet, because each is a design decision rather
than a mechanical addition: probabilistic entity resolution and merge/unmerge,
backdated corrections, and policy-filtered reads. Natural-key matching here is
exact only; a wrong probabilistic match welds two real entities together and is
painful to undo, so it belongs behind a human review queue rather than in the
write path.
"""

from .model import (
    ABSENT,
    LINK_EXACT_KEY,
    LINK_MANUAL,
    LINK_MINTED,
    EntityType,
    EntityTypeRegistry,
    EntityVersion,
    FieldProvenance,
    FieldValue,
    SourceRef,
    Xref,
    new_entity_id,
    to_decimal,
    utc_now,
)
from .resolution import (
    REASON_GUARDED,
    REASON_NOT_OWNER,
    REASON_OWNER,
    REASON_RECENCY,
    REASON_RESTATED,
    REASON_TIEBREAK,
    REASON_TRUST,
    FieldConflict,
    Resolution,
    resolve,
)
from .store import (
    ACTION_READ,
    DEFAULT_PATH,
    EntityStoreError,
    ObserveResult,
    ReadResult,
    SqliteEntityStore,
)

__all__ = [
    "ABSENT",
    "ACTION_READ",
    "DEFAULT_PATH",
    "EntityStoreError",
    "EntityType",
    "EntityTypeRegistry",
    "EntityVersion",
    "FieldConflict",
    "FieldProvenance",
    "FieldValue",
    "LINK_EXACT_KEY",
    "LINK_MANUAL",
    "LINK_MINTED",
    "ObserveResult",
    "ReadResult",
    "REASON_GUARDED",
    "REASON_NOT_OWNER",
    "REASON_OWNER",
    "REASON_RECENCY",
    "REASON_RESTATED",
    "REASON_TIEBREAK",
    "REASON_TRUST",
    "Resolution",
    "SourceRef",
    "SqliteEntityStore",
    "Xref",
    "new_entity_id",
    "resolve",
    "to_decimal",
    "utc_now",
]

"""nosilo_core — the domain-agnostic control plane every domain pack runs on.

This package is the part of Kern that is *not* about supply chain. It carries no
knowledge of EOQ, safety stock, replenishment or pricing, and it must stay that
way: it is the substrate a second domain pack (source-to-pay, order-to-cash,
customer operations) is meant to reuse without inheriting SCM.

What lives here, and why each piece is core rather than domain:

``guided``
    The never-unprotected contract. Every result is ``EXECUTED`` or carries an
    executable path (``OPTIONS`` / ``HANDOFF`` / ``ESCALATED``). Nothing about
    that shape is supply-chain specific.
``escalation``
    Typed routes to the right human, built on ``guided``'s packets.
``writeback``
    The safe-staging write plane: dry-run changeset -> risk tier by
    reversibility -> signed, content-bound approval -> idempotent apply ->
    auditable rollback. A connector to *any* system of record implements the
    same surface.
``writeback_store``
    Durable audit/idempotency ledger for the above.
``registry``
    The ``prepare -> run -> qa -> deliver`` tool contract and the registry that
    drives it, including the "QA fails => no deliverable" veto.
``types``
    ``JobRequest`` / ``JobResult`` and the status vocabulary shared by every
    tool in every domain.
``llm``
    The ``LLMProvider`` protocol plus a rules-only fallback, so a domain pack
    never imports a vendor SDK directly. This is the seat a multi-model router
    takes later.
``entities``
    Canonical entities: stable identity, per-field provenance, bitemporal
    history, and cross-references to source-system ids. The core stores them
    without knowing what any of them mean - entity types are declared by the
    domain (``src/entities.py`` for the supply-chain ones).

Dependency direction is one-way and enforced by
``tests/test_core_boundary.py``: ``nosilo_core`` may not import from ``src``,
``scm_agent``, ``jobs`` or ``webapp``. Domain packs depend on the core; the core
never depends on a domain. If a change here needs something from ``src``, that
is the signal the thing being added is domain logic and belongs in the domain
pack instead.

Imports inside this package are relative on purpose: the package is meant to be
liftable into its own distribution without editing a single import line.
"""

from . import entities, escalation, guided, llm, registry, types, writeback, writeback_store

__all__ = [
    "entities",
    "escalation",
    "guided",
    "llm",
    "registry",
    "types",
    "writeback",
    "writeback_store",
]

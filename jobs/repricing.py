"""Multichannel repricing playbook (Linchpin 3.0 PR-18, plan section 7 P3
``repricing_multichannel``).

Turns a price list -- PR-16's ``src.price_optimizer`` portfolio output (via
:func:`prices_from_optimizer`) or a manually-specified ``{sku: price}``
dict, either way -- into a per-channel safe-staging ``Changeset`` against
Shopify/MercadoLibre/Odoo (``src.connectors.{shopify,meli,odoo}_prices``),
gated by PR-17's ``src.pricing_guardrails.gate_price_changeset`` BEFORE the
changeset is ever handed back as something an operator can approve
(:func:`stage_repricing` raises :class:`RepricingGuardrailBlocked` instead
of returning a stageable changeset -- plan QA row: "changeset sin
explicacion legible + citas => no sale"), applied only under a real signed
``Approval`` (:func:`apply_repricing` never sets
``auto_apply_reversible=True`` -- Golden Rule 11: autonomy for a brand-new
external write surface is not earned yet, so this NEVER auto-applies even
though a price change is itself a ``TIER_REVERSIBLE`` risk tier), and
verified by reading the channel back after apply so a write that silently
didn't take is caught immediately, not trusted
(:func:`verify_applied` -- plan QA row: "apply sin verificacion = incidente").

Pure orchestration over the safe-staging plane already proven by
``src/writeback.py`` + ``src/connectors/{odoo,excel}.py``: this module adds
zero new safety machinery, only channel wiring. A multichannel client is a
separate ``PriceStore`` per channel (one ``ShopifyPriceStore``,
``MeliPriceStore``, or ``OdooPriceStore`` instance each) -- this module
stages/applies ONE channel per call so an operator reviews and approves
each channel's diff independently; a CLI/webapp wiring multiple channels
together is a later PR's concern (not this one's).
"""

from __future__ import annotations

from dataclasses import dataclass

from scm_agent.knowledge import GroundedCitation, KnowledgeBase
from src import writeback
from src.price_optimizer import PriceOptimizationResult
from src.pricing_guardrails import GATE_MAX_MOVE_PCT_DEFAULT, GuardrailGateResult, gate_price_changeset

# L3 citation grounding for the central gate (scm_agent.citation_gate's
# already-registered "pricing" tool_key anchors: basic_pricing_theory /
# price_sensitivity_measurement / markdown_pricing -- no new anchor mapping
# needed). Verified to resolve >=2 real citations against the committed
# books graph with an empty brief (tests/test_repricing_job.py).
#
# _CANDIDATE_POOL was 3 (the same shallow-pool recall defect fixed in
# jobs/integrated_plan.py, jobs/price_intelligence.py and
# scm_agent/packages.py::_run_step, 3.0-audit finding #7 -- this module was
# the one instance that defect fix never reached): with only the top 3
# candidates offered to the strict MAX_HOPS gate, a new unrelated source
# added to the books graph can rank its own on-topic-sounding labels above
# the real "pricing" anchors and starve the gate down to zero surviving
# citations, blocking every repricing changeset regardless of how good the
# reason text is. Widened to 8 to match packages.py's empirically-verified
# ceiling for this exact tool_key's anchor set -- grounding stays on the
# fixed keyword set above, not the caller's free-text reason, so this is
# deterministic across every call.
_CANDIDATE_POOL = 8
_CITATION_KEYWORDS = (
    "price optimization", "price change", "repricing", "markdown pricing",
    "price elasticity", "multichannel pricing",
)
_TOOL_KEY = "pricing"

TIER = writeback.TIER_REVERSIBLE  # a price can always be set back -- plan section 7 P3


class RepricingGuardrailBlocked(RuntimeError):
    """Raised by :func:`stage_repricing` when PR-17's central pricing gate
    rejects the changeset -- it is never handed back as a stageable
    ``Changeset`` an operator could approve (plan section 7 P3's QA row).
    ``self.gate`` carries the full verdict (reason + any citations) for
    whatever caught this to log or surface."""

    def __init__(self, channel: str, gate: GuardrailGateResult) -> None:
        self.channel = channel
        self.gate = gate
        super().__init__(f"repricing for channel {channel!r} blocked by pricing guardrails: {gate.reason}")


class RepricingVerificationFailed(RuntimeError):
    """Raised by :func:`verify_applied` when a post-apply read-back does not
    match what was staged -- plan section 7: "apply sin verificacion =
    incidente". Never silently swallowed; ``self.mismatches`` is
    ``((sku, staged_value, live_value), ...)`` for whatever caught this to
    report as an incident."""

    def __init__(self, channel: str, mismatches: tuple[tuple[str, object, object], ...]) -> None:
        self.channel = channel
        self.mismatches = mismatches
        detail = "; ".join(f"{sku}: staged={staged!r} live={live!r}" for sku, staged, live in mismatches)
        super().__init__(f"post-apply verification failed for channel {channel!r}: {detail}")


def prices_from_optimizer(results: dict[str, PriceOptimizationResult]) -> dict[str, float]:
    """Extract a ``{sku: proposed_price}`` map from PR-16's
    ``optimize_portfolio_prices`` output. Only ``status == "ok"`` entries
    carry a ``proposed_price``; ``needs_data`` SKUs are silently excluded --
    never a fabricated price (matches PR-16's own "needs_data, never a
    fabricated number" contract, and Golden Rule 14: this is an intentional
    drop of SKUs that have no signal, not a hidden cap on ones that do).
    """
    return {
        sku: r.proposed_price
        for sku, r in results.items()
        if r.status == "ok" and r.proposed_price is not None
    }


def gated_citations(
    brief: str = "", *, kb: KnowledgeBase | None = None, limit: int = _CANDIDATE_POOL,
) -> list[GroundedCitation]:
    """Candidate L3 citations for the central gate -- the RAW ranked
    candidates (:meth:`KnowledgeBase.ground_citations_detailed`), not
    pre-filtered: :func:`~src.pricing_guardrails.gate_price_changeset`
    itself runs ``scm_agent.citation_gate.filter_citations`` on whatever is
    passed here (see its docstring), so pre-filtering here would gate
    twice. Same pattern ``jobs/price_intelligence.py::gated_citations``
    already established for its own tool."""
    kb = kb or KnowledgeBase()
    return kb.ground_citations_detailed(_CITATION_KEYWORDS, brief, limit=limit)


def stage_repricing(
    store: object,
    channel: str,
    prices: dict[str, float],
    *,
    idempotency_key: str,
    reason: str,
    kb: KnowledgeBase | None = None,
    candidate_citations: list[GroundedCitation] | None = None,
    max_move_pct: float | None = GATE_MAX_MOVE_PCT_DEFAULT,
    landed_costs: dict[str, float] | None = None,
) -> writeback.Changeset:
    """Build a dry-run price ``Changeset`` for one channel (per
    ``writeback.stage()``) and gate it through PR-17's central pricing
    guardrail BEFORE returning it.

    ``store`` implements the writeback store surface (read/applied_keys/
    claim/release/commit/rollback) -- a ``ShopifyPriceStore``,
    ``MeliPriceStore``, or ``OdooPriceStore``
    (``src.connectors.{shopify,meli,odoo}_prices``), or any store matching
    that surface (``src.writeback.InMemoryStore`` in tests).

    ``reason`` is REQUIRED (no default): a changeset with a blank reason is
    exactly what the gate blocks (plan QA row), so forcing every caller to
    supply one here surfaces that failure at the call site instead of a
    generic gate error later. Raises :class:`RepricingGuardrailBlocked` --
    never returns a changeset that failed the gate.

    ``max_move_pct``/``landed_costs`` are forwarded verbatim to the gate's
    economic sanity checks (max % move per change, default 50%; below-cost
    block when landed costs are supplied) -- see
    :func:`src.pricing_guardrails.gate_price_changeset`.
    """
    edits = {sku: {"price": price} for sku, price in prices.items()}
    changeset = writeback.stage(
        store, channel, edits, risk_tier=TIER, idempotency_key=idempotency_key, reason=reason,
    )
    citations = candidate_citations if candidate_citations is not None else gated_citations(reason, kb=kb)
    gate = gate_price_changeset(
        changeset, kb=kb or KnowledgeBase(), candidate_citations=citations, tool_key=_TOOL_KEY,
        max_move_pct=max_move_pct, landed_costs=landed_costs,
    )
    if not gate.approved:
        raise RepricingGuardrailBlocked(channel, gate)
    return changeset


def apply_repricing(
    store: object,
    changeset: writeback.Changeset,
    approval: writeback.Approval,
    *,
    now: float | None = None,
) -> writeback.ApplyResult:
    """Apply a staged, gate-approved changeset. Requires a real signed
    ``Approval`` -- this NEVER auto-applies (``auto_apply_reversible`` is
    always ``False``), even though a price change is itself
    ``TIER_REVERSIBLE`` and every other reversible writeback flow in this
    repo (``OdooConnector.apply_restock``, ``apply_draft_purchase_orders``)
    defaults to auto-apply. Golden Rule 11: autonomy for a brand-new
    external write surface is earned with A4 evidence, which does not exist
    yet for a tool that did not exist until this PR. Raises
    ``writeback.WritebackRefused`` for a missing/mismatched/expired
    approval, matching every other writeback flow in this repo.
    """
    return writeback.apply(store, changeset, approval=approval, now=now, auto_apply_reversible=False)


def verify_applied(store: object, changeset: writeback.Changeset, channel: str) -> None:
    """Read the channel back post-apply and confirm every staged value
    actually landed (plan section 7: "apply sin verificacion = incidente").
    Raises :class:`RepricingVerificationFailed` -- never silently ignored --
    on any mismatch between what was staged (``Change.after``) and what the
    channel now reports live via ``store.read()``.
    """
    mismatches: list[tuple[str, object, object]] = []
    for c in changeset.changes:
        if c.is_noop:
            continue
        live = store.read(c.entity_id).get(c.field)
        if live != c.after:
            mismatches.append((c.entity_id, c.after, live))
    if mismatches:
        raise RepricingVerificationFailed(channel, tuple(mismatches))


@dataclass(frozen=True)
class ChannelRepricingResult:
    """Outcome of one full channel cycle (stage -> approve -> apply -> verify)."""

    channel: str
    changeset: writeback.Changeset
    apply_result: writeback.ApplyResult
    verified: bool


def run_channel_repricing(
    store: object,
    channel: str,
    prices: dict[str, float],
    *,
    idempotency_key: str,
    reason: str,
    approved_by: str,
    kb: KnowledgeBase | None = None,
    candidate_citations: list[GroundedCitation] | None = None,
    now: float | None = None,
    ttl_seconds: float = 900.0,
) -> ChannelRepricingResult:
    """Full one-channel cycle: stage (gated) -> approve -> apply -> verify.

    ``approved_by`` names the human who reviewed the staged diff -- calling
    this function is itself the "human approves" step in code (matching the
    same shape ``src/connectors/excel.py``'s own module-docstring example
    chains ``approve()`` then ``apply()``); a real operator UI would call
    :func:`stage_repricing` first to show the diff, wait for a human click,
    then call :func:`apply_repricing` (+ :func:`verify_applied`) separately
    once ``approved_by`` is known -- the primitives above are what such a UI
    would actually call across that time gap. Raises
    :class:`RepricingGuardrailBlocked`, ``writeback.WritebackRefused``, or
    :class:`RepricingVerificationFailed` -- never silently degrades.
    """
    changeset = stage_repricing(
        store, channel, prices, idempotency_key=idempotency_key, reason=reason,
        kb=kb, candidate_citations=candidate_citations,
    )
    approval = writeback.approve(changeset, approved_by, now=now, ttl_seconds=ttl_seconds)
    result = apply_repricing(store, changeset, approval, now=now)
    verify_applied(store, changeset, channel)
    return ChannelRepricingResult(channel=channel, changeset=changeset, apply_result=result, verified=True)

"""S3 PDP (product detail page) content generation (Linchpin 3.0 PR-24, plan
section 8 "Track B -- SEO", S3 row: ``pdp_writer.py`` -- "esquema estricto de
ficha verificable; QA: cada afirmacion mapea a un campo del catalogo (regla
10)").

Generates a title tag, a meta description, and an on-page body description
for one SKU (or a whole catalog batch), from the client's OWN catalog data
only. Reuses ``schema_gen.CatalogItem``/``ExcludedCatalogItem``/
``derive_availability``/``normalize_condition_token`` (Golden Rule 5, DRY --
the same "same product consistently" reason ``feeds.py`` and ``llms_txt.py``
already reuse them for: this module's copy and S2's JSON-LD must never
disagree about the SAME SKU's stock/condition/price). :class:`PdpEnrichment`
adds exactly the two fields real PDP copy wants that neither ``CatalogItem``
nor any other canonical shape in this repo carries -- ``category`` and a
free-form ``attributes`` dict (mirrors
``src.pricing_intel.match.fuzzy.ProductAttributes.attributes`` -- same
"known decisive facts, not fabricated; a key absent is no evidence, never
invented" contract, applied here to content instead of matching).

No network I/O (HARD RULE) -- pure text generation from already-loaded
``CatalogItem``/``PdpEnrichment`` data, plus an optional local file write
(matches ``src/export.py``'s own "write to disk from src/" precedent, same
as ``schema_gen.py``/``feeds.py``/``llms_txt.py``).

**Golden Rule 10, extended to content generation ("LLM never silent in the
data path" -> "no invented product claims"):** every sentence in
``body_copy``/``meta_description`` is a :class:`Claim` that names the exact
catalog field it was built from (``Claim.field``/``Claim.field_value``).
:func:`verify_claims_traceable` re-checks every claim against that citation
-- see its own docstring for the two grounding rules (extractive vs.
derived claims) and an honest statement of what this heuristic does and
does not catch.

**Template-first, LLM-enhancement-optional (plan section 8 S3 instruction).**
The DEFAULT generator (:func:`build_pdp_content`) is pure deterministic
string templating -- every claim is quite literally the field value with
fixed surrounding words, so ``verify_claims_traceable`` passes trivially by
construction for it. An LLM-enhanced path exists purely as an OPTIONAL
rewrite/polish upgrade, following ``src.pricing_intel.match.adjudicate``'s
injected-callable convention EXACTLY (see that module's own docstring for
the full rationale): this module is ``src/`` (pure functions only, plan
rule 1), so it never calls an LLM provider itself -- a caller injects an
already-configured :data:`PdpLlmEnhancer` callable. No provider is wired in
THIS PR (``llm=None`` is the shipped default, same stub/defer pattern as
``pricing_intel.extract``'s tier-5 stub and ``adjudicate.adjudicate_pair``);
a future job wiring one is expected to build it on top of
``scm_agent.llm.LLMProvider.complete()`` the same way
``scm_agent.llm.narrative_rewrite`` already does for tool-result summaries
-- this module deliberately does not invent a second LLM integration point,
only the strict request/response schema and re-verification a caller's
wrapper around ``LLMProvider`` must satisfy.

**The LLM path can rewrite, never invent, and never touches safety-critical
facts.** :func:`enhance_with_llm` splits a product's claims into "soft"
(title, brand, category, attributes, description, price -- rephrase-safe,
the only ones ever handed to the injected callable) and "protected"
(availability, condition -- a stock/condition statement is exactly the kind
of fact a careless rewrite could flip, e.g. "Available now" -> "Currently
unavailable", which is worse than a rewrite that drops it). Protected
sentences are ALWAYS appended verbatim by this module's own fixed closed-
vocabulary mapping (:data:`AVAILABILITY_COPY`/:data:`CONDITION_COPY`) --
the LLM never sees or produces that text, so it is structurally unable to
alter it. The rewrite is additionally rejected (falling back to the
unchanged template output, ``generator`` stays ``"template"``) if the
response fails its strict schema, the call raises, the new meta description
exceeds :data:`META_DESCRIPTION_MAX_CHARS`, or the product's own title no
longer appears anywhere in the rewritten text -- unlike
``adjudicate.adjudicate_pair`` (a single advisory call a caller wraps
individually, where a wrong-type response is a fail-fast programmer error),
this is a batch content path: ANY enhancement failure degrades to the
already-verified template output rather than raising, matching
``scm_agent.llm.narrative_rewrite``'s "always optional decoration, never
load-bearing" contract.

**No Kern watermark inside title_tag/meta_description/body_copy.** This is
the client's OWN on-page copy, meant to be pasted directly into their PDP --
exactly the same reasoning ``schema_gen.py``'s module docstring gives for
its JSON-LD carrying no third-party attribution (unlike ``llms_txt.py``'s
footer line, which is legitimate because an llms.txt file is a site
publishing information ABOUT itself, not the product copy itself).

**Column-naming convention:** ``product_id``/``title``/``brand``/``price``/
``currency``/``description``/``availability``/``on_hand``/``condition`` are
``schema_gen.CatalogItem``'s own columns, reused verbatim (see that
module's docstring for the full naming-reuse rationale). ``category`` and
``attributes`` are new to this module (see above).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.client_profile import slugify_client_id
from src.export import write_summary_csv
from src.seo.schema_gen import (
    SCHEMA_AVAILABILITY_TOKENS,
    SCHEMA_CONDITION_TOKENS,
    CatalogItem,
    ExcludedCatalogItem,
    derive_availability,
    normalize_condition_token,
)

logger = logging.getLogger(__name__)

# Google SERP truncation is commonly cited around 50-60 characters for a
# title tag (~600px) and ~155 characters for a meta description snippet --
# practical, widely-used defaults, not a guarantee Google will never
# truncate differently (it periodically does, for both). Named constants so
# a future PR can tune them in one place rather than hunting magic numbers.
TITLE_TAG_MAX_CHARS = 60
META_DESCRIPTION_MAX_CHARS = 155

# Fixed, closed-vocabulary sentences for DERIVED claims (see module
# docstring / verify_claims_traceable). Keyed by schema_gen's own bare
# tokens so the two modules can never drift silently -- the assertions
# below fail import if schema_gen ever adds a token this module has not
# been taught to phrase.
AVAILABILITY_COPY: dict[str, str] = {
    "InStock": "Available now.",
    "OutOfStock": "Currently unavailable.",
    "PreOrder": "Available for pre-order.",
    "BackOrder": "Available on backorder.",
    "LimitedAvailability": "Limited availability -- order soon.",
    "Discontinued": "This product has been discontinued.",
}
CONDITION_COPY: dict[str, str] = {
    "NewCondition": "Sold as new.",
    "UsedCondition": "Sold as used.",
    "RefurbishedCondition": "Sold as refurbished.",
    "DamagedCondition": "Sold as used, with cosmetic damage.",
}
assert set(AVAILABILITY_COPY) == set(SCHEMA_AVAILABILITY_TOKENS)
assert set(CONDITION_COPY) == set(SCHEMA_CONDITION_TOKENS)

# Claim.field values whose text comes from AVAILABILITY_COPY/CONDITION_COPY
# (derived, closed-vocabulary) rather than a literal field echo (extractive)
# -- see verify_claims_traceable and enhance_with_llm's "soft vs protected"
# split, both of which branch on this set.
PROTECTED_CLAIM_FIELDS = frozenset({"availability", "on_hand", "condition"})


@dataclass(frozen=True)
class PdpEnrichment:
    """PDP-only facts ``schema_gen.CatalogItem`` does not carry -- kept as a
    separate, optional companion rather than adding columns to
    ``CatalogItem`` (which ``schema_gen.py``/``feeds.py``/``llms_txt.py``
    all already depend on verbatim). ``attributes`` is free-form
    already-normalized facts (pack_size, color, material...), same contract
    as ``pricing_intel.match.fuzzy.ProductAttributes.attributes``: a key
    absent here is simply not claimed, never invented."""

    category: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Claim:
    """One discrete, checkable factual statement inserted verbatim into
    ``body_copy`` -- plus exactly which source field grounds it (Golden Rule
    10 extended to content generation). ``field_value`` is the raw source
    datum, rendered as a string: for an EXTRACTIVE claim (``field`` not in
    :data:`PROTECTED_CLAIM_FIELDS`) it is the literal value copied into
    ``text``; for a DERIVED claim (``field`` in
    :data:`PROTECTED_CLAIM_FIELDS`) it is the bare schema.org token
    (e.g. ``"InStock"``) that :data:`AVAILABILITY_COPY`/
    :data:`CONDITION_COPY` maps to the closed-vocabulary sentence in
    ``text`` -- see :func:`verify_claims_traceable` for how the two kinds
    are checked differently."""

    text: str
    field: str
    field_value: str


@dataclass(frozen=True)
class PdpContent:
    """One SKU's finished PDP copy, plus its full claim-level provenance
    trace (the strict output schema the plan's S3 row calls for).

    ``source_fields`` maps CATALOG FIELD NAME -> the exact generated text
    traced from it -- the INVERSE key direction of
    ``schema_gen.GeneratedProduct.source_fields`` (which maps a stable
    json-ld path -> catalog column), because JSON-LD output has stable
    per-field keys and this module's output is prose with no such
    structure; ``field`` is the stable, citable key here instead.
    ``claims`` carries the richer per-sentence detail
    :func:`verify_claims_traceable` actually checks against; keep both in
    sync -- ``source_fields`` is always derived FROM ``claims``, never
    supplied independently, by every function in this module."""

    product_id: str
    title_tag: str
    meta_description: str
    body_copy: str
    claims: tuple[Claim, ...]
    source_fields: dict[str, str]
    generator: str = "template"  # "template" | "llm" -- see module docstring


@dataclass(frozen=True)
class PdpCatalogReport:
    generated: tuple[PdpContent, ...]
    excluded: tuple[ExcludedCatalogItem, ...]
    n_generated: int
    n_excluded: int
    n_llm_enhanced: int
    summary: str


def missing_required_pdp_fields(item: CatalogItem) -> list[str]:
    """PDP copy needs far less than JSON-LD (``schema_gen.missing_required_
    fields``) or a shopping feed (``feeds.missing_required_feed_fields``) --
    only a name to write about. Every other field is optional and simply
    produces a shorter (never fabricated) claim list when absent -- see
    :func:`build_claims`."""
    missing: list[str] = []
    if not item.product_id or not item.product_id.strip():
        missing.append("product_id")
    if not item.title or not item.title.strip():
        missing.append("title")
    return missing


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _truncate_at_word(text: str, max_len: int) -> str:
    """Truncate ``text`` to at most ``max_len`` characters at a word
    boundary, appending an ellipsis -- never mid-word, so the result never
    reads as a different, truncated-into-a-new-word claim."""
    if len(text) <= max_len:
        return text
    ellipsis = "..."
    budget = max(0, max_len - len(ellipsis))
    truncated = text[:budget].rsplit(" ", 1)[0].rstrip(",.;:- ")
    return f"{truncated}{ellipsis}" if truncated else text[:max_len]


# -- claim builders (each maps exactly one CatalogItem/PdpEnrichment field
#    to exactly one Claim -- see module docstring) --------------------------


def _title_claim(item: CatalogItem) -> Claim:
    title = item.title.strip()
    text = title if title.endswith((".", "!", "?")) else f"{title}."
    return Claim(text=text, field="title", field_value=title)


def _brand_claim(item: CatalogItem) -> Claim | None:
    if not item.brand or not item.brand.strip():
        return None
    brand = item.brand.strip()
    return Claim(text=f"By {brand}.", field="brand", field_value=brand)


def _category_claim(enrichment: PdpEnrichment | None) -> Claim | None:
    if enrichment is None or not enrichment.category or not enrichment.category.strip():
        return None
    category = enrichment.category.strip()
    return Claim(text=f"Category: {category}.", field="category", field_value=category)


def _attribute_claims(enrichment: PdpEnrichment | None) -> list[Claim]:
    if enrichment is None or not enrichment.attributes:
        return []
    claims: list[Claim] = []
    for key in sorted(enrichment.attributes):  # deterministic order for reproducible copy
        value = enrichment.attributes[key]
        if value is None or not str(value).strip():
            continue
        label = key.replace("_", " ").strip().capitalize()
        value_str = str(value).strip()
        claims.append(Claim(text=f"{label}: {value_str}.", field=f"attributes.{key}", field_value=value_str))
    return claims


def _description_claim(item: CatalogItem) -> Claim | None:
    if not item.description or not item.description.strip():
        return None
    # field_value is the COLLAPSED value (matches what actually lands in
    # `text`, below) rather than the raw multi-line/extra-whitespace source
    # string -- a raw description containing internal newlines would
    # otherwise never appear as a literal substring of its own one-line
    # claim text, which would make verify_claims_traceable flag this
    # module's own valid template output as untraceable.
    collapsed = _one_line(item.description.strip())
    text = collapsed if collapsed.endswith((".", "!", "?")) else f"{collapsed}."
    return Claim(text=text, field="description", field_value=collapsed)


def _price_claim(item: CatalogItem) -> Claim | None:
    if item.price is None or item.price <= 0 or not item.currency or not item.currency.strip():
        return None
    price_str = f"{round(float(item.price), 2):.2f}"
    text = f"Priced at {price_str} {item.currency.strip()}."
    return Claim(text=text, field="price", field_value=price_str)


def _availability_claim(item: CatalogItem) -> Claim | None:
    token, source = derive_availability(item)
    if token is None or source not in ("availability", "on_hand"):
        return None  # no usable signal, or derive_availability's reason string -- never guessed
    return Claim(text=AVAILABILITY_COPY[token], field=source, field_value=token)


def _condition_claim(item: CatalogItem) -> Claim | None:
    token = normalize_condition_token(item.condition)
    if token is None:
        return None
    return Claim(text=CONDITION_COPY[token], field="condition", field_value=token)


def build_claims(item: CatalogItem, enrichment: PdpEnrichment | None = None) -> list[Claim]:
    """All claims for ``item``, in a fixed generation order (title first,
    always present; every other claim only when a real field backs it --
    Golden Rule 14, never pad with filler)."""
    claims = [_title_claim(item)]
    brand_claim = _brand_claim(item)
    if brand_claim is not None:
        claims.append(brand_claim)
    category_claim = _category_claim(enrichment)
    if category_claim is not None:
        claims.append(category_claim)
    claims.extend(_attribute_claims(enrichment))
    description_claim = _description_claim(item)
    if description_claim is not None:
        claims.append(description_claim)
    price_claim = _price_claim(item)
    if price_claim is not None:
        claims.append(price_claim)
    availability_claim = _availability_claim(item)
    if availability_claim is not None:
        claims.append(availability_claim)
    condition_claim = _condition_claim(item)
    if condition_claim is not None:
        claims.append(condition_claim)
    return claims


def build_title_tag(item: CatalogItem) -> str:
    """``{title} | {brand}`` when it fits :data:`TITLE_TAG_MAX_CHARS`, else
    the bare title, else a word-boundary truncation -- never a fabricated
    brand/title, only ever what the catalog says."""
    title = item.title.strip()
    brand = (item.brand or "").strip()
    if brand:
        candidate = f"{title} | {brand}"
        if len(candidate) <= TITLE_TAG_MAX_CHARS:
            return candidate
    if len(title) <= TITLE_TAG_MAX_CHARS:
        return title
    return _truncate_at_word(title, TITLE_TAG_MAX_CHARS)


def build_meta_description(claims: Sequence[Claim]) -> str:
    """Greedily join WHOLE claim sentences (never a mid-sentence cut, so no
    fragment can misread as a different claim) up to
    :data:`META_DESCRIPTION_MAX_CHARS`, in ``claims``' own order -- always
    includes at least the first (title) claim, so a sparse catalog row still
    gets a short, accurate, non-empty meta description instead of a blank
    one or invented filler."""
    if not claims:
        return ""
    parts: list[str] = [claims[0].text]
    total = len(parts[0])
    for claim in claims[1:]:
        addition = f" {claim.text}"
        if total + len(addition) > META_DESCRIPTION_MAX_CHARS:
            break
        parts.append(claim.text)
        total += len(addition)
    text = " ".join(parts)
    return _truncate_at_word(text, META_DESCRIPTION_MAX_CHARS)


def build_pdp_content(item: CatalogItem, enrichment: PdpEnrichment | None = None) -> PdpContent:
    """Build ONE SKU's PDP copy. Raises ``ValueError`` naming what is
    missing if :func:`missing_required_pdp_fields` is non-empty (same
    never-emit-partial-output contract as ``schema_gen.build_product_
    jsonld``)."""
    missing = missing_required_pdp_fields(item)
    if missing:
        raise ValueError(f"cannot build PDP content for product_id={item.product_id!r}: missing {missing}")

    claims = build_claims(item, enrichment)
    title_tag = build_title_tag(item)
    meta_description = build_meta_description(claims)
    body_copy = " ".join(c.text for c in claims)
    source_fields = {c.field: c.text for c in claims}
    return PdpContent(
        product_id=item.product_id, title_tag=title_tag, meta_description=meta_description,
        body_copy=body_copy, claims=tuple(claims), source_fields=source_fields, generator="template",
    )


def verify_claims_traceable(content: PdpContent) -> list[str]:
    """Golden Rule 10 QA, extended to content generation: every claim in
    ``content.claims`` must (a) appear verbatim in ``content.body_copy``,
    and (b) be grounded in its cited source field. Empty list = every claim
    traces (Golden Rule 10 satisfied for this SKU's copy).

    Two grounding rules, by claim kind (:data:`PROTECTED_CLAIM_FIELDS`):
      - EXTRACTIVE claims (title, brand, category, description, price,
        ``attributes.*``) -- ``field_value`` (the raw catalog value the
        template copied) must appear, case-insensitively, as a substring of
        ``claim.text``. Trivially true for the template generator (it IS
        the value plus fixed surrounding words) -- the real value of this
        check is for LLM-rewritten claims, where it catches a rewrite that
        silently drops or alters the cited value.
      - DERIVED claims (availability, on_hand, condition) -- a bare token
        like ``"InStock"`` never appears literally in prose, so instead
        this checks ``claim.text`` is EXACTLY the closed-vocabulary
        sentence :data:`AVAILABILITY_COPY`/:data:`CONDITION_COPY` maps for
        the token in ``claim.field_value`` -- i.e. this really is one of
        the sentences this module is allowed to say for that fact, not
        something else.

    Honest limitation (do not oversell): this is a templating-integrity /
    anti-fabrication check, not semantic entailment. It reliably catches
    "the cited value is nowhere in the sentence" and "this derived sentence
    isn't one this module is allowed to produce for that token" -- both
    strong fabrication signals for a small, closed generator -- but it
    cannot catch a claim that reuses the right substring while still
    misrepresenting it in surrounding invented text (e.g. a rewrite that
    correctly quotes the SKU price but wraps it in an unrelated made-up
    sentence). Precision is bounded by what a cheap string heuristic can
    see; a human review pass is still the right gate for LLM-enhanced
    output specifically (see module docstring)."""
    issues: list[str] = []
    for i, claim in enumerate(content.claims):
        if claim.text not in content.body_copy:
            issues.append(f"claim[{i}] ({claim.field}) text not found verbatim in body_copy: {claim.text!r}")
            continue
        if claim.field in PROTECTED_CLAIM_FIELDS:
            vocab = CONDITION_COPY if claim.field == "condition" else AVAILABILITY_COPY
            expected = vocab.get(claim.field_value)
            if expected is None or claim.text != expected:
                issues.append(
                    f"claim[{i}] ({claim.field}) text {claim.text!r} is not the closed-vocabulary "
                    f"sentence for token {claim.field_value!r}"
                )
        elif claim.field_value.strip().lower() not in claim.text.lower():
            issues.append(
                f"claim[{i}] ({claim.field}) field_value {claim.field_value!r} not found in "
                f"claim text {claim.text!r}"
            )
    return issues


def claims_traceable_passed(content: PdpContent) -> bool:
    return not verify_claims_traceable(content)


# -- optional LLM-enhancement path (unwired in this PR -- see module
#    docstring's "template-first, LLM-enhancement-optional" section) -------


@dataclass(frozen=True)
class PdpEnhancementRequest:
    """Everything an injected LLM enhancer needs to polish already-verified
    template copy. ``claims`` carries ONLY the "soft" (rephrase-safe)
    claims (see :func:`enhance_with_llm`) -- a protected availability/
    condition claim is never included, so the callable is structurally
    unable to see, and therefore cannot rewrite, that text."""

    product_id: str
    claims: tuple[Claim, ...]
    template_meta_description: str
    template_body_copy: str


@dataclass(frozen=True)
class LlmPdpEnhancement:
    """Strict schema (Golden Rule 10) an injected :data:`PdpLlmEnhancer`
    callable must return. This module never free-parses raw LLM text --
    whatever wraps the actual provider call is responsible for turning its
    output into this shape (or raising), the same boundary
    ``adjudicate.LlmAdjudicationResponse`` documents for its own future
    implementation. This repo has no pydantic dependency (see
    ``adjudicate.py``'s own note) -- enforced the same way, via
    ``__post_init__``."""

    meta_description: str
    body_copy: str
    reason: str

    def __post_init__(self) -> None:
        if not self.meta_description or not self.meta_description.strip():
            raise ValueError("meta_description must be a non-empty string")
        if not self.body_copy or not self.body_copy.strip():
            raise ValueError("body_copy must be a non-empty string")
        if not self.reason or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")


PdpLlmEnhancer = Callable[[PdpEnhancementRequest], LlmPdpEnhancement]


def _mentions(text: str, value: str) -> bool:
    return bool(value.strip()) and value.strip().lower() in text.lower()


def enhance_with_llm(content: PdpContent, llm: PdpLlmEnhancer) -> PdpContent:
    """Optionally upgrade ``content``'s soft-claim prose via an injected LLM
    callable -- see module docstring for the full soft/protected split and
    fallback contract. ALWAYS returns ``content`` unchanged (``generator``
    stays ``"template"``) on: the callable raising, a wrong return type, a
    meta description exceeding :data:`META_DESCRIPTION_MAX_CHARS`, or the
    product's own title going missing from the rewritten text. Never raises
    itself -- an LLM enhancement is optional decoration, never load-bearing
    (mirrors ``scm_agent.llm.narrative_rewrite``'s contract)."""
    soft_claims = tuple(c for c in content.claims if c.field not in PROTECTED_CLAIM_FIELDS)
    protected_claims = tuple(c for c in content.claims if c.field in PROTECTED_CLAIM_FIELDS)
    if not soft_claims:
        return content  # nothing rephrase-safe to hand to an LLM

    request = PdpEnhancementRequest(
        product_id=content.product_id, claims=soft_claims,
        template_meta_description=content.meta_description, template_body_copy=content.body_copy,
    )
    try:
        response = llm(request)
    except Exception:
        logger.debug("pdp LLM enhancement call failed for %s", content.product_id, exc_info=True)
        return content

    if not isinstance(response, LlmPdpEnhancement):
        logger.debug(
            "pdp LLM enhancement for %s returned %s, expected LlmPdpEnhancement",
            content.product_id, type(response).__name__,
        )
        return content
    if len(response.meta_description) > META_DESCRIPTION_MAX_CHARS:
        return content

    title_claim = content.claims[0]  # build_claims always emits the title claim first
    if not _mentions(response.body_copy, title_claim.field_value) or not _mentions(
        response.meta_description, title_claim.field_value
    ):
        return content  # rule-10 re-verification: the product's own name must survive a rewrite

    protected_suffix = " ".join(c.text for c in protected_claims)
    final_body_copy = f"{response.body_copy} {protected_suffix}".strip() if protected_suffix else response.body_copy

    return PdpContent(
        product_id=content.product_id, title_tag=content.title_tag,
        meta_description=response.meta_description, body_copy=final_body_copy,
        claims=content.claims, source_fields=content.source_fields, generator="llm",
    )


def catalog_to_pdp_content(
    items: Sequence[CatalogItem],
    enrichments: Mapping[str, PdpEnrichment] | None = None,
    *,
    llm: PdpLlmEnhancer | None = None,
    llm_budget: int | None = None,
) -> PdpCatalogReport:
    """The batch entry point: one :class:`PdpContent` per SKU that has a
    ``product_id``+``title``, one :class:`ExcludedCatalogItem` per SKU that
    does not (Golden Rule 14). ``llm``/``llm_budget`` are both optional and
    default to the template-only path (see module docstring) --
    ``llm_budget`` caps how many SKUs in this batch may be handed to the
    injected callable at all (a simple call-count budget cap, Golden Rule
    10's "budget cap" requirement enforced at the level this pure function
    controls; the injected callable's own token/dollar budget, if any,
    belongs to whatever wraps a real provider outside ``src/``)."""
    enrichments = enrichments or {}
    generated: list[PdpContent] = []
    excluded: list[ExcludedCatalogItem] = []
    remaining_budget = llm_budget
    n_llm_enhanced = 0

    for item in items:
        pid = item.product_id if item.product_id and item.product_id.strip() else "<missing product_id>"
        missing = missing_required_pdp_fields(item)
        if missing:
            excluded.append(ExcludedCatalogItem(pid, tuple(missing)))
            continue

        content = build_pdp_content(item, enrichments.get(item.product_id))
        if llm is not None and (remaining_budget is None or remaining_budget > 0):
            enhanced = enhance_with_llm(content, llm)
            if enhanced.generator == "llm":
                n_llm_enhanced += 1
                if remaining_budget is not None:
                    remaining_budget -= 1
            content = enhanced
        generated.append(content)

    summary = (
        f"pdp_writer: {len(generated)} SKU(s) produced PDP copy "
        f"({n_llm_enhanced} LLM-enhanced, {len(generated) - n_llm_enhanced} template-only), "
        f"{len(excluded)} excluded for missing required field(s)."
    )
    return PdpCatalogReport(
        generated=tuple(generated), excluded=tuple(excluded), n_generated=len(generated),
        n_excluded=len(excluded), n_llm_enhanced=n_llm_enhanced, summary=summary,
    )


def _safe_filename(product_id: str) -> str:
    """Filesystem-safe filename stem for a per-SKU PDP file. Same helper
    ``schema_gen._safe_filename`` defines (transliterate-then-collapse via
    ``slugify_client_id``, hash fallback for a product_id that slugifies to
    nothing) -- kept as this module's own private copy rather than an
    import of a leading-underscore name from a sibling module (no S2 module
    exports or reuses it across module boundaries either)."""
    try:
        return slugify_client_id(product_id)
    except ValueError:
        return "sku-" + hashlib.sha256(product_id.encode("utf-8")).hexdigest()[:16]


def _content_as_dict(content: PdpContent) -> dict:
    return {
        "product_id": content.product_id,
        "title_tag": content.title_tag,
        "meta_description": content.meta_description,
        "body_copy": content.body_copy,
        "generator": content.generator,
        "source_fields": content.source_fields,
        "claims": [{"text": c.text, "field": c.field, "field_value": c.field_value} for c in content.claims],
    }


def write_pdp_content(report: PdpCatalogReport, out_dir: str | Path) -> dict[str, Path]:
    """Writes one ``<slug>.pdp.json`` file per generated SKU, one combined
    ``catalog_pdp_content.json`` array for review, and a
    ``pdp_writer_excluded.csv`` so an exclusion is never silent (Golden Rule
    14) -- mirrors ``schema_gen.write_catalog_jsonld``'s exact three-output
    shape. Local file writes only (module docstring)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    per_sku_dir = d / "pdp"
    per_sku_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {}
    for g in report.generated:
        path = per_sku_dir / f"{_safe_filename(g.product_id)}.pdp.json"
        path.write_text(json.dumps(_content_as_dict(g), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written[f"pdp:{g.product_id}"] = path

    combined_path = d / "catalog_pdp_content.json"
    combined_path.write_text(
        json.dumps([_content_as_dict(g) for g in report.generated], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    written["combined"] = combined_path

    excluded_path = d / "pdp_writer_excluded.csv"
    if report.excluded:
        rows = [{"product_id": e.product_id, "reasons": "; ".join(e.reasons)} for e in report.excluded]
        written["excluded_csv"] = write_summary_csv(rows, excluded_path)
    else:
        pd.DataFrame(columns=["product_id", "reasons"]).to_csv(excluded_path, index=False)
        written["excluded_csv"] = excluded_path

    return written

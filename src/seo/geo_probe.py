"""S5 GEO (generative engine optimization) visibility probes (Linchpin 3.0
PR-25, plan section 8 "Track B -- SEO", S5 row: ``geo_probe.py`` -- "Sondas
de citacion en motores AI, share of voice"). FINAL PR of the 25-PR plan.

**What this measures.** A GEO probe asks an AI engine a caller-supplied
question shaped like something a real buyer would ask a chat assistant
(e.g. "best warehouse racking for a 5000 sqft facility") and scans the raw
answer text for a mention of the client's own brand, domain, or product
name -- a share-of-voice signal for how often the client actually gets
cited when someone asks an AI assistant instead of searching Google. This
is the least-precedented module in the whole plan: S1-S4 all extend a real
integration already in this repo (advertools crawls, the client's own
catalog, ``extruct``'s adapter); GEO visibility measurement has no such
precedent here, and the honest thing to do is ship the SIMPLEST version
that is actually true, not a broad multi-engine framework this repo cannot
back up.

**Why exactly ONE engine ships wired, and why that is deliberate, not an
oversight.** This repo has no existing integration with any public AI
chat surface for this purpose. Building a bespoke scraper against a
consumer chat product's UI (ChatGPT, Gemini, Perplexity, an AI Overview
panel...) would violate the SAME anti-evasion rule that governs
``src/pricing_intel/``'s acquisition layer (see that package's own
docstrings): acquisition must be polite, ToS-compliant, and
self-identifying, degrading rather than disguising itself when blocked --
never impersonating a browser to defeat bot detection. Most consumer AI
chat products' terms of service prohibit automated querying outright, so
there is no ToS-compliant way to "ask ChatGPT" or "ask Google AI Overview"
programmatically today without that engine's own OFFICIAL API. Claude is
different: this repo already has a sanctioned, official integration
(``scm_agent.llm.LLMProvider`` / ``ClaudeProvider``, the Anthropic Python
SDK) used elsewhere for narrative rewriting and document extraction. This
module's ONE real, working probe target (:func:`build_claude_probe`) wraps
that same official API. See :data:`SINGLE_SANCTIONED_ENGINE_CAVEAT` --
read it before wiring a second engine into this module.

**The pluggable-callable pattern (Golden Rule 1 -- src/ never does network
I/O itself).** Every probe function in this module takes a caller-supplied
``probe: Callable[[str], str]`` -- "send this query string somewhere, get
a response string back" -- exactly the injected-callable convention
``src/pricing_intel/match/adjudicate.py`` and ``src/seo/pdp_writer.py``'s
``PdpLlmEnhancer`` already use for the same reason: this module is
``src/`` (pure functions only), so it must never itself hold an API
client. :func:`build_claude_probe` is the one built-in factory that
produces such a callable from ``scm_agent.llm.LLMProvider`` (imported
lazily, inside the function, mirroring ``src/voice/doc_reader.py``'s
``_default_model()`` -- an engine-layer module must not statically depend
on the agent layer). A caller with a different, OFFICIALLY sanctioned
engine API (their own OpenAI key, a licensed AI Overview data feed, etc.)
can inject their own callable the same way -- this module makes no
assumption about WHICH engine a callable talks to; it only requires an
honest ``engine`` label naming it, so results stay attributable.

**Matching: exact substring first, RapidFuzz partial-ratio second.**
:func:`detect_brand_mention` looks for a caller-supplied brand/domain/
product term as a literal, case-insensitive substring of the response
first (cheap, precise, zero false positives). If none of the terms
appears verbatim, a sentence-level RapidFuzz ``partial_ratio`` pass
catches a close paraphrase an AI engine is prone to produce (a dropped
hyphen, a merged/split word, a pluralization) -- RapidFuzz is already the
``dataquality`` extra's fuzzy-matching dependency (not duplicated here,
per the PR instructions); like ``src/sku_dedup.py``, this module falls
back to the stdlib ``difflib`` when the extra is not installed, so the
base install still works. This is simple string/fuzzy matching, not
semantic entailment -- it cannot tell "recommends the client's product"
from "explicitly says NOT to buy the client's product"; a human reviewing
:attr:`ProbeResult.mention_context` (the exact sentence the match came
from) is the honest way to read sentiment, same limitation
``pdp_writer.verify_claims_traceable`` states plainly for its own
string-heuristic check rather than oversell precision it doesn't have.

**Golden Rule 7 (total provenance) for externally-observed data.** A
:class:`ProbeResult` retains exactly what was asked (``query``), which
engine answered (``engine``), when (``probed_at``, UTC ISO-8601), and the
COMPLETE raw response (``response_text``, never truncated or summarized)
-- the same "which engine, when, what was actually asked and returned"
standard ``src/pricing_intel``'s competitor-offer provenance holds itself
to. :func:`run_probe_set` never silently drops a failed query out of a
batch (Golden Rule 14) -- a probe callable raising is recorded as an
explicit :class:`ProbeError`, never folded into a fabricated
"not mentioned" :class:`ProbeResult`, and :func:`aggregate_share_of_voice`
reports the error count in its own summary text rather than silently
excluding it.

**Append-only note.** This module returns data; it does not persist
anything itself (no state/ledger writes here, plan rule 8's append-only
requirement applies to whatever caller chooses to log these results over
time -- ``src/state/store.py``'s snapshot machinery is the natural home
for that, wiring it is out of this PR's scope).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

try:  # optional fast path -- rapidfuzz lives in the `dataquality` extra already
    from rapidfuzz import fuzz

    def _partial_ratio(a: str, b: str) -> float:
        return float(fuzz.partial_ratio(a, b))
except ImportError:  # stdlib fallback (same degrade as src/sku_dedup.py)
    import difflib

    def _partial_ratio(a: str, b: str) -> float:
        matcher = difflib.SequenceMatcher(None, a, b)
        # difflib has no direct partial_ratio equivalent; approximate it via
        # the best-matching contiguous block, RapidFuzz's own definition of
        # partial_ratio -- a coarser, pure-stdlib approximation, not a
        # drop-in numerical match, but the same "shorter string found
        # somewhere inside the longer one" intuition.
        block = matcher.find_longest_match(0, len(a), 0, len(b))
        if block.size == 0 or not a or not b:
            return 0.0
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        window = longer[max(0, block.b - block.a): max(0, block.b - block.a) + len(shorter)]
        return difflib.SequenceMatcher(None, shorter, window).ratio() * 100.0

logger = logging.getLogger(__name__)

# -- the single-sanctioned-engine caveat (see module docstring) -------------
# Kept as its own module-level constant (not only prose in the docstring) so
# a future PR cannot silently delete the warning by trimming the docstring --
# tests assert this constant's content directly.
SINGLE_SANCTIONED_ENGINE_CAVEAT = (
    "GEO probing in this module ships with exactly ONE sanctioned, working engine "
    "target: Claude, via scm_agent.llm.LLMProvider's official Anthropic API "
    "integration (build_claude_probe). Any OTHER engine (a hypothetical ChatGPT, "
    "Google AI Overview, Perplexity, or similar prober) MUST be wired through that "
    "engine's own OFFICIAL, ToS-compliant API before it is added here -- this module "
    "deliberately does NOT, and must NEVER, scrape a consumer AI chat product's UI. "
    "Most consumer AI chat products' terms of service prohibit automated querying, "
    "and doing so anyway would violate the same anti-evasion rule that governs "
    "src/pricing_intel/'s acquisition layer: acquisition must be polite, "
    "self-identifying, and degrade rather than disguise itself on blocking."
)

CLAUDE_ENGINE_LABEL = "claude"

# RapidFuzz partial_ratio is a 0-100 scale (see src/pricing_intel/match/fuzzy.py
# for the same scale convention). Deliberately conservative -- this is the
# ONLY signal used when an exact substring match fails, so a low threshold
# would manufacture false "mentioned" claims. 85 requires the matched
# sentence to be a close paraphrase of the brand term, not a loose one.
BRAND_MENTION_FUZZY_THRESHOLD = 85.0

# How many characters of surrounding text an exact-match mention_context
# keeps on each side of the matched term -- generous enough that a normal
# AI-chat sentence is captured whole (see tests for hand-verified examples),
# not a hard guarantee for very long sentences (a truncation always keeps a
# leading/trailing "..." marker rather than silently cutting mid-word).
_CONTEXT_RADIUS_CHARS = 120

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


# -- matching --------------------------------------------------------------


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _split_sentences(text: str) -> list[str]:
    """Naive sentence split on ., !, ? followed by whitespace, or a newline
    -- good enough to give a fuzzy mention a readable context snippet from
    AI-generated prose; not a full NLP sentence tokenizer."""
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def _context_window(text: str, start: int, length: int) -> str:
    lo = max(0, start - _CONTEXT_RADIUS_CHARS)
    hi = min(len(text), start + length + _CONTEXT_RADIUS_CHARS)
    snippet = text[lo:hi].strip()
    prefix = "..." if lo > 0 else ""
    suffix = "..." if hi < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


def _find_exact_mention(response_text: str, brand_terms: Sequence[str]) -> tuple[str, int] | None:
    """Case-insensitive literal substring search. Checks ``brand_terms`` in
    the caller's own order and returns the FIRST one found, with its start
    index in the ORIGINAL (non-lowered) ``response_text`` so the context
    window slices the real text, not the lowered copy."""
    lowered = response_text.lower()
    for term in brand_terms:
        term_clean = term.strip()
        if not term_clean:
            continue
        idx = lowered.find(term_clean.lower())
        if idx != -1:
            return term_clean, idx
    return None


def _find_fuzzy_mention(
    response_text: str, brand_terms: Sequence[str], *, threshold: float
) -> str | None:
    """Sentence-level RapidFuzz partial_ratio pass -- returns the first
    sentence (in response order) whose normalized text scores >= threshold
    against any brand term (terms checked in the caller's own order per
    sentence), or None. See module docstring for why this only runs after
    an exact match already failed."""
    sentences = _split_sentences(response_text)
    for sentence in sentences:
        normalized_sentence = _normalize(sentence)
        for term in brand_terms:
            term_clean = term.strip()
            if not term_clean:
                continue
            score = _partial_ratio(normalized_sentence, _normalize(term_clean))
            if score >= threshold:
                return sentence
    return None


def detect_brand_mention(
    response_text: str,
    brand_terms: Sequence[str],
    *,
    fuzzy_threshold: float = BRAND_MENTION_FUZZY_THRESHOLD,
) -> tuple[bool, str | None]:
    """Scans ``response_text`` for any of ``brand_terms`` (brand name,
    domain, product names -- caller's choice, checked in the given order).
    Returns ``(brand_mentioned, mention_context)``: exact case-insensitive
    substring match is tried first; if none hits, a sentence-level
    RapidFuzz fuzzy pass catches a close paraphrase. ``(False, None)`` when
    neither finds anything, including when ``response_text``/``brand_terms``
    is empty (never guesses a mention from no evidence)."""
    if not response_text or not response_text.strip():
        return False, None
    cleaned_terms = [t for t in brand_terms if t and t.strip()]
    if not cleaned_terms:
        return False, None

    exact = _find_exact_mention(response_text, cleaned_terms)
    if exact is not None:
        term, idx = exact
        return True, _context_window(response_text, idx, len(term))

    fuzzy_sentence = _find_fuzzy_mention(response_text, cleaned_terms, threshold=fuzzy_threshold)
    if fuzzy_sentence is not None:
        return True, fuzzy_sentence

    return False, None


# -- probe result / error shapes --------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """One probe query's outcome against one engine -- full provenance kept
    (Golden Rule 7): the exact question, the exact raw answer, which engine,
    and when. ``mention_context`` is the exact sentence/snippet the mention
    was found in (never fabricated) -- ``None`` iff ``brand_mentioned`` is
    ``False``."""

    query: str
    engine: str
    response_text: str
    brand_mentioned: bool
    mention_context: str | None
    probed_at: str  # UTC ISO-8601, matches src/state/store.py's own isoformat() convention

    def __post_init__(self) -> None:
        if not self.query or not self.query.strip():
            raise ValueError("query must be a non-empty string")
        if not self.engine or not self.engine.strip():
            raise ValueError("engine must be a non-empty string")
        if not self.probed_at or not self.probed_at.strip():
            raise ValueError("probed_at must be a non-empty string")
        if self.brand_mentioned and self.mention_context is None:
            raise ValueError("mention_context is required when brand_mentioned is True")
        if not self.brand_mentioned and self.mention_context is not None:
            raise ValueError("mention_context must be None when brand_mentioned is False")


@dataclass(frozen=True)
class ProbeError:
    """One probe query that FAILED to run (the callable raised, or returned
    a non-string) -- kept as its own explicit record so a batch failure is
    never silently folded into a fabricated "not mentioned" ProbeResult
    (Golden Rule 14)."""

    query: str
    engine: str
    error: str
    probed_at: str


def run_probe(
    query: str,
    probe: Callable[[str], str],
    *,
    engine: str,
    brand_terms: Sequence[str],
    fuzzy_threshold: float = BRAND_MENTION_FUZZY_THRESHOLD,
) -> ProbeResult:
    """Runs ONE query against ONE engine's probe callable and scans the raw
    response for a brand mention. ``probe`` is caller-supplied (see module
    docstring for why this module never talks to an engine itself). Raises
    whatever ``probe`` raises, and ``TypeError`` if it returns a non-string
    -- a single-query failure is a programmer/caller-visible error here;
    :func:`run_probe_set` is the batch entry point that catches per-query
    failures explicitly as a :class:`ProbeError`."""
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")
    response_text = probe(query)
    if not isinstance(response_text, str):
        raise TypeError(f"probe callable must return str, got {type(response_text).__name__}")

    mentioned, context = detect_brand_mention(response_text, brand_terms, fuzzy_threshold=fuzzy_threshold)
    probed_at = datetime.now(timezone.utc).isoformat()
    return ProbeResult(
        query=query, engine=engine, response_text=response_text,
        brand_mentioned=mentioned, mention_context=context, probed_at=probed_at,
    )


def run_probe_set(
    queries: Sequence[str],
    probe: Callable[[str], str],
    *,
    engine: str,
    brand_terms: Sequence[str],
    fuzzy_threshold: float = BRAND_MENTION_FUZZY_THRESHOLD,
) -> tuple[list[ProbeResult], list[ProbeError]]:
    """Runs every query in ``queries`` against ``probe``, in order, on the
    SAME engine. Golden Rule 14 (no silent caps): every query is attempted,
    nothing is truncated, and a per-query failure becomes an explicit
    :class:`ProbeError` rather than being dropped from the result set."""
    results: list[ProbeResult] = []
    errors: list[ProbeError] = []
    for query in queries:
        try:
            results.append(
                run_probe(query, probe, engine=engine, brand_terms=brand_terms, fuzzy_threshold=fuzzy_threshold)
            )
        except Exception as exc:  # noqa: BLE001 -- a probe callable can raise anything; recorded, never swallowed
            logger.warning("geo_probe: probe failed for query %r on engine %r: %s", query, engine, exc)
            errors.append(
                ProbeError(query=query, engine=engine, error=str(exc), probed_at=datetime.now(timezone.utc).isoformat())
            )
    return results, errors


# -- share-of-voice aggregation ----------------------------------------------


@dataclass(frozen=True)
class ShareOfVoiceSummary:
    """Aggregates a set of :class:`ProbeResult` into a mention-rate
    percentage -- the full query set and every raw response are retained
    (``results``, Golden Rule 7), never summarized away."""

    results: tuple[ProbeResult, ...]
    errors: tuple[ProbeError, ...]
    n_queries: int
    n_mentioned: int
    n_errors: int
    mention_rate_pct: float
    engines: tuple[str, ...]
    summary: str


def aggregate_share_of_voice(
    results: Sequence[ProbeResult],
    errors: Sequence[ProbeError] = (),
) -> ShareOfVoiceSummary:
    """``mention_rate_pct = 100 * n_mentioned / n_queries``, rounded to one
    decimal place. Raises ``ValueError`` on an empty ``results`` -- a rate
    computed from zero probes is not a meaningful statistic and would
    otherwise silently read as "0% mentioned" (Golden Rule 14). Failed
    probes (``errors``) are reported in ``summary`` but do NOT count toward
    ``n_queries``/the percentage -- they never ran, so they carry no signal
    either way."""
    if not results:
        raise ValueError("cannot compute a share-of-voice summary from zero probe results")

    n_queries = len(results)
    n_mentioned = sum(1 for r in results if r.brand_mentioned)
    mention_rate_pct = round(100.0 * n_mentioned / n_queries, 1)
    engines = tuple(sorted({r.engine for r in results}))
    n_errors = len(errors)

    summary = (
        f"geo_probe: {n_mentioned}/{n_queries} probe(s) ({mention_rate_pct}%) mentioned the "
        f"brand across engine(s) {', '.join(engines)}"
    )
    if n_errors:
        summary += f"; {n_errors} probe(s) failed and are excluded from this rate (see errors)"
    summary += "."

    return ShareOfVoiceSummary(
        results=tuple(results), errors=tuple(errors), n_queries=n_queries, n_mentioned=n_mentioned,
        n_errors=n_errors, mention_rate_pct=mention_rate_pct, engines=engines, summary=summary,
    )


# -- the one sanctioned, working probe: Claude via scm_agent.llm.LLMProvider -


@runtime_checkable
class GeoLlmModel(Protocol):
    """The slice of LLMProvider a GEO probe needs (scm_agent.llm.LLMProvider
    fits) -- same minimal-Protocol pattern as src/voice/doc_reader.py's
    DocModel, so this engine-layer module never statically imports the
    agent layer."""

    def available(self) -> bool: ...
    def complete(self, prompt: str) -> str: ...


class _UnavailableModel:
    def available(self) -> bool:
        return False

    def complete(self, prompt: str) -> str:
        return ""


def _default_model() -> GeoLlmModel:
    """Resolve the default Claude provider lazily (keeps this module free of
    a static agent-layer import, same as src/voice/doc_reader.py's own
    ``_default_model``)."""
    try:
        from scm_agent.llm import get_provider

        return get_provider()
    except Exception:
        return _UnavailableModel()


def build_claude_probe(model: GeoLlmModel | None = None) -> Callable[[str], str]:
    """The ONE sanctioned, working probe target this module ships (see
    module docstring / :data:`SINGLE_SANCTIONED_ENGINE_CAVEAT`). Wraps
    ``scm_agent.llm.LLMProvider.complete()`` -- Claude's official Anthropic
    API integration, already used elsewhere in this repo (narrative
    rewriting, document extraction) -- as a ``Callable[[str], str]`` this
    module's own :func:`run_probe`/:func:`run_probe_set` accept.

    ``model`` defaults to the repo's configured provider
    (``scm_agent.llm.get_provider()``, resolved lazily); inject a stub for
    tests, or a different :class:`GeoLlmModel`-shaped object if the caller
    manages its own Claude client. Raises ``RuntimeError`` immediately if
    no provider is available (no ``ANTHROPIC_API_KEY`` configured) rather
    than returning a callable that would silently produce a batch of empty
    responses -- a caller that wants to check availability ahead of time
    can call ``.available()`` on their own model/``get_provider()`` result
    first."""
    resolved = model if model is not None else _default_model()
    if not resolved.available():
        raise RuntimeError(
            "no Claude provider available -- set ANTHROPIC_API_KEY (or inject a "
            "configured GeoLlmModel) before building a probe"
        )

    def _probe(query: str) -> str:
        return resolved.complete(query)

    return _probe

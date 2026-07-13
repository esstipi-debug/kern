"""Optional LLM adjudication for the ambiguous band (Linchpin 3.0 PR-14,
plan S6.5 step 4 -- "Adjudicacion LLM opcional franja 0.5-0.85 (mismo/
distinto/variante + razon) -> propone, nunca confirma solo").

Golden rule 10 ("LLM jamas silencioso en el camino de datos") applies here
exactly as it does to ``extract.py``'s tier-5 LLM extractor: strict schema
(:class:`LlmAdjudicationResponse` -- a frozen, eagerly-validated dataclass;
this repo has no pydantic dependency, so the schema is enforced the same way
every other ``models.py``/``sanity.py`` record enforces its own -- see
``__post_init__``), and -- since match/ is ``src/`` (pure functions only,
plan rule 1: "no I/O side effects in src/ beyond what's explicitly
specified") -- this module NEVER calls an LLM provider itself. A caller
injects an already-configured callable (``llm: LlmAdjudicator``); the actual
network request, budget-cap bookkeeping, and daily-cap enforcement (rule 10)
belong to that injected callable / its caller (a future job, not this PR),
exactly mirroring ``extract.py``'s ``_extract_via_llm`` stub -- see that
module's own docstring for the identical design call.

**"Propone, nunca confirma solo" is enforced structurally, not by
convention**: :func:`adjudicate_pair` returns an :class:`AdjudicationResult`
and NOTHING ELSE -- it never touches :mod:`sku_map`, never sets
``MatchCandidate.status``, and its own return type has no ``confirmed``
verdict value (only ``"same" | "different" | "variant" | "deferred"``,
:data:`VERDICTS`). Only a caller choosing to write a
:class:`~src.pricing_intel.models.MatchCandidate` via
``sku_map.SkuMap.record(..., confirmed_by=...)`` can turn an LLM's
"same" proposal into an actual ``confirmed`` entry -- and that call is a
deliberate, auditable, separate act (the caller supplies
``confirmed_by="llm"`` explicitly to record that basis; see
``sku_map.py``'s own docstring). This module has no path to do that itself.

**No LLM provider wired in this PR** (``llm=None``, the shipped default --
same stub/defer pattern as ``extract.py``'s tier 5): :func:`adjudicate_pair`
returns a ``"deferred"`` verdict, ``confidence=0.0``, and an explanatory
``reason`` -- never raises, never fabricates a same/different/variant
judgement. A future PR wiring a real provider passes ``llm=<callable>``;
this function's contract does not change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .fuzzy import ProductAttributes
from .probabilistic import CONFIRM_THRESHOLD, SUSPECT_THRESHOLD

# plan S6.5 point 4: "franja 0.5-0.85" -- the probabilistic score band where
# an LLM's judgement is expected to add the most value (too uncertain for
# algorithmic auto-confirm, too plausible to reject outright). This is a
# SUBSET of probabilistic.py's wider "suspect" range
# [SUSPECT_THRESHOLD, CONFIRM_THRESHOLD) = [0.50, 0.96) -- a suspect score
# in [0.85, 0.96) is left for direct human review (sku_map.py) rather than
# LLM adjudication, per the plan's own literal band.
ADJUDICATION_BAND: tuple[float, float] = (0.5, 0.85)

VERDICTS: tuple[str, ...] = ("same", "different", "variant")
_ALL_OUTCOMES: tuple[str, ...] = VERDICTS + ("deferred",)


def is_in_adjudication_band(score: float) -> bool:
    """Whether ``score`` falls in the plan's literal LLM-adjudication band
    ``[0.5, 0.85)`` -- both bounds are within probabilistic.py's own
    thresholds (``SUSPECT_THRESHOLD=0.50``, ``CONFIRM_THRESHOLD=0.96``) by
    construction; asserted once at import time below rather than re-checked
    per call."""
    if not (0.0 <= score <= 1.0):
        raise ValueError(f"score must be within [0, 1], got {score!r}")
    lo, hi = ADJUDICATION_BAND
    return lo <= score < hi


assert SUSPECT_THRESHOLD <= ADJUDICATION_BAND[0] < ADJUDICATION_BAND[1] <= CONFIRM_THRESHOLD


@dataclass(frozen=True)
class AdjudicationRequest:
    """Everything an LLM adjudicator needs to judge one pair -- the two
    products plus the probabilistic score that flagged it as ambiguous."""

    our: ProductAttributes
    competitor: ProductAttributes
    probabilistic_score: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.probabilistic_score <= 1.0):
            raise ValueError(f"probabilistic_score must be within [0, 1], got {self.probabilistic_score!r}")


@dataclass(frozen=True)
class LlmAdjudicationResponse:
    """Strict schema (plan rule 10) an injected :data:`LlmAdjudicator`
    callable must return. This module never free-parses raw LLM text --
    whatever wraps the actual provider call is responsible for turning its
    output into this shape (or raising), the same boundary
    ``extract.py``'s deferred tier 5 documents for its own future
    implementation."""

    verdict: str  # "same" | "different" | "variant"
    reason: str
    confidence: float

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}, got {self.verdict!r}")
        if not self.reason or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be within [0, 1], got {self.confidence!r}")


@dataclass(frozen=True)
class AdjudicationResult:
    """Outcome of :func:`adjudicate_pair` -- ALWAYS advisory (module
    docstring). ``extractor``/``extractor_version`` follow the same
    procedence convention as ``models.CompetitorOffer`` (golden rule 7,
    applied here to a match decision instead of a price observation)."""

    verdict: str  # "same" | "different" | "variant" | "deferred"
    reason: str
    confidence: float
    extractor: str = "llm"
    extractor_version: str = "unwired"

    def __post_init__(self) -> None:
        if self.verdict not in _ALL_OUTCOMES:
            raise ValueError(f"verdict must be one of {_ALL_OUTCOMES}, got {self.verdict!r}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be within [0, 1], got {self.confidence!r}")


LlmAdjudicator = Callable[[AdjudicationRequest], LlmAdjudicationResponse]


def adjudicate_pair(
    request: AdjudicationRequest,
    *,
    llm: LlmAdjudicator | None = None,
    extractor_version: str = "unwired",
) -> AdjudicationResult:
    """Propose a same/different/variant verdict for ``request`` -- or defer
    when no LLM provider is wired (this PR's shipped state). Never raises on
    a missing provider; never confirms a match by itself (module docstring).

    ``llm`` is called with ``request`` and must return a validated
    :class:`LlmAdjudicationResponse` (or raise) -- this function does not
    catch provider exceptions, matching this repo's "fail fast at the
    boundary" convention (coding-style.md) rather than swallowing a broken
    integration into a silent "deferred".
    """
    if llm is None:
        return AdjudicationResult(
            verdict="deferred",
            reason="no_llm_provider_configured",
            confidence=0.0,
            extractor="llm",
            extractor_version="unwired",
        )

    response = llm(request)
    if not isinstance(response, LlmAdjudicationResponse):
        raise TypeError(
            f"llm callable must return LlmAdjudicationResponse, got {type(response).__name__}"
        )
    return AdjudicationResult(
        verdict=response.verdict,
        reason=response.reason,
        confidence=response.confidence,
        extractor="llm",
        extractor_version=extractor_version,
    )

"""Product matching for the pricing titan (Linchpin 3.0 PR-14, plan section
6.5 "Matching de producto"): turns one (our SKU, competitor listing) pair
into a state -- ``confirmed`` / ``suspect`` / ``rejected`` -- never a bare
boolean, and a versioned, append-only record of who/what decided it.

Pipeline (plan S6.5, each step its own module):
  1. ``gtin.py``          -- exact GTIN/EAN/UPC check-digit match -> confirmed
                             (0.99), ``confirmed_by="auto"``.
  2. ``fuzzy.py``         -- cheap RapidFuzz blocking on title+brand -> a
                             short list of plausible (our, competitor) pairs
                             worth scoring.
  3. ``probabilistic.py`` -- compound score over title/brand/attributes.
                             SPIKE (this PR): ``splink>=4`` installs cleanly
                             and its DuckDB backend runs a real linking
                             prediction on this Windows py3.11 venv -- the
                             plan's risk #4 (duckdb wheels) did NOT
                             materialize. It is NOT wired into this module,
                             though: a naive Splink comparison over raw
                             title text scores a wrong-model reword (XM5 vs
                             XM4) HIGHER than a correct reword with reordered
                             words, because Jaro-Winkler/Levenshtein-style
                             comparisons are prefix/order-sensitive in a way
                             RapidFuzz's token_sort_ratio is not -- closing
                             that gap needs real preprocessing/calibration
                             work this PR's budget does not cover. Plan
                             section 14 risk #4 explicitly accepts this:
                             "el PR no se bloquea" -- so PR-14 ships the
                             fully-specified fallback (plan S6.5 point 3's
                             own fallback text: "score compuesto RapidFuzz
                             por campo + reglas de atributos... calibrado
                             contra el set etiquetado") as the real,
                             hand-verified, precision-tested scorer. See
                             ``probabilistic.py``'s module docstring for the
                             full writeup and ``pyproject.toml``'s new
                             ``matching`` extra for the pinned upgrade path.
  4. ``adjudicate.py``    -- optional LLM adjudication for the 0.5-0.85 band
                             (plan point 4): proposes same/different/variant
                             + reason, never confirms alone. No LLM provider
                             is wired in this PR (same stub/defer pattern as
                             ``extract.py``'s tier-5 LLM extractor).
  5. ``sku_map.py``       -- versioned, append-only store of every match
                             decision (plan point 5), immutable per version;
                             a re-review is a new row, never an overwrite
                             (golden rule 8).

Nothing in this package performs network I/O or touches a database except
``sku_map.py`` (the versioned store itself, by design) -- ``gtin.py``,
``fuzzy.py``, ``probabilistic.py`` and ``adjudicate.py`` are pure functions
over already-assembled :class:`~src.pricing_intel.match.fuzzy.ProductAttributes`.
"""

from __future__ import annotations

from .adjudicate import (
    ADJUDICATION_BAND,
    AdjudicationRequest,
    AdjudicationResult,
    LlmAdjudicationResponse,
    adjudicate_pair,
    is_in_adjudication_band,
)
from .fuzzy import (
    BLOCKING_THRESHOLD,
    BlockingCandidate,
    ProductAttributes,
    block_candidates,
    blocking_score,
)
from .gtin import (
    GTIN_CONFIRMED_BY,
    GTIN_CONFIRMED_SCORE,
    match_by_gtin,
    normalize_gtin,
)
from .probabilistic import (
    ATTRIBUTE_CONFLICT_CEILING,
    CONFIRM_THRESHOLD,
    DECISIVE_ATTRIBUTE_KEYS,
    SUSPECT_THRESHOLD,
    ProbabilisticScore,
    classify_score,
    score_pair,
    score_to_match_candidate,
)
from .sku_map import (
    AUTO_CONFIRMED_BY,
    LLM_CONFIRMED_BY,
    SkuMap,
    SkuMapEntry,
    default_sku_map,
)

__all__ = [
    "ADJUDICATION_BAND",
    "ATTRIBUTE_CONFLICT_CEILING",
    "AUTO_CONFIRMED_BY",
    "BLOCKING_THRESHOLD",
    "CONFIRM_THRESHOLD",
    "DECISIVE_ATTRIBUTE_KEYS",
    "GTIN_CONFIRMED_BY",
    "GTIN_CONFIRMED_SCORE",
    "LLM_CONFIRMED_BY",
    "SUSPECT_THRESHOLD",
    "AdjudicationRequest",
    "AdjudicationResult",
    "BlockingCandidate",
    "LlmAdjudicationResponse",
    "ProbabilisticScore",
    "ProductAttributes",
    "SkuMap",
    "SkuMapEntry",
    "adjudicate_pair",
    "block_candidates",
    "blocking_score",
    "classify_score",
    "default_sku_map",
    "is_in_adjudication_band",
    "match_by_gtin",
    "normalize_gtin",
    "score_pair",
    "score_to_match_candidate",
]

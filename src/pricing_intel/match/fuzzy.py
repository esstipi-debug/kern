"""Cheap RapidFuzz blocking on title+brand (Linchpin 3.0 PR-14, plan S6.5
step 2 -- "Blocking barato RapidFuzz (titulo+marca) -> candidatos").

Blocking's job is NOT to decide a match -- it is a cheap pre-filter that
turns an our-catalog x competitor-catalog cross product (expensive at scale)
into a short list of plausible (our, competitor) pairs worth handing to the
more expensive :mod:`probabilistic` scorer, or skipping entirely when
:mod:`gtin` already confirmed the pair. RapidFuzz (already in the
``dataquality`` extra -- not duplicated here) is a C++-backed Levenshtein
implementation; scoring a few thousand SKUs per competitor site against our
catalog is milliseconds, not minutes, on a single machine.

:class:`ProductAttributes` is the shared "what match/ needs to know about
one product" shape -- defined here (the earliest stage that needs it) and
reused unchanged by :mod:`probabilistic` and :mod:`adjudicate`, so a caller
builds it exactly once per product regardless of how many pipeline stages
that product's candidate pairs pass through.

RapidFuzz's own scale (0-100) is kept throughout this module -- every score
here is blocking-only, never a final confidence; :mod:`probabilistic` is
what rescales onto the [0, 1] confidence scale ``MatchCandidate.score`` and
the rest of ``models.py`` use.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

# Title carries far more discriminating signal than brand: two products from
# the SAME brand with wildly different titles are almost never the same SKU,
# but an identical title under a slightly different brand string is often a
# reseller/private-label variant of the same physical product. Not tuned
# against data (this is a cheap pre-filter, not the final score) -- picked to
# weight title as the dominant signal while still letting a badly-mismatched
# brand pull a pair's blocking_score down.
TITLE_WEIGHT = 0.7
BRAND_WEIGHT = 0.3

# 0-100 RapidFuzz scale. Deliberately permissive (blocking's only job is to
# avoid scoring OBVIOUSLY unrelated pairs with the more expensive
# probabilistic step next -- the real precision decision happens there);
# see probabilistic.py's CONFIRM_THRESHOLD/SUSPECT_THRESHOLD for the actual
# match-confidence cutoffs.
BLOCKING_THRESHOLD = 55.0


@dataclass(frozen=True)
class ProductAttributes:
    """The minimal shape match/ needs to compare two products.

    ``attributes`` is a free-form dict of already-normalized, already
    EXTRACTED facts (pack_size, model, size, capacity, color...) --
    :mod:`probabilistic`'s per-field rules read known decisive keys from it
    when present; parsing them out of raw title text is out of this PR's
    scope (a catalog-ingestion or ``normalize.py`` concern). A key absent
    from BOTH sides of a pair is "no evidence", never inferred as a match or
    a conflict -- see ``probabilistic.py``'s ``_attribute_similarity`` for
    the honest-uncertainty rule this feeds.
    """

    product_id: str
    title: str
    brand: str
    attributes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.product_id or not self.product_id.strip():
            raise ValueError("product_id must be a non-empty string")
        if not self.title or not self.title.strip():
            raise ValueError("title must be a non-empty string")
        if not self.brand or not self.brand.strip():
            raise ValueError("brand must be a non-empty string")


@dataclass(frozen=True)
class BlockingCandidate:
    """One (our, competitor) pair that survived the cheap title+brand
    blocking pass, with its RapidFuzz component scores (0-100 scale) and the
    weighted ``blocking_score`` (also 0-100) used to rank/threshold.
    """

    our: ProductAttributes
    competitor: ProductAttributes
    title_score: float
    brand_score: float
    blocking_score: float


def _normalize_text(text: str) -> str:
    """Lower-case + collapse whitespace -- RapidFuzz's ratio functions are
    case-sensitive (verified: ``fuzz.ratio("Apple", "apple") == 80.0``, not
    100.0), so this normalization is load-bearing, not cosmetic."""
    return " ".join(text.strip().lower().split())


def blocking_score(our: ProductAttributes, competitor: ProductAttributes) -> BlockingCandidate:
    """Score one (our, competitor) pair -- ``fuzz.token_sort_ratio`` (order-
    invariant: sorts each string's tokens before comparing, so "Apple
    iPhone" and "iPhone - Apple" score identically to "Apple iPhone" vs
    "Apple iPhone") on the lower-cased, whitespace-normalized title and
    brand, weighted 70/30.

    Reference examples (see tests/test_pricing_intel_match_fuzzy.py):
      identical title+brand                    -> blocking_score 100.0
      title differs only by whitespace/case     -> blocking_score 100.0
      title matches, brand totally different    -> title_score=100.0,
        brand_score=0.0 -> blocking_score = 0.7*100 + 0.3*0 = 70.0
      both title and brand totally different    -> low blocking_score
    """
    # Lazy: rapidfuzz ships in the optional dataquality extra, absent in prod's .[web,mcp]
    # install -- import here so this module stays import-safe on the app's boot chain.
    from rapidfuzz import fuzz

    title_score = fuzz.token_sort_ratio(_normalize_text(our.title), _normalize_text(competitor.title))
    brand_score = fuzz.token_sort_ratio(_normalize_text(our.brand), _normalize_text(competitor.brand))
    combined = TITLE_WEIGHT * title_score + BRAND_WEIGHT * brand_score
    return BlockingCandidate(
        our=our, competitor=competitor, title_score=title_score, brand_score=brand_score, blocking_score=combined
    )


def block_candidates(
    our_catalog: Sequence[ProductAttributes],
    competitor_catalog: Sequence[ProductAttributes],
    *,
    threshold: float = BLOCKING_THRESHOLD,
) -> list[BlockingCandidate]:
    """Score every (our, competitor) pair and keep only those at or above
    ``threshold`` (RapidFuzz 0-100 scale), sorted by ``blocking_score``
    descending -- the short list :mod:`probabilistic`'s ``score_pair`` (or a
    caller looping over it) actually scores next.

    O(len(our_catalog) * len(competitor_catalog)) -- fine at the catalog
    sizes this product targets (plan S6.2: a few thousand SKUs per
    competitor site); a larger deployment would swap in RapidFuzz's own
    batched ``process.cdist`` without changing this function's contract.
    """
    if not (0.0 <= threshold <= 100.0):
        raise ValueError(f"threshold must be within [0, 100], got {threshold!r}")
    candidates = [blocking_score(o, c) for o in our_catalog for c in competitor_catalog]
    candidates = [c for c in candidates if c.blocking_score >= threshold]
    candidates.sort(key=lambda c: c.blocking_score, reverse=True)
    return candidates

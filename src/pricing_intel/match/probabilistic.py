"""Probabilistic product-match score (Linchpin 3.0 PR-14, plan S6.5 step 3).

**Splink spike (mandatory per this PR's brief, plan section 14 risk #4):**
``pip install "splink>=4"`` installs cleanly on this Windows py3.11 venv
(``splink==4.0.16``, pulling ``duckdb==1.5.4``) and its DuckDB backend runs a
real ``Linker(...).inference.predict()`` query without error -- the specific
wheel risk the plan flagged did NOT materialize here. That clears the bar
the PR brief set for continuing to try ("if it fails to install OR fails a
basic smoke-test query, stop and use the fallback").

Splink is nonetheless NOT wired into this module. A second, deeper spike
(scoring the SAME reworded-title/wrong-model-variant pairs this module's own
reference examples use, via ``cl.JaroWinklerAtThresholds`` with explicit,
untrained m/u probabilities so the comparison stays deterministic) scored a
WRONG match higher than a RIGHT one: Jaro-Winkler is prefix/order-sensitive,
so "Sony WH-1000XM5..." vs "WH-1000XM5...- Sony" (same product, brand token
moved to the end) loses more similarity than "Sony WH-1000XM5..." vs "Sony
WH-1000XM4..." (wrong model, one digit swapped, word order untouched) --
exactly backwards. RapidFuzz's ``token_sort_ratio`` (this module's actual
scorer, below) sorts tokens before comparing and does not have this
failure mode. Closing that gap for Splink would need real preprocessing and
m/u calibration -- out of this PR's budget, and not required to ship: plan
S6.5 point 3 pre-specifies exactly this module's fallback ("score compuesto
RapidFuzz por campo + reglas de atributos... calibrado contra el set
etiquetado"), and plan section 14 risk #4 explicitly accepts this outcome
("el PR no se bloquea"). ``pyproject.toml``'s new ``matching`` extra pins
``splink>=4`` as the documented future upgrade path once that calibration
work happens; nothing here imports it.

**The compound score actually shipped**, per field:
  - ``title_similarity``    -- ``fuzz.token_sort_ratio`` on the lower-cased,
    whitespace-normalized title (order-invariant -- see module docstring
    above for why this matters).
  - ``brand_similarity``    -- ``fuzz.token_sort_ratio`` on brand.
  - ``attribute_similarity`` -- a RULE, not a fuzzy ratio (plan point 3:
    "reglas de atributos (talla/pack/modelo)"): the fraction of DECISIVE
    keys (:data:`DECISIVE_ATTRIBUTE_KEYS`) present on BOTH sides that agree
    exactly (case/whitespace-normalized). A decisive key present on only one
    side is not compared (honest "no evidence", never guessed -- golden
    rule 14). No shared decisive keys at all -> neutral 1.0 (text similarity
    alone decides -- see the worked "genuinely ambiguous" example below for
    why this is still often not enough to auto-confirm).
  - Any DISAGREEMENT on a shared decisive key is a hard override: the
    combined score is capped at :data:`ATTRIBUTE_CONFLICT_CEILING`
    regardless of how similar the titles read (a 500ml bottle and a 2L
    bottle of the identical product are, deliberately, different SKUs).

``score = TITLE_WEIGHT*title_similarity + BRAND_WEIGHT*brand_similarity +
ATTRIBUTE_WEIGHT*attribute_similarity``, then the conflict cap if it
applies. :func:`classify_score` maps the [0, 1] result onto the plan's
states: ``>= CONFIRM_THRESHOLD`` -> ``confirmed`` (auto, no human needed --
this is the ONE case besides ``gtin.py`` where match/ auto-confirms);
``[SUSPECT_THRESHOLD, CONFIRM_THRESHOLD)`` -> ``suspect``; below that ->
``rejected``. ``CONFIRM_THRESHOLD=0.96`` was picked (not the more obvious
0.9) specifically because 0.9 was measured to auto-confirm real
"same-brand, similar-wording, different SKU" pairs during calibration
(worked example below) -- 0.96 is the tightest threshold that still lets a
genuinely reworded (not reworded+respecified) title through.

Hand-verified worked examples (identical to
``tests/test_pricing_intel_match_probabilistic.py`` and the ``"same"``/
``"different"`` rows of ``tests/fixtures/matching_labeled.csv``):

  1. Reworded title, matching brand, no attributes either side (Coca-Cola
     "Bottle 2L" reordered to "2L Bottle" -- token_sort_ratio sorts both to
     the identical token multiset, so ``title_similarity = 1.0`` exactly):
       score = 0.55*1.0 + 0.15*1.0 + 0.30*1.0 = 1.0  -> confirmed.

  2. Decisive attribute CONFLICT (Sony WH-1000XM5 vs WH-1000XM4, same
     brand, ``model`` differs): title_similarity = 0.9807692307692307
     (measured), so the pre-cap score would be
     0.55*0.9807692307692307 + 0.15*1.0 + 0.30*1.0 = 0.9394230769..., but
     the conflict cap forces the result down to exactly
     ATTRIBUTE_CONFLICT_CEILING = 0.45 -> rejected, no matter how similar
     the titles read.

  3. Genuinely ambiguous -- same brand, similar wording, NO decisive
     attribute recorded on either side ("Samsung Galaxy S23 Smartphone" vs
     "Samsung Galaxy S23 Ultra Smartphone" -- a real product-tier
     extension, not a rewording): title_similarity = 0.90625 (measured,
     exact fraction), so
     score = 0.55*0.90625 + 0.15*1.0 + 0.30*1.0 = 0.9484375
     -- below CONFIRM_THRESHOLD (0.96) -> suspect, NOT silently
     auto-confirmed, exactly the behavior this PR's QA bar requires. This
     is also why CONFIRM_THRESHOLD could not simply be 0.90: that would
     have auto-confirmed this pair (0.9484375 >= 0.90) despite it being a
     different, more expensive SKU.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from rapidfuzz import fuzz

from ..models import MatchCandidate
from .fuzzy import ProductAttributes

# Attribute keys treated as DECISIVE -- categorical facts where disagreement
# means "different sellable unit", not "different phrasing" (plan point 3:
# "talla/pack/modelo"; capacity/color added as the same class of fact for
# electronics/apparel). Compared as normalized exact strings, never fuzzily
# -- "500ml" and "1L" are not "close", they are a different SKU.
DECISIVE_ATTRIBUTE_KEYS: tuple[str, ...] = ("pack_size", "size", "model", "capacity", "color")

# Weighted-sum coefficients (sum to 1.0 by construction -- enforced by a
# module-level assertion below so a future edit can't silently break the
# [0, 1] contract on ProbabilisticScore.score).
TITLE_WEIGHT = 0.55
BRAND_WEIGHT = 0.15
ATTRIBUTE_WEIGHT = 0.30
assert abs((TITLE_WEIGHT + BRAND_WEIGHT + ATTRIBUTE_WEIGHT) - 1.0) < 1e-9

# Any shared decisive attribute disagreeing caps the combined score here,
# regardless of title/brand similarity (module docstring, worked example 2).
# Chosen to sit BELOW SUSPECT_THRESHOLD (0.50) -- a confirmed attribute
# conflict is a "rejected", not merely a "suspect", since it is positive
# evidence of a different SKU rather than an absence of evidence.
ATTRIBUTE_CONFLICT_CEILING = 0.45

# plan S6.5's own text: "solo confirmed (o >=0.9) alimenta P2/A5" names 0.9
# as a plausible floor; calibration against tests/fixtures/matching_labeled.csv
# (worked example 3 above) showed 0.9 auto-confirms a real different-SKU
# pair. 0.96 is the tightest round threshold clearing every "same" fixture
# row while excluding every "different" one -- see
# tests/test_pricing_intel_matching_labeled_precision.py.
CONFIRM_THRESHOLD = 0.96
SUSPECT_THRESHOLD = 0.50


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


@dataclass(frozen=True)
class ProbabilisticScore:
    """One scored (our, competitor) pair -- the compound score plus its
    components, so a caller (or a human reviewing a ``suspect`` entry) can
    see WHY, not just the final number."""

    our: ProductAttributes
    competitor: ProductAttributes
    title_similarity: float
    brand_similarity: float
    attribute_similarity: float
    attribute_conflict: bool
    conflicting_attributes: tuple[str, ...]
    score: float


def _attribute_similarity(
    our_attrs: dict[str, str], competitor_attrs: dict[str, str]
) -> tuple[float, bool, tuple[str, ...]]:
    """Fraction of shared decisive keys that agree (normalized exact
    match); ``(1.0, False, ())`` -- neutral, no evidence either way -- when
    no decisive key is present on both sides. A key present on only one
    side is never compared (honest "unknown", not a guessed mismatch)."""
    shared = [k for k in DECISIVE_ATTRIBUTE_KEYS if k in our_attrs and k in competitor_attrs]
    if not shared:
        return 1.0, False, ()
    conflicts = tuple(k for k in shared if _normalize_text(str(our_attrs[k])) != _normalize_text(str(competitor_attrs[k])))
    similarity = (len(shared) - len(conflicts)) / len(shared)
    return similarity, bool(conflicts), conflicts


def score_pair(our: ProductAttributes, competitor: ProductAttributes) -> ProbabilisticScore:
    """Compute the compound probabilistic score for one (our, competitor)
    pair (module docstring has the full formula and three worked examples).
    Pure function -- no I/O, no randomness, fully deterministic for a given
    pair of :class:`~src.pricing_intel.match.fuzzy.ProductAttributes`.
    """
    title_sim = fuzz.token_sort_ratio(_normalize_text(our.title), _normalize_text(competitor.title)) / 100.0
    brand_sim = fuzz.token_sort_ratio(_normalize_text(our.brand), _normalize_text(competitor.brand)) / 100.0
    attr_sim, conflict, conflicting_keys = _attribute_similarity(our.attributes, competitor.attributes)

    raw = TITLE_WEIGHT * title_sim + BRAND_WEIGHT * brand_sim + ATTRIBUTE_WEIGHT * attr_sim
    if conflict:
        raw = min(raw, ATTRIBUTE_CONFLICT_CEILING)
    score = max(0.0, min(1.0, raw))

    return ProbabilisticScore(
        our=our,
        competitor=competitor,
        title_similarity=title_sim,
        brand_similarity=brand_sim,
        attribute_similarity=attr_sim,
        attribute_conflict=conflict,
        conflicting_attributes=conflicting_keys,
        score=score,
    )


def classify_score(score: float) -> str:
    """Map a [0, 1] score onto ``MatchCandidate.status`` (plan S6.5: a
    pipeline of STATES, never a bare boolean).

    Reference: 0.96 -> "confirmed" (>= CONFIRM_THRESHOLD); 0.9484375 (worked
    example 3) -> "suspect"; 0.50 -> "suspect" (boundary is inclusive); 0.45
    (worked example 2, the conflict ceiling) -> "rejected"; 0.0 ->
    "rejected".
    """
    if not (0.0 <= score <= 1.0):
        raise ValueError(f"score must be within [0, 1], got {score!r}")
    if score >= CONFIRM_THRESHOLD:
        return "confirmed"
    if score >= SUSPECT_THRESHOLD:
        return "suspect"
    return "rejected"


def score_to_match_candidate(
    result: ProbabilisticScore,
    *,
    site: str,
    competitor_sku_ref: str,
    now: datetime | None = None,
) -> MatchCandidate:
    """Build a :class:`~src.pricing_intel.models.MatchCandidate` from a
    :class:`ProbabilisticScore`. ``status="confirmed"`` sets
    ``confirmed_by="auto"`` (plan S6.5: "solo confirmed (o >=0.9) alimenta
    P2/A5" -- a probabilistic score clearing :data:`CONFIRM_THRESHOLD` is,
    like the GTIN path, algorithmic self-confirmation, not a human decision)
    and ``confirmed_at=now``; ``suspect``/``rejected`` leave
    ``confirmed_by=None`` -- nobody has confirmed anything yet, and
    :mod:`adjudicate`/a human reviewer via :mod:`sku_map` is what may change
    that later, as a NEW versioned entry (golden rule 8).
    """
    status = classify_score(result.score)
    reason = "probabilistic_score"
    if result.attribute_conflict:
        reason = f"attribute_conflict:{','.join(result.conflicting_attributes)}"
    elif status == "confirmed":
        reason = "probabilistic_score_high_confidence"
    elif status == "suspect":
        reason = "probabilistic_score_inconclusive"

    confirmed_by = None
    confirmed_at = None
    if status == "confirmed":
        confirmed_by = "auto"
        confirmed_at = now if now is not None else datetime.now(timezone.utc)

    return MatchCandidate(
        our_product_id=result.our.product_id,
        competitor_sku_ref=competitor_sku_ref,
        site=site,
        method="probabilistic",
        score=result.score,
        status=status,
        reason=reason,
        confirmed_by=confirmed_by,
        confirmed_at=confirmed_at,
    )

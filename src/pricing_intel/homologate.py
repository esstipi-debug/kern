"""Homologation cascade (Discovery-Assisted Price Intel plan, PR-4): for
each product discovered on a competitor's site (``discover.DiscoveredProduct``,
PR-2's output), decide which of OUR products it is -- if any -- by running
the SAME match cascade PR-14 already shipped (``match/gtin.py`` ->
``match/fuzzy.py`` -> ``match/probabilistic.py`` -> ``match/adjudicate.py``),
never a bespoke scoring rule of its own. This module's only job is
orchestration: sequencing those four existing pure functions per discovered
product and projecting their outcome onto one ``HomologationRow`` -- "my SKU
<-> competitor product <-> method <-> confidence <-> status" -- the table
this whole feature is named after.

Pure module, no I/O (mirrors every other ``src/pricing_intel/match/*``
module's own invariant -- see e.g. ``gtin.py``'s docstring): ``sku_map``
persistence (turning a ``confirmed``/human-reviewed row into a durable,
versioned entry) is explicitly the CALLER's job, PR-5, not this one's --
this module never imports ``sku_map`` and never writes anywhere.

**The cascade, per discovered product:**
  1. :func:`gtin.match_by_gtin` against every ``(our_product_id, our_gtin)``
     pair in ``our_gtins`` -- the one algorithmic auto-confirm path besides
     probabilistic >= ``CONFIRM_THRESHOLD`` (module docstring of
     ``gtin.py``).
  2. On a GTIN miss, :func:`fuzzy.block_candidates` shortlists our catalog
     against the discovered product's title+brand; the highest-
     ``blocking_score`` survivor is the ONE candidate carried into scoring
     (this cascade is sequential/best-candidate, exactly as ``fuzzy.py`` and
     ``probabilistic.py`` document their own contracts -- not "score
     everything and argmax").
  3. :func:`probabilistic.score_pair` + :func:`probabilistic.classify_score`
     on that pair, via :func:`probabilistic.score_to_match_candidate` --
     this module calls the real threshold, it does not re-decide it.
     ``>= CONFIRM_THRESHOLD`` (0.96) -> ``confirmed``, ``confirmed_by=
     "auto"``. Below ``SUSPECT_THRESHOLD`` (0.50) -> ``rejected`` (this is
     also where a decisive-attribute conflict lands, per
     ``probabilistic.py``'s hard conflict cap -- see its own worked example
     2). Everything in between stays ``suspect``, ``confirmed_by=None`` --
     unconditionally; see the safety note below.
  4. When a ``suspect`` score additionally falls in
     :data:`adjudicate.ADJUDICATION_BAND` (``[0.5, 0.85)``, a strict SUBSET
     of the wider suspect range), :func:`adjudicate.adjudicate_pair` is
     called to ENRICH the row's ``reason`` with an advisory verdict --
     ``deferred`` when ``llm=None`` (this module's default, matching every
     other unwired-LLM path in this repo -- golden rule 10). The row's
     ``status``/``confirmed_by`` are NEVER changed by this step; see the
     safety note.

**Safety note (the one invariant this whole module exists to protect):**
``status="suspect"`` rows NEVER get ``confirmed_by`` set, no matter what an
LLM adjudicator proposes -- ``adjudicate.adjudicate_pair`` structurally
cannot return a "confirmed" verdict (see that module's own docstring:
"propone, nunca confirma solo"), and this module does not invent a path
around that. Auto-confirmation happens ONLY via step 1 (gtin) or step 3
clearing ``CONFIRM_THRESHOLD`` -- both algorithmic, both already-shipped,
audited paths. :class:`HomologationRow` enforces this structurally too (see
its own ``__post_init__``): a non-``confirmed`` row is REQUIRED to carry
``confirmed_by=None``, not merely conventionally expected to.

**Attributes for the competitor side:** ``discover.DiscoveredProduct`` does
not carry a parsed ``attributes`` dict (title/brand/gtin/price_hint/offers
only -- see that module's docstring). :func:`probabilistic.score_pair`'s
decisive-attribute conflict rule (the categorical-fact half of golden rule
14's "no evidence, never guessed" -- pack_size/size/model/capacity/color)
still needs real per-product values to do its job, so this module reads them
the SAME way ``discover.py`` reads title/brand/gtin: straight off whatever
raw JSON-LD/microdata Offer dict(s) ``DiscoveredProduct.offers`` already
carries "for provenance" (that module's own words) -- never fabricated,
never inferred from title text. A key absent from every offer dict on the
page stays absent here too, which is precisely the "no evidence" case
``probabilistic.py`` already handles honestly.

NO PII (NON-GOAL 3): only title/brand/gtin/attributes are ever read here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from .discover import DiscoveredProduct
from .match import adjudicate, gtin
from .match.fuzzy import ProductAttributes, block_candidates
from .match.probabilistic import DECISIVE_ATTRIBUTE_KEYS, score_pair, score_to_match_candidate
from .models import MatchCandidate

# The only ``method`` values this cascade can actually produce. "fuzzy" is
# reserved in ``models.MATCH_METHODS`` for a future exact-fuzzy-only confirm
# path this module does not implement (blocking here is only ever a cheap
# pre-filter, never a confirm basis on its own -- see ``fuzzy.py``'s own
# docstring); "llm"/"human" are ``sku_map``-only bases a caller assigns when
# it later records a human/LLM-accepted verdict (PR-5), never something this
# pure cascade assigns itself.
_NO_CANDIDATE_METHOD = "none"
HOMOLOGATION_METHODS: tuple[str, ...] = ("gtin", "probabilistic", _NO_CANDIDATE_METHOD)
HOMOLOGATION_STATUSES: tuple[str, ...] = ("confirmed", "suspect", "rejected")


def _require_nonempty(field_name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True)
class HomologationRow:
    """One discovered product's homologation outcome -- "my SKU <->
    competitor product <-> method <-> confidence <-> status", one row per
    :class:`~src.pricing_intel.discover.DiscoveredProduct` passed to
    :func:`homologate`. Projects a
    :class:`~src.pricing_intel.models.MatchCandidate` (``our_product_id``
    set) plus the "matched nothing" case (``our_product_id=None``,
    ``status="rejected"``, a ``reason`` explaining why -- golden rule 14:
    reported, never silently dropped).
    """

    our_product_id: str | None
    competitor_sku_ref: str
    site: str
    method: str
    score: float
    status: str
    reason: str
    confirmed_by: str | None

    def __post_init__(self) -> None:
        _require_nonempty("competitor_sku_ref", self.competitor_sku_ref)
        _require_nonempty("site", self.site)
        if self.method not in HOMOLOGATION_METHODS:
            raise ValueError(f"method must be one of {HOMOLOGATION_METHODS}, got {self.method!r}")
        if self.status not in HOMOLOGATION_STATUSES:
            raise ValueError(f"status must be one of {HOMOLOGATION_STATUSES}, got {self.status!r}")
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(f"score must be within [0, 1], got {self.score!r}")
        if self.status == "confirmed":
            if not self.confirmed_by:
                raise ValueError("a 'confirmed' row must set confirmed_by")
            if self.our_product_id is None:
                raise ValueError("a 'confirmed' row must set our_product_id")
        elif self.confirmed_by is not None:
            # Structural guard against the ONE behavior this module exists
            # to forbid: a suspect/rejected row silently carrying a
            # confirmed_by -- see module docstring's safety note.
            raise ValueError(f"a {self.status!r} row must not set confirmed_by, got {self.confirmed_by!r}")


@dataclass(frozen=True)
class HomologationReport:
    """The full output of :func:`homologate` -- one :class:`HomologationRow`
    per input :class:`~src.pricing_intel.discover.DiscoveredProduct`
    (``rows``), plus counts and the ``unmatched`` subset (``rows`` where
    ``our_product_id is None``) so a caller doesn't need to re-filter for
    golden rule 14's "reported, never dropped" guarantee.
    """

    rows: tuple[HomologationRow, ...]
    n_confirmed: int
    n_suspect: int
    n_unmatched: int
    unmatched: tuple[HomologationRow, ...]


def _candidate_to_row(candidate: MatchCandidate) -> HomologationRow:
    return HomologationRow(
        our_product_id=candidate.our_product_id,
        competitor_sku_ref=candidate.competitor_sku_ref,
        site=candidate.site,
        method=candidate.method,
        score=candidate.score,
        status=candidate.status,
        reason=candidate.reason,
        confirmed_by=candidate.confirmed_by,
    )


def _unmatched_row(*, competitor_sku_ref: str, site: str, reason: str) -> HomologationRow:
    return HomologationRow(
        our_product_id=None,
        competitor_sku_ref=competitor_sku_ref,
        site=site,
        method=_NO_CANDIDATE_METHOD,
        score=0.0,
        status="rejected",
        reason=reason,
        confirmed_by=None,
    )


def _attributes_from_offers(offers: tuple[dict, ...]) -> dict[str, str]:
    """Pull whichever of ``probabilistic.DECISIVE_ATTRIBUTE_KEYS`` are
    stated, verbatim, on any raw Offer/Product dict ``offers`` carries --
    the SAME "read what's already there, never fabricate" discipline
    ``discover.py`` uses for title/brand/gtin (see its ``_scalar``/
    ``_first_field``). A key absent from every offer stays absent here too,
    which feeds ``probabilistic.py``'s own honest "no evidence" rule for a
    key present on only one side of a pair.
    """
    found: dict[str, str] = {}
    for offer in offers:
        for key in DECISIVE_ATTRIBUTE_KEYS:
            if key in found:
                continue
            value = offer.get(key)
            if isinstance(value, dict):
                value = value.get("name") or value.get("@value")
            if value not in (None, ""):
                found[key] = str(value)
    return found


def _try_gtin(
    discovered: DiscoveredProduct,
    our_gtins: dict[str, str] | None,
    *,
    now: datetime | None,
) -> MatchCandidate | None:
    if not discovered.gtin or not our_gtins:
        return None
    for our_product_id, our_gtin_value in our_gtins.items():
        candidate = gtin.match_by_gtin(
            our_product_id, our_gtin_value, discovered.url, discovered.site, discovered.gtin, now=now
        )
        if candidate is not None:
            return candidate
    return None


def _homologate_one(
    discovered: DiscoveredProduct,
    our_catalog: Sequence[ProductAttributes],
    *,
    our_gtins: dict[str, str] | None,
    llm: adjudicate.LlmAdjudicator | None,
    now: datetime | None,
) -> HomologationRow:
    gtin_candidate = _try_gtin(discovered, our_gtins, now=now)
    if gtin_candidate is not None:
        return _candidate_to_row(gtin_candidate)

    if not discovered.title or not discovered.brand:
        return _unmatched_row(
            competitor_sku_ref=discovered.url, site=discovered.site, reason="missing_title_or_brand"
        )

    competitor = ProductAttributes(
        product_id=discovered.url,
        title=discovered.title,
        brand=discovered.brand,
        attributes=_attributes_from_offers(discovered.offers),
    )

    candidates = block_candidates(our_catalog, [competitor])
    if not candidates:
        return _unmatched_row(
            competitor_sku_ref=discovered.url, site=discovered.site, reason="no_block_candidates"
        )

    best = candidates[0]
    result = score_pair(best.our, competitor)
    candidate = score_to_match_candidate(result, site=discovered.site, competitor_sku_ref=discovered.url, now=now)
    row = _candidate_to_row(candidate)

    if row.status == "suspect" and adjudicate.is_in_adjudication_band(result.score):
        request = adjudicate.AdjudicationRequest(
            our=best.our, competitor=competitor, probabilistic_score=result.score
        )
        adjudication = adjudicate.adjudicate_pair(request, llm=llm)
        row = HomologationRow(
            our_product_id=row.our_product_id,
            competitor_sku_ref=row.competitor_sku_ref,
            site=row.site,
            method=row.method,
            score=row.score,
            status=row.status,  # unconditionally unchanged -- see module safety note
            reason=f"{row.reason}|adjudication_{adjudication.verdict}:{adjudication.reason}",
            confirmed_by=row.confirmed_by,  # unconditionally unchanged (None)
        )

    return row


def homologate(
    discovered: Sequence[DiscoveredProduct],
    our_catalog: Sequence[ProductAttributes],
    *,
    our_gtins: dict[str, str] | None = None,
    llm: adjudicate.LlmAdjudicator | None = None,
    now: datetime | None = None,
) -> HomologationReport:
    """Run the match cascade (gtin -> fuzzy -> probabilistic -> adjudicate)
    for every ``discovered`` product against ``our_catalog``, producing one
    :class:`HomologationRow` each -- see module docstring for the full
    per-product cascade and the auto-confirm safety invariant.

    ``our_gtins`` maps ``our_product_id -> our_gtin`` for the GTIN step
    (omit or pass ``{}``/``None`` to skip it entirely -- every discovered
    product then falls straight to fuzzy/probabilistic). ``llm`` is passed
    through unchanged to :func:`adjudicate.adjudicate_pair`; ``None`` (the
    default) defers, exactly as that module documents.

    Pure: no I/O, no persistence -- ``sku_map`` is the caller's job (PR-5).
    """
    rows = tuple(
        _homologate_one(product, our_catalog, our_gtins=our_gtins, llm=llm, now=now) for product in discovered
    )
    unmatched = tuple(row for row in rows if row.our_product_id is None)
    n_confirmed = sum(1 for row in rows if row.status == "confirmed")
    n_suspect = sum(1 for row in rows if row.status == "suspect")
    return HomologationReport(
        rows=rows,
        n_confirmed=n_confirmed,
        n_suspect=n_suspect,
        n_unmatched=len(unmatched),
        unmatched=unmatched,
    )

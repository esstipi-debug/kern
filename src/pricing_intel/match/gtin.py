"""Exact GTIN/EAN/UPC check-digit match (Linchpin 3.0 PR-14, plan S6.5 step 1
-- "GTIN/EAN/UPC exacto (check-digit, python-stdnum) -> confirmed (0.99)").

``python-stdnum`` is already in the ``dataquality`` extra (not duplicated
here). Its ``stdnum.ean`` module validates the GTIN family uniformly --
EAN-8, UPC-A (12 digits), EAN-13 and GTIN-14 all share the same weighted
mod-10 check-digit algorithm, and ``stdnum.ean.validate`` accepts all four
lengths (verified against the installed ``python-stdnum==2.2`` source: the
length check is ``len(number) not in (14, 13, 12, 8)``) -- one function
covers every code this product needs to compare, with no separate UPC path
required.

Hand-verified reference (see ``tests/test_pricing_intel_match_gtin.py``):
EAN-13 ``4006381333931`` is the standard GS1/IFA demo barcode. Check digit
by hand -- take the first 12 digits ``400638133393``, weight alternating
3/1 from the rightmost digit (weight 3 first):

    digits (right to left): 3 9 3 3 3 1 8 3 6 0 0 4
    weights (right to left): 3 1 3 1 3 1 3 1 3 1 3 1
    products: 9 9 9 3 9 1 24 3 18 0 0 4  -> sum = 89
    check digit = (10 - 89 % 10) % 10 = (10 - 9) % 10 = 1

...which matches the code's own trailing ``1``. A single flipped digit
(``4006381333930``) fails this check and is correctly rejected.

This module never touches the ledger or ``sku_map`` -- it only produces (or
withholds) a :class:`~src.pricing_intel.models.MatchCandidate`; the caller
(a later PR's playbook, or a test) decides whether/how to persist it via
``sku_map.SkuMap.record``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from stdnum import ean
from stdnum.exceptions import ValidationError

from ..models import MatchCandidate

# plan S6.5 step 1: "-> confirmed (0.99)". The exact-GTIN path is the one
# case in the whole match/ pipeline that needs no human or LLM review --
# GS1 check digits make a false positive astronomically unlikely -- so it
# writes its own ``confirmed_by`` sentinel rather than requiring a caller to
# supply one (see the task brief: "'auto' for the exact-GTIN path").
GTIN_CONFIRMED_SCORE = 0.99
GTIN_CONFIRMED_BY = "auto"


def normalize_gtin(raw: str) -> str | None:
    """Return the compact, check-digit-valid GTIN/EAN/UPC form of ``raw``
    (whitespace/hyphen separators stripped), or ``None`` if it does not
    validate -- wrong length, non-digit characters, or a bad check digit.
    Never raises; a caller with an untrusted/optional field can call this
    directly without a try/except.
    """
    if not raw or not raw.strip():
        return None
    try:
        return ean.validate(raw)
    except ValidationError:
        return None


def match_by_gtin(
    our_product_id: str,
    our_gtin: str | None,
    competitor_sku_ref: str,
    site: str,
    competitor_gtin: str | None,
    *,
    now: datetime | None = None,
) -> MatchCandidate | None:
    """Exact GTIN/EAN/UPC check-digit match (plan S6.5 step 1).

    Returns a ``confirmed`` :class:`MatchCandidate` at score
    :data:`GTIN_CONFIRMED_SCORE` when both sides carry a check-digit-valid
    code that, once normalized, are IDENTICAL. Returns ``None`` -- never a
    low-score candidate, never a guess -- whenever either side is missing,
    malformed, or the two normalized codes simply differ; the caller is
    expected to fall through to :mod:`fuzzy`/:mod:`probabilistic` in that
    case (plan S6.5's pipeline is sequential, not "try everything and pick
    the best").

    Reference examples (see tests/test_pricing_intel_match_gtin.py):
      our_gtin="4006381333931", competitor_gtin="4006381333931"
        -> confirmed, score=0.99, method="gtin", confirmed_by="auto"
      our_gtin="4006381333931", competitor_gtin="400-638-133-3931" (same
        code, hyphenated) -> confirmed (normalization strips separators)
      our_gtin="4006381333931", competitor_gtin="4006381333930" (bad check
        digit) -> None
      our_gtin=None, competitor_gtin="4006381333931" -> None
      our_gtin="4006381333931", competitor_gtin="0036000291452" (different,
        valid, product) -> None
    """
    our_norm = normalize_gtin(our_gtin) if our_gtin else None
    comp_norm = normalize_gtin(competitor_gtin) if competitor_gtin else None
    if our_norm is None or comp_norm is None or our_norm != comp_norm:
        return None

    if now is None:
        now = datetime.now(timezone.utc)
    return MatchCandidate(
        our_product_id=our_product_id,
        competitor_sku_ref=competitor_sku_ref,
        site=site,
        method="gtin",
        score=GTIN_CONFIRMED_SCORE,
        status="confirmed",
        reason=f"gtin_exact_match:{our_norm}",
        confirmed_by=GTIN_CONFIRMED_BY,
        confirmed_at=now,
    )

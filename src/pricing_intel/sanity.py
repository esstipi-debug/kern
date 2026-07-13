"""Data-quality gate for the pricing titan (Linchpin 3.0 PR-12, plan section
6.6 "Sanidad de datos"): turns one already-extracted, not-yet-persisted price
observation into ACCEPT / QUARANTINE / DISCARD, with every reject carrying an
:class:`~scm_agent.events.Event` a caller can hand to an
:class:`~scm_agent.events.EventLedger` (F0, reused verbatim -- this PR does
not build a parallel event mechanism).

Where this sits in the pipeline: ``extract.py`` (PR-11) turns fetched HTML
into an :class:`~src.pricing_intel.extract.ExtractionResult`; a caller (a
later PR's ``jobs/price_intelligence.py``) combines that with acquisition
context (site, competitor_sku_ref, observed_at, match/ output) into a
:class:`RawOfferCandidate` -- the same field set as
:class:`~src.pricing_intel.models.CompetitorOffer` but WITHOUT
``CompetitorOffer.__post_init__``'s eager validation, because this module's
entire purpose is to catch and quarantine/discard exactly the malformed and
suspicious candidates that eager validation would otherwise either reject
with a raised exception (a bad price/currency -- fine, ``CompetitorOffer``
already refuses those outright, which is inconvenient here since we want an
EVENT, not a crash) or silently accept (a >40% intraday jump, a 30-day MAD
outlier -- ``CompetitorOffer`` has no way to know those are suspicious; that
is precisely this module's job). Once a candidate clears every gate,
:func:`to_competitor_offer` builds the real, validated ``CompetitorOffer``
-- ``CompetitorOffer.__post_init__`` re-validates independently as defense
in depth.

Five rules, each its own small pure function (plan S6.6, one test per rule):
  1. :func:`check_basic_validity`     -- price <= 0 / unknown currency /
     contradictory availability => DISCARD.
  2. :func:`check_intraday_delta` + :func:`resolve_pending_confirmation` --
     |delta| > 40% without ``promo_flag`` => QUARANTINE pending a
     confirmatory second read within 1h.
  3. :func:`check_mad_outlier`        -- median absolute deviation over a
     trailing 30-day window => QUARANTINE.
  4. :func:`check_staleness`          -- a critical pair unread for 2x its
     SLA => ``stale_feed`` event (the OLD data is flagged, never deleted --
     golden rule 8; this function never touches the ledger, it only reports).
  5. Blocking/circuit-breaker (403/429/captcha/empty DOM/frozen price) lives
     in ``acquire/base.py`` (a fetcher-level concern, not a per-offer one) --
     see that module's ``CircuitBreaker``.

Design call -- "pending confirmation" state (rule 2, explicitly left open by
the PR brief): this module stores NO state and touches no disk (it is a
``src/`` pure module, matching this repo's "no I/O side effects in src/"
convention -- see ``extract.py``'s own docstring for the identical choice
around ``extraction_failed``). :class:`PendingConfirmation` is a plain,
transient, immutable record of "what to compare the next read against and
by when" -- the CALLER (a later PR's playbook/scheduler cycle, matching plan
rule 9 "todo componente continuo degrada a batch") is responsible for
holding it between the two reads (in memory for a single scheduler tick, or
in a small quarantine table it owns) and invoking
:func:`resolve_pending_confirmation` when the next observation for the same
(site, competitor_sku_ref) pair arrives. This keeps sanity.py itself fully
unit-testable without a clock or a scheduler, and does not force a schema
change onto PR-10's already-shipped, already-tested ``PriceLedger``.

Event emission follows ``scm_agent/monitors.py``'s established convention
(not a new one): every check function accepts an optional
``ledger: EventLedger | None``. With a ledger, the event is recorded via
``ledger.emit()`` (idempotent -- a repeat within the dedup window is
dropped, same rule as everywhere else) and the verdict's ``.event`` is the
event only if it was actually (newly) recorded, else ``None``. With
``ledger=None`` (the default), ``.event`` is always the constructed
candidate event, unfiltered -- what makes every rule trivially unit-testable
without touching SQLite. Either way, the verdict's ``.status`` (accept /
quarantine / discard) reflects the DATA outcome and never changes just
because a notification got deduped away.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from scm_agent.events import Event, EventLedger

from .models import CompetitorOffer

# -- thresholds (plan S6.6, verbatim where the plan gives a number) ---------

# "|Delta| > 40% intradia sin promo_flag => cuarentena hasta segunda lectura
# confirmatoria (<=1h)".
INTRADAY_DELTA_THRESHOLD = Decimal("0.40")
CONFIRMATION_WINDOW = timedelta(hours=1)
# How close the confirmatory second read must land to the QUARANTINED
# candidate's price to actually confirm the jump (rather than a transient
# glitch that reverts back toward the old price). Not specified numerically
# by the plan -- 5% is a deliberately tight tolerance: "yes, this new price
# is real" requires the second read to essentially agree with the first,
# not merely also be far from the old price.
CONFIRMATION_TOLERANCE = Decimal("0.05")

# MAD outlier detection: the modified z-score (Iglewicz & Hoaglin 1993) is
# 0.6745 * (x - median) / MAD; |score| > 3.5 is the standard recommended
# threshold for "outlier". Both constants are the textbook values, not
# tuned -- see check_mad_outlier's docstring for the hand-worked example.
MAD_CONSISTENCY_CONSTANT = Decimal("0.6745")
MAD_Z_THRESHOLD = Decimal("3.5")
# Fewer than this many trailing observations is not enough history to judge
# an outlier against -- a brand-new (sku, competitor) pair's first few reads
# are ACCEPTed with reason "insufficient_history" rather than flagged, an
# honest degrade (never a silently wrong verdict) matching plan rule 14.
MIN_MAD_WINDOW = 5

# Deliberately non-exhaustive but broad ISO 4217 active-currency-code table
# (majors + the plan's ICP LatAm markets + the rest of the world's common
# currencies) -- "moneda desconocida" (S6.6 rule 1) means "not recognizable
# at all", not "not one we currently trade in"; this is a plausibility gate,
# not a market allowlist. python-stdnum (already in the dataquality extra)
# does not ship an iso4217 submodule as of this PR's pin -- verified against
# this repo's installed 1.x -- so the table is hand-rolled rather than
# introducing a new dependency for one lookup.
KNOWN_CURRENCY_CODES: frozenset[str] = frozenset(
    {
        # majors
        "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "CNY", "HKD", "SGD",
        # LatAm (plan S6.2's ICP)
        "MXN", "BRL", "ARS", "CLP", "COP", "PEN", "UYU", "BOB", "PYG", "GTQ", "CRC",
        "DOP", "HNL", "NIO", "PAB",
        # Europe (non-euro)
        "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "RON", "BGN", "TRY", "ISK",
        # Asia-Pacific
        "INR", "IDR", "MYR", "PHP", "THB", "VND", "KRW", "TWD", "PKR",
        # MENA / Africa
        "AED", "SAR", "ILS", "EGP", "ZAR", "NGN", "KES", "MAD",
    }
)


class SanityStatus(str, Enum):
    """The three outcomes every sanity rule below resolves to."""

    ACCEPT = "accept"
    QUARANTINE = "quarantine"
    DISCARD = "discard"


@dataclass(frozen=True)
class RawOfferCandidate:
    """Pre-validation shape of one observed offer -- the same field set as
    ``models.CompetitorOffer`` plus ``availability_conflict``, but WITHOUT
    ``CompetitorOffer``'s eager ``__post_init__`` checks (see module
    docstring for why). A caller assembles one of these from
    ``extract.ExtractionResult`` plus the acquisition context
    (site/competitor_sku_ref/observed_at/match output) that ``extract.py``
    deliberately does not have.

    ``availability_conflict`` is a caller-supplied signal, not something
    this dataclass infers: it is True when the acquisition step captured
    two disagreeing availability signals for the SAME read (e.g. a JSON-LD
    node said "InStock" while a second structured-data candidate on the
    same page said "OutOfStock") -- an honest "the source contradicts
    itself" flag, as opposed to ``availability=None`` which means "the
    source simply didn't say" (not a contradiction, just missing).
    """

    observed_at: datetime
    site: str
    competitor_sku_ref: str
    matched_product_id: str | None
    match_confidence: float
    price: Decimal | None
    currency: str | None
    price_normalized: Decimal | None
    shipping: Decimal | None
    availability: str | None
    promo_flag: bool
    list_price: Decimal | None
    acquisition_tier: str
    extractor: str
    extractor_version: str
    extraction_confidence: float
    availability_conflict: bool = False


@dataclass(frozen=True)
class PendingConfirmation:
    """The quarantine state for one unconfirmed >40% intraday price jump
    (plan S6.6 rule 2). See module docstring's "Design call" for why this is
    a transient, caller-held record rather than something sanity.py
    persists itself."""

    candidate: RawOfferCandidate
    previous_price_normalized: Decimal
    delta_pct: Decimal
    confirm_by: datetime


@dataclass(frozen=True)
class SanityVerdict:
    """Outcome of running one sanity rule against one candidate (or, for
    :func:`resolve_pending_confirmation`, one pending quarantine)."""

    status: SanityStatus
    reason: str
    event: Event | None = None
    pending: PendingConfirmation | None = None


def _emit_one(event: Event, ledger: EventLedger | None) -> Event | None:
    """Record ``event`` in ``ledger`` (idempotent -- see module docstring)
    and return it only if it was actually (newly) recorded; with
    ``ledger=None`` return ``event`` unfiltered, exactly matching
    ``scm_agent/monitors.py``'s own ``_emit`` convention for the
    single-event case."""
    if ledger is None:
        return event
    return event if ledger.emit(event) else None


def _dedup_key(prefix: str, candidate: RawOfferCandidate) -> str:
    return f"{prefix}:{candidate.site}:{candidate.competitor_sku_ref}:{candidate.observed_at.isoformat()}"


# -- rule 1: basic validity ---------------------------------------------------


def check_basic_validity(candidate: RawOfferCandidate, *, ledger: EventLedger | None = None) -> SanityVerdict:
    """Price <= 0, unknown currency, or contradictory availability => DISCARD
    with an event (plan S6.6 rule 1). Checked in that order -- the plan's own
    listed order -- so a candidate failing on more than one axis always
    reports the same, deterministic ``reason``.

    Reference examples (see tests/test_pricing_intel_sanity.py):
      price=Decimal("0")                       -> discard, "invalid_price"
      price=Decimal("-5")                       -> discard, "invalid_price"
      currency="XYZ" (not in KNOWN_CURRENCY_CODES) -> discard, "unknown_currency"
      availability_conflict=True                -> discard, "contradictory_availability"
      price=Decimal("19.99"), currency="USD", no conflict -> accept
    """
    reason: str | None = None
    if candidate.price is None or candidate.price <= 0:
        reason = "invalid_price"
    elif candidate.price_normalized is not None and candidate.price_normalized <= 0:
        reason = "invalid_price"
    elif candidate.currency is None or candidate.currency.strip().upper() not in KNOWN_CURRENCY_CODES:
        reason = "unknown_currency"
    elif candidate.availability_conflict:
        reason = "contradictory_availability"

    if reason is None:
        return SanityVerdict(SanityStatus.ACCEPT, "basic_validity_passed")

    event = Event(
        type="offer_discarded",
        severity="warning",
        source="pricing_intel.sanity",
        dedup_key=_dedup_key("offer_discarded", candidate),
        sku=candidate.matched_product_id,
        payload={
            "reason": reason,
            "site": candidate.site,
            "competitor_sku_ref": candidate.competitor_sku_ref,
            "price": None if candidate.price is None else str(candidate.price),
            "currency": candidate.currency,
            "availability": candidate.availability,
            "extractor": candidate.extractor,
            "extractor_version": candidate.extractor_version,
        },
        ts=candidate.observed_at,
    )
    return SanityVerdict(SanityStatus.DISCARD, reason, event=_emit_one(event, ledger))


# -- rule 2: intraday delta ---------------------------------------------------


def check_intraday_delta(
    candidate: RawOfferCandidate,
    previous_price_normalized: Decimal | None,
    *,
    ledger: EventLedger | None = None,
) -> SanityVerdict:
    """|delta| > 40% vs the previous reading, without ``promo_flag`` =>
    QUARANTINE pending a confirmatory second read within
    :data:`CONFIRMATION_WINDOW` (plan S6.6 rule 2). ``promo_flag=True``
    explains the jump outright and is exempted -- a real promo IS a big
    intraday move, that is the point of a promo.

    Must be called AFTER :func:`check_basic_validity` has ACCEPTed the
    candidate (``candidate.price_normalized`` is assumed present).

    Reference example: previous=Decimal("100"), candidate=Decimal("145"),
    promo_flag=False -> delta 45% > 40% -> QUARANTINE.
    previous=Decimal("100"), candidate=Decimal("145"), promo_flag=True ->
    ACCEPT (promo exempts it). previous=Decimal("100"),
    candidate=Decimal("135") -> delta 35% <= 40% -> ACCEPT.
    """
    if candidate.price_normalized is None:
        raise ValueError("candidate.price_normalized must be set -- run check_basic_validity first")
    if previous_price_normalized is None or previous_price_normalized == 0:
        return SanityVerdict(SanityStatus.ACCEPT, "no_previous_reading")

    delta = abs(candidate.price_normalized - previous_price_normalized) / previous_price_normalized
    if delta <= INTRADAY_DELTA_THRESHOLD:
        return SanityVerdict(SanityStatus.ACCEPT, "within_delta_threshold")
    if candidate.promo_flag:
        return SanityVerdict(SanityStatus.ACCEPT, "large_delta_promo_flagged")

    confirm_by = candidate.observed_at + CONFIRMATION_WINDOW
    pending = PendingConfirmation(
        candidate=candidate,
        previous_price_normalized=previous_price_normalized,
        delta_pct=delta,
        confirm_by=confirm_by,
    )
    event = Event(
        type="offer_quarantined",
        severity="warning",
        source="pricing_intel.sanity",
        dedup_key=_dedup_key("offer_quarantined:intraday_delta", candidate),
        sku=candidate.matched_product_id,
        payload={
            "reason": "intraday_delta_unconfirmed",
            "site": candidate.site,
            "competitor_sku_ref": candidate.competitor_sku_ref,
            "previous_price_normalized": str(previous_price_normalized),
            "candidate_price_normalized": str(candidate.price_normalized),
            "delta_pct": str(delta),
            "confirm_by": confirm_by.isoformat(),
        },
        ts=candidate.observed_at,
    )
    return SanityVerdict(
        SanityStatus.QUARANTINE, "intraday_delta_unconfirmed", event=_emit_one(event, ledger), pending=pending
    )


def resolve_pending_confirmation(
    pending: PendingConfirmation,
    confirming_price_normalized: Decimal,
    confirming_observed_at: datetime,
    *,
    ledger: EventLedger | None = None,
) -> SanityVerdict:
    """Resolve a :class:`PendingConfirmation` against the next read for the
    same (site, competitor_sku_ref) pair.

    DISCARD (with event) if the confirmatory read arrives after
    ``pending.confirm_by`` (the 1h window expired -- plan S6.6 rule 2), or if
    it lands outside :data:`CONFIRMATION_TOLERANCE` of the quarantined
    candidate's price (the "jump" didn't hold up -- most likely a transient
    glitch). ACCEPT if the second read confirms the jump within tolerance
    and within the window.

    Reference example (pending candidate price = Decimal("145")):
      confirming=Decimal("144"), within 1h  -> accept, "intraday_delta_confirmed"
      confirming=Decimal("101"), within 1h  -> discard, "intraday_delta_not_confirmed"
        (reverted toward the old Decimal("100") price -- the jump was a glitch)
      confirming=Decimal("145"), 2h later   -> discard, "intraday_delta_confirmation_expired"
    """
    candidate = pending.candidate
    if confirming_observed_at > pending.confirm_by:
        event = Event(
            type="offer_discarded",
            severity="warning",
            source="pricing_intel.sanity",
            dedup_key=_dedup_key("offer_discarded:intraday_delta_expired", candidate),
            sku=candidate.matched_product_id,
            payload={
                "reason": "intraday_delta_confirmation_expired",
                "site": candidate.site,
                "competitor_sku_ref": candidate.competitor_sku_ref,
                "quarantined_price_normalized": str(candidate.price_normalized),
                "confirm_by": pending.confirm_by.isoformat(),
                "confirming_observed_at": confirming_observed_at.isoformat(),
            },
            ts=confirming_observed_at,
        )
        return SanityVerdict(
            SanityStatus.DISCARD, "intraday_delta_confirmation_expired", event=_emit_one(event, ledger)
        )

    quarantined_price = candidate.price_normalized
    assert quarantined_price is not None  # guaranteed by check_intraday_delta's precondition
    confirm_delta = abs(confirming_price_normalized - quarantined_price) / quarantined_price
    if confirm_delta <= CONFIRMATION_TOLERANCE:
        return SanityVerdict(SanityStatus.ACCEPT, "intraday_delta_confirmed")

    event = Event(
        type="offer_discarded",
        severity="warning",
        source="pricing_intel.sanity",
        dedup_key=_dedup_key("offer_discarded:intraday_delta_not_confirmed", candidate),
        sku=candidate.matched_product_id,
        payload={
            "reason": "intraday_delta_not_confirmed",
            "site": candidate.site,
            "competitor_sku_ref": candidate.competitor_sku_ref,
            "quarantined_price_normalized": str(quarantined_price),
            "confirming_price_normalized": str(confirming_price_normalized),
            "confirm_delta_pct": str(confirm_delta),
        },
        ts=confirming_observed_at,
    )
    return SanityVerdict(SanityStatus.DISCARD, "intraday_delta_not_confirmed", event=_emit_one(event, ledger))


# -- rule 3: MAD outlier over the trailing 30-day window ----------------------


def check_mad_outlier(
    candidate: RawOfferCandidate,
    trailing_window: Sequence[Decimal],
    *,
    threshold: Decimal = MAD_Z_THRESHOLD,
    ledger: EventLedger | None = None,
) -> SanityVerdict:
    """Outliers by median absolute deviation (MAD) over the trailing 30-day
    window for this (sku, competitor) pair => QUARANTINE (plan S6.6 rule 3).
    ``trailing_window`` is the caller-supplied set of ``price_normalized``
    values from that window (the 30-day scoping itself is the caller's
    concern -- a ledger query -- this function is pure over whatever window
    it is handed).

    Modified z-score (Iglewicz & Hoaglin): ``0.6745 * (x - median) / MAD``;
    ``|score| > 3.5`` is the standard outlier threshold.

    Hand-worked reference example:
      trailing_window = [100, 101, 99, 102, 98, 100, 101] (Decimal)
      median = 100; abs deviations = [0, 1, 1, 2, 2, 0, 1]; MAD = median(that) = 1
      candidate 130 -> z = 0.6745 * 30 / 1 = 20.235  -> |z| > 3.5 -> QUARANTINE
      candidate 101 -> z = 0.6745 * 1 / 1  = 0.6745   -> |z| <= 3.5 -> ACCEPT

    Fewer than :data:`MIN_MAD_WINDOW` points is not enough history to judge
    -- ACCEPT with reason "insufficient_history" (never a guessed verdict).
    A perfectly flat window (MAD == 0) makes the modified z-score
    ill-defined (division by zero); a candidate that still matches that flat
    value ACCEPTs, any other value is flagged conservatively.
    """
    if candidate.price_normalized is None:
        raise ValueError("candidate.price_normalized must be set -- run check_basic_validity first")
    if len(trailing_window) < MIN_MAD_WINDOW:
        return SanityVerdict(SanityStatus.ACCEPT, "insufficient_history")

    median = statistics.median(trailing_window)
    abs_deviations = [abs(x - median) for x in trailing_window]
    mad = statistics.median(abs_deviations)

    price = candidate.price_normalized
    if mad == 0:
        if price == median:
            return SanityVerdict(SanityStatus.ACCEPT, "matches_stable_history")
        reason = "mad_outlier_zero_variance"
        z_score = None
    else:
        z_score = MAD_CONSISTENCY_CONSTANT * (price - median) / mad
        if abs(z_score) <= threshold:
            return SanityVerdict(SanityStatus.ACCEPT, "within_mad_threshold")
        reason = "mad_outlier"

    event = Event(
        type="offer_quarantined",
        severity="warning",
        source="pricing_intel.sanity",
        dedup_key=_dedup_key("offer_quarantined:mad_outlier", candidate),
        sku=candidate.matched_product_id,
        payload={
            "reason": reason,
            "site": candidate.site,
            "competitor_sku_ref": candidate.competitor_sku_ref,
            "candidate_price_normalized": str(price),
            "window_median": str(median),
            "window_mad": str(mad),
            "window_size": len(trailing_window),
            "z_score": None if z_score is None else str(z_score),
            "threshold": str(threshold),
        },
        ts=candidate.observed_at,
    )
    return SanityVerdict(SanityStatus.QUARANTINE, reason, event=_emit_one(event, ledger))


# -- rule 4: staleness ---------------------------------------------------------


def check_staleness(
    *,
    site: str,
    competitor_sku_ref: str,
    matched_product_id: str | None,
    last_observed_at: datetime,
    sla_hours: float,
    now: datetime,
    ledger: EventLedger | None = None,
) -> Event | None:
    """A critical (sku, competitor) pair not observed within 2x its
    configured SLA => ``stale_feed`` event (plan S6.6 rule 4). The OLD data
    itself is never touched here -- append-only (golden rule 8) means a
    stale reading stays exactly where it is; this function only reports.

    Returns ``None`` (no event) when the pair is within its 2x-SLA window.

    Reference example: sla_hours=6.0 (a critical pair), last_observed_at =
    now - 13h -> 13h > 12h (2x6) -> stale_feed event.
    last_observed_at = now - 10h -> 10h <= 12h -> None (past the SLA once,
    but not yet past 2x -- not stale by this rule's definition).
    """
    if sla_hours <= 0:
        raise ValueError(f"sla_hours must be > 0, got {sla_hours!r}")
    hours_since = (now - last_observed_at).total_seconds() / 3600.0
    threshold_hours = 2 * sla_hours
    if hours_since <= threshold_hours:
        return None

    event = Event(
        type="stale_feed",
        severity="warning",
        source="pricing_intel.sanity",
        dedup_key=f"stale_feed:{site}:{competitor_sku_ref}",
        sku=matched_product_id,
        payload={
            "site": site,
            "competitor_sku_ref": competitor_sku_ref,
            "last_observed_at": last_observed_at.isoformat(),
            "hours_since_last_observed": hours_since,
            "sla_hours": sla_hours,
            "threshold_hours": threshold_hours,
        },
        ts=now,
    )
    return _emit_one(event, ledger)


# -- final assembly ------------------------------------------------------------


def to_competitor_offer(candidate: RawOfferCandidate) -> CompetitorOffer:
    """Build the real, validated :class:`CompetitorOffer` once ``candidate``
    has cleared every applicable sanity gate. ``CompetitorOffer.__post_init__``
    re-validates independently as defense in depth -- if IT raises here, a
    gate above had a bug, not the data."""
    if candidate.price is None:
        raise ValueError("candidate.price must be set")
    if candidate.currency is None:
        raise ValueError("candidate.currency must be set")
    if candidate.price_normalized is None:
        raise ValueError("candidate.price_normalized must be set")
    if candidate.availability is None:
        raise ValueError("candidate.availability must be set (resolved, not 'unknown')")

    return CompetitorOffer(
        observed_at=candidate.observed_at,
        site=candidate.site,
        competitor_sku_ref=candidate.competitor_sku_ref,
        matched_product_id=candidate.matched_product_id,
        match_confidence=candidate.match_confidence,
        price=candidate.price,
        currency=candidate.currency.strip().upper(),
        price_normalized=candidate.price_normalized,
        shipping=candidate.shipping,
        availability=candidate.availability,
        promo_flag=candidate.promo_flag,
        list_price=candidate.list_price,
        acquisition_tier=candidate.acquisition_tier,
        extractor=candidate.extractor,
        extractor_version=candidate.extractor_version,
        extraction_confidence=candidate.extraction_confidence,
    )

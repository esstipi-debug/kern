"""Continuous price monitoring (Linchpin 3.0 PR-15, plan sections 6.1/6.8/6.9
-- the last PR of Fase B, wiring F0's scheduler and Fase A's event/autonomy
machinery onto the titan for the first time).

Two acquisition paths converge on ONE pipeline here:

  1. **L0, scheduled, pull:** :func:`run_price_monitor_cycle` -- periodically
     re-acquires the CURRENT price for every ``sku_map`` pair CONFIRMED
     against MercadoLibre's public Items API
     (``src.pricing_intel.acquire.meli_api``). Registered as
     :data:`PRICE_MONITOR_JOB` with ``jobs.scheduler.JobRegistry`` (F0,
     PR-3) -- in production, a ``BackgroundScheduler`` trigger; in tests/CI,
     ``JobRegistry.run_once()`` calls the exact same function synchronously,
     no daemon, no sleeping (golden rule 9).
  2. **L2, push:** ``webapp/app.py``'s ``POST /api/watch`` route parses a
     changedetection.io webhook (``src.pricing_intel.acquire.watcher``) and
     calls :func:`accept_observation` directly -- the SAME function the L0
     path calls after IT acquires a reading, so both tiers run through
     identical sanity-gate -> ledger-append -> market-signal-event logic
     (never two subtly-different copies of "is this a meaningful change").

Scope, documented (golden rule 14: no silent caps) -- this Fase B sequence
built exactly three acquisition tiers, no more:
  - **L0** MercadoLibre (this module's scheduled poll).
  - **L1** structured PDP extraction -- ``jobs/price_intelligence.py``'s
    ONE-SHOT playbook only (PR-13); this PR does NOT add a scheduled L1
    poll (widening this job's acquisition beyond MELI is a natural, but
    NOT-YET-BUILT, follow-on).
  - **L2** changedetection.io -- push-based (the webapp route above), never
    polled by this module.
  - **L3** dedicated Scrapy spiders were deliberately NOT built in this
    Fase B sequence at all (plan section 6.1 lists ``spiders/``/
    ``browser.py`` as later-PR file-tree entries) -- nobody should assume
    an L3 tier exists just because L0/L1/L2 do.

Same ``EventLedger`` everywhere (no second event stream): this module's own
``price_move``/``competitor_oos``/``promo_detected``/``new_competitor_listing``
emissions (via ``src.pricing_intel.events``) and the acquisition-layer health
events already emitted by ``sanity.py``'s checks and
``acquire/base.py``'s ``CircuitBreaker`` (``offer_discarded``/
``offer_quarantined``/``site_degraded``/``extraction_failed``/
``stale_feed``) all flow through the ONE ``scm_agent.events.EventLedger``
instance a caller passes to :func:`run_price_monitor_cycle` (or
``webapp/app.py`` passes to :func:`accept_observation` directly).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from scm_agent.events import Event, EventLedger
from src.pricing_intel.acquire import meli_api
from src.pricing_intel.acquire.base import (
    CircuitBreaker,
    SiteNotApprovedError,
    SiteNotConfiguredError,
    classify_blocking_signal,
    require_approved_site,
)
from src.pricing_intel.acquire.meli_api import MELI_DOMAIN, MeliApiFetcher, MeliParseError, parse_meli_item_json
from src.pricing_intel.events import detect_market_signal_events, new_competitor_listing_event
from src.pricing_intel.ledger import PriceLedger, default_ledger
from src.pricing_intel.match.sku_map import SkuMap, SkuMapEntry, default_sku_map
from src.pricing_intel.models import CompetitorOffer
from src.pricing_intel.normalize import PriceNormalizationError, convert_to_base_currency
from src.pricing_intel.sanity import (
    RawOfferCandidate,
    SanityStatus,
    check_basic_validity,
    check_intraday_delta,
    check_mad_outlier,
    to_competitor_offer,
)

from .scheduler import ScheduledJob

SOURCE = "jobs.price_monitor"

MAD_WINDOW_DAYS = 30

# Production trigger: plan S6.2's "competidores criticos cada 2-6h" -- 4h is
# the documented midpoint (a config knob, not load-bearing logic; a real
# deploy can pass its own trigger_args when adding PRICE_MONITOR_JOB to its
# own JobRegistry).
DEFAULT_CADENCE_HOURS = 4


@dataclass(frozen=True)
class AcceptOutcome:
    """Outcome of running one already-acquired
    :class:`~src.pricing_intel.sanity.RawOfferCandidate` through the shared
    sanity-gate -> ledger-append -> market-signal-event pipeline
    (:func:`accept_observation`). Deliberately a 3-value status vocabulary
    (accepted/quarantined/discarded, mirroring
    ``sanity.SanityStatus`` exactly) -- unlike
    ``jobs/price_intelligence.py``'s 4-value ``RowOutcome`` ("skipped" is
    also a status there), this function only ever runs on a candidate that
    already cleared ACQUISITION (the caller's job, before calling this) --
    an FX-conversion failure is reported as "discarded" here (a data-quality
    outcome, same bucket ``check_basic_validity`` already occupies), not a
    fourth status.
    """

    status: str  # "accepted" | "quarantined" | "discarded"
    reason: str
    offer: CompetitorOffer | None = None
    events: tuple[Event, ...] = ()


def accept_observation(
    candidate: RawOfferCandidate,
    *,
    ledger: PriceLedger,
    event_ledger: EventLedger | None,
    detect_new_listing: bool = False,
) -> AcceptOutcome:
    """Run one already-acquired ``RawOfferCandidate`` through the SAME
    sanity-gate -> ledger-append -> market-signal-event pipeline both PR-15
    acquisition paths share (see module docstring). One pipeline regardless
    of which tier produced the candidate.

    ``detect_new_listing=True`` (``webapp/app.py``'s L2 receiver only --
    see that route's docstring) additionally emits a
    ``new_competitor_listing`` event when this ``(site,
    competitor_sku_ref)`` pair has NO prior ledger reading at all. The L0
    scheduled cycle never passes this: by definition it only ever
    re-acquires ``sku_map`` pairs that were already CONFIRMED (and
    therefore already known), so "new" cannot occur on that path.
    """
    previous = ledger.latest_by_sku(candidate.site, candidate.competitor_sku_ref)
    previous_offer = previous.offer if previous is not None else None
    is_new_pair = previous is None

    verdict = check_basic_validity(candidate, ledger=event_ledger)
    if verdict.status == SanityStatus.DISCARD:
        return AcceptOutcome(status="discarded", reason=verdict.reason)

    try:
        price_normalized = convert_to_base_currency(candidate.price, candidate.currency)
    except PriceNormalizationError:
        return AcceptOutcome(status="discarded", reason="fx_rate_unavailable")
    candidate = replace(candidate, price_normalized=price_normalized)

    delta_verdict = check_intraday_delta(
        candidate, previous_offer.price_normalized if previous_offer else None, ledger=event_ledger
    )
    if delta_verdict.status == SanityStatus.QUARANTINE:
        return AcceptOutcome(status="quarantined", reason=delta_verdict.reason)

    window_start = candidate.observed_at - timedelta(days=MAD_WINDOW_DAYS)
    trailing_window = [
        r.offer.price_normalized
        for r in ledger.history_for_sku(candidate.site, candidate.competitor_sku_ref)
        if r.offer.observed_at >= window_start
    ]
    mad_verdict = check_mad_outlier(candidate, trailing_window, ledger=event_ledger)
    if mad_verdict.status == SanityStatus.QUARANTINE:
        return AcceptOutcome(status="quarantined", reason=mad_verdict.reason)

    offer = to_competitor_offer(candidate)
    ledger.append([offer], now=offer.observed_at)

    events = list(detect_market_signal_events(offer, previous_offer, ledger=event_ledger))
    if detect_new_listing and is_new_pair:
        new_listing_event = new_competitor_listing_event(
            site=offer.site,
            competitor_sku_ref=offer.competitor_sku_ref,
            matched_product_id=offer.matched_product_id,
            now=offer.observed_at,
            ledger=event_ledger,
        )
        if new_listing_event is not None:
            events.append(new_listing_event)

    return AcceptOutcome(status="accepted", reason="ok", offer=offer, events=tuple(events))


# -- L0: MercadoLibre scheduled poll ------------------------------------------


@dataclass(frozen=True)
class PairOutcome:
    """What happened to one confirmed ``sku_map`` pair this cycle -- always
    recorded, never silently dropped (golden rule 14)."""

    site: str
    competitor_sku_ref: str
    matched_product_id: str
    status: str  # "accepted" | "quarantined" | "discarded" | "skipped"
    reason: str
    events: tuple[Event, ...] = ()


@dataclass(frozen=True)
class PriceMonitorCycleReport:
    now: datetime
    pairs_checked: int
    outcomes: tuple[PairOutcome, ...]

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(ev for outcome in self.outcomes for ev in outcome.events)

    @property
    def summary(self) -> str:
        if not self.pairs_checked:
            return "Price monitor cycle: no confirmed MercadoLibre pair(s) to check."
        by_status = Counter(o.status for o in self.outcomes)
        parts = ", ".join(f"{n} {status}" for status, n in sorted(by_status.items()))
        return f"Price monitor cycle: {self.pairs_checked} confirmed MercadoLibre pair(s) checked ({parts})."


def _check_one_pair(
    entry: SkuMapEntry,
    *,
    meli_domain: str,
    ledger: PriceLedger,
    event_ledger: EventLedger | None,
    breaker: CircuitBreaker,
    client: httpx.Client,
    now: datetime,
) -> PairOutcome:
    base = dict(site=meli_domain, competitor_sku_ref=entry.competitor_sku_ref, matched_product_id=entry.our_product_id)

    if not breaker.allow_request(now):
        return PairOutcome(**base, status="skipped", reason="circuit_open")

    fetcher = MeliApiFetcher(client=client, domain=meli_domain)
    observation = fetcher.fetch(entry.competitor_sku_ref, now=now)

    if observation.status_code is None:
        # Transport-level failure -- NOT a blocking signal (MeliApiFetcher's
        # own docstring); the breaker is left untouched, matching
        # jobs/price_intelligence.py's FetchError handling.
        return PairOutcome(**base, status="skipped", reason="fetch_error")

    blocking = classify_blocking_signal(status_code=observation.status_code, html=observation.html)
    if blocking is not None:
        breaker.record_failure(reason=blocking, now=now, ledger=event_ledger)
        return PairOutcome(**base, status="skipped", reason=f"blocked:{blocking}")
    breaker.record_success()

    if observation.html is None or observation.status_code != 200:
        return PairOutcome(**base, status="skipped", reason=f"fetch_failed:status_{observation.status_code}")

    try:
        parsed = parse_meli_item_json(observation.html, fetched_at=now)
    except MeliParseError as exc:
        if event_ledger is not None:
            event_ledger.emit(Event(
                type="extraction_failed",
                severity="warning",
                source=SOURCE,
                dedup_key=f"extraction_failed:{meli_domain}:{entry.competitor_sku_ref}:{now.isoformat()}",
                sku=entry.our_product_id,
                payload={"site": meli_domain, "competitor_sku_ref": entry.competitor_sku_ref, "reason": str(exc)},
                ts=now,
            ))
        return PairOutcome(**base, status="skipped", reason="extraction_failed")

    candidate = RawOfferCandidate(
        observed_at=now,
        site=meli_domain,
        competitor_sku_ref=entry.competitor_sku_ref,
        matched_product_id=entry.our_product_id,
        match_confidence=1.0,  # sku_map already CONFIRMED this pair -- not this cycle's concern
        price=parsed.price,
        currency=parsed.currency,
        price_normalized=None,
        shipping=None,
        availability=parsed.availability,
        promo_flag=False,  # the basic Items API item schema carries no list/regular-price signal
        list_price=None,
        acquisition_tier="L0",
        extractor=meli_api.MELI_EXTRACTOR,
        extractor_version=meli_api.MELI_API_VERSION,
        extraction_confidence=meli_api.MELI_API_CONFIDENCE,
    )
    outcome = accept_observation(candidate, ledger=ledger, event_ledger=event_ledger)
    return PairOutcome(**base, status=outcome.status, reason=outcome.reason, events=outcome.events)


def run_price_monitor_cycle(
    *,
    sku_map: SkuMap | None = None,
    ledger: PriceLedger | None = None,
    event_ledger: EventLedger | None = None,
    http_client: httpx.Client | None = None,
    sites_config_dir: str | Path | None = None,
    meli_domain: str = MELI_DOMAIN,
    now: datetime | None = None,
) -> PriceMonitorCycleReport:
    """One full continuous-monitoring cycle: every ``sku_map`` pair
    CONFIRMED against ``meli_domain`` -> re-acquire via MercadoLibre's L0
    API -> the shared sanity/ledger/market-signal pipeline
    (:func:`accept_observation`).

    Golden rule 9 ("todo componente continuo degrada a batch"): a plain,
    all-default-kwargs, synchronous function -- directly callable in a test,
    and exactly the shape ``jobs.scheduler.ScheduledJob.func`` requires (see
    :data:`PRICE_MONITOR_JOB` below). No sleeping, no background thread.

    ``ledger``/``sku_map`` default to ``PriceLedger.default_ledger()`` /
    ``SkuMap.default_sku_map()`` -- process-wide CACHED singletons (matching
    ``jobs/price_intelligence.py``'s own ``run()``) -- and are deliberately
    NEVER closed by this function even when it constructed them itself:
    closing a shared singleton's connection would break every other caller
    of ``default_ledger()``/``default_sku_map()`` for the rest of the
    process. ``event_ledger``/``http_client`` are NOT cached singletons
    (``EventLedger()``/``httpx.Client()`` each open a fresh connection) --
    those two ARE closed here when this function constructed them itself,
    leaving a caller-supplied instance of either open (the caller's
    lifecycle).
    """
    now = now or datetime.now(timezone.utc)
    owns_event_ledger = event_ledger is None
    owns_client = http_client is None
    sku_map = sku_map if sku_map is not None else default_sku_map()
    ledger = ledger if ledger is not None else default_ledger()
    event_ledger = event_ledger if event_ledger is not None else EventLedger()
    client = http_client if http_client is not None else httpx.Client()

    try:
        pairs = [e for e in sku_map.list_all_confirmed() if e.site == meli_domain]

        kwargs = {} if sites_config_dir is None else {"config_dir": sites_config_dir}
        try:
            config = require_approved_site(meli_domain, **kwargs)
        except (SiteNotConfiguredError, SiteNotApprovedError) as exc:
            # The compliance gate refuses this domain outright (e.g. the
            # REAL api.mercadolibre.com, see config/sites/api.mercadolibre.com.yaml) --
            # every confirmed pair is honestly reported as skipped (golden
            # rule 14), never silently dropped from the cycle.
            outcomes = tuple(
                PairOutcome(
                    site=meli_domain, competitor_sku_ref=e.competitor_sku_ref, matched_product_id=e.our_product_id,
                    status="skipped", reason=f"site_not_approved:{type(exc).__name__}",
                )
                for e in pairs
            )
            return PriceMonitorCycleReport(now=now, pairs_checked=len(pairs), outcomes=outcomes)

        breaker = CircuitBreaker.for_site(config)
        outcomes = tuple(
            _check_one_pair(
                e, meli_domain=meli_domain, ledger=ledger, event_ledger=event_ledger,
                breaker=breaker, client=client, now=now,
            )
            for e in pairs
        )
        return PriceMonitorCycleReport(now=now, pairs_checked=len(pairs), outcomes=outcomes)
    finally:
        if owns_client:
            client.close()
        if owns_event_ledger:
            event_ledger.close()


# Registrable with jobs.scheduler.JobRegistry (F0, PR-3) -- same function,
# either called directly/via run_once() (tests, CI, golden rule 9) or run
# under this trigger by a real BackgroundScheduler in production.
PRICE_MONITOR_JOB = ScheduledJob(
    id="price_monitor_cycle",
    func=run_price_monitor_cycle,
    trigger="interval",
    trigger_args={"hours": DEFAULT_CADENCE_HOURS},
)

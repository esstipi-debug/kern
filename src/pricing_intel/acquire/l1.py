"""Shared L1 (structured-data PDP) acquisition prefix -- gate -> tier
ceiling check -> circuit breaker -> fetch -> blocking classification ->
price extraction (final whole-branch review of the discovery-assisted price
intel plan, Finding 2).

``jobs/price_intelligence.py``'s ``_acquire_one`` and ``jobs/price_watch.py``'s
``_check_one_pair`` each carried this ~40-line sequence independently, near-
verbatim, INCLUDING the NON-GOAL-1 degrade-only-never-retry discipline (one
failed attempt records against the :class:`~.base.CircuitBreaker` and
returns; nothing here ever retries within one call). That duplication meant a
future hardening of the block/retry policy applied to one acquisition path
could silently fail to reach the other. This module is the single place that
sequence now lives; both jobs call :func:`acquire_l1_offer`.

Each caller keeps its OWN divergent TAIL -- ``jobs.price_intelligence.
_acquire_one``'s inline multi-check sanity gate (basic validity / intraday
delta / MAD outlier, plus FX normalization) vs. ``jobs.price_watch.
_check_one_pair``'s single ``jobs.price_monitor.accept_observation`` call --
this module only gets a caller as far as "here is one successfully extracted
:class:`~..sanity.RawOfferCandidate`" or "here is the honest, machine-
readable reason we stopped before that point" (golden rule 14); it never
itself decides accepted/quarantined/discarded, and it never emits the
``extraction_failed`` event (the two callers use different ``source``/
payload-key conventions for that event -- a pre-existing, intentional
difference this extraction does not collapse; each caller emits its own
using the ``extraction_attempts`` this function hands back on that one
specific skip reason).

Lives in ``src/pricing_intel/acquire/`` (not ``jobs/``) because it is pure
acquisition plumbing -- the same category of thing ``require_approved_site``/
``CircuitBreaker``/``classify_blocking_signal`` (``base.py``) and
``fetch_pdp_html`` (``pdp_fetcher.py``) already are -- and putting it here,
rather than in either ``jobs/price_watch.py`` or ``jobs/price_intelligence.py``,
structurally avoids ever having one of those two ``jobs/*.py`` modules import
the other. Deliberately NOT merged into ``base.py`` itself: ``base.py`` is a
dependency OF ``pdp_fetcher.py`` (``RawObservation``), so importing
``fetch_pdp_html`` back into ``base.py`` would create a ``base`` <->
``pdp_fetcher`` cycle within this same package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from scm_agent.events import EventLedger

from ..extract import ExtractionFailed, extract_price
from ..models import ACQUISITION_TIERS
from ..normalize import detect_promo
from ..sanity import RawOfferCandidate
from . import base
from .pdp_fetcher import FetchError, fetch_pdp_html


@dataclass(frozen=True)
class AcquiredOffer:
    """The shared prefix's happy-path result: one successfully extracted L1
    candidate, ready for the caller's OWN divergent tail (never decided
    here -- see module docstring)."""

    candidate: RawOfferCandidate


@dataclass(frozen=True)
class AcquisitionSkipped:
    """The shared prefix stopped before extraction succeeded. ``reason`` is
    the same machine-readable vocabulary both ``RowOutcome``
    (``jobs.price_intelligence``) and ``PairOutcome`` (``jobs.price_watch``)
    already used (golden rule 14) -- the caller wraps it in its OWN outcome
    type (the two have different field shapes, so this module never
    constructs either). ``extraction_attempts`` is set ONLY when
    ``reason == "extraction_failed"`` -- see module docstring for why event
    emission for that case stays with each caller."""

    reason: str
    extraction_attempts: tuple[str, ...] | None = None


def _resolve_site(
    site: str,
    *,
    site_configs: dict[str, object],
    breakers: dict[str, base.CircuitBreaker],
    sites_config_dir: str | Path | None,
    breaker_kwargs: dict,
) -> object:
    """Resolve (and per-run cache) one domain's approved ``SiteConfig``, or
    the ``SiteNotConfiguredError``/``SiteNotApprovedError`` that refused it.
    Identical caching shape both callers already used independently
    (``jobs.price_watch``'s own ``_resolve_site_config`` keeps its separate
    copy for Task 9's scaling step, an unrelated concern -- see that
    function's docstring; ``site_configs``/``breakers`` are the SAME dicts
    threaded through both, so whichever runs first for a domain populates
    the cache the other reads)."""
    if site not in site_configs:
        try:
            kwargs = {} if sites_config_dir is None else {"config_dir": sites_config_dir}
            config = base.require_approved_site(site, **kwargs)
            site_configs[site] = config
            breakers[site] = base.CircuitBreaker.for_site(config, **breaker_kwargs)
        except (base.SiteNotConfiguredError, base.SiteNotApprovedError) as exc:
            site_configs[site] = exc
    return site_configs[site]


def acquire_l1_offer(
    *,
    site: str | None,
    competitor_ref: str,
    matched_product_id: str | None,
    match_confidence: float,
    client: httpx.Client,
    now: datetime,
    site_configs: dict[str, object],
    breakers: dict[str, base.CircuitBreaker],
    sites_config_dir: str | Path | None = None,
    event_ledger: EventLedger | None = None,
    html_path: str | None = None,
    currency_hint: str | None = None,
    breaker_kwargs: dict | None = None,
) -> AcquiredOffer | AcquisitionSkipped:
    """The shared gate -> tier -> breaker -> fetch -> classify -> extract
    prefix. See module docstring for the full rationale and the divergent-
    tail boundary.

    ``site=None`` (an id-only ref -- ``jobs.price_intelligence``'s own case
    for a bare marketplace id with no resolvable URL) skips straight to
    ``AcquisitionSkipped("id_ref_requires_l0_api_not_yet_available")`` --
    ``jobs.price_watch``'s ``SkuMapEntry.site`` is never ``None`` so this
    branch is simply unreached on that call path.

    ``html_path`` (``jobs.price_intelligence``-only case: a client-supplied,
    already-fetched PDP snapshot) bypasses the breaker/fetch/classify step
    entirely -- no network call at all -- but the tier-ceiling check above it
    still applies exactly as it does on the live-fetch path.

    ``site_configs``/``breakers`` are per-RUN caches the caller owns and
    reuses across every candidate in one acquisition pass (one dict per
    call to ``jobs.price_intelligence.run``/``jobs.price_watch.
    run_price_watch_cycle``) -- mutated in place, exactly as both callers'
    pre-extraction code already did.
    """
    if site is None:
        return AcquisitionSkipped("id_ref_requires_l0_api_not_yet_available")

    cached = _resolve_site(
        site, site_configs=site_configs, breakers=breakers,
        sites_config_dir=sites_config_dir, breaker_kwargs=breaker_kwargs or {},
    )
    if isinstance(cached, Exception):
        return AcquisitionSkipped(f"site_not_approved:{type(cached).__name__}")
    config = cached
    # This shared prefix only ever acquires at L1 (structured-data
    # extraction) -- a domain approved only up to L0 must not be touched
    # here even though its ToS/robots decision is otherwise "approved".
    if ACQUISITION_TIERS.index("L1") > ACQUISITION_TIERS.index(config.max_tier_allowed):
        return AcquisitionSkipped("tier_not_approved")
    breaker = breakers[site]

    if html_path is not None:
        try:
            html = Path(html_path).read_text(encoding="utf-8")
        except OSError as exc:
            return AcquisitionSkipped(f"html_path_unreadable:{exc}")
    else:
        if not breaker.allow_request(now):
            return AcquisitionSkipped("circuit_open")
        result = fetch_pdp_html(competitor_ref, client=client, now=now)
        if isinstance(result, FetchError):
            # A transport-level failure is NOT a blocking signal
            # (classify_blocking_signal's own docstring) -- an ordinary
            # transient failure must not trip the breaker, and is never
            # retried within this call (NON-GOAL 1).
            return AcquisitionSkipped(f"fetch_error:{result.reason}")
        blocking = base.classify_blocking_signal(status_code=result.status_code, html=result.html)
        if blocking is not None:
            # Degrade-only, NEVER a retry (NON-GOAL 1): one failed attempt
            # is recorded against the breaker; no second request happens
            # within this call.
            breaker.record_failure(reason=blocking, now=now, ledger=event_ledger)
            return AcquisitionSkipped(f"blocked:{blocking}")
        breaker.record_success()
        if result.html is None or result.status_code != 200:
            return AcquisitionSkipped(f"fetch_failed:status_{result.status_code}")
        html = result.html

    try:
        extraction = extract_price(html, currency_hint=currency_hint)
    except ExtractionFailed as exc:
        return AcquisitionSkipped("extraction_failed", extraction_attempts=tuple(exc.attempts))

    # A price successfully extracted from a live PDP but with no stated
    # availability is assumed InStock -- the same documented business
    # assumption both callers' own prior code carried (selector/price-parser
    # cascade tiers never state availability at all, see extract.py).
    availability = extraction.availability or "InStock"
    candidate = RawOfferCandidate(
        observed_at=now, site=site, competitor_sku_ref=competitor_ref,
        matched_product_id=matched_product_id, match_confidence=match_confidence,
        price=extraction.price, currency=extraction.currency, price_normalized=None,
        shipping=None, availability=availability,
        promo_flag=detect_promo(extraction.price, extraction.list_price),
        list_price=extraction.list_price, acquisition_tier="L1",
        extractor=extraction.extractor, extractor_version=extraction.extractor_version,
        extraction_confidence=extraction.confidence,
    )
    return AcquiredOffer(candidate)

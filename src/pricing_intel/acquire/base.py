"""Acquisition-layer protocol, per-domain compliance gate, and circuit
breaker for the pricing titan (Linchpin 3.0 PR-12, plan sections 6.1/6.6/6.7).

Three things live here, all specific to "how does a fetcher earn the right
to run, and how does it stop running when a site pushes back" -- not a
general resilience library:

1. :class:`Fetcher` -- the protocol every real fetcher (``meli_api.py``,
   ``watcher.py``, ``spiders/``, ``browser.py`` -- all later PRs, per the
   plan's file tree) implements. Sketched here for the first time (PR-11's
   ``structured.py`` docstring said this protocol did not exist yet).
2. :func:`require_approved_site` -- the hard compliance gate (plan S6.7:
   "sin YAML aprobado, el fetcher se niega a correr", the exact same pattern
   as ``jobs/qa.py``'s "QA fails => no deliverable"). Loads and validates
   ``config/sites/<domain>.yaml`` into a
   :class:`~src.pricing_intel.models.SiteConfig` and REFUSES (raises) for
   any domain lacking an approved config file -- never a silent proceed.
3. :class:`CircuitBreaker` -- plan S6.6 rule 5: on blocking suspicion
   (403/429/captcha/empty DOM/frozen price), trip, degrade the domain's
   effective acquisition tier by one step, and short-circuit further fetch
   attempts (no network call) until a cooldown elapses. "Degradar, nunca
   evadir" (plan S6.0 principle 5) -- there is deliberately no retry-harder,
   no user-agent rotation, no proxy pool here; a blocked site gets LESS
   aggressive, never disguised.

No network I/O happens in this module -- same invariant as the rest of
``src/pricing_intel`` (``acquire/__init__.py``'s own docstring). A real
fetcher performs the actual HTTP/browser call and reports its outcome
(status code, HTML, or a classified blocking signal) back to the
``CircuitBreaker`` it owns; this module only holds the state machine and the
compliance gate around that call, not the call itself.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

import yaml

from scm_agent.events import Event, EventLedger

from ..models import ACQUISITION_TIERS, SiteConfig

# config/sites/<domain>.yaml -- one file per domain (plan S6.7). Overridable
# the same way ledger.py/events.py expose an env override for their default
# paths, so tests never touch the repo's real config/ directory unless they
# choose to.
DEFAULT_SITES_CONFIG_DIR = Path(
    os.environ.get("LINCHPIN_SITES_CONFIG_DIR", "").strip() or "config/sites"
)

# A domain must already be the bare, normalized form CompetitorOffer itself
# requires (models.py: no "://", no spaces) -- this is a second, defensive
# check specifically against path-traversal/injection through the filename
# join below (config_dir / f"{domain}.yaml"), independent of whatever
# validation happened upstream.
_SAFE_DOMAIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")


@dataclass(frozen=True)
class RawObservation:
    """The minimal shape a real fetcher (a later PR) hands back to its
    caller after one fetch attempt against ``sku_ref`` -- just enough for
    the circuit breaker and ``extract.py``'s cascade to do their jobs.
    Deliberately thin: per-tier fetchers (API JSON, watcher webhook payload,
    spider response) carry richer native shapes of their own; this is the
    common denominator, not a replacement for those."""

    sku_ref: str
    fetched_at: datetime
    status_code: int | None  # None for non-HTTP tiers (e.g. an API SDK call)
    html: str | None  # the raw page body, when applicable (L1/L3); None otherwise


@runtime_checkable
class Fetcher(Protocol):
    """Anything that can retrieve one :class:`RawObservation` for a
    competitor SKU reference against one domain. ``domain`` and ``tier``
    identify which :class:`~src.pricing_intel.models.SiteConfig` and which
    :class:`CircuitBreaker` govern this fetcher -- a real implementation is
    expected to call :func:`require_approved_site` once at construction
    time (never per-fetch) and consult its own ``CircuitBreaker`` before
    every ``fetch()`` call."""

    domain: str
    tier: str  # one of models.ACQUISITION_TIERS -- this fetcher's OWN tier

    def fetch(self, sku_ref: str) -> RawObservation: ...


# -- per-domain compliance gate (plan S6.7) -----------------------------------


class SiteNotConfiguredError(LookupError):
    """No ``config/sites/<domain>.yaml`` exists for ``domain`` at all -- the
    hard gate. A fetcher must never run against an unconfigured domain."""

    def __init__(self, domain: str, path: Path) -> None:
        self.domain = domain
        self.path = path
        super().__init__(
            f"no site config for domain '{domain}' at {path} -- "
            "a fetcher refuses to run without an approved config/sites/*.yaml (plan S6.7)"
        )


class SiteNotApprovedError(LookupError):
    """A ``config/sites/<domain>.yaml`` exists but its own ``tos_decision``
    is "prohibited" or its ``robots_txt_respected`` is False -- configured,
    but explicitly not cleared to run (``SiteConfig.is_approved``)."""

    def __init__(self, domain: str, config: SiteConfig) -> None:
        self.domain = domain
        self.config = config
        super().__init__(
            f"site config for domain '{domain}' is not approved "
            f"(tos_decision={config.tos_decision!r}, robots_txt_respected={config.robots_txt_respected!r}) -- "
            "a fetcher refuses to run (plan S6.7)"
        )


def _config_path(domain: str, config_dir: Path | str) -> Path:
    if not _SAFE_DOMAIN.match(domain):
        raise ValueError(f"domain must be a bare normalized hostname, got {domain!r}")
    return Path(config_dir) / f"{domain}.yaml"


def load_site_config(domain: str, *, config_dir: Path | str = DEFAULT_SITES_CONFIG_DIR) -> SiteConfig:
    """Load and validate ``config/sites/<domain>.yaml`` into a
    :class:`~src.pricing_intel.models.SiteConfig`. Raises
    :class:`SiteNotConfiguredError` if the file does not exist, or
    ``ValueError``/``TypeError`` (from ``SiteConfig.__post_init__``) if it
    exists but fails the dataclass's own field validation -- either way,
    never returns a half-valid config."""
    path = _config_path(domain, config_dir)
    if not path.exists():
        raise SiteNotConfiguredError(domain, path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a YAML mapping, got {type(raw).__name__}")
    data = dict(raw)
    data.setdefault("domain", domain)
    if data["domain"] != domain:
        raise ValueError(f"{path}: 'domain' field {data['domain']!r} does not match filename domain {domain!r}")
    return SiteConfig(**data)


def require_approved_site(domain: str, *, config_dir: Path | str = DEFAULT_SITES_CONFIG_DIR) -> SiteConfig:
    """The hard gate a fetcher calls before running (plan S6.7: "sin YAML
    aprobado, el fetcher se niega a correr" -- same enforcement pattern as
    ``jobs/qa.py``'s "QA fails => no deliverable"). Raises
    :class:`SiteNotConfiguredError` for a missing config, or
    :class:`SiteNotApprovedError` for a configured-but-not-approved domain
    (prohibited ToS or unrespected robots.txt). Returns the approved
    :class:`SiteConfig` otherwise."""
    config = load_site_config(domain, config_dir=config_dir)
    if not config.is_approved:
        raise SiteNotApprovedError(domain, config)
    return config


# -- circuit breaker (plan S6.6 rule 5) ---------------------------------------

# One step down models.ACQUISITION_TIERS -- L3 (spiders, the most fragile
# tier) degrades to L2 (watcher), L2 to L1 (structured data), L1 to L0
# (official API, if one exists for this domain). L0 has nowhere lower to
# degrade to: per plan S6.0 #5, a domain still blocking at L0 gets DROPPED
# by its caller, not disguised -- this module signals that by leaving
# effective_tier at "L0" (see CircuitBreaker.effective_tier).
DEGRADE_TIER: dict[str, str] = {"L3": "L2", "L2": "L1", "L1": "L0"}

# Case-insensitive substrings that show up in a captcha/bot-check
# interstitial's body text. Deliberately small and conservative -- a
# false-negative here just means one more failure is needed before the
# breaker trips on the 403/429 signal instead; a false positive would
# wrongly degrade a healthy fetcher, which is the worse failure mode.
_CAPTCHA_MARKERS = ("captcha", "are you a human", "verify you are human", "recaptcha", "unusual traffic")


def classify_blocking_signal(
    *,
    status_code: int | None = None,
    html: str | None = None,
    identical_price_streak: bool = False,
) -> str | None:
    """Best-effort classification of one fetch attempt's raw signals into
    one of the plan's blocking-suspicion categories (S6.6 rule 5): a
    403/429 status, an empty (blank/whitespace-only) DOM, a captcha marker
    in the body text, or a caller-computed "identical price for weeks"
    streak flag (computed upstream from ledger history -- reading the
    ledger is I/O and does not belong in this pure classifier). Returns
    ``None`` when nothing here looks like blocking -- an ordinary transient
    failure (timeout, 500, a parse error) is NOT a blocking signal and must
    not trip the breaker; only genuine blocking suspicion degrades a tier.
    """
    if status_code == 403:
        return "blocked_403"
    if status_code == 429:
        return "blocked_429"
    if html is not None and not html.strip():
        return "empty_dom"
    if html is not None:
        lowered = html.lower()
        if any(marker in lowered for marker in _CAPTCHA_MARKERS):
            return "captcha_detected"
    if identical_price_streak:
        return "identical_price_streak"
    return None


class CircuitBreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-domain circuit breaker (plan S6.6 rule 5). A small, generic,
    testable closed/open/half-open state machine -- not a general
    resilience library, just enough to satisfy "stop hitting a site that's
    blocking us":

    - **CLOSED** (healthy): every fetch attempt is allowed.
    - **OPEN** (tripped): ``allow_request`` returns False -- callers must
      NOT touch the network -- until ``cooldown_seconds`` has elapsed.
    - **HALF_OPEN** (probing): exactly one fetch attempt is allowed through
      to test whether the site has stopped blocking; its outcome
      (``record_success``/``record_failure``) decides CLOSED vs re-OPEN.

    ``effective_tier`` starts at ``configured_tier`` (from this domain's
    ``SiteConfig.max_tier_allowed``) and steps down one level
    (:data:`DEGRADE_TIER`) the instant the breaker actually trips.
    Deliberately NOT restored automatically on a later success -- a tier
    downgrade is a standing caution the plan expects a human/config change
    to lift (mirrors golden rule 11's T1<->T2 asymmetry: degrade is
    immediate and automatic, promotion is evidence-gated), not something
    that silently re-escalates the moment one probe happens to succeed.
    """

    def __init__(
        self,
        domain: str,
        configured_tier: str,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 900.0,
    ) -> None:
        if configured_tier not in ACQUISITION_TIERS:
            raise ValueError(f"configured_tier must be one of {ACQUISITION_TIERS}, got {configured_tier!r}")
        if failure_threshold < 1:
            raise ValueError(f"failure_threshold must be >= 1, got {failure_threshold!r}")
        if cooldown_seconds < 0:
            raise ValueError(f"cooldown_seconds must be >= 0, got {cooldown_seconds!r}")
        self.domain = domain
        self._configured_tier = configured_tier
        self._effective_tier = configured_tier
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._state = CircuitBreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: datetime | None = None

    @classmethod
    def for_site(cls, config: SiteConfig, **kwargs: object) -> CircuitBreaker:
        """Convenience constructor seeded from an approved
        :class:`SiteConfig` -- ``configured_tier`` starts at
        ``config.max_tier_allowed``, matching plan S6.7's "tier maximo
        permitido"."""
        return cls(config.domain, config.max_tier_allowed, **kwargs)  # type: ignore[arg-type]

    @property
    def state(self) -> CircuitBreakerState:
        return self._state

    @property
    def configured_tier(self) -> str:
        return self._configured_tier

    @property
    def effective_tier(self) -> str:
        """The tier a fetcher should actually use right now -- equal to
        ``configured_tier`` until this breaker trips at least once, then
        one (or more, across repeated trips) step(s) lower per
        :data:`DEGRADE_TIER`, floored at "L0"."""
        return self._effective_tier

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def allow_request(self, now: datetime) -> bool:
        """Whether a fetch attempt may proceed right now WITHOUT touching
        the network. CLOSED always allows. OPEN allows nothing until
        ``cooldown_seconds`` has elapsed since it tripped, at which point it
        transitions to HALF_OPEN and allows exactly that one probe.
        HALF_OPEN itself allows nothing further until the in-flight probe
        resolves via ``record_success``/``record_failure`` -- this is what
        stops a caller from hammering a blocking site with parallel probes.
        """
        if self._state == CircuitBreakerState.CLOSED:
            return True
        if self._state == CircuitBreakerState.OPEN:
            if self._opened_at is not None and (now - self._opened_at).total_seconds() >= self._cooldown_seconds:
                self._state = CircuitBreakerState.HALF_OPEN
                return True
            return False
        return False  # HALF_OPEN: a probe is already in flight

    def record_success(self) -> None:
        """A fetch attempt succeeded (no blocking signal). Closes the
        breaker and resets the failure counter. Does NOT restore
        ``effective_tier`` -- see the class docstring."""
        self._consecutive_failures = 0
        self._state = CircuitBreakerState.CLOSED
        self._opened_at = None

    def record_failure(
        self, *, reason: str, now: datetime, ledger: EventLedger | None = None
    ) -> Event | None:
        """Record one blocking-signal failure (``reason`` from
        :func:`classify_blocking_signal` -- e.g. "blocked_403",
        "blocked_429", "captcha_detected", "empty_dom",
        "identical_price_streak"; an ordinary non-blocking failure should
        never reach this method at all, see that function's docstring).

        A HALF_OPEN probe that fails re-opens immediately (no new tier
        degrade -- it already degraded on the original trip) and restarts
        the cooldown. Otherwise counts toward ``failure_threshold``; returns
        the ``site_degraded`` :class:`~scm_agent.events.Event` the instant
        the breaker actually trips (CLOSED -> OPEN), else ``None``.
        """
        if self._state == CircuitBreakerState.HALF_OPEN:
            self._state = CircuitBreakerState.OPEN
            self._opened_at = now
            return None

        self._consecutive_failures += 1
        if self._state == CircuitBreakerState.OPEN:
            return None
        if self._consecutive_failures < self._failure_threshold:
            return None
        return self._trip(reason=reason, now=now, ledger=ledger)

    def _trip(self, *, reason: str, now: datetime, ledger: EventLedger | None) -> Event:
        self._state = CircuitBreakerState.OPEN
        self._opened_at = now
        previous_tier = self._effective_tier
        self._effective_tier = DEGRADE_TIER.get(previous_tier, previous_tier)
        event = Event(
            type="site_degraded",
            severity="warning",
            source="pricing_intel.acquire.base",
            dedup_key=f"site_degraded:{self.domain}:{now.isoformat()}",
            payload={
                "domain": self.domain,
                "reason": reason,
                "previous_tier": previous_tier,
                "effective_tier": self._effective_tier,
                "consecutive_failures": self._consecutive_failures,
            },
            ts=now,
        )
        if ledger is None:
            return event
        return event if ledger.emit(event) else None

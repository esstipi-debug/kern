"""GDELT-backed supplier disruption screen (Kern capability M-disruption).

Turns a supplier list into a disruption-exposure signal by querying the free
GDELT DOC 2.0 API (no key, metadata only) for recent disruption-themed news that
mentions each supplier, then maps that signal onto the risk engine (``src.risk``)
so it flows through the same 5x5 heatmap / EMV ranking / guided options as a
hand-built risk register. That mapping -- ``to_risk_factor`` feeding
``src.risk.assess_portfolio`` -- is what "wires GDELT into the risk tool".

Two hard properties, mirroring the read-only price watch:

* **100% read-only observation.** This module never imports ``nosilo_core.writeback``;
  it only GETs public news metadata. Nothing is ever written anywhere.
* **The GDELT signal is a SCREENING proxy, not a calibrated probability.** The
  derived ``likelihood`` prioritises which suppliers to investigate; it is not a
  real annual disruption probability. The deliverable states this plainly -- do
  not let a caller read it as a forecast.

Attribution is mandatory under GDELT's free-commercial-use licence: any surfaced
result cites the GDELT Project (https://www.gdeltproject.org/). GDELT returns
article METADATA only, so Kern surfaces links/titles, never article bodies (the
text stays the publisher's copyright).

Pure stdlib (``urllib``) -- zero non-base dependencies, safe on the app boot
chain (webapp.app -> scm_agent -> tools -> jobs -> src).
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import exp
from typing import Callable

from src.risk import RiskFactor

GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_ATTRIBUTION = "The GDELT Project (https://www.gdeltproject.org/)"
_USER_AGENT = "KernDisruptionScan/1.0 (+https://linchpin.fly.dev; read-only news screen)"

# GDELT enforces 1 request / 5s per IP; over-limit returns HTTP 200 with a
# PLAINTEXT body ("Please limit requests...") instead of JSON. We throttle
# proactively and detect the plaintext body defensively.
_MIN_REQUEST_INTERVAL_S = 5.0
_DEFAULT_TIMEOUT_S = 20.0

# GDELT DOC 2.0 caps records at 250 and only reaches ~3 months back via timespan.
_MAX_RECORDS = 250
_DEFAULT_TIMESPAN = "3m"

# A disruption-news screen has no true impact value; when the caller gives no
# annual spend we fall back to a nominal exposure so EMV ranking still orders by
# the news signal rather than dividing by zero.
_DEFAULT_IMPACT_VALUE = 50_000.0


class GdeltError(Exception):
    """Base for GDELT fetch problems -- callers degrade per-supplier, never crash."""


class GdeltRateLimited(GdeltError):
    """GDELT returned its plaintext rate-limit notice instead of JSON."""


class GdeltUnavailable(GdeltError):
    """The GDELT endpoint could not be reached or returned an unusable body."""


# A fetcher takes a fully-built request URL and returns the raw response body.
# Injecting it is the test/offline seam: the default hits the network, tests and
# ``--demo`` pass a fetcher that returns canned JSON so nothing touches GDELT.
Fetcher = Callable[[str], str]


# -- disruption themes ------------------------------------------------------
# GDELT GKG theme codes (verified present in LOOKUP-GKGTHEMES.TXT) mapped to the
# src.risk category they represent. Specific codes only -- the broad ones
# (PROTEST 57M, NATURAL_DISASTER 72M, MANMADE_DISASTER_IMPLIED 221M) are too
# noisy to OR into a name-anchored query. There is no SUPPLY_CHAIN / RECALL /
# TARIFF theme, so those signals ride in via the supplier-name anchor + the
# themes below rather than a dedicated code.
THEME_CATEGORY: dict[str, str] = {
    "STRIKE": "operational",
    "UNREST_CLOSINGBORDER": "geopolitical",
    "UNREST_HUNGERSTRIKE": "operational",
    "NATURAL_DISASTER_EARTHQUAKE": "environmental",
    "NATURAL_DISASTER_FLOOD": "environmental",
    "NATURAL_DISASTER_HURRICANE": "environmental",
    "NATURAL_DISASTER_TYPHOON": "environmental",
    "NATURAL_DISASTER_CYCLONE": "environmental",
    "NATURAL_DISASTER_WILDFIRE": "environmental",
    "NATURAL_DISASTER_TSUNAMI": "environmental",
    "NATURAL_DISASTER_VOLCANO": "environmental",
    "NATURAL_DISASTER_LANDSLIDE": "environmental",
    "NATURAL_DISASTER_DROUGHT": "environmental",
    "MANMADE_DISASTER_GAS_EXPLOSION": "operational",
    "MANMADE_DISASTER_PIPELINE_EXPLOSION": "operational",
    "MANMADE_DISASTER_CHEMICAL_FIRE": "operational",
    "MANMADE_DISASTER_POWER_OUTAGES": "operational",
    "MANMADE_DISASTER_DERAILMENT": "logistics",
    "MARITIME_INCIDENT": "logistics",
    "MARITIME_PIRACY": "logistics",
    "WB_167_PORTS": "logistics",
    "ECON_BANKRUPTCY": "financial",
    "ECON_TRADE_DISPUTE": "geopolitical",
    "SHORTAGE": "supply",
}
DISRUPTION_THEMES: tuple[str, ...] = tuple(THEME_CATEGORY)

# Best-effort per-article category inference. A single OR'd query cannot tell us
# which theme matched which article (ArtList omits that), so we INFER a "likely
# category" from the headline. This is a display hint ONLY -- the exposure SCORE
# and ranking never depend on it, and every surface that shows it labels it as
# inferred. Keywords are word-START anchored (see _CATEGORY_PATTERNS) so short
# stems like "port" cannot match inside "report"/"support"/"transport" -- the
# false-friend that would otherwise mislabel a bankruptcy/strike headline as
# logistics. English + Spanish/Portuguese stems, since ES/PT coverage is the
# whole point of using GDELT. Financial/operational are checked before logistics
# so a genuine bankruptcy/strike wins a tie against an incidental transport word.
_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("financial", ("bankrupt", "insolvenc", "default", "liquidation", "quiebra",
                   "falencia", "concordata")),
    ("operational", ("strik", "shutdown", "outage", "explosion", "explosao", "blast", "plant",
                     "factory", "power", "walkout", "huelga", "paro", "apagon", "fabrica",
                     "planta", "greve")),
    ("environmental", ("flood", "earthquake", "hurricane", "typhoon", "cyclone", "wildfire",
                       "fire", "storm", "tsunami", "volcano", "landslide", "drought", "quake",
                       "inundac", "terremoto", "incendio", "sequia", "tormenta", "enchente")),
    ("logistics", ("port", "shipping", "vessel", "container", "freight", "rail", "derail",
                   "cargo", "maritime", "shipment", "puerto", "carga", "buque", "naviera",
                   "ferroviar", "porto")),
    ("geopolitical", ("sanction", "tariff", "trade war", "border", "embargo", "unrest", "protest",
                      "sancion", "arancel", "frontera", "protesta", "sancao", "tarifa")),
    ("supply", ("shortage", "scarcity", "supply", "escasez", "desabast", "falta de")),
)
# Word-start-anchored patterns, checked in the tuple order above.
_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (category, re.compile(r"\b(?:" + "|".join(re.escape(k) for k in keywords) + r")", re.IGNORECASE))
    for category, keywords in _CATEGORY_KEYWORDS
)

# GDELT sourcecountry uses FIPS 10-4, NOT ISO. The two that bite: Australia is
# AS (not AU) and Chile is CI (not CL).
COUNTRY_FIPS: dict[str, str] = {
    "australia": "AS", "brazil": "BR", "brasil": "BR", "united states": "US", "usa": "US",
    "us": "US", "united kingdom": "UK", "uk": "UK", "britain": "UK", "mexico": "MX",
    "argentina": "AR", "chile": "CI", "new zealand": "NZ", "china": "CH", "germany": "GM",
    "spain": "SP", "france": "FR", "india": "IN", "japan": "JA", "canada": "CA",
    "colombia": "CO", "peru": "PE",
}


def country_to_fips(country: str | None) -> str | None:
    """Map a country name (or an already-FIPS 2-letter code) to a FIPS 10-4 code."""
    if not country:
        return None
    key = str(country).strip().lower()
    if key in COUNTRY_FIPS:
        return COUNTRY_FIPS[key]
    code = str(country).strip().upper()
    # Already a 2-letter code we recognise as a FIPS value.
    if len(code) == 2 and code in set(COUNTRY_FIPS.values()):
        return code
    return None


@dataclass(frozen=True)
class DisruptionArticle:
    """One GDELT article: metadata only (never the article body)."""

    url: str
    title: str
    seendate: datetime
    domain: str
    language: str
    sourcecountry: str
    category: str  # inferred from the title (best-effort display hint)


@dataclass(frozen=True)
class SupplierDisruption:
    """A supplier's aggregated disruption signal over the lookback window."""

    supplier: str
    country: str
    annual_spend: float
    article_count: int
    distinct_sources: int
    recency_days: float          # days since the most recent article (inf if none)
    exposure_score: float        # 0..1 screening signal (NOT a probability)
    dominant_category: str
    categories: dict[str, int] = field(default_factory=dict)
    most_recent: datetime | None = None
    sample_articles: tuple[DisruptionArticle, ...] = ()
    fetch_failed: bool = False   # true if the GDELT call errored (signal unknown)


def build_query(
    supplier: str,
    *,
    country: str | None = None,
    themes: tuple[str, ...] = DISRUPTION_THEMES,
) -> str:
    """Build the GDELT query string: the supplier name anchored to disruption themes.

    Shape: ``"Supplier Name" (theme:A OR theme:B ...) [sourcecountry:XX]``. The
    quoted name is the anchor; the OR'd themes narrow to disruption news. Country
    scoping is optional and off by default -- a disruption at a Brazilian
    supplier is often first reported by non-Brazilian outlets, so filtering by
    source country would cost recall.
    """
    name = supplier.strip().replace('"', "")
    theme_expr = " OR ".join(f"theme:{t}" for t in themes)
    query = f'"{name}" ({theme_expr})'
    fips = country_to_fips(country)
    if fips:
        query += f" sourcecountry:{fips}"
    return query


def build_url(query: str, *, timespan: str = _DEFAULT_TIMESPAN, maxrecords: int = _MAX_RECORDS) -> str:
    """Assemble the full DOC 2.0 request URL (mode=ArtList, JSON, newest first)."""
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "timespan": timespan,
        "maxrecords": str(min(maxrecords, _MAX_RECORDS)),
        "sort": "DateDesc",
    }
    return f"{GDELT_ENDPOINT}?{urllib.parse.urlencode(params)}"


_last_request_at: float = 0.0


def _urllib_fetch(url: str, *, timeout: float = _DEFAULT_TIMEOUT_S) -> str:
    """Default network fetcher: throttled stdlib GET. Injected away in tests/demo."""
    global _last_request_at
    wait = _MIN_REQUEST_INTERVAL_S - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed GDELT host)
            # utf-8-sig strips a leading BOM if GDELT sends one, so the JSON body is
            # not mistaken for the plaintext rate-limit notice by the '{' check.
            body = resp.read().decode("utf-8-sig", errors="replace")
    except Exception as exc:  # network/DNS/timeout -> unavailable, degrade gracefully
        raise GdeltUnavailable(f"GDELT request failed: {exc}") from exc
    finally:
        _last_request_at = time.monotonic()
    return body


def _infer_category(title: str) -> str:
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(title):
            return category
    return "supply"


def _parse_seendate(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def parse_articles(body: str) -> list[DisruptionArticle]:
    """Parse a raw DOC 2.0 JSON body into articles, guarding GDELT's quirks.

    Guards: the rate-limit notice is plaintext with HTTP 200 (not JSON), and an
    empty result is literally ``{}`` (no ``articles`` key), not ``{"articles":[]}``.
    """
    # Strip a leading BOM (an injected fetcher may carry one) before the '{' check,
    # so a valid JSON body is never mistaken for the plaintext rate-limit notice.
    stripped = body.lstrip("﻿").lstrip()
    if not stripped.startswith("{"):
        raise GdeltRateLimited(stripped[:120].strip() or "GDELT returned a non-JSON body")
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise GdeltUnavailable(f"GDELT returned unparseable JSON: {exc}") from exc
    articles = data.get("articles") or []
    if not isinstance(articles, list):
        raise GdeltUnavailable(f"GDELT 'articles' was {type(articles).__name__}, not a list")
    out: list[DisruptionArticle] = []
    for a in articles:
        if not isinstance(a, dict):
            continue
        seen = _parse_seendate(a.get("seendate", ""))
        if seen is None:
            continue
        title = str(a.get("title", "")).strip()
        out.append(DisruptionArticle(
            url=str(a.get("url", "")),
            title=title,
            seendate=seen,
            domain=str(a.get("domain", "")),
            language=str(a.get("language", "")),
            sourcecountry=str(a.get("sourcecountry", "")),
            category=_infer_category(title),
        ))
    return out


def gdelt_search(
    supplier: str,
    *,
    country: str | None = None,
    fetcher: Fetcher | None = None,
    themes: tuple[str, ...] = DISRUPTION_THEMES,
    timespan: str = _DEFAULT_TIMESPAN,
    maxrecords: int = _MAX_RECORDS,
) -> list[DisruptionArticle]:
    """Query GDELT for disruption articles mentioning ``supplier``. Read-only."""
    fetch = fetcher or _urllib_fetch
    url = build_url(build_query(supplier, country=country, themes=themes),
                    timespan=timespan, maxrecords=maxrecords)
    body = fetch(url)
    return parse_articles(body)


def _exposure_score(article_count: int, recency_days: float) -> float:
    """Map volume + recency onto a 0..1 screening signal (NOT a probability).

    Volume saturates -- five disruption articles is a strong signal, fifty is not
    ten times stronger. Recency modulates: a cluster three months stale matters
    less than the same cluster last week, but never zeroes (it still happened).
    """
    if article_count <= 0:
        return 0.0
    volume = 1.0 - exp(-article_count / 5.0)          # 5 -> .63, 15 -> .95
    recency = exp(-max(recency_days, 0.0) / 30.0)      # <7d ~ full, ~90d ~ .05
    return round(min(1.0, volume * (0.5 + 0.5 * recency)), 4)


def score_supplier(
    supplier: str,
    country: str,
    annual_spend: float,
    articles: list[DisruptionArticle],
    *,
    now: datetime | None = None,
    fetch_failed: bool = False,
) -> SupplierDisruption:
    """Aggregate a supplier's articles into its disruption signal."""
    now = now or datetime.now(timezone.utc)
    counts: dict[str, int] = {}
    for a in articles:
        counts[a.category] = counts.get(a.category, 0) + 1
    most_recent = max((a.seendate for a in articles), default=None)
    recency_days = (now - most_recent).total_seconds() / 86_400.0 if most_recent else float("inf")
    dominant = max(counts, key=counts.get) if counts else "supply"
    top = tuple(sorted(articles, key=lambda a: a.seendate, reverse=True)[:3])
    return SupplierDisruption(
        supplier=supplier,
        country=country,
        annual_spend=annual_spend,
        article_count=len(articles),
        distinct_sources=len({a.domain for a in articles if a.domain}),
        recency_days=recency_days,
        exposure_score=_exposure_score(len(articles), recency_days),
        dominant_category=dominant,
        categories=counts,
        most_recent=most_recent,
        sample_articles=top,
        fetch_failed=fetch_failed,
    )


def to_risk_factor(sig: SupplierDisruption, *, default_impact: float = _DEFAULT_IMPACT_VALUE) -> RiskFactor:
    """Map a disruption signal onto a RiskFactor -- the wiring into the risk engine.

    ``likelihood`` carries the news-screen exposure score (0..1). It is a
    prioritisation signal, not a calibrated annual probability; the deliverable
    says so. ``impact_value`` scales with the supplier's annual spend when known.
    ``detectability_days`` is the recency of the freshest event (a fresh event is
    detectable now; silence for months is not).
    """
    impact = sig.annual_spend if sig.annual_spend > 0 else default_impact
    detect = 90.0 if sig.most_recent is None else min(max(sig.recency_days, 0.0), 90.0)
    return RiskFactor(
        name=f"Disruption exposure: {sig.supplier}",
        category=sig.dominant_category,
        likelihood=sig.exposure_score,
        impact_value=impact,
        exposure=sig.annual_spend,
        detectability_days=detect,
        owner=sig.supplier,
    )


@dataclass(frozen=True)
class SupplierRow:
    """One row of the input supplier list."""

    supplier: str
    country: str = ""
    annual_spend: float = 0.0


def scan_suppliers(
    rows: list[SupplierRow],
    *,
    fetcher: Fetcher | None = None,
    themes: tuple[str, ...] = DISRUPTION_THEMES,
    timespan: str = _DEFAULT_TIMESPAN,
    maxrecords: int = _MAX_RECORDS,
    now: datetime | None = None,
) -> list[SupplierDisruption]:
    """Screen every supplier against GDELT. A per-supplier fetch failure degrades
    to a zero-signal row flagged ``fetch_failed`` -- one unreachable query never
    sinks the whole scan."""
    now = now or datetime.now(timezone.utc)
    out: list[SupplierDisruption] = []
    for row in rows:
        try:
            articles = gdelt_search(
                row.supplier, country=row.country or None, fetcher=fetcher,
                themes=themes, timespan=timespan, maxrecords=maxrecords,
            )
            out.append(score_supplier(row.supplier, row.country, row.annual_spend, articles, now=now))
        except GdeltError:
            out.append(score_supplier(row.supplier, row.country, row.annual_spend, [], now=now,
                                      fetch_failed=True))
    return out

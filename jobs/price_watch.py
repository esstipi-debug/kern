"""Discovery crawl wiring (Discovery-Assisted Price Intel plan, Task 3 /
PR-3): the network entry point of the whole discovery-assisted playbook.

Flow, in order, none of it skippable:

  1. :func:`~src.pricing_intel.acquire.auto_approve.auto_approve_site`
     (Task 1 / PR-1) -- resolve ``seed_url``'s domain and self-onboard it via
     robots.txt ONLY. A rejection here (bad URL, robots.txt disallow, an
     already-existing config of any status) returns an honest skip -- **the
     crawl adapter below is NEVER invoked** on this path.
  2. :func:`~src.pricing_intel.acquire.base.require_approved_site` (the hard
     compliance gate) -- re-checked HERE, independently of step 1's own
     verdict, so a stale or wrong ``OnboardingResult.approved=True`` (a
     race, a caller-injected test double, a future bug in
     ``auto_approve_site`` itself) can never smuggle a crawl past the one
     authoritative source of truth for "is this domain actually approved
     right now": ``config/sites/<domain>.yaml`` on disk. Raises
     :class:`~src.pricing_intel.acquire.base.SiteNotConfiguredError` or
     :class:`~src.pricing_intel.acquire.base.SiteNotApprovedError` are
     caught here and turned into the same kind of honest, no-crawl skip.
  3. The crawl itself -- an advertools adapter whose pattern is copied (not
     reinvented) from ``jobs/seo_audit.py::_crawl_domain``: the identical
     politeness posture (``ROBOTSTXT_OBEY=True``, a bounded
     ``DOWNLOAD_DELAY``/``CONCURRENT_REQUESTS_PER_DOMAIN``, an identifiable,
     non-rotating ``USER_AGENT`` -- reused from
     ``src.pricing_intel.acquire.pdp_fetcher.USER_AGENT``, the SAME UA
     ``auto_approve_site``'s own robots.txt check uses by default), and the
     identical ``xpath_selectors={"page_html": "/html"}`` so the crawled
     DataFrame carries real page markup. Unlike the SEO audit, no
     ``robots.txt``/``sitemap.xml`` extra seeds -- this crawl only cares
     about reachable product pages, not site-level SEO signals.
     ``AdvertoolsUnavailableError`` and ``pages_from_crawl_dataframe`` (the
     DataFrame -> ``CrawledPage`` adapter) are REUSED from ``jobs.seo_audit``
     verbatim, not re-copied.
  4. :func:`~src.pricing_intel.discover.filter_product_pages` (Task 2 /
     PR-2) -- keeps only pages carrying real JSON-LD/microdata Product/Offer
     structured data; every other crawled page is silently, non-erroneously
     dropped (that module's own documented, reviewed contract -- see its
     docstring). ``pages_crawled`` in the returned payload lets a caller see
     the crawled-vs-discovered gap without this module fabricating a
     per-page reason ``discover.py`` deliberately does not compute.

This is a THIRD-PARTY competitor site under the pricing-intel ToS/robots-
approval workflow (``config/sites/*.yaml``) -- deliberately NOT
``seo_audit``'s own ``confirmed_domain``/``DomainNotConfirmedError`` gate,
which is scoped to a client auditing their OWN site under an SEO engagement,
a different concern with different approvers (see that module's docstring,
point 1).

No silent caps (golden rule 14): a domain that fails either gate (auto-
approval or the hard compliance re-check) is reported back with a
machine-readable ``skipped_reason`` and an empty ``discovered`` list --
never a bare ``[]`` with no explanation, and never an uncaught exception.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from jobs.seo_audit import AdvertoolsUnavailableError, _ensure_scripts_on_path, pages_from_crawl_dataframe
from src.pricing_intel.acquire import base
from src.pricing_intel.acquire.auto_approve import OnboardingResult, auto_approve_site
from src.pricing_intel.acquire.pdp_fetcher import USER_AGENT as DEFAULT_USER_AGENT
from src.pricing_intel.discover import DiscoveredProduct, filter_product_pages

# Same politeness posture as jobs/seo_audit.py::_crawl_domain -- a small,
# fixed per-request delay and a low per-domain concurrency, never tuned up
# to "as fast as possible". prepare() prefers the domain's OWN approved
# SiteConfig.rate_limit_seconds when available; this is only the fallback.
DEFAULT_DOWNLOAD_DELAY_SECONDS = 0.5
DEFAULT_CONCURRENT_REQUESTS_PER_DOMAIN = 2
DEFAULT_CRAWL_OUTPUT_FILE = Path("data") / "price_watch_crawl.jl"


def _crawl_domain(
    seed_url: str,
    *,
    hostname: str,
    output_file: Path,
    follow_links: bool,
    user_agent: str,
    download_delay: float,
    concurrent_requests_per_domain: int,
    scrapy_log_level: str,
) -> pd.DataFrame:
    """Advertools crawl adapter -- pattern copied (not reinvented) from
    ``jobs/seo_audit.py::_crawl_domain``: identical ``custom_settings``
    shape, identical fresh-output-file discipline (advertools APPENDS to an
    existing ``.jl`` file, so a stale one is unlinked first), identical
    ``xpath_selectors``. No ``robots.txt``/``sitemap.xml`` extra seeds here
    (unlike the SEO audit) -- this crawl only needs reachable product pages,
    not site-level SEO signals.
    """
    try:
        import advertools as adv
    except ImportError as exc:
        raise AdvertoolsUnavailableError() from exc

    _ensure_scripts_on_path()
    output_file.parent.mkdir(parents=True, exist_ok=True)
    if output_file.exists():
        output_file.unlink()

    adv.crawl(
        [seed_url],
        str(output_file),
        follow_links=follow_links,
        allowed_domains=[hostname],
        xpath_selectors={"page_html": "/html"},
        custom_settings={
            "USER_AGENT": user_agent,
            "ROBOTSTXT_OBEY": True,
            "DOWNLOAD_DELAY": download_delay,
            "CONCURRENT_REQUESTS_PER_DOMAIN": concurrent_requests_per_domain,
            "LOG_LEVEL": scrapy_log_level,
        },
    )
    if not output_file.exists():
        return pd.DataFrame()
    return pd.read_json(output_file, lines=True)


def _skip(domain: str | None, onboarding: OnboardingResult, reason: str) -> dict:
    """The honest, no-crawl skip shape shared by both gate failures --
    always a machine-readable ``skipped_reason``, never a bare empty result."""
    return {
        "domain": domain,
        "discovered": [],
        "pages_crawled": 0,
        "onboarding": onboarding,
        "site_config": None,
        "skipped_reason": reason,
    }


def prepare(seed_url: str, params: dict | None = None) -> dict:
    """The network entry point of the discovery-assisted playbook -- see
    module docstring for the full gate -> crawl -> filter flow. ``seed_url``
    is this job's actual required input (a URL), not a ``data_path`` -- same
    shape as ``jobs/seo_audit.py::prepare``/``jobs/price_intelligence.py``'s
    one-shot mode.

    ``params``:
      - ``config_dir`` (default ``base.DEFAULT_SITES_CONFIG_DIR``),
        ``robots_reader``, ``user_agent`` (default
        ``pdp_fetcher.USER_AGENT``) -- passed through to
        :func:`auto_approve_site` (the test seam that keeps onboarding fully
        offline).
      - ``follow_links`` (default True), ``download_delay`` (defaults to
        the approved ``SiteConfig.rate_limit_seconds``),
        ``concurrent_requests_per_domain``, ``scrapy_log_level`` (default
        "ERROR"), ``crawl_output_file``.
    """
    params = params or {}
    config_dir = params.get("config_dir", base.DEFAULT_SITES_CONFIG_DIR)
    user_agent = str(params.get("user_agent", DEFAULT_USER_AGENT))

    onboarding = auto_approve_site(
        seed_url, config_dir=config_dir, robots_reader=params.get("robots_reader"), user_agent=user_agent,
    )
    if not onboarding.approved or onboarding.domain is None:
        return _skip(onboarding.domain, onboarding, f"not_approved:{onboarding.reason}")

    domain = onboarding.domain
    try:
        site_config = base.require_approved_site(domain, config_dir=config_dir)
    except (base.SiteNotConfiguredError, base.SiteNotApprovedError) as exc:
        return _skip(domain, onboarding, f"site_gate_refused:{type(exc).__name__}")

    output_file = Path(params.get("crawl_output_file") or DEFAULT_CRAWL_OUTPUT_FILE)
    df = _crawl_domain(
        seed_url,
        hostname=domain,
        output_file=output_file,
        follow_links=bool(params.get("follow_links", True)),
        user_agent=user_agent,
        download_delay=float(params.get("download_delay", site_config.rate_limit_seconds)),
        concurrent_requests_per_domain=int(
            params.get("concurrent_requests_per_domain", DEFAULT_CONCURRENT_REQUESTS_PER_DOMAIN)
        ),
        scrapy_log_level=str(params.get("scrapy_log_level", "ERROR")),
    )
    pages = pages_from_crawl_dataframe(df, hostname=domain)
    discovered: list[DiscoveredProduct] = filter_product_pages(pages, site=domain)

    return {
        "domain": domain,
        "discovered": discovered,
        "pages_crawled": len(pages),
        "onboarding": onboarding,
        "site_config": site_config,
        "skipped_reason": None,
    }

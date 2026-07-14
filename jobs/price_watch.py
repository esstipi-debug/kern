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

Task 5 / PR-5 adds the other end of the playbook: :func:`run_homologation`
wires :func:`~src.pricing_intel.homologate.homologate` (Task 4) onto
``prepare()``'s ``discovered`` products against the client's own catalog,
persisting every ``confirmed``/``suspect`` row to the versioned
``sku_map`` (never ``rejected``/unmatched rows -- see that function's own
docstring for the exact safety invariant), and :func:`write_homologation`
publishes the resulting table as ``homologation_table.csv`` plus a separate
``homologation_unmatched.csv`` (golden rule 14 -- nothing dropped silently).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from jobs.seo_audit import AdvertoolsUnavailableError, _ensure_scripts_on_path, pages_from_crawl_dataframe
from src.export import write_summary_csv
from src.pricing_intel.acquire import base
from src.pricing_intel.acquire.auto_approve import OnboardingResult, auto_approve_site
from src.pricing_intel.acquire.pdp_fetcher import USER_AGENT as DEFAULT_USER_AGENT
from src.pricing_intel.discover import DiscoveredProduct, filter_product_pages
from src.pricing_intel.homologate import HomologationReport, HomologationRow, homologate
from src.pricing_intel.match.sku_map import SkuMap, default_sku_map
from src.pricing_intel.models import MatchCandidate

# Same politeness posture as jobs/seo_audit.py::_crawl_domain -- a small,
# fixed per-request delay and a low per-domain concurrency, never tuned up
# to "as fast as possible". prepare() prefers the domain's OWN approved
# SiteConfig.rate_limit_seconds when available, but this is an unconditional
# FLOOR, not just a fallback: this crawl targets a THIRD-PARTY site under the
# no-evasion non-goal, so neither that site's own (possibly zero, e.g. a
# `Crawl-delay: 0` robots.txt auto-approved by Task 1) rate_limit_seconds nor
# an explicit params["download_delay"] override may ever push the actual
# DOWNLOAD_DELAY below this value -- see _resolve_download_delay().
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


def _resolve_download_delay(params: dict, site_config: base.SiteConfig) -> float:
    """The crawl's actual ``DOWNLOAD_DELAY`` -- the domain's own approved
    ``SiteConfig.rate_limit_seconds`` (or an explicit
    ``params["download_delay"]`` override) UNCONDITIONALLY floored at
    :data:`DEFAULT_DOWNLOAD_DELAY_SECONDS`. Neither a site's own declared
    rate (which could be ``0.0`` -- e.g. a real ``Crawl-delay: 0`` robots.txt
    auto-approved by Task 1) nor a caller-supplied override may resolve
    faster than this floor: this crawl targets a THIRD-PARTY site under the
    no-evasion non-goal, never the client's own site, so there is no
    legitimate reason to ever run it "as fast as possible"."""
    requested = float(params.get("download_delay", site_config.rate_limit_seconds))
    return max(requested, DEFAULT_DOWNLOAD_DELAY_SECONDS)


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
        the approved ``SiteConfig.rate_limit_seconds``; see
        :func:`_resolve_download_delay` -- this default AND any explicit
        override are unconditionally floored at
        ``DEFAULT_DOWNLOAD_DELAY_SECONDS``, never allowed to run faster),
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
        download_delay=_resolve_download_delay(params, site_config),
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


# -- Task 5 / PR-5: persist homologation + publish the table ----------------


def _persist_row(sku_map: SkuMap, row: HomologationRow, *, now: datetime) -> None:
    """Append one ``confirmed``/``suspect`` :class:`HomologationRow` to
    ``sku_map`` as a new, versioned entry (golden rule 8 -- NEVER an
    in-place update; see ``sku_map.py``'s own module docstring). Only ever
    called by :func:`run_homologation` for ``row.status in ("confirmed",
    "suspect")`` -- a ``rejected``/unmatched row is never passed here.

    ``confirmed_at`` is set ONLY for a genuine ``confirmed`` row (``now``,
    the SAME timestamp :func:`run_homologation` passed to
    :func:`~src.pricing_intel.homologate.homologate`) -- a ``suspect`` row's
    persisted entry always carries ``confirmed_at=None`` alongside
    ``confirmed_by=None``, mirroring ``HomologationRow.__post_init__``'s own
    structural guard against a suspect row silently carrying either.

    Safety-critical invariant, enforced HERE independently of
    ``HomologationRow.__post_init__`` (review fix, round 1): a non-
    ``confirmed`` row must never carry a non-``None`` ``confirmed_by``.
    ``HomologationRow``'s own constructor already refuses this combination,
    and neither :class:`~src.pricing_intel.models.MatchCandidate`'s
    ``__post_init__`` nor :meth:`SkuMap.record` independently re-checks it
    (``SkuMap.record`` only checks the forward direction -- a ``confirmed``
    candidate requires a truthy ``confirmed_by``). Without this local guard,
    a future refactor of ``homologate.py``/``adjudicate.py``, or any new
    caller constructing a row/candidate some other way, could let a non-
    ``None`` ``confirmed_by`` slip onto a ``suspect``/``rejected`` row and
    sail straight through into durable ``sku_map`` storage unnoticed.
    """
    if row.our_product_id is None:
        # Structurally unreachable for confirmed/suspect rows -- homologate.py's
        # cascade only ever sets our_product_id=None on a rejected/unmatched row
        # (see that module's docstring) -- but guarded explicitly rather than
        # trusting that invariant silently across a future change.
        raise ValueError(f"cannot persist a {row.status!r} row with our_product_id=None")

    if row.status != "confirmed" and row.confirmed_by is not None:
        raise ValueError(
            f"cannot persist a {row.status!r} row with confirmed_by={row.confirmed_by!r} -- "
            "only a 'confirmed' row may carry confirmed_by"
        )

    confirmed_at = now if row.status == "confirmed" else None
    candidate = MatchCandidate(
        our_product_id=row.our_product_id,
        competitor_sku_ref=row.competitor_sku_ref,
        site=row.site,
        method=row.method,
        score=row.score,
        status=row.status,
        reason=row.reason,
        confirmed_by=row.confirmed_by,
        confirmed_at=confirmed_at,
    )
    sku_map.record(candidate, now=now)


def run_homologation(
    payload: dict,
    *,
    sku_map: SkuMap | None = None,
    now: datetime | None = None,
) -> HomologationReport:
    """Run PR-4's :func:`~src.pricing_intel.homologate.homologate` cascade
    on ``payload["discovered"]`` (``prepare()``'s own output key) against
    ``payload["our_catalog"]`` -- the client's OWN catalog, which
    ``prepare()`` never produces itself (it only crawls the competitor
    site); a caller merges it into the payload before calling this
    function. ``payload["our_gtins"]``/``payload["llm"]`` are optional,
    passed straight through to ``homologate()`` unchanged.

    Every ``confirmed``/``suspect`` row in the resulting
    :class:`~src.pricing_intel.homologate.HomologationReport` is persisted
    to ``sku_map`` as a new, versioned entry (golden rule 8); ``rejected``
    rows (including every row in ``report.unmatched``) are reported back to
    the caller but NEVER persisted -- NON-GOAL 4: this function only ever
    calls ``sku_map.record`` (append-only match metadata), never a writeback
    to the competitor or our own catalog (``src/writeback.py`` is never
    imported here).

    ``sku_map`` defaults to the process-wide :func:`default_sku_map`
    singleton and is NEVER closed by this function, even when it
    constructed it itself -- mirrors
    ``jobs.price_monitor.run_price_monitor_cycle``'s own singleton-lifecycle
    discipline (see that function's docstring): closing a shared
    singleton's connection would break every other caller of
    ``default_sku_map()`` for the rest of the process.
    """
    resolved_now = now if now is not None else datetime.now(timezone.utc)
    discovered = payload.get("discovered") or []
    our_catalog = payload.get("our_catalog") or []
    our_gtins = payload.get("our_gtins")
    llm = payload.get("llm")

    report = homologate(discovered, our_catalog, our_gtins=our_gtins, llm=llm, now=resolved_now)

    store = sku_map if sku_map is not None else default_sku_map()
    for row in report.rows:
        if row.status in ("confirmed", "suspect"):
            _persist_row(store, row, now=resolved_now)

    return report


_HOMOLOGATION_TABLE_COLUMNS: tuple[str, ...] = (
    "my_sku", "competitor_product", "method", "confidence", "status",
)
_HOMOLOGATION_UNMATCHED_COLUMNS: tuple[str, ...] = ("competitor_product", "site", "reason")


def write_homologation(
    report: HomologationReport, out_dir: str | Path, client: str = "Client"
) -> dict[str, Path]:
    """The homologation table deliverable: ``homologation_table.csv``
    (columns ``my_sku, competitor_product, method, confidence, status`` --
    every row that DID land on one of our SKUs, i.e. ``report.rows`` minus
    ``report.unmatched``) plus ``homologation_unmatched.csv`` --
    ``report.unmatched`` verbatim, its OWN file so an unmatched competitor
    product is never silently absent from the output (golden rule 14). Both
    files always exist, even when empty (a stable header, same "nothing to
    report" idiom ``jobs.seo_priority.write_operational`` and
    ``jobs.markdown_liquidation_job.write_operational`` already use) --
    never a missing file with no explanation.

    Every string cell is passed through ``src.sanitize.defuse_formula``
    before it reaches disk -- ``write_summary_csv`` already applies it
    per-value (the SAME calling convention
    ``jobs.price_intelligence.write_operational`` uses for its own CSV
    output), so a ``competitor_sku_ref``/``reason`` starting with
    ``=``/``+``/``-``/``@`` (OWASP CSV-injection) is neutralized here too.
    """
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)

    matched_rows = [row for row in report.rows if row.our_product_id is not None]

    table_path = d / "homologation_table.csv"
    if matched_rows:
        table_data = [
            {
                "my_sku": row.our_product_id,
                "competitor_product": row.competitor_sku_ref,
                "method": row.method,
                "confidence": row.score,
                "status": row.status,
            }
            for row in matched_rows
        ]
        written = {"csv": write_summary_csv(table_data, table_path)}
    else:
        pd.DataFrame(columns=list(_HOMOLOGATION_TABLE_COLUMNS)).to_csv(table_path, index=False)
        written = {"csv": table_path}

    unmatched_path = d / "homologation_unmatched.csv"
    if report.unmatched:
        unmatched_data = [
            {"competitor_product": row.competitor_sku_ref, "site": row.site, "reason": row.reason}
            for row in report.unmatched
        ]
        written["unmatched_csv"] = write_summary_csv(unmatched_data, unmatched_path)
    else:
        pd.DataFrame(columns=list(_HOMOLOGATION_UNMATCHED_COLUMNS)).to_csv(unmatched_path, index=False)
        written["unmatched_csv"] = unmatched_path

    # ``client`` is accepted for interface symmetry with every other
    # write_operational-style deliverable builder in this repo (e.g.
    # jobs.price_intelligence.write_operational) -- this table has no
    # per-client Summary sheet of its own to stamp it into.
    _ = client

    return written

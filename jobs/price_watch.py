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

Task 6 / PR-6 adds the recurring half of the playbook: :func:`run_price_watch_cycle`
re-acquires the CURRENT price for every CONFIRMED ``sku_map`` pair via the
L1 structured-data PDP path -- ``pdp_fetcher.fetch_pdp_html`` gated by
``require_approved_site``/``CircuitBreaker``, EXACTLY
``jobs.price_intelligence.py``'s own ``_acquire_one`` discipline (never
reinvented) -- and converges on ``jobs.price_monitor.accept_observation``
for every sanity-gate/ledger-append/market-signal-event decision (REUSED
verbatim, never a second implementation -- see that module's own docstring
for why one pipeline must serve every acquisition tier). Registered as
:data:`PRICE_WATCH_JOB` with ``jobs.scheduler.JobRegistry`` -- same
``run_once()``-in-tests, no-daemon-no-sleep discipline as
``jobs.price_monitor.PRICE_MONITOR_JOB`` (golden rule 9).

LIMITATION -- scoping to "confirmed discovery pairs" is INCIDENTAL, not
enforced: ``SkuMap.list_all_confirmed()`` enumerates every confirmed pair
across the WHOLE store (any site, any match method) -- ``SkuMapEntry``
carries no provenance/source field distinguishing a discovery-onboarded
pair from one confirmed through some other path. This cycle relies on the
per-pair ``SiteConfig.max_tier_allowed`` ceiling check (identical to
``price_intelligence._acquire_one``'s own) to keep a pair like
MercadoLibre's -- confirmed for ``jobs.price_monitor``'s own L0 poll, and
approved only to L0 (``config/sites/meli-api.test.yaml``) -- out of this
L1 cycle: that L1 attempt is honestly reported ``skipped:
tier_not_approved``, never fetched and never silently escalated (raising
a site's approved ceiling is explicitly a LATER PR's concern, not this
cycle's).

That separation, however, is a CURRENT, ACCIDENTAL fact of today's
codebase, not a structural guarantee. As of this writing the ONLY writer
of ``status="confirmed"`` rows anywhere in ``sku_map`` is this module's
own :func:`run_homologation` (Task 5). Both
``src/pricing_intel/match/sku_map.py`` and
``src/pricing_intel/match/adjudicate.py`` explicitly document and
anticipate a SECOND write path -- a human "manual T2 review" or an
operator accepting an LLM-adjudicated match proposal -- that could call
``SkuMap.record(status="confirmed", ...)`` directly against ANY site a
human has approved to L1 or higher, not just a discovery-onboarded one.
If/when that second write path ships, a non-discovery confirmed pair on
an L1+-approved site would pass this cycle's tier-ceiling check and be
silently swept up and re-acquired here too, since nothing on
``SkuMapEntry``/``list_all_confirmed()`` marks it as out of this cycle's
scope. Fixing that (a provenance/source tag on ``SkuMapEntry``, or some
other explicit allow-list) is a schema/design decision for a human to
make in a dedicated follow-up PR -- deliberately NOT invented
unilaterally here (the original Task 6 brief's own instruction was to
escalate rather than invent a new tagging mechanism unilaterally).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

from jobs.price_monitor import PairOutcome, accept_observation
from jobs.seo_audit import AdvertoolsUnavailableError, _ensure_scripts_on_path, pages_from_crawl_dataframe
from scm_agent.events import Event, EventLedger
from src.export import write_summary_csv
from src.guided import GuidedOutcome
from src.pricing_intel.acquire import base
from src.pricing_intel.acquire.auto_approve import OnboardingResult, auto_approve_site
from src.pricing_intel.acquire.l1 import AcquisitionSkipped, acquire_l1_offer
from src.pricing_intel.acquire.pdp_fetcher import USER_AGENT as DEFAULT_USER_AGENT
from src.pricing_intel.discover import DiscoveredProduct, filter_product_pages
from src.pricing_intel.homologate import HomologationReport, HomologationRow, homologate
from src.pricing_intel.ledger import PriceLedger, default_ledger
from src.pricing_intel.match.sku_map import SkuMap, SkuMapEntry, default_sku_map
from src.pricing_intel.models import MatchCandidate

from .price_watch_scaling import ScaledWatch, SkuScalingRequest, _scale_one
from .scheduler import ScheduledJob

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


# -- Task 6 / PR-6: recurring re-acquisition of confirmed discovery pairs ----

SOURCE = "jobs.price_watch"

# Production trigger: same documented-midpoint config-knob spirit as
# jobs.price_monitor.DEFAULT_CADENCE_HOURS -- this module's OWN knob (each
# recurring job owns its own cadence, not a shared constant), independently
# tunable without touching the L0 MELI poll's cadence.
DEFAULT_CADENCE_HOURS = 4


def _resolve_site_config(
    entry: SkuMapEntry,
    *,
    site_configs: dict[str, object],
    breakers: dict[str, base.CircuitBreaker],
    sites_config_dir: str | Path | None,
) -> object:
    """Resolve (and per-cycle cache) one pair's approved ``SiteConfig``, or the
    ``SiteNotConfiguredError``/``SiteNotApprovedError`` that refused it -- the
    SINGLE approval gate shared by this cycle's scaling (:func:`_scale_one`) and
    acquisition (:func:`_check_one_pair`) steps, so an unapproved site can be
    neither scaled nor fetched. ``site_configs`` caches the ``SiteConfig`` or the
    caught exception per domain; ``breakers`` gets one ``CircuitBreaker`` per
    resolved domain (same shape as ``jobs.price_intelligence._acquire_one``)."""
    if entry.site not in site_configs:
        try:
            kwargs = {} if sites_config_dir is None else {"config_dir": sites_config_dir}
            config = base.require_approved_site(entry.site, **kwargs)
            site_configs[entry.site] = config
            breakers[entry.site] = base.CircuitBreaker.for_site(config)
        except (base.SiteNotConfiguredError, base.SiteNotApprovedError) as exc:
            site_configs[entry.site] = exc
    return site_configs[entry.site]


def _check_one_pair(
    entry: SkuMapEntry,
    *,
    ledger: PriceLedger,
    event_ledger: EventLedger | None,
    site_configs: dict[str, object],
    breakers: dict[str, base.CircuitBreaker],
    client: httpx.Client,
    sites_config_dir: str | Path | None,
    now: datetime,
) -> PairOutcome:
    """Re-acquire one CONFIRMED ``sku_map`` pair via the shared
    ``src.pricing_intel.acquire.l1.acquire_l1_offer`` prefix (final whole-
    branch review, Finding 2 -- this function used to carry the gate/tier/
    breaker/fetch/classify/extract sequence inline, near-verbatim with
    ``jobs.price_intelligence._acquire_one``'s own copy; both now call the
    same shared helper. ``site_configs``/``breakers`` are per-cycle caches
    keyed by domain -- unchanged shape, so a domain with multiple confirmed
    pairs still shares ONE ``SiteConfig``/``CircuitBreaker`` for the whole
    cycle).

    The final sanity-gate -> ledger-append -> market-signal-event decision
    is made ENTIRELY by :func:`jobs.price_monitor.accept_observation` --
    this function's OWN divergent tail (everything past a successful
    extraction) only hands the acquired candidate off to it; it never itself
    decides accepted/quarantined/discarded (that decision, and every
    ``Event`` reported back, always originates from ``accept_observation``,
    converged exactly as Task 6 requires).
    """
    base_fields = dict(site=entry.site, competitor_sku_ref=entry.competitor_sku_ref, matched_product_id=entry.our_product_id)

    acquired = acquire_l1_offer(
        site=entry.site, competitor_ref=entry.competitor_sku_ref, matched_product_id=entry.our_product_id,
        match_confidence=1.0,  # sku_map already CONFIRMED this pair
        client=client, now=now, site_configs=site_configs, breakers=breakers,
        sites_config_dir=sites_config_dir, event_ledger=event_ledger,
    )
    if isinstance(acquired, AcquisitionSkipped):
        if acquired.reason == "extraction_failed":
            # This module's OWN extraction_failed event shape (source/payload
            # keys) -- the shared prefix deliberately never emits this event
            # itself (see acquire_l1_offer's own docstring).
            if event_ledger is not None:
                event_ledger.emit(Event(
                    type="extraction_failed", severity="warning", source=SOURCE,
                    dedup_key=f"extraction_failed:{entry.site}:{entry.competitor_sku_ref}:{now.isoformat()}",
                    sku=entry.our_product_id,
                    payload={
                        "site": entry.site, "competitor_sku_ref": entry.competitor_sku_ref,
                        "attempts": list(acquired.extraction_attempts or ()),
                    },
                    ts=now,
                ))
        return PairOutcome(**base_fields, status="skipped", reason=acquired.reason)

    # CONVERGE HERE (the task's one CRITICAL invariant): every sanity-gate ->
    # ledger-append -> market-signal-event decision for this candidate is
    # made by the SAME accept_observation() jobs.price_monitor's own L0 path
    # calls -- this function never re-implements any part of that pipeline.
    outcome = accept_observation(acquired.candidate, ledger=ledger, event_ledger=event_ledger)
    return PairOutcome(**base_fields, status=outcome.status, reason=outcome.reason, events=outcome.events)


# -- Task 9 / PR-9: wire the R5 bounded auto-scaling guard into the cycle -----
#
# The per-SKU scaling DECISION -- SkuScalingRequest/ScaledWatch and the SOLE
# place a cadence/tier change is decided (_scale_one, delegating entirely to
# watch_policy.plan_watch_escalation, PR-8) -- lives in the sibling
# jobs.price_watch_scaling module (keeps this file under the 800-line cap). This
# module only WIRES it: run_price_watch_cycle resolves each approved pair's
# SiteConfig, then calls _scale_one BEFORE that pair is acquired. There is no
# other code path in this file that changes a tier.


@dataclass(frozen=True)
class PriceWatchCycleReport:
    """Mirrors ``jobs.price_monitor.PriceMonitorCycleReport``'s exact shape
    and status vocabulary (accepted/quarantined/discarded/skipped) -- a
    separate class (rather than reusing that one directly) only because it
    is a distinct report FOR a distinct cycle, but never a second vocabulary
    or a second way to compute ``events``/``summary``.

    Task 9 adds two scaling-step outputs (empty unless ``scaling_request_for`` is
    supplied): ``pending_escalations`` -- the human-approval ``GuidedOutcome`` for
    each SKU whose desired tier exceeds its site's ceiling (nothing applied; the
    tool/PR-11 and any operator surface render these) -- and ``scaled_watches`` --
    each SKU whose cadence tightened within the ceiling."""

    now: datetime
    pairs_checked: int
    outcomes: tuple[PairOutcome, ...]
    pending_escalations: tuple[GuidedOutcome, ...] = ()
    scaled_watches: tuple[ScaledWatch, ...] = ()

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(ev for outcome in self.outcomes for ev in outcome.events)

    @property
    def summary(self) -> str:
        if not self.pairs_checked:
            return "Price watch cycle: no confirmed discovery pair(s) to check."
        by_status = Counter(o.status for o in self.outcomes)
        parts = ", ".join(f"{n} {status}" for status, n in sorted(by_status.items()))
        return f"Price watch cycle: {self.pairs_checked} confirmed discovery pair(s) checked ({parts})."


def run_price_watch_cycle(
    *,
    sku_map: SkuMap | None = None,
    ledger: PriceLedger | None = None,
    event_ledger: EventLedger | None = None,
    http_client: httpx.Client | None = None,
    sites_config_dir: str | Path | None = None,
    scaling_request_for: Callable[[SkuMapEntry], SkuScalingRequest | None] | None = None,
    now: datetime | None = None,
) -> PriceWatchCycleReport:
    """One full continuous-monitoring cycle: every ``sku_map`` pair with
    ``status == "confirmed"`` (across the WHOLE store -- any site, any match
    method; see this module's own docstring LIMITATION note -- today's
    tier-ceiling-only scoping is an incidental, unenforced fact of the
    current codebase, not a structural guarantee) -> re-acquire via the L1
    structured-data PDP path -> the shared sanity/ledger/market-signal
    pipeline (:func:`jobs.price_monitor.accept_observation`).

    Golden rule 9 ("todo componente continuo degrada a batch"): a plain,
    all-default-kwargs, synchronous function -- directly callable in a test,
    and exactly the shape ``jobs.scheduler.ScheduledJob.func`` requires (see
    :data:`PRICE_WATCH_JOB` below). No sleeping, no background thread.

    ``ledger``/``sku_map`` default to ``PriceLedger.default_ledger()`` /
    ``SkuMap.default_sku_map()`` -- process-wide CACHED singletons -- and
    are deliberately NEVER closed by this function even when it constructed
    them itself: closing a shared singleton's connection would break every
    other caller of ``default_ledger()``/``default_sku_map()`` for the rest
    of the process (mirrors ``jobs.price_monitor.run_price_monitor_cycle``'s
    own singleton-lifecycle discipline verbatim -- see that function's
    docstring). ``event_ledger``/``http_client`` are NOT cached singletons
    -- those two ARE closed here when this function constructed them itself,
    leaving a caller-supplied instance of either open (the caller's
    lifecycle).

    ``scaling_request_for`` (Task 9, R5): an OPTIONAL callable mapping a
    ``SkuMapEntry`` to a :class:`SkuScalingRequest` (or ``None``). Each approved
    pair's desire is routed -- BEFORE that pair is acquired -- through
    :func:`_scale_one`: within the approved tier it tightens the SKU's cadence
    (``report.scaled_watches``); above the ceiling it is surfaced for human
    approval (``report.pending_escalations``) and applies nothing. Default
    ``None`` == exact PR-6 behavior (the guard is never consulted). This job
    never decides WHICH SKUs deserve escalation -- that value model is the
    caller's.
    """
    now = now or datetime.now(timezone.utc)
    owns_event_ledger = event_ledger is None
    owns_client = http_client is None
    sku_map = sku_map if sku_map is not None else default_sku_map()
    ledger = ledger if ledger is not None else default_ledger()
    event_ledger = event_ledger if event_ledger is not None else EventLedger()
    client = http_client if http_client is not None else httpx.Client()

    try:
        pairs = sku_map.list_all_confirmed()
        site_configs: dict[str, object] = {}
        breakers: dict[str, base.CircuitBreaker] = {}
        outcomes: list[PairOutcome] = []
        pending_escalations: list[GuidedOutcome] = []
        scaled_watches: list[ScaledWatch] = []
        for e in pairs:
            # R5 scaling step -- decided BEFORE this pair is acquired, and only
            # for a site that is actually approved (an unapproved site can be
            # neither scaled nor fetched; _check_one_pair reports it skipped).
            if scaling_request_for is not None:
                config = _resolve_site_config(
                    e, site_configs=site_configs, breakers=breakers, sites_config_dir=sites_config_dir,
                )
                if not isinstance(config, Exception):
                    applied_cadence, guided = _scale_one(
                        e, config, scaling_request_for(e),
                        current_cadence_hours=DEFAULT_CADENCE_HOURS, now=now,
                    )
                    if guided is not None:
                        pending_escalations.append(guided)
                    if applied_cadence is not None:
                        scaled_watches.append(ScaledWatch(
                            site=e.site, competitor_sku_ref=e.competitor_sku_ref,
                            matched_product_id=e.our_product_id, applied_cadence_hours=applied_cadence,
                        ))
            outcomes.append(_check_one_pair(
                e, ledger=ledger, event_ledger=event_ledger, site_configs=site_configs,
                breakers=breakers, client=client, sites_config_dir=sites_config_dir, now=now,
            ))
        return PriceWatchCycleReport(
            now=now, pairs_checked=len(pairs), outcomes=tuple(outcomes),
            pending_escalations=tuple(pending_escalations), scaled_watches=tuple(scaled_watches),
        )
    finally:
        if owns_client:
            client.close()
        if owns_event_ledger:
            event_ledger.close()


# Registrable with jobs.scheduler.JobRegistry (F0, PR-3) -- same function,
# either called directly/via run_once() (tests, CI, golden rule 9) or run
# under this trigger by a real BackgroundScheduler in production. Same shape
# as jobs.price_monitor.PRICE_MONITOR_JOB -- a second, independent
# ScheduledJob entry (not a second detection/scheduling mechanism: both
# jobs converge on accept_observation, they merely re-acquire via different
# tiers on their own cadences).
PRICE_WATCH_JOB = ScheduledJob(
    id="price_watch_cycle",
    func=run_price_watch_cycle,
    trigger="interval",
    trigger_args={"hours": DEFAULT_CADENCE_HOURS},
)

"""S1 technical SEO audit -- orchestration (Linchpin 3.0 PR-22, plan section 8
"Track B -- SEO", S1 row: ``jobs/seo_audit.py``).

``src/seo/crawl_audit.py`` owns the pure detection checks; this module owns
everything that touches the outside world or applies a business judgment
call on top of those checks:

  1. **Compliance gate** -- same discipline as ``src/pricing_intel/acquire/
     base.py``'s ``require_approved_site`` ("no fetcher runs against an
     unconfigured/unapproved domain"), but purpose-built and lighter: this
     job crawls the CLIENT'S OWN site under an SEO engagement, not a THIRD
     PARTY's site under pricing_intel's ToS/robots-approval workflow, so it
     does not reuse ``SiteConfig``/``config/sites/*.yaml`` (that machinery
     is scoped to competitor-acquisition legal review, a different concern
     with different approvers). Instead: ``params['confirmed_domain']`` must
     be supplied and must match ``seed_url``'s own hostname exactly, or
     :class:`DomainNotConfirmedError` is raised -- an audit never runs
     against a domain nobody explicitly named.
  2. **The crawl itself** -- ``advertools.crawl()`` (the ``seo`` extra),
     which shells out to Scrapy's own ``robots.txt``-respecting,
     rate-limited crawler (``ROBOTSTXT_OBEY=True``, a bounded
     ``DOWNLOAD_DELAY``/``CONCURRENT_REQUESTS_PER_DOMAIN``, and an
     identifiable ``USER_AGENT`` -- same "politeness" standard as
     ``src/pricing_intel/acquire/pdp_fetcher.py``'s own identifiable,
     non-rotating User-Agent; no proxy rotation, no header spoofing, no
     anti-bot evasion of any kind). ``robots.txt``/``sitemap.xml`` are added
     as explicit seed URLs (nothing on a typical page links to them, so
     ``follow_links`` alone would never reach them) purely so
     ``crawl_audit.check_robots_txt``/``check_sitemap_xml`` have a page
     record to inspect.
  3. **Ranking** -- groups ``crawl_audit.run_checks``'s flat finding list by
     ``issue_type`` and scores each group by a documented, DELIBERATELY
     simple heuristic: ``severity_weight(issue_type) x affected_count``
     (:data:`SEVERITY_WEIGHTS` below). This is a coarse prioritization aid,
     not a calibrated business-impact model -- it is not tuned against real
     traffic or ranking data, and :class:`RankedIssue.description` says so
     plainly every time (no oversold precision). The top 20 (default) by
     score become the audit's headline deliverable; the FULL, uncapped
     finding list always travels alongside it (Golden Rule 14).
  4. **Lighthouse** (:func:`run_lighthouse_audit`) -- a Node CLI binary, NOT
     a pip package, checked via ``shutil.which("lighthouse")`` at RUN time
     (never at import time). Absent (the common case -- most environments
     running this repo do not have Node/Lighthouse installed) or failing
     degrades to an honestly-labeled, non-crashing result; the report's
     ``lighthouse_note`` always says explicitly whether those checks ran.

**Verified crawl-output note** (informs the adapter below, real output
inspected against ``advertools==0.18.0`` + ``scrapy==2.17.0`` on this
Windows py3.11 venv, 2026-07): ``advertools.crawl()`` writes one JSON-lines
row per fetched URL. ``img_src``/``img_alt`` are ``"@@"``-joined and
POSITIONALLY ALIGNED per ``<img>`` tag on the page (a missing ``alt``
attribute becomes an empty string AT THAT POSITION, never a shorter list --
each is built from the same per-``<img>`` attribute DataFrame). ``status``
correctly reflects a 404/5xx target as long as that page's own error
response is HTML (``HTTPERROR_ALLOW_ALL=True`` is advertools' own default);
a JSON error body -- e.g. FastAPI's default 404 -- makes advertools' own
link-extraction step raise internally and DROP that page's item entirely, a
real, verified quirk of the installed version combination, not something
this module can fix from the outside (a synthetic test site should serve an
HTML 404, matching real-world sites, to avoid it). The crawl does not retain
full page markup by default; ``xpath_selectors={"page_html": "/html"}``
below is what makes ``page.html`` (and therefore ``find_structured_data_
issues``/``find_duplicate_content``) available at all -- confirmed to return
the serialized ``<html>...</html>`` markup verbatim, including ``<script
type="application/ld+json">`` contents. ``sitemap.xml`` (``application/xml``
content-type) is NOT parsed as HTML by Scrapy, so ``page_html`` comes back
empty for it -- :func:`crawl_audit.check_sitemap_xml` accounts for this by
also checking ``content_type``.

Network I/O lives ONLY in this module's crawl/Lighthouse functions (HARD
RULE) -- ``src/seo/crawl_audit.py`` never imports ``advertools``/``httpx``/
``subprocess`` at all.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from src.export import write_summary_csv
from src.seo import crawl_audit

# Identifiable, honest User-Agent (same "politeness" standard as
# src/pricing_intel/acquire/pdp_fetcher.py's USER_AGENT -- no browser
# impersonation, no rotation; a site operator inspecting logs can tell
# exactly what hit their server and why).
DEFAULT_USER_AGENT = "KernSeoAudit/1.0 (+https://kern.example/seo-audit-bot)"

# Rate-limiting defaults (Golden Rule / plan S6.0 "cortesia tecnica" applied
# to an SEO crawl too, even though it is the client's own site): a small,
# fixed per-request delay and a low per-domain concurrency, never tuned up
# to "as fast as possible".
DEFAULT_DOWNLOAD_DELAY_SECONDS = 0.5
DEFAULT_CONCURRENT_REQUESTS_PER_DOMAIN = 2

# robots.txt/sitemap.xml would not otherwise be discovered by follow_links
# (nothing on a typical page <a href>s them) -- added as explicit seed URLs
# so crawl_audit's site-level checks have a page record to inspect.
_EXTRA_SEED_PATHS = ("/robots.txt", "/sitemap.xml")

DEFAULT_TOP_N_ISSUES = 20

# Ranking heuristic weights (plan S8 row S1: "tu propia heuristica simple y
# documentada"). 1-10, hand-assigned by rough SEO severity, NOT calibrated
# against measured traffic/ranking impact -- see RankedIssue.description,
# which repeats this caveat in every report so it is never read as more
# precise than it is.
SEVERITY_WEIGHTS: dict[str, int] = {
    crawl_audit.ROBOTS_TXT_MISSING: 10,
    crawl_audit.MISSING_TITLE: 9,
    crawl_audit.BROKEN_INTERNAL_LINK: 8,
    crawl_audit.SITEMAP_MISSING: 8,
    crawl_audit.DUPLICATE_TITLE: 6,
    crawl_audit.DUPLICATE_CONTENT: 6,
    crawl_audit.ROBOTS_TXT_SUSPICIOUS: 6,
    crawl_audit.MISSING_STRUCTURED_DATA: 5,
    crawl_audit.MALFORMED_STRUCTURED_DATA: 5,
    crawl_audit.SITEMAP_SUSPICIOUS: 5,
    crawl_audit.MISSING_META_DESCRIPTION: 4,
    crawl_audit.DUPLICATE_META_DESCRIPTION: 3,
    crawl_audit.MISSING_CANONICAL: 3,
    crawl_audit.MISSING_ALT_TEXT: 2,
}
_DEFAULT_SEVERITY_WEIGHT = 1  # an issue_type this module does not recognize -- never crashes, just ranks low
_MAX_EXAMPLE_URLS = 5


class AdvertoolsUnavailableError(RuntimeError):
    """Raised when ``advertools`` (the ``seo`` extra) is not installed.
    Crawling IS this job's core function -- unlike Lighthouse, there is no
    meaningful degrade short of not crawling at all, so this raises a clear,
    actionable error instead of a bare ``ImportError`` deep in a call stack
    (same pattern as ``jobs/scheduler.py``'s ``SchedulerUnavailableError``).
    ``src.seo.crawl_audit``'s checks still work standalone against any
    already-built ``CrawledPage`` list with zero extra install."""

    def __init__(self) -> None:
        super().__init__(
            "advertools is not installed - install the 'seo' extra "
            "(pip install -e '.[seo]') to run a real site crawl; "
            "src.seo.crawl_audit's checks work standalone against an "
            "already-built list of CrawledPage records with no extra install"
        )


class DomainNotConfirmedError(ValueError):
    """``params['confirmed_domain']`` was missing or did not match
    ``seed_url``'s own hostname -- see module docstring's compliance-gate
    section. Refuses to run rather than silently crawling a mistyped or
    unintended domain."""

    def __init__(self, *, seed_url: str, hostname: str | None, confirmed_domain: object) -> None:
        self.seed_url = seed_url
        self.hostname = hostname
        self.confirmed_domain = confirmed_domain
        super().__init__(
            f"seed_url {seed_url!r} resolves to domain {hostname!r}, which does not match "
            f"params['confirmed_domain']={confirmed_domain!r}. An SEO audit crawl requires an "
            "explicit, operator-supplied go-ahead naming the EXACT domain to crawl (same "
            "compliance discipline as src/pricing_intel/acquire/base.py's require_approved_site)."
        )


@dataclass(frozen=True)
class RankedIssue:
    """One issue-TYPE, grouped and scored across every page it affects --
    the top-N deliverable view. ``example_urls`` is capped at
    :data:`_MAX_EXAMPLE_URLS`; ``affected_count`` is never capped (Golden
    Rule 14 -- the true count is always shown even when the example list is
    shortened)."""

    issue_type: str
    severity_weight: int
    affected_count: int
    score: float
    example_urls: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class LighthouseResult:
    available: bool
    url: str
    performance_score: float | None
    seo_score: float | None
    accessibility_score: float | None
    note: str


@dataclass(frozen=True)
class SeoAuditReport:
    domain: str
    pages_crawled: int
    findings: tuple[crawl_audit.Finding, ...]  # full, UNCAPPED (Golden Rule 14)
    ranked_issues: tuple[RankedIssue, ...]  # top-N, capped, heuristic-ranked
    robots_txt: crawl_audit.RobotsTxtCheck
    sitemap: crawl_audit.SitemapCheck
    lighthouse: LighthouseResult
    top_n_requested: int
    summary: str


# -- domain confirmation gate -------------------------------------------------


def _hostname(url: str) -> str | None:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    return host[4:] if host.startswith("www.") else host


def _require_confirmed_domain(seed_url: str, confirmed_domain: object) -> str:
    hostname = _hostname(seed_url)
    if hostname is None:
        raise ValueError(f"seed_url must be an absolute http(s) URL, got {seed_url!r}")
    if not confirmed_domain or not isinstance(confirmed_domain, str):
        raise DomainNotConfirmedError(seed_url=seed_url, hostname=hostname, confirmed_domain=confirmed_domain)
    normalized_confirmed = confirmed_domain.strip().lower()
    if normalized_confirmed.startswith("www."):
        normalized_confirmed = normalized_confirmed[4:]
    if normalized_confirmed != hostname:
        raise DomainNotConfirmedError(seed_url=seed_url, hostname=hostname, confirmed_domain=confirmed_domain)
    return hostname


# -- the crawl adapter (network I/O) -----------------------------------------


def _ensure_scripts_on_path() -> None:
    """``advertools.crawl()`` shells out to the ``scrapy`` console script via
    a bare ``subprocess.run(["scrapy", ...])`` -- no explicit path, no
    ``env=`` override. It only resolves if the venv's ``Scripts``/``bin``
    directory (where pip installs console-script shims, sibling to the
    running interpreter) is on ``PATH``. A plain ``python -m ...`` run does
    NOT activate the venv, so ``PATH`` often lacks it (verified on this
    Windows venv). Idempotently prepends it to THIS process's ``PATH``
    (inherited by the child subprocess) rather than requiring every caller
    to have run the venv's activate script first."""
    scripts_dir = str(Path(sys.executable).parent)
    entries = os.environ.get("PATH", "").split(os.pathsep)
    if scripts_dir not in entries:
        os.environ["PATH"] = os.pathsep.join([scripts_dir, os.environ.get("PATH", "")])


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
    try:
        import advertools as adv
    except ImportError as exc:
        raise AdvertoolsUnavailableError() from exc

    _ensure_scripts_on_path()
    output_file.parent.mkdir(parents=True, exist_ok=True)
    if output_file.exists():
        output_file.unlink()  # advertools APPENDS to an existing file -- start clean every run

    base = seed_url.rstrip("/")
    url_list = [seed_url] + [base + path for path in _EXTRA_SEED_PATHS]

    adv.crawl(
        url_list,
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


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _clean_str(value: object) -> str | None:
    if _is_missing(value):
        return None
    text = str(value)
    return text if text.strip() else None


def _split_joined(value: object) -> tuple[str, ...]:
    text = _clean_str(value)
    return tuple(text.split("@@")) if text else ()


def _images_from_row(row: pd.Series) -> tuple[crawl_audit.ImageRef, ...]:
    srcs = _split_joined(row.get("img_src"))
    alts = _split_joined(row.get("img_alt"))
    # advertools aligns img_src/img_alt positionally per <img> tag (see
    # module docstring's verified crawl-output note) -- zip_longest is a
    # defensive fallback only, in case a future advertools version ever
    # misaligns the two joined strings.
    return tuple(crawl_audit.ImageRef(src=s, alt=a or "") for s, a in zip_longest(srcs, alts, fillvalue=""))


def _internal_links_from_row(row: pd.Series, *, hostname: str) -> tuple[str, ...]:
    links = _split_joined(row.get("links_url"))
    internal = [link for link in links if _hostname(link) == hostname]
    return tuple(dict.fromkeys(internal))  # de-dup, preserve first-seen order


def _page_from_row(row: pd.Series, *, hostname: str) -> crawl_audit.CrawledPage:
    status = row.get("status")
    status_code = None if _is_missing(status) else int(status)
    return crawl_audit.CrawledPage(
        url=str(row.get("url")),
        status_code=status_code,
        title=_clean_str(row.get("title")),
        meta_description=_clean_str(row.get("meta_desc")),
        canonical=_clean_str(row.get("canonical")),
        html=_clean_str(row.get("page_html")),
        content_type=_clean_str(row.get("resp_headers_Content-Type")),
        internal_links=_internal_links_from_row(row, hostname=hostname),
        images=_images_from_row(row),
    )


def pages_from_crawl_dataframe(df: pd.DataFrame, *, hostname: str) -> list[crawl_audit.CrawledPage]:
    """Adapt an ``advertools.crawl()`` output DataFrame (or one loaded back
    via ``pd.read_json(path, lines=True)``) into :class:`crawl_audit.
    CrawledPage` records. Pure pandas-shape adaptation -- no network I/O
    (the crawl itself already happened by the time this runs); exported so
    a test (or another caller with an already-crawled DataFrame) can build
    ``CrawledPage`` records without re-running a live crawl."""
    if df.empty or "url" not in df.columns:
        return []
    return [_page_from_row(row, hostname=hostname) for _, row in df.iterrows()]


# -- Lighthouse (optional, external Node CLI binary) -------------------------


DEFAULT_LIGHTHOUSE_TIMEOUT_SECONDS = 120.0


def run_lighthouse_audit(url: str, *, timeout: float = DEFAULT_LIGHTHOUSE_TIMEOUT_SECONDS) -> LighthouseResult:
    """Best-effort Lighthouse CLI run against ``url``. Checked at RUN time
    via ``shutil.which`` (never at import time) -- Lighthouse is a Node CLI
    binary, not a pip package, and most environments running this repo will
    not have it installed. Degrades gracefully: absence or any failure comes
    back as an honestly-labeled ``LighthouseResult(available=False, ...)``,
    never an exception and never a silently-skipped section (Golden Rule
    14 -- the caller must surface ``note`` in the report)."""
    binary = shutil.which("lighthouse")
    if binary is None:
        return LighthouseResult(
            False, url, None, None, None,
            "lighthouse CLI not found on PATH -- Lighthouse-sourced checks are not available "
            "in this environment (install Node.js + 'npm install -g lighthouse' to enable them).",
        )
    try:
        proc = subprocess.run(
            [
                binary, url, "--output=json", "--quiet",
                "--chrome-flags=--headless --no-sandbox",
                "--only-categories=performance,seo,accessibility",
            ],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return LighthouseResult(False, url, None, None, None, f"lighthouse run failed: {exc}")

    if proc.returncode != 0:
        return LighthouseResult(
            False, url, None, None, None,
            f"lighthouse exited with code {proc.returncode}: {(proc.stderr or '')[:300]}",
        )
    try:
        categories = json.loads(proc.stdout).get("categories", {})
        perf = categories.get("performance", {}).get("score")
        seo_score = categories.get("seo", {}).get("score")
        a11y = categories.get("accessibility", {}).get("score")
    except (json.JSONDecodeError, AttributeError) as exc:
        return LighthouseResult(False, url, None, None, None, f"lighthouse output was not parseable JSON: {exc}")
    return LighthouseResult(True, url, perf, seo_score, a11y, "lighthouse ran successfully")


# -- ranking -------------------------------------------------------------


def rank_issues(
    findings: list[crawl_audit.Finding], *, top_n: int = DEFAULT_TOP_N_ISSUES
) -> tuple[RankedIssue, ...]:
    """Group ``findings`` by ``issue_type`` and rank by
    ``severity_weight(issue_type) x affected_count`` (see module docstring
    for the heuristic's scope/limits). Ties break by ``issue_type`` name for
    a deterministic, testable order."""
    groups: dict[str, list[crawl_audit.Finding]] = defaultdict(list)
    for f in findings:
        groups[f.issue_type].append(f)

    ranked: list[RankedIssue] = []
    for issue_type, group in groups.items():
        urls = sorted({f.url for f in group})
        weight = SEVERITY_WEIGHTS.get(issue_type, _DEFAULT_SEVERITY_WEIGHT)
        score = float(weight * len(urls))
        examples = tuple(urls[:_MAX_EXAMPLE_URLS])
        remainder = len(urls) - len(examples)
        more_note = f" (+{remainder} more not shown here)" if remainder > 0 else ""
        ranked.append(RankedIssue(
            issue_type=issue_type,
            severity_weight=weight,
            affected_count=len(urls),
            score=score,
            example_urls=examples,
            description=(
                f"{issue_type}: {len(urls)} page(s)/instance(s) affected{more_note}. "
                f"Heuristic score = severity_weight({weight}) x affected_count({len(urls)}) = {score:.0f}. "
                "Ranks by a simple severity-x-reach heuristic, not measured traffic/ranking impact."
            ),
        ))
    ranked.sort(key=lambda r: (-r.score, r.issue_type))
    return tuple(ranked[:top_n])


# -- prepare / run / verify / write_operational -------------------------


def prepare(seed_url: str, params: dict | None = None) -> dict:
    """Gate the domain, run the real crawl (and, unless disabled, a
    Lighthouse pass), and hand ``run`` a ready-to-score payload. This job's
    natural input is a URL, not a CSV -- like ``jobs/price_intelligence.py``'s
    one-shot mode, the first positional argument is the job's actual
    required input rather than a ``data_path``.

    ``params``:
      - ``confirmed_domain`` (required): the exact domain the operator has
        authorized crawling (module docstring's compliance gate).
      - ``follow_links`` (default True), ``top_n`` (default 20),
        ``user_agent``, ``download_delay``, ``concurrent_requests_per_domain``,
        ``scrapy_log_level`` (default "ERROR"), ``crawl_output_file``,
        ``run_lighthouse`` (default True), ``lighthouse_url`` (defaults to
        ``seed_url``).
    """
    params = params or {}
    hostname = _require_confirmed_domain(seed_url, params.get("confirmed_domain"))

    output_file = Path(params.get("crawl_output_file") or Path("data") / "seo_audit_crawl.jl")
    df = _crawl_domain(
        seed_url,
        hostname=hostname,
        output_file=output_file,
        follow_links=bool(params.get("follow_links", True)),
        user_agent=str(params.get("user_agent", DEFAULT_USER_AGENT)),
        download_delay=float(params.get("download_delay", DEFAULT_DOWNLOAD_DELAY_SECONDS)),
        concurrent_requests_per_domain=int(
            params.get("concurrent_requests_per_domain", DEFAULT_CONCURRENT_REQUESTS_PER_DOMAIN)
        ),
        scrapy_log_level=str(params.get("scrapy_log_level", "ERROR")),
    )
    pages = pages_from_crawl_dataframe(df, hostname=hostname)

    lighthouse: LighthouseResult
    if params.get("run_lighthouse", True):
        lighthouse = run_lighthouse_audit(str(params.get("lighthouse_url") or seed_url))
    else:
        lighthouse = LighthouseResult(False, seed_url, None, None, None, "lighthouse checks skipped (run_lighthouse=False)")

    return {
        "domain": hostname,
        "pages": pages,
        "top_n": int(params.get("top_n", DEFAULT_TOP_N_ISSUES)),
        "lighthouse": lighthouse,
    }


def run(payload: dict) -> SeoAuditReport:
    """Pure: turns an already-crawled ``payload`` (as built by
    :func:`prepare`, or hand-built for tests) into a scored report. No
    network I/O here -- that already happened in ``prepare``."""
    pages: list[crawl_audit.CrawledPage] = payload["pages"]
    top_n = int(payload.get("top_n", DEFAULT_TOP_N_ISSUES))
    lighthouse: LighthouseResult = payload.get("lighthouse") or LighthouseResult(
        False, "", None, None, None, "no lighthouse result supplied"
    )

    findings = crawl_audit.run_checks(pages)
    ranked = rank_issues(list(findings), top_n=top_n)
    robots = crawl_audit.check_robots_txt(pages)
    sitemap = crawl_audit.check_sitemap_xml(pages)

    summary = (
        f"SEO audit over {len(pages)} crawled URL(s): {len(findings)} finding(s) grouped into "
        f"{len(ranked)} ranked issue-type(s) (top {top_n} shown, by severity x reach heuristic). "
        f"robots.txt {'present' if robots.present else 'MISSING'}; "
        f"sitemap.xml {'present' if sitemap.present else 'MISSING'}. "
        f"Lighthouse: {'ran' if lighthouse.available else 'not available in this environment'}."
    )

    return SeoAuditReport(
        domain=str(payload.get("domain", "")),
        pages_crawled=len(pages),
        findings=findings,
        ranked_issues=ranked,
        robots_txt=robots,
        sitemap=sitemap,
        lighthouse=lighthouse,
        top_n_requested=top_n,
        summary=summary,
    )


def verify(report: SeoAuditReport) -> list[str]:
    """QA gate (matches ``jobs/qa.py``'s ``verify_*``/``*_passed`` naming
    convention, kept local to this module like ``jobs/seo_priority.py``'s
    own ``verify``/``seo_priority_passed`` -- same Track B precedent)."""
    issues: list[str] = []
    if report.pages_crawled == 0:
        issues.append("no pages were crawled")
    if len(report.ranked_issues) > report.top_n_requested:
        issues.append("ranked_issues exceeds top_n_requested")

    valid_types = set(crawl_audit.ISSUE_TYPES)
    for r in report.ranked_issues:
        if r.issue_type not in valid_types:
            issues.append(f"unknown issue_type in ranked_issues: {r.issue_type!r}")
        if r.affected_count <= 0:
            issues.append(f"{r.issue_type}: affected_count must be > 0")
        if not r.example_urls:
            issues.append(f"{r.issue_type}: ranked issue has no example_urls (must be citable, Golden Rule 7)")

    scores = [r.score for r in report.ranked_issues]
    if scores != sorted(scores, reverse=True):
        issues.append("ranked_issues is not sorted by score descending")

    if not report.lighthouse.note:
        issues.append("lighthouse result has no note explaining its availability")

    return issues


def seo_audit_passed(report: SeoAuditReport) -> bool:
    return not verify(report)


_ISSUES_CSV_COLUMNS = (
    "rank", "issue_type", "severity_weight", "affected_count", "score", "example_urls", "description",
)
_FINDINGS_CSV_COLUMNS = ("issue_type", "url", "detail")


def write_operational(report: SeoAuditReport, out_dir: str | Path) -> dict[str, Path]:
    """The machine-readable deliverable: the top-N ranked issues (the
    headline view) plus the FULL, uncapped raw finding list (Golden Rule 14
    -- nothing is silently dropped, only summarized). Mirrors ``jobs/
    seo_priority.py``'s dual-CSV precedent (main + full detail)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)

    ranked_rows = [
        {
            "rank": i + 1,
            "issue_type": r.issue_type,
            "severity_weight": r.severity_weight,
            "affected_count": r.affected_count,
            "score": r.score,
            "example_urls": "; ".join(r.example_urls),
            "description": r.description,
        }
        for i, r in enumerate(report.ranked_issues)
    ]
    ranked_path = d / "seo_audit_top_issues.csv"
    if ranked_rows:
        out = {"issues_csv": write_summary_csv(ranked_rows, ranked_path)}
    else:
        pd.DataFrame(columns=list(_ISSUES_CSV_COLUMNS)).to_csv(ranked_path, index=False)
        out = {"issues_csv": ranked_path}

    findings_rows = [{"issue_type": f.issue_type, "url": f.url, "detail": f.detail} for f in report.findings]
    findings_path = d / "seo_audit_all_findings.csv"
    if findings_rows:
        out["findings_csv"] = write_summary_csv(findings_rows, findings_path)
    else:
        pd.DataFrame(columns=list(_FINDINGS_CSV_COLUMNS)).to_csv(findings_path, index=False)
        out["findings_csv"] = findings_path

    return out

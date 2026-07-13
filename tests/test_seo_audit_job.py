"""Tests for jobs/seo_audit.py (Linchpin 3.0 PR-22, S1 technical SEO audit
orchestration).

Layers:
  1. Domain-confirmation gate (no network at all -- the gate runs before any
     crawl attempt).
  2. The advertools-DataFrame -> CrawledPage adapter, exercised against a
     hand-built DataFrame shaped exactly like a REAL ``advertools.crawl()``
     output (columns/values verified against a real local crawl during this
     PR's development -- see jobs/seo_audit.py's module docstring).
  3. Ranking (`rank_issues`) with hand-verified severity x reach scores.
  4. Lighthouse: absent-binary path (mocked ``shutil.which`` -> None) and a
     mocked-present path (mocked ``subprocess.run`` -> canned JSON) -- no
     real Lighthouse/Node/Chrome install required either way.
  5. `run`/`verify`/`seo_audit_passed` against a hand-built payload.
  6. `write_operational` CSV round-trip.
  7. One real end-to-end test: a SYNTHETIC multi-page site served locally by
     a FastAPI/uvicorn test app (never a real live site), crawled for real
     via `advertools.crawl()`, with deliberately planted issues (a page
     missing a title, a broken internal link, a page with no structured
     data) that the full `prepare()` -> `run()` pipeline must find and
     report. Guarded by `pytest.importorskip` for every extra it needs
     (advertools/fastapi/uvicorn/httpx) so the rest of the suite runs fine
     without the 'seo'/'web' extras installed.
"""

from __future__ import annotations

import math
import sys
import threading
import time

import pandas as pd
import pytest

from jobs import seo_audit as sa
from src.seo import crawl_audit as ca

# -- domain confirmation gate (no network) -----------------------------------


def test_require_confirmed_domain_accepts_matching_domain() -> None:
    hostname = sa._require_confirmed_domain("https://shop.example.com/", "shop.example.com")
    assert hostname == "shop.example.com"


def test_require_confirmed_domain_strips_www_on_both_sides() -> None:
    hostname = sa._require_confirmed_domain("https://www.shop.example.com/", "www.shop.example.com")
    assert hostname == "shop.example.com"


def test_require_confirmed_domain_rejects_mismatch() -> None:
    with pytest.raises(sa.DomainNotConfirmedError):
        sa._require_confirmed_domain("https://shop.example.com/", "not-the-same.example.com")


def test_require_confirmed_domain_rejects_missing_confirmation() -> None:
    with pytest.raises(sa.DomainNotConfirmedError):
        sa._require_confirmed_domain("https://shop.example.com/", None)


def test_require_confirmed_domain_rejects_non_http_seed_url() -> None:
    with pytest.raises(ValueError):
        sa._require_confirmed_domain("ftp://shop.example.com/", "shop.example.com")


def test_prepare_raises_before_any_crawl_attempt_on_domain_mismatch() -> None:
    # No advertools import happens on this path -- the gate is the very
    # first thing prepare() does, so this passes even without the 'seo'
    # extra installed.
    with pytest.raises(sa.DomainNotConfirmedError):
        sa.prepare("https://shop.example.com/", {"confirmed_domain": "someone-elses-site.example.com"})


def test_crawl_domain_raises_a_clear_error_when_advertools_is_unavailable(monkeypatch, tmp_path) -> None:
    # Force `import advertools` to fail regardless of whether it is actually
    # installed in this environment -- sys.modules[name] = None makes the
    # import system raise ImportError immediately (a standard test trick).
    monkeypatch.setitem(sys.modules, "advertools", None)
    with pytest.raises(sa.AdvertoolsUnavailableError):
        sa._crawl_domain(
            "https://shop.example.com/", hostname="shop.example.com", output_file=tmp_path / "out.jl",
            follow_links=True, user_agent="test", download_delay=0.1,
            concurrent_requests_per_domain=1, scrapy_log_level="ERROR",
        )


# -- advertools DataFrame -> CrawledPage adapter -----------------------------


def _crawl_row(**overrides) -> dict:
    row = {
        "url": "https://shop.example.com/a",
        "status": 200,
        "title": "Widget A",
        "meta_desc": "Buy widget A",
        "canonical": "https://shop.example.com/a",
        "page_html": "<html><body><h1>Widget A</h1></body></html>",
        "resp_headers_Content-Type": "text/html; charset=utf-8",
        "links_url": "https://shop.example.com/b@@https://external.example.com/x",
        "img_src": "https://shop.example.com/img/a.jpg",
        "img_alt": "Widget A photo",
    }
    row.update(overrides)
    return row


def test_pages_from_crawl_dataframe_maps_columns_and_filters_external_links() -> None:
    df = pd.DataFrame([_crawl_row()])
    pages = sa.pages_from_crawl_dataframe(df, hostname="shop.example.com")
    assert len(pages) == 1
    p = pages[0]
    assert p.url == "https://shop.example.com/a"
    assert p.status_code == 200
    assert p.title == "Widget A"
    assert p.meta_description == "Buy widget A"
    assert p.canonical == "https://shop.example.com/a"
    assert "Widget A" in (p.html or "")
    assert p.content_type == "text/html; charset=utf-8"
    # the external link is dropped, only the same-domain link remains.
    assert p.internal_links == ("https://shop.example.com/b",)
    assert p.images == (ca.ImageRef(src="https://shop.example.com/img/a.jpg", alt="Widget A photo"),)


def test_pages_from_crawl_dataframe_handles_nan_title_and_missing_alt() -> None:
    df = pd.DataFrame([_crawl_row(
        title=math.nan, meta_desc=math.nan, canonical=math.nan,
        img_src="https://shop.example.com/img/c1.jpg@@https://shop.example.com/img/c2.jpg",
        img_alt="@@C2 alt",  # verified real advertools shape: first image has no alt (empty), second does
    )])
    pages = sa.pages_from_crawl_dataframe(df, hostname="shop.example.com")
    p = pages[0]
    assert p.title is None
    assert p.meta_description is None
    assert p.canonical is None
    assert p.images == (
        ca.ImageRef(src="https://shop.example.com/img/c1.jpg", alt=""),
        ca.ImageRef(src="https://shop.example.com/img/c2.jpg", alt="C2 alt"),
    )


def test_pages_from_crawl_dataframe_handles_nan_status_and_no_links() -> None:
    df = pd.DataFrame([_crawl_row(status=math.nan, links_url=math.nan, img_src=math.nan, img_alt=math.nan)])
    pages = sa.pages_from_crawl_dataframe(df, hostname="shop.example.com")
    p = pages[0]
    assert p.status_code is None
    assert p.internal_links == ()
    assert p.images == ()


def test_pages_from_crawl_dataframe_empty_dataframe_returns_empty_list() -> None:
    assert sa.pages_from_crawl_dataframe(pd.DataFrame(), hostname="shop.example.com") == []


# -- ranking ------------------------------------------------------------------


def test_rank_issues_hand_verified_scores() -> None:
    findings = [
        ca.Finding(ca.MISSING_TITLE, "u1", "no title"),
        ca.Finding(ca.MISSING_TITLE, "u2", "no title"),
        ca.Finding(ca.BROKEN_INTERNAL_LINK, "u3", "..."),
    ]
    # MISSING_TITLE weight=9, affected=2 -> score=18.
    # BROKEN_INTERNAL_LINK weight=8, affected=1 -> score=8.
    ranked = sa.rank_issues(findings, top_n=20)
    assert len(ranked) == 2
    assert ranked[0].issue_type == ca.MISSING_TITLE
    assert ranked[0].severity_weight == 9
    assert ranked[0].affected_count == 2
    assert ranked[0].score == 18.0
    assert ranked[0].example_urls == ("u1", "u2")
    assert ranked[1].issue_type == ca.BROKEN_INTERNAL_LINK
    assert ranked[1].score == 8.0


def test_rank_issues_deduplicates_by_url_within_one_issue_type() -> None:
    # Same URL flagged twice for the same issue_type (e.g. two separate
    # <img> tags missing alt on one page) counts as ONE affected page, not two.
    findings = [
        ca.Finding(ca.MISSING_ALT_TEXT, "u1", "image A has no alt"),
        ca.Finding(ca.MISSING_ALT_TEXT, "u1", "image B has no alt"),
    ]
    ranked = sa.rank_issues(findings, top_n=20)
    assert len(ranked) == 1
    assert ranked[0].affected_count == 1


def test_rank_issues_caps_at_top_n_and_breaks_ties_by_issue_type_name() -> None:
    # 25 distinct, unrecognized issue types -> each gets the default weight
    # (1) and exactly one affected URL -> score=1 for all -- a pure
    # alphabetical tie-break, zero-padded so string order == numeric order.
    findings = [ca.Finding(f"custom_issue_{i:02d}", f"u{i}", "...") for i in range(25)]
    ranked = sa.rank_issues(findings, top_n=20)
    assert len(ranked) == 20
    assert [r.issue_type for r in ranked] == [f"custom_issue_{i:02d}" for i in range(20)]
    assert all(r.severity_weight == sa._DEFAULT_SEVERITY_WEIGHT for r in ranked)


def test_rank_issues_caps_example_urls_but_not_affected_count() -> None:
    findings = [ca.Finding(ca.MISSING_ALT_TEXT, f"u{i}", "...") for i in range(8)]
    ranked = sa.rank_issues(findings, top_n=20)
    assert ranked[0].affected_count == 8
    assert len(ranked[0].example_urls) == sa._MAX_EXAMPLE_URLS
    assert "more" in ranked[0].description


# -- Lighthouse (mocked -- no real Node/Chrome/Lighthouse needed) ------------


def test_run_lighthouse_audit_absent_binary_degrades_gracefully(monkeypatch) -> None:
    monkeypatch.setattr(sa.shutil, "which", lambda name: None)
    result = sa.run_lighthouse_audit("https://shop.example.com/")
    assert result.available is False
    assert "not found on PATH" in result.note
    assert result.performance_score is None


def test_run_lighthouse_audit_present_and_successful(monkeypatch) -> None:
    monkeypatch.setattr(sa.shutil, "which", lambda name: "/usr/bin/lighthouse")

    class _FakeProc:
        returncode = 0
        stdout = '{"categories": {"performance": {"score": 0.87}, "seo": {"score": 0.95}, "accessibility": {"score": 0.9}}}'
        stderr = ""

    monkeypatch.setattr(sa.subprocess, "run", lambda *a, **k: _FakeProc())
    result = sa.run_lighthouse_audit("https://shop.example.com/")
    assert result.available is True
    assert result.performance_score == 0.87
    assert result.seo_score == 0.95
    assert result.accessibility_score == 0.9


def test_run_lighthouse_audit_nonzero_exit_degrades_gracefully(monkeypatch) -> None:
    monkeypatch.setattr(sa.shutil, "which", lambda name: "/usr/bin/lighthouse")

    class _FakeProc:
        returncode = 1
        stdout = ""
        stderr = "chrome failed to launch"

    monkeypatch.setattr(sa.subprocess, "run", lambda *a, **k: _FakeProc())
    result = sa.run_lighthouse_audit("https://shop.example.com/")
    assert result.available is False
    assert "chrome failed to launch" in result.note


def test_run_lighthouse_audit_timeout_degrades_gracefully(monkeypatch) -> None:
    monkeypatch.setattr(sa.shutil, "which", lambda name: "/usr/bin/lighthouse")

    def _raise(*a, **k):
        raise sa.subprocess.TimeoutExpired(cmd="lighthouse", timeout=1.0)

    monkeypatch.setattr(sa.subprocess, "run", _raise)
    result = sa.run_lighthouse_audit("https://shop.example.com/", timeout=1.0)
    assert result.available is False
    assert "failed" in result.note


# -- run / verify / seo_audit_passed (hand-built payload, no crawl) ---------


_MALFORMED_OFFER_HTML = """<html><head><title>Gadget</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Gadget",
 "offers":{"@type":"Offer","priceCurrency":"USD"}}
</script></head><body><h1>Gadget</h1></body></html>"""


def _lighthouse_skipped(url: str = "https://shop.example.com/") -> sa.LighthouseResult:
    return sa.LighthouseResult(False, url, None, None, None, "lighthouse checks skipped (run_lighthouse=False)")


def test_run_builds_a_report_that_passes_verify() -> None:
    pages = [
        ca.CrawledPage(
            url="https://shop.example.com/", status_code=200, title="Home",
            meta_description="Welcome", canonical="https://shop.example.com/",
            html="<html><body><h1>Home</h1></body></html>",
        ),
        ca.CrawledPage(
            url="https://shop.example.com/no-title", status_code=200, title=None,
            meta_description="desc", canonical="https://shop.example.com/no-title",
            html=_MALFORMED_OFFER_HTML,
        ),
    ]
    payload = {"domain": "shop.example.com", "pages": pages, "top_n": 20, "lighthouse": _lighthouse_skipped()}
    report = sa.run(payload)

    assert report.pages_crawled == 2
    assert report.domain == "shop.example.com"
    assert any(r.issue_type == ca.MISSING_TITLE for r in report.ranked_issues)
    assert any(f.issue_type == ca.MISSING_TITLE for f in report.findings)
    assert sa.seo_audit_passed(report) is True
    assert sa.verify(report) == []


def test_verify_flags_zero_pages_crawled() -> None:
    payload = {"domain": "shop.example.com", "pages": [], "top_n": 20, "lighthouse": _lighthouse_skipped()}
    report = sa.run(payload)
    issues = sa.verify(report)
    assert any("no pages" in i for i in issues)
    assert sa.seo_audit_passed(report) is False


def test_run_summary_mentions_lighthouse_unavailable() -> None:
    payload = {
        "domain": "shop.example.com",
        "pages": [ca.CrawledPage(url="https://shop.example.com/", status_code=200, title="Home")],
        "top_n": 20,
        "lighthouse": _lighthouse_skipped(),
    }
    report = sa.run(payload)
    assert "not available" in report.summary


# -- write_operational ---------------------------------------------------


def test_write_operational_round_trips_ranked_and_raw_findings(tmp_path) -> None:
    pages = [ca.CrawledPage(url="https://shop.example.com/no-title", status_code=200, title=None)]
    payload = {"domain": "shop.example.com", "pages": pages, "top_n": 20, "lighthouse": _lighthouse_skipped()}
    report = sa.run(payload)

    out = sa.write_operational(report, tmp_path)
    issues_df = pd.read_csv(out["issues_csv"])
    findings_df = pd.read_csv(out["findings_csv"])

    assert "missing_title" in issues_df["issue_type"].tolist()
    assert (findings_df["issue_type"] == "missing_title").any()


def test_write_operational_on_clean_report_writes_header_only_csvs(tmp_path) -> None:
    # A fully clean, fully-accounted-for crawl: one page with everything
    # present (title/meta/canonical/structured data), plus robots.txt and
    # sitemap.xml both fetched and sane -- run_checks() finds nothing at
    # all, so both CSVs come back header-only.
    pages = [
        ca.CrawledPage(
            url="https://shop.example.com/", status_code=200, title="Home",
            meta_description="Welcome", canonical="https://shop.example.com/",
            html="""<html><head><title>Home</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"Home",
"offers":{"@type":"Offer","price":"1.00","priceCurrency":"USD"}}</script></head>
<body><h1>Home</h1></body></html>""",
        ),
        ca.CrawledPage(
            url="https://shop.example.com/robots.txt", status_code=200,
            html="<html><body><p>User-agent: *\nAllow: /</p></body></html>",
        ),
        ca.CrawledPage(
            url="https://shop.example.com/sitemap.xml", status_code=200,
            content_type="application/xml",
        ),
    ]
    payload = {"domain": "shop.example.com", "pages": pages, "top_n": 20, "lighthouse": _lighthouse_skipped()}
    report = sa.run(payload)
    assert report.findings == ()
    out = sa.write_operational(report, tmp_path)
    issues_df = pd.read_csv(out["issues_csv"])
    findings_df = pd.read_csv(out["findings_csv"])
    assert len(issues_df) == 0
    assert len(findings_df) == 0
    assert list(issues_df.columns) == list(sa._ISSUES_CSV_COLUMNS)
    assert list(findings_df.columns) == list(sa._FINDINGS_CSV_COLUMNS)


# -- full end-to-end: real advertools crawl against a local synthetic site --

pytest.importorskip("advertools")
pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")
pytest.importorskip("httpx")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse, PlainTextResponse, Response  # noqa: E402

_PORT = 8765
_BASE = f"http://127.0.0.1:{_PORT}"

_app = FastAPI()


@_app.exception_handler(404)
async def _not_found(request: Request, exc: HTTPException) -> HTMLResponse:
    return HTMLResponse("<html><head><title>Not Found</title></head><body><h1>404</h1></body></html>", status_code=404)


@_app.get("/", response_class=HTMLResponse)
def _home() -> str:
    return (
        "<html><head><title>Acme Shop</title>"
        '<meta name="description" content="Acme Shop home page.">'
        f'<link rel="canonical" href="{_BASE}/"></head>'
        "<body><h1>Acme Shop</h1>"
        f'<img src="/img/hero.jpg" alt="Acme hero banner">'
        f'<a href="{_BASE}/widget">Widget</a>'
        f'<a href="{_BASE}/gone">Dead link</a>'  # PLANTED: broken internal link
        "<script type=\"application/ld+json\">"
        '{"@context":"https://schema.org","@type":"Product","name":"Acme Shop",'
        '"offers":{"@type":"Offer","price":"0.00","priceCurrency":"USD"}}'
        "</script></body></html>"
    )


@_app.get("/widget", response_class=HTMLResponse)
def _widget() -> str:
    # PLANTED: no <title> tag at all, no structured data at all, and an
    # <img> with no alt text -- three planted issues on one reachable page.
    return (
        '<html><head><meta name="description" content="Buy the widget."></head>'
        '<body><h1>Widget</h1><img src="/img/widget.jpg">'
        f'<a href="{_BASE}/">Home</a></body></html>'
    )


@_app.get("/robots.txt", response_class=PlainTextResponse)
def _robots() -> str:
    return f"User-agent: *\nAllow: /\nSitemap: {_BASE}/sitemap.xml\n"


@_app.get("/sitemap.xml")
def _sitemap() -> Response:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{_BASE}/</loc></url></urlset>"
    )
    return Response(content=xml, media_type="application/xml")


@pytest.fixture(scope="module", autouse=False)
def _synthetic_site():
    """Starts the tiny FastAPI app above on a background thread and polls it
    until it accepts connections -- a real (local, synthetic) HTTP server,
    never a real live third-party site, per the PR's testing requirement."""
    def _run() -> None:
        config = uvicorn.Config(_app, host="127.0.0.1", port=_PORT, log_level="warning")
        uvicorn.Server(config).run()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{_BASE}/", timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        pytest.fail("synthetic FastAPI test site did not become ready in time")

    yield _BASE


def test_prepare_and_run_end_to_end_against_synthetic_site(_synthetic_site, tmp_path) -> None:
    seed_url = _synthetic_site + "/"
    params = {
        "confirmed_domain": "127.0.0.1",
        "crawl_output_file": tmp_path / "crawl.jl",
        "scrapy_log_level": "ERROR",
        "run_lighthouse": False,  # no real Lighthouse/Node install required for this test
        "top_n": 20,
    }
    payload = sa.prepare(seed_url, params)
    assert payload["pages"], "expected at least one crawled page"

    report = sa.run(payload)

    assert report.pages_crawled >= 3  # home, widget, robots.txt, sitemap.xml, and the dead-link target
    assert any(
        f.issue_type == ca.MISSING_TITLE and f.url.endswith("/widget") for f in report.findings
    ), report.findings
    assert any(
        f.issue_type == ca.BROKEN_INTERNAL_LINK and "/gone" in f.detail for f in report.findings
    ), report.findings
    assert any(f.issue_type == ca.MISSING_ALT_TEXT and f.url.endswith("/widget") for f in report.findings)
    assert any(
        f.issue_type == ca.MISSING_STRUCTURED_DATA and f.url.endswith("/widget") for f in report.findings
    ), report.findings
    assert report.robots_txt.present is True
    assert report.robots_txt.looks_sane is True
    assert report.sitemap.present is True
    assert len(report.ranked_issues) <= 20

    issues = sa.verify(report)
    assert issues == [], issues

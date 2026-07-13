"""Tests for src/seo/crawl_audit.py (Linchpin 3.0 PR-22, S1 technical SEO audit).

Every check is exercised against small, HAND-CONSTRUCTED `CrawledPage` lists
(numbers/URLs verified by hand in each test's own comments) -- no network,
no advertools/scrapy import anywhere in this file, matching the module's own
"pure, no network I/O" contract. `jobs/seo_audit.py`'s own test file covers
the real advertools crawl adapter and the ranking/orchestration layer.
"""

from __future__ import annotations

from src.seo import crawl_audit as ca

# -- title ------------------------------------------------------------------


def test_find_missing_titles_flags_none_and_whitespace_only() -> None:
    pages = [
        ca.CrawledPage(url="u1", title="Home"),
        ca.CrawledPage(url="u2", title=None),
        ca.CrawledPage(url="u3", title="   "),
    ]
    findings = ca.find_missing_titles(pages)
    assert {f.url for f in findings} == {"u2", "u3"}
    assert all(f.issue_type == ca.MISSING_TITLE for f in findings)


def test_find_duplicate_titles_groups_by_exact_title_and_ignores_missing() -> None:
    pages = [
        ca.CrawledPage(url="u1", title="Same Title"),
        ca.CrawledPage(url="u2", title="Same Title"),
        ca.CrawledPage(url="u3", title="Unique"),
        ca.CrawledPage(url="u4", title=None),
    ]
    findings = ca.find_duplicate_titles(pages)
    assert {f.url for f in findings} == {"u1", "u2"}
    assert all(f.issue_type == ca.DUPLICATE_TITLE for f in findings)
    assert all("Same Title" in f.detail and "2 pages" in f.detail for f in findings)


# -- meta description ---------------------------------------------------


def test_find_missing_meta_descriptions() -> None:
    pages = [
        ca.CrawledPage(url="u1", meta_description="Buy stuff"),
        ca.CrawledPage(url="u2", meta_description=None),
        ca.CrawledPage(url="u3", meta_description=""),
    ]
    findings = ca.find_missing_meta_descriptions(pages)
    assert {f.url for f in findings} == {"u2", "u3"}


def test_find_duplicate_meta_descriptions() -> None:
    pages = [
        ca.CrawledPage(url="u1", meta_description="Welcome to the shop"),
        ca.CrawledPage(url="u2", meta_description="Welcome to the shop"),
        ca.CrawledPage(url="u3", meta_description="Different copy"),
    ]
    findings = ca.find_duplicate_meta_descriptions(pages)
    assert {f.url for f in findings} == {"u1", "u2"}
    assert all(f.issue_type == ca.DUPLICATE_META_DESCRIPTION for f in findings)


# -- canonical ----------------------------------------------------------


def test_find_missing_canonical() -> None:
    pages = [
        ca.CrawledPage(url="u1", canonical="https://shop.example.com/u1"),
        ca.CrawledPage(url="u2", canonical=None),
    ]
    findings = ca.find_missing_canonical(pages)
    assert [f.url for f in findings] == ["u2"]
    assert findings[0].issue_type == ca.MISSING_CANONICAL


# -- structured data (reuses extract_product_metadata) -----------------


_VALID_OFFER_HTML = """<html><head><title>Widget</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Widget",
 "offers":{"@type":"Offer","price":"19.99","priceCurrency":"USD"}}
</script></head><body><h1>Widget</h1></body></html>"""

_MALFORMED_OFFER_HTML = """<html><head><title>Gadget</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Gadget",
 "offers":{"@type":"Offer","priceCurrency":"USD"}}
</script></head><body><h1>Gadget</h1></body></html>"""

_NO_STRUCTURED_DATA_HTML = """<html><head><title>About Us</title></head>
<body><h1>About Us</h1><p>Company info, no product markup here.</p></body></html>"""


def test_find_structured_data_issues_covers_valid_missing_malformed_and_no_html() -> None:
    pages = [
        ca.CrawledPage(url="valid", html=_VALID_OFFER_HTML),
        ca.CrawledPage(url="malformed", html=_MALFORMED_OFFER_HTML),
        ca.CrawledPage(url="missing", html=_NO_STRUCTURED_DATA_HTML),
        ca.CrawledPage(url="no-html", html=None),
        ca.CrawledPage(url="empty-html", html="   "),
    ]
    findings = ca.find_structured_data_issues(pages)
    by_url = {f.url: f for f in findings}

    assert "valid" not in by_url
    assert by_url["malformed"].issue_type == ca.MALFORMED_STRUCTURED_DATA
    assert "price" in by_url["malformed"].detail
    assert by_url["missing"].issue_type == ca.MISSING_STRUCTURED_DATA
    assert by_url["no-html"].issue_type == ca.MISSING_STRUCTURED_DATA
    assert "was captured" in by_url["no-html"].detail
    assert by_url["empty-html"].issue_type == ca.MISSING_STRUCTURED_DATA
    assert len(findings) == 4


# -- broken internal links -----------------------------------------------


def test_find_broken_internal_links_only_flags_targets_with_known_4xx_5xx_status() -> None:
    pages = [
        ca.CrawledPage(
            url="https://shop.example.com/",
            status_code=200,
            internal_links=(
                "https://shop.example.com/a",
                "https://shop.example.com/dead",
                "https://shop.example.com/unseen",  # never crawled -- NOT flagged (see docstring)
            ),
        ),
        ca.CrawledPage(url="https://shop.example.com/a", status_code=200),
        ca.CrawledPage(url="https://shop.example.com/dead", status_code=404),
    ]
    findings = ca.find_broken_internal_links(pages)
    assert len(findings) == 1
    assert findings[0].issue_type == ca.BROKEN_INTERNAL_LINK
    assert findings[0].url == "https://shop.example.com/"
    assert "https://shop.example.com/dead" in findings[0].detail
    assert "404" in findings[0].detail


def test_find_broken_internal_links_dedupes_repeated_link_on_same_page() -> None:
    pages = [
        ca.CrawledPage(
            url="p1", status_code=200,
            internal_links=("dead", "dead", "dead"),  # e.g. same link repeated in nav + body
        ),
        ca.CrawledPage(url="dead", status_code=500),
    ]
    findings = ca.find_broken_internal_links(pages)
    assert len(findings) == 1


# -- alt text -------------------------------------------------------------


def test_find_missing_alt_text() -> None:
    pages = [
        ca.CrawledPage(
            url="u1",
            images=(
                ca.ImageRef(src="img1.jpg", alt="a widget photo"),
                ca.ImageRef(src="img2.jpg", alt=""),
                ca.ImageRef(src="img3.jpg", alt="   "),
            ),
        ),
    ]
    findings = ca.find_missing_alt_text(pages)
    assert {f.detail.split()[1] for f in findings} == {"img2.jpg", "img3.jpg"}
    assert all(f.issue_type == ca.MISSING_ALT_TEXT for f in findings)


# -- duplicate content -----------------------------------------------------


_LONG_BODY = "Hello World this is a test page with enough content to clear the minimum dedup length"


def test_find_duplicate_content_is_case_and_whitespace_insensitive_but_ignores_short_pages() -> None:
    pages = [
        ca.CrawledPage(url="u1", html=f"<html><body> {_LONG_BODY} </body></html>"),
        # same content, different tag case/extra whitespace -- still an exact match after normalization
        ca.CrawledPage(url="u2", html=f"<HTML><BODY>  {_LONG_BODY.upper()}  </BODY></HTML>"),
        ca.CrawledPage(url="u3", html="<html><body> Something completely different and unique here </body></html>"),
        ca.CrawledPage(url="u4", html="short"),
        ca.CrawledPage(url="u5", html="short"),  # identical to u4 but both below the length floor -- excluded
    ]
    # u1 and u2 differ in tag case and surrounding whitespace, but collapse to
    # the SAME whitespace-normalized, lowercased signature.
    assert " ".join(pages[0].html.split()).lower() == " ".join(pages[1].html.split()).lower()

    findings = ca.find_duplicate_content(pages)
    assert {f.url for f in findings} == {"u1", "u2"}
    assert all(f.issue_type == ca.DUPLICATE_CONTENT for f in findings)


# -- robots.txt / sitemap.xml ----------------------------------------------


def test_check_robots_txt_present_and_sane() -> None:
    pages = [ca.CrawledPage(
        url="https://shop.example.com/robots.txt", status_code=200,
        html="<html><body><p>User-agent: *\nAllow: /</p></body></html>",
    )]
    result = ca.check_robots_txt(pages)
    assert result == ca.RobotsTxtCheck(True, 200, True, result.detail)


def test_check_robots_txt_missing() -> None:
    result = ca.check_robots_txt([])
    assert result.present is False
    assert result.status_code is None
    assert result.looks_sane is False


def test_check_robots_txt_present_but_error_status_counts_as_not_present() -> None:
    pages = [ca.CrawledPage(url="https://shop.example.com/robots.txt", status_code=404, html="Not Found")]
    result = ca.check_robots_txt(pages)
    assert result.present is False
    assert result.status_code == 404


def test_check_robots_txt_present_but_no_user_agent_directive_is_suspicious() -> None:
    pages = [ca.CrawledPage(
        url="https://shop.example.com/robots.txt", status_code=200,
        html="<html><body><p></p></body></html>",
    )]
    result = ca.check_robots_txt(pages)
    assert result.present is True
    assert result.looks_sane is False


def test_check_sitemap_xml_present_via_content_type() -> None:
    pages = [ca.CrawledPage(
        url="https://shop.example.com/sitemap.xml", status_code=200,
        content_type="application/xml", html=None,
    )]
    result = ca.check_sitemap_xml(pages)
    assert result == ca.SitemapCheck(True, 200, True, result.detail)


def test_check_sitemap_xml_present_via_body_when_content_type_missing() -> None:
    pages = [ca.CrawledPage(
        url="https://shop.example.com/sitemap.xml", status_code=200,
        content_type=None, html="<urlset><url><loc>https://shop.example.com/a</loc></url></urlset>",
    )]
    result = ca.check_sitemap_xml(pages)
    assert result.present is True
    assert result.looks_sane is True


def test_check_sitemap_xml_missing() -> None:
    result = ca.check_sitemap_xml([])
    assert result.present is False


def test_robots_and_sitemap_findings_combines_both_checks() -> None:
    robots_missing = ca.RobotsTxtCheck(False, None, False, "robots.txt was not fetched during the crawl")
    sitemap_suspicious = ca.SitemapCheck(True, 200, False, "sitemap.xml present but did not look like XML")
    findings = ca.robots_and_sitemap_findings(robots_missing, sitemap_suspicious)
    types = {f.issue_type for f in findings}
    assert types == {ca.ROBOTS_TXT_MISSING, ca.SITEMAP_SUSPICIOUS}

    robots_sane = ca.RobotsTxtCheck(True, 200, True, "ok")
    sitemap_sane = ca.SitemapCheck(True, 200, True, "ok")
    assert ca.robots_and_sitemap_findings(robots_sane, sitemap_sane) == []


# -- run_checks: aggregation + planted-issue reporting ----------------------


def test_run_checks_finds_and_reports_each_planted_issue() -> None:
    """A small synthetic site with THREE deliberately planted issues (mirrors
    the PR-22 acceptance QA scenario): a missing title, a broken internal
    link, and a page with no structured data at all."""
    pages = [
        ca.CrawledPage(
            url="https://shop.example.com/",
            status_code=200, title="Shop Home", meta_description="Welcome",
            canonical="https://shop.example.com/",
            html=_VALID_OFFER_HTML,
            internal_links=("https://shop.example.com/broken", "https://shop.example.com/about"),
        ),
        ca.CrawledPage(
            # PLANTED ISSUE 1: no title.
            url="https://shop.example.com/no-title",
            status_code=200, title=None, meta_description="Has a description",
            canonical="https://shop.example.com/no-title", html=_VALID_OFFER_HTML,
        ),
        ca.CrawledPage(
            # PLANTED ISSUE 2 target: the homepage links here and it 404s.
            url="https://shop.example.com/broken", status_code=404,
        ),
        ca.CrawledPage(
            # PLANTED ISSUE 3: a real page with no structured data at all.
            url="https://shop.example.com/about", status_code=200,
            title="About", meta_description="About us", canonical="https://shop.example.com/about",
            html=_NO_STRUCTURED_DATA_HTML,
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

    findings = ca.run_checks(pages)

    assert any(f.issue_type == ca.MISSING_TITLE and f.url == "https://shop.example.com/no-title" for f in findings)
    assert any(
        f.issue_type == ca.BROKEN_INTERNAL_LINK and f.url == "https://shop.example.com/"
        and "https://shop.example.com/broken" in f.detail
        for f in findings
    )
    assert any(
        f.issue_type == ca.MISSING_STRUCTURED_DATA and f.url == "https://shop.example.com/about"
        for f in findings
    )
    # robots.txt/sitemap.xml are both present and sane -- no site-level finding for either.
    assert not any(f.issue_type in (ca.ROBOTS_TXT_MISSING, ca.SITEMAP_MISSING) for f in findings)

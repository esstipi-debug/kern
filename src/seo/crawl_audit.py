"""S1 technical SEO audit -- pure detection checks (Linchpin 3.0 PR-22, plan
section 8 "Track B -- SEO", S1 row: ``src/seo/crawl_audit.py``).

Everything in this module operates on already-fetched page data
(:class:`CrawledPage`) that a caller assembled -- there is NO network I/O and
NO ``advertools``/``scrapy`` import here (HARD RULE: ``src/`` stays pure;
the real site crawl lives in ``jobs/seo_audit.py``, which builds
``CrawledPage`` records from an ``advertools.crawl()`` run and feeds them
through the ``find_*``/``check_*`` functions below). A test (or any other
caller) can exercise every check with hand-built ``CrawledPage`` fixtures,
with no crawler, no subprocess, and no network at all.

Structured-data checks (:func:`find_structured_data_issues`) REUSE
``src.pricing_intel.acquire.structured.extract_product_metadata`` (Fase B,
PR-11) rather than adding a second extruct/JSON-LD integration -- Golden
Rule 5 (DRY) plus the plan's own instruction ("Reusa el adapter extruct de
6.4"). That adapter already tries extruct first and falls back to a
hand-rolled ld+json-only parser when extruct is unavailable or errors; this
module inherits that same honest degrade for free, and never re-implements
JSON-LD/microdata parsing itself. That check is schema.org Product/Offer
specific -- a "missing_structured_data" finding on a non-product page (a
blog post, a category listing) is informational, not necessarily
actionable; this module does not try to guess a page's type, and the
finding's ``detail`` says so plainly (no oversold precision).

Ranking is deliberately NOT this module's job -- ``jobs/seo_audit.py``
groups+ranks the :class:`Finding` list this module returns by its own
documented severity-x-reach heuristic (plan S8 row S1: "your own simple,
documented heuristic"). Everything here is one flat, UNCAPPED list of
findings (Golden Rule 14 -- no silent caps; a caller that wants a top-N view
built the capping itself, on top of the full list this module returns).

``advertools`` conflates "alt attribute absent" and "alt='' " into the same
empty string when it extracts ``img_alt`` (see ``jobs/seo_audit.py``'s own
docstring for the verified crawl-output note) -- :class:`ImageRef` inherits
that same limitation and documents it rather than pretending to distinguish
the two.

The eight "content-quality" checks (title/meta description/canonical/
structured-data/alt-text/duplicate-content, everything except
``find_broken_internal_links`` and the robots.txt/sitemap.xml checks) only
look at :func:`_content_pages`: a page with a known non-200 status, or a
``robots.txt``/``sitemap.xml`` infrastructure URL, is excluded -- "robots.txt
has no ``<title>``" is not a real technical-SEO finding, and a 404 page's own
missing title is not something to fix (a page LINKING to that 404 is a
different, real issue -- :func:`find_broken_internal_links` deliberately
does NOT apply this filter, since it needs every page's status to resolve
link targets).
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from src.pricing_intel.acquire.structured import extract_product_metadata

# -- issue type constants (jobs/seo_audit.py's ranking heuristic keys off these) --
MISSING_TITLE = "missing_title"
DUPLICATE_TITLE = "duplicate_title"
MISSING_META_DESCRIPTION = "missing_meta_description"
DUPLICATE_META_DESCRIPTION = "duplicate_meta_description"
MISSING_CANONICAL = "missing_canonical"
MISSING_STRUCTURED_DATA = "missing_structured_data"
MALFORMED_STRUCTURED_DATA = "malformed_structured_data"
BROKEN_INTERNAL_LINK = "broken_internal_link"
MISSING_ALT_TEXT = "missing_alt_text"
DUPLICATE_CONTENT = "duplicate_content"
ROBOTS_TXT_MISSING = "robots_txt_missing"
ROBOTS_TXT_SUSPICIOUS = "robots_txt_suspicious"
SITEMAP_MISSING = "sitemap_missing"
SITEMAP_SUSPICIOUS = "sitemap_suspicious"

ISSUE_TYPES = (
    MISSING_TITLE, DUPLICATE_TITLE, MISSING_META_DESCRIPTION, DUPLICATE_META_DESCRIPTION,
    MISSING_CANONICAL, MISSING_STRUCTURED_DATA, MALFORMED_STRUCTURED_DATA,
    BROKEN_INTERNAL_LINK, MISSING_ALT_TEXT, DUPLICATE_CONTENT,
    ROBOTS_TXT_MISSING, ROBOTS_TXT_SUSPICIOUS, SITEMAP_MISSING, SITEMAP_SUSPICIOUS,
)

# A page's visible content must be at least this long (collapsed whitespace,
# characters) before it is eligible for the duplicate-content check -- a
# near-empty stub page (e.g. a placeholder or a redirect shell) matching
# another near-empty stub is not a meaningful "duplicate content" signal.
# Named constant per Golden Rule (no magic numbers).
_MIN_CONTENT_LENGTH_FOR_DEDUP = 40


@dataclass(frozen=True)
class ImageRef:
    """One ``<img>`` found on a page. ``alt`` is ``""`` both when the ``alt``
    attribute is entirely absent AND when it is present-but-empty -- the
    upstream crawl adapter cannot always tell the two apart (see module
    docstring); this module treats both as "no alt text" for the
    ``find_missing_alt_text`` check, which is the conservative, honest
    reading (a genuinely-decorative ``alt=""`` is rare enough in practice
    that flagging it for a human to confirm costs little)."""

    src: str
    alt: str = ""


@dataclass(frozen=True)
class CrawledPage:
    """One already-fetched page's normalized data. ``jobs/seo_audit.py``'s
    advertools-crawl adapter builds these from a live crawl; tests build
    them by hand. No network I/O and no advertools/scrapy dependency lives
    in this module at all.

    ``html`` is the raw page markup when the crawl adapter retained it (it
    feeds :func:`find_structured_data_issues` and ``find_duplicate_content``);
    it may be ``None``/empty for a non-HTML response (``robots.txt``,
    ``sitemap.xml``) or when the caller chose not to retain full markup.
    ``internal_links`` is the set of same-domain URLs this page links to
    (already resolved to absolute URLs by the caller); ``images`` is every
    ``<img>`` found on the page.
    """

    url: str
    status_code: int | None = None
    title: str | None = None
    meta_description: str | None = None
    canonical: str | None = None
    html: str | None = None
    content_type: str | None = None
    internal_links: tuple[str, ...] = ()
    images: tuple[ImageRef, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Finding:
    """One raw, ungrouped audit finding -- Golden Rule 14: this is the
    non-capped, full record. ``jobs/seo_audit.py``'s ranking groups+caps a
    SUMMARIZED view of a list of these; it never drops one silently from
    the list this module returns."""

    issue_type: str
    url: str
    detail: str


@dataclass(frozen=True)
class RobotsTxtCheck:
    present: bool
    status_code: int | None
    looks_sane: bool  # best-effort text check, not a full robots.txt parser
    detail: str


@dataclass(frozen=True)
class SitemapCheck:
    present: bool
    status_code: int | None
    looks_sane: bool  # best-effort content-type/body check, not a full XML validator
    detail: str


def _clean(text: str | None) -> str:
    return (text or "").strip()


# URL suffixes that are infrastructure files, not indexable content pages --
# checked separately by check_robots_txt/check_sitemap_xml. Content-quality
# checks below (title/meta/canonical/structured-data/alt-text/duplicate-
# content) exclude them via _content_pages: "robots.txt has no <title>" is
# not a real technical-SEO finding.
_INFRA_URL_SUFFIXES = ("/robots.txt", "sitemap.xml")


def _is_infra_url(url: str) -> bool:
    lowered = url.rstrip("/").lower()
    return any(lowered.endswith(suffix) for suffix in _INFRA_URL_SUFFIXES)


def _content_pages(pages: Sequence[CrawledPage]) -> list[CrawledPage]:
    """Pages eligible for the content-quality checks: NOT a known-bad
    response (``status_code`` is either unknown/``None`` -- a caller that
    doesn't track it -- or a successful 200) and NOT an infrastructure file
    (``robots.txt``/``sitemap.xml``). A 404/5xx page's own missing title is
    not a real technical-SEO issue to fix; the fact that another page LINKS
    to it is a different, real issue -- :func:`find_broken_internal_links`
    deliberately does NOT use this filter, since it needs every page's
    status to resolve link targets."""
    return [p for p in pages if p.status_code in (None, 200) and not _is_infra_url(p.url)]


# -- title / meta description / canonical -----------------------------------


def find_missing_titles(pages: Sequence[CrawledPage]) -> list[Finding]:
    return [
        Finding(MISSING_TITLE, p.url, "no <title> tag (or an empty one) found on this page")
        for p in _content_pages(pages)
        if not _clean(p.title)
    ]


def _find_duplicates(
    pages: Sequence[CrawledPage], *, field_getter, issue_type: str, label: str
) -> list[Finding]:
    """Shared grouping logic for the two duplicate-field checks below --
    group pages by a non-empty field value, flag every page in a group of
    size > 1."""
    groups: dict[str, list[str]] = defaultdict(list)
    for p in pages:
        value = _clean(field_getter(p))
        if value:
            groups[value].append(p.url)

    findings: list[Finding] = []
    for value, urls in sorted(groups.items()):
        if len(urls) > 1:
            for u in sorted(urls):
                findings.append(Finding(issue_type, u, f"{label} {value!r} is shared by {len(urls)} pages"))
    return findings


def find_duplicate_titles(pages: Sequence[CrawledPage]) -> list[Finding]:
    return _find_duplicates(
        _content_pages(pages), field_getter=lambda p: p.title, issue_type=DUPLICATE_TITLE, label="title",
    )


def find_missing_meta_descriptions(pages: Sequence[CrawledPage]) -> list[Finding]:
    return [
        Finding(MISSING_META_DESCRIPTION, p.url, "no meta description found on this page")
        for p in _content_pages(pages)
        if not _clean(p.meta_description)
    ]


def find_duplicate_meta_descriptions(pages: Sequence[CrawledPage]) -> list[Finding]:
    return _find_duplicates(
        _content_pages(pages), field_getter=lambda p: p.meta_description,
        issue_type=DUPLICATE_META_DESCRIPTION, label="meta description",
    )


def find_missing_canonical(pages: Sequence[CrawledPage]) -> list[Finding]:
    return [
        Finding(MISSING_CANONICAL, p.url, "no canonical tag found on this page")
        for p in _content_pages(pages)
        if not _clean(p.canonical)
    ]


# -- structured data (reuses pricing_intel's extract_product_metadata) ------


def _malformed_offer_reasons(offers: tuple[dict, ...], *, source: str) -> list[str]:
    """An Offer node that exists but is missing the load-bearing ``price``
    field reads as malformed/incomplete -- deliberately narrow scope (see
    module docstring): this does not attempt to validate every schema.org
    Offer property, only the one whose absence makes the structured data
    useless to a shopping/search consumer."""
    reasons = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        price = offer.get("price")
        if price is None or (isinstance(price, str) and not price.strip()):
            reasons.append(f"{source} Offer node is missing a 'price' value")
    return reasons


def find_structured_data_issues(pages: Sequence[CrawledPage]) -> list[Finding]:
    """Missing or malformed Product/Offer structured data, via
    ``extract_product_metadata`` (see module docstring for the reuse
    rationale and the schema.org-Product-specific scope of this check)."""
    findings: list[Finding] = []
    for p in _content_pages(pages):
        if not (p.html or "").strip():
            findings.append(Finding(
                MISSING_STRUCTURED_DATA, p.url,
                "no page markup was captured for this URL, so structured data could not be checked",
            ))
            continue

        meta = extract_product_metadata(p.html)
        has_any = bool(meta.json_ld_offers) or bool(meta.microdata_offers) or meta.opengraph_price is not None
        if not has_any:
            findings.append(Finding(
                MISSING_STRUCTURED_DATA, p.url,
                "no JSON-LD/microdata Offer or OpenGraph product price found on this page "
                "(informational if this is not meant to be a product page)",
            ))
            continue

        for reason in _malformed_offer_reasons(meta.json_ld_offers, source="JSON-LD"):
            findings.append(Finding(MALFORMED_STRUCTURED_DATA, p.url, reason))
        for reason in _malformed_offer_reasons(meta.microdata_offers, source="microdata"):
            findings.append(Finding(MALFORMED_STRUCTURED_DATA, p.url, reason))
    return findings


# -- broken internal links ---------------------------------------------------


def find_broken_internal_links(pages: Sequence[CrawledPage]) -> list[Finding]:
    """Flags a page's outgoing internal link when the LINKED page's own
    crawled status is >= 400. A link to a URL that was never captured by
    the crawl at all (out of crawl scope, blocked by robots.txt, ...) is
    NOT flagged here -- this check only reports what it can actually cite
    (Golden Rule 7), it never assumes "not seen" means "broken"."""
    status_by_url = {p.url: p.status_code for p in pages}
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for p in pages:
        for link in p.internal_links:
            target_status = status_by_url.get(link)
            if target_status is None or target_status < 400:
                continue
            key = (p.url, link)
            if key in seen:
                continue
            seen.add(key)
            findings.append(Finding(
                BROKEN_INTERNAL_LINK, p.url, f"links to {link} which returned status {target_status}",
            ))
    return findings


# -- image alt text -----------------------------------------------------------


def find_missing_alt_text(pages: Sequence[CrawledPage]) -> list[Finding]:
    findings: list[Finding] = []
    for p in _content_pages(pages):
        for img in p.images:
            if not img.alt.strip():
                findings.append(Finding(MISSING_ALT_TEXT, p.url, f"image {img.src} has no alt text"))
    return findings


# -- duplicate content ---------------------------------------------------


def _content_signature(html: str) -> str:
    collapsed = " ".join(html.split()).lower()
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()


def find_duplicate_content(pages: Sequence[CrawledPage]) -> list[Finding]:
    """Groups pages whose full-markup content hashes identically (whitespace-
    collapsed, case-insensitive) -- an exact-duplicate signal, not a
    near-duplicate/similarity model (that would need a real content-
    similarity library, out of this PR's scope; this module states its
    heuristic plainly rather than oversell precision it doesn't have)."""
    groups: dict[str, list[str]] = defaultdict(list)
    for p in _content_pages(pages):
        html = p.html or ""
        if len(" ".join(html.split())) < _MIN_CONTENT_LENGTH_FOR_DEDUP:
            continue
        groups[_content_signature(html)].append(p.url)

    findings: list[Finding] = []
    for signature, urls in sorted(groups.items()):
        if len(urls) > 1:
            for u in sorted(urls):
                findings.append(Finding(
                    DUPLICATE_CONTENT, u,
                    f"identical page content (hash {signature[:12]}) shared by {len(urls)} URLs",
                ))
    return findings


# -- robots.txt / sitemap.xml presence + basic sanity ------------------------


def _find_page(pages: Sequence[CrawledPage], *, suffix: str) -> CrawledPage | None:
    for p in pages:
        if p.url.rstrip("/").lower().endswith(suffix):
            return p
    return None


def check_robots_txt(pages: Sequence[CrawledPage]) -> RobotsTxtCheck:
    page = _find_page(pages, suffix="/robots.txt")
    if page is None:
        return RobotsTxtCheck(False, None, False, "robots.txt was not fetched during the crawl")
    if page.status_code != 200:
        return RobotsTxtCheck(False, page.status_code, False, f"robots.txt returned status {page.status_code}")
    looks_sane = "user-agent" in (page.html or "").lower()
    detail = (
        "robots.txt present and contains a 'User-agent' directive" if looks_sane else
        "robots.txt present (status 200) but no 'User-agent' directive found "
        "(best-effort text check, not a full robots.txt parser)"
    )
    return RobotsTxtCheck(True, page.status_code, looks_sane, detail)


def check_sitemap_xml(pages: Sequence[CrawledPage]) -> SitemapCheck:
    page = _find_page(pages, suffix="sitemap.xml")
    if page is None:
        return SitemapCheck(False, None, False, "sitemap.xml was not fetched during the crawl")
    if page.status_code != 200:
        return SitemapCheck(False, page.status_code, False, f"sitemap.xml returned status {page.status_code}")
    content_type = (page.content_type or "").lower()
    body = (page.html or "").lower()
    looks_sane = "xml" in content_type or "<urlset" in body or "<sitemapindex" in body
    detail = (
        "sitemap.xml present (status 200), content-type/body indicates XML" if looks_sane else
        "sitemap.xml present (status 200) but content-type/body did not look like XML "
        "(best-effort check, not a full XML validator)"
    )
    return SitemapCheck(True, page.status_code, looks_sane, detail)


def robots_and_sitemap_findings(robots: RobotsTxtCheck, sitemap: SitemapCheck) -> list[Finding]:
    """Turns the two site-level checks above into :class:`Finding` rows so
    ``jobs/seo_audit.py`` can rank them alongside every per-page finding."""
    findings: list[Finding] = []
    if not robots.present:
        findings.append(Finding(ROBOTS_TXT_MISSING, "/robots.txt", robots.detail))
    elif not robots.looks_sane:
        findings.append(Finding(ROBOTS_TXT_SUSPICIOUS, "/robots.txt", robots.detail))
    if not sitemap.present:
        findings.append(Finding(SITEMAP_MISSING, "/sitemap.xml", sitemap.detail))
    elif not sitemap.looks_sane:
        findings.append(Finding(SITEMAP_SUSPICIOUS, "/sitemap.xml", sitemap.detail))
    return findings


# -- run every check ----------------------------------------------------------


def run_checks(pages: Sequence[CrawledPage]) -> tuple[Finding, ...]:
    """Every check in this module, concatenated into one flat, UNCAPPED list
    (Golden Rule 14) -- ``jobs/seo_audit.py`` groups and ranks a top-N VIEW
    of this; nothing here is ever dropped."""
    findings: list[Finding] = []
    findings += find_missing_titles(pages)
    findings += find_duplicate_titles(pages)
    findings += find_missing_meta_descriptions(pages)
    findings += find_duplicate_meta_descriptions(pages)
    findings += find_missing_canonical(pages)
    findings += find_structured_data_issues(pages)
    findings += find_broken_internal_links(pages)
    findings += find_missing_alt_text(pages)
    findings += find_duplicate_content(pages)
    findings += robots_and_sitemap_findings(check_robots_txt(pages), check_sitemap_xml(pages))
    return tuple(findings)

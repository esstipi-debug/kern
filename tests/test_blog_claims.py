"""Content gate over every published article: the section 10 denylist and the
promise that a cited code path actually exists.

This is the test that keeps a future article from shipping a claim nobody can
check. It reads the markdown on disk rather than the rendered HTML, so it fails
in CI at the moment the file lands, not after a deploy.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from webapp.blog_loader import BLOG_DIR, load_posts

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTS = load_posts()

#: documentation/KERN_BRAND_PRODUCT_BIBLE.md section 10 + CHECKLIST.md, as
#: literal substrings. Case-insensitive. Keep this list in sync with the bible;
#: it is the machine half of a rule that is otherwise only prose.
DENYLIST = (
    "ia de ultima generacion", "ia de última generación",
    "state-of-the-art ai", "state of the art ai",
    "ia que predice el futuro", "ai that predicts the future", "predicts the future",
    "autonomia total", "autonomía total", "fully autonomous",
    "sin intervencion humana", "sin intervención humana", "no human in the loop",
    "integramos tu inventario de mercado libre",
    "integracion con shopify", "integración con shopify", "shopify integration",
    "amazon integration", "integracion con amazon", "integración con amazon",
    "panel de control para tu ceo", "ceo dashboard",
    "reemplaza a tu equipo de analistas", "replaces your analysts",
)

#: An autonomy percentage is banned outright: the figures were never re-audited.
AUTONOMY_PCT = re.compile(r"\b\d{1,3}\s?%\s*(de\s+)?(autonom|autonomous)", re.I)

#: "3 of 4 runs" style frequency claims. The only allowed wording is that three
#: of the four possible endings stop on a human *by design*.
FREQUENCY = re.compile(r"\b3\s*(of|de)\s*4\s*(runs|veces|corridas)", re.I)


@pytest.mark.parametrize("post", POSTS, ids=lambda p: f"{p.lang}-{p.slug}")
def test_no_denylisted_claim(post) -> None:
    haystack = post.path.read_text(encoding="utf-8").lower()
    hits = [phrase for phrase in DENYLIST if phrase in haystack]
    assert not hits, f"{post.path.name} contains banned claims: {hits}"


@pytest.mark.parametrize("post", POSTS, ids=lambda p: f"{p.lang}-{p.slug}")
def test_no_autonomy_percentage_or_frequency(post) -> None:
    text = post.path.read_text(encoding="utf-8")
    assert not AUTONOMY_PCT.search(text), f"{post.path.name}: autonomy percentage"
    assert not FREQUENCY.search(text), f"{post.path.name}: unmeasured frequency claim"


@pytest.mark.parametrize("post", POSTS, ids=lambda p: f"{p.lang}-{p.slug}")
def test_every_code_source_exists(post) -> None:
    """`sources.kind: code` is a promise that the reader can open the file."""
    for src in post.sources:
        if src["kind"] != "code":
            continue
        assert (REPO_ROOT / src["ref"]).exists(), (
            f"{post.path.name} cites {src['ref']!r}, which is not in the repo"
        )


@pytest.mark.parametrize("post", POSTS, ids=lambda p: f"{p.lang}-{p.slug}")
def test_has_at_least_one_code_source(post) -> None:
    assert any(s["kind"] == "code" for s in post.sources), (
        f"{post.path.name} has no kind: code source"
    )


@pytest.mark.parametrize("post", POSTS, ids=lambda p: f"{p.lang}-{p.slug}")
def test_answer_is_the_first_body_paragraph(post) -> None:
    """Crawlers cite the answer block, so the rendered first paragraph has to be
    it, not a lede the writer added above it."""
    body = re.sub(r"\A\s*#\s+.*?(\n|$)", "", post.body_md, count=1)
    first = next(line.strip() for line in body.split("\n") if line.strip())
    assert " ".join(first.split()) == post.answer


@pytest.mark.parametrize("post", POSTS, ids=lambda p: f"{p.lang}-{p.slug}")
def test_answer_length(post) -> None:
    n = len(post.answer.split())
    assert 40 <= n <= 80, f"{post.path.name}: answer is {n} words, contract is 40-80"


@pytest.mark.parametrize("post", POSTS, ids=lambda p: f"{p.lang}-{p.slug}")
def test_description_length(post) -> None:
    assert len(post.description) <= 160, (
        f"{post.path.name}: description is {len(post.description)} chars, max 160"
    )


@pytest.mark.parametrize("post", POSTS, ids=lambda p: f"{p.lang}-{p.slug}")
def test_body_faq_matches_frontmatter(post) -> None:
    headings = re.findall(r"^### (.+)$", post.body_md, re.M)
    assert headings == [f.q for f in post.faq], (
        f"{post.path.name}: body FAQ headings do not match the frontmatter faq list"
    )


def test_every_pair_has_both_languages_except_stocky() -> None:
    by_pair: dict[str, set[str]] = {}
    for p in POSTS:
        by_pair.setdefault(p.pair, set()).add(p.lang)
    for pair, langs in by_pair.items():
        if pair == "stocky-alt":
            assert langs == {"es"}, "stocky-alt must be Spanish only: EN is /stocky-alternative"
        else:
            assert langs == {"es", "en"}, f"pair {pair} is missing a language: {langs}"


def test_slugs_are_globally_unique() -> None:
    slugs = [p.slug for p in POSTS]
    assert len(slugs) == len(set(slugs)), "slugs must be globally unique across languages"


def test_briefs_and_templates_are_not_published() -> None:
    """The renderer reads only es/ and en/. If a brief ever lands in one of them
    it would become a public URL."""
    published = {p.path.name for p in POSTS}
    assert not any(n.startswith("TEMPLATE") for n in published)
    assert (BLOG_DIR / "briefs").is_dir()
    assert not any(p.path.parent.name == "briefs" for p in POSTS)

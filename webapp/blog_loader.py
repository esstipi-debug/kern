"""Load the GEO blog posts from ``documentation/blog/{es,en}/`` and render their
markdown to HTML.

Two jobs, both deliberately small:

1. **Load and validate.** Frontmatter is parsed with PyYAML (already core, not an
   optional extra -- ``webapp.app`` must import this module at boot). The loader
   **fails closed**: a file missing a required key, or whose ``slug`` does not
   match its filename, raises rather than shipping a half-built page. The design
   spec is explicit that the index simply omits a file that is not there, but a
   file that *is* there and is malformed is a bug, not a silent skip.

2. **Render a markdown subset.** H1-H3, paragraphs, ul/ol, links, inline code,
   fenced code, blockquote, strong/em and GFM tables -- the set the briefs
   actually produce. Everything is escaped first, so **no raw HTML survives**,
   which is what the spec requires and what keeps an article body from being an
   injection surface. Adding `markdown` to the `[web]` extra was considered and
   rejected in the spec unless this subset proves too thin.

Only ``es/`` and ``en/`` are read. ``briefs/``, ``TEMPLATE.*`` and the ``README``
files are never mounted.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date as _date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from webapp.blog_figures import render_figure

BLOG_DIR = Path(__file__).resolve().parent.parent / "documentation" / "blog"
LANGS = ("es", "en")

#: Frontmatter keys the loader refuses to do without. Mirrors section 6 of
#: docs/superpowers/specs/2026-08-25-blog-geo-i18n-design.md.
REQUIRED_KEYS = (
    "pair", "lang", "slug", "title", "description",
    "date", "updated", "query", "answer", "cta", "offer", "sources", "faq",
)
VALID_CTA = ("demo", "diagnostico", "both")

#: The one pair whose English half is not a /blog/ URL: it already ships as its
#: own landing page and owns the "stocky alternative" query.
STOCKY_EN_PATH = "/stocky-alternative"


class BlogPostError(ValueError):
    """A post on disk is malformed. Raised at load time, never swallowed."""


@dataclass(frozen=True)
class FaqItem:
    q: str
    a: str


@dataclass(frozen=True)
class BlogPost:
    pair: str
    lang: str
    slug: str
    title: str
    description: str
    date: str
    updated: str
    query: str
    answer: str
    cta: str
    offer: str
    sources: tuple[dict[str, str], ...]
    faq: tuple[FaqItem, ...]
    noindex: bool
    body_md: str
    path: Path

    @property
    def url_path(self) -> str:
        return f"/blog/{self.slug}"


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

_FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", re.S)


def _split_frontmatter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    m = _FM_RE.match(text)
    if not m:
        raise BlogPostError(f"{path.name}: no YAML frontmatter delimited by ---")
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise BlogPostError(f"{path.name}: frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise BlogPostError(f"{path.name}: frontmatter is not a mapping")
    return meta, m.group(2)


def _as_str(value: Any) -> str:
    # PyYAML turns an unquoted 2026-08-25 into a datetime.date; the contract
    # wants the ISO string, and reformatting here keeps every consumer (JSON-LD,
    # sitemap, llms.txt) from having to care which form the author used.
    if isinstance(value, _date):
        return value.isoformat()
    return str(value).strip()


def _parse_post(path: Path, lang: str) -> BlogPost:
    meta, body = _split_frontmatter(path.read_text(encoding="utf-8"), path)

    missing = [k for k in REQUIRED_KEYS if k not in meta or meta[k] in (None, "")]
    if missing:
        raise BlogPostError(f"{path.name}: missing frontmatter keys: {', '.join(missing)}")

    slug = _as_str(meta["slug"])
    if slug != path.stem:
        raise BlogPostError(f"{path.name}: slug {slug!r} does not match filename")
    if _as_str(meta["lang"]) != lang:
        raise BlogPostError(f"{path.name}: lang {meta['lang']!r} does not match folder {lang!r}")

    cta = _as_str(meta["cta"])
    if cta not in VALID_CTA:
        raise BlogPostError(f"{path.name}: cta {cta!r} not in {VALID_CTA}")

    raw_faq = meta["faq"]
    if not isinstance(raw_faq, list) or len(raw_faq) < 3:
        raise BlogPostError(f"{path.name}: faq must be a list of at least 3 items")
    faq = tuple(FaqItem(q=_as_str(i["q"]), a=_as_str(i["a"])) for i in raw_faq)

    raw_sources = meta["sources"]
    if not isinstance(raw_sources, list) or not raw_sources:
        raise BlogPostError(f"{path.name}: sources must be a non-empty list")
    sources = tuple({"kind": _as_str(s.get("kind", "")), "ref": _as_str(s.get("ref", ""))} for s in raw_sources)

    return BlogPost(
        pair=_as_str(meta["pair"]), lang=lang, slug=slug,
        title=_as_str(meta["title"]), description=_as_str(meta["description"]),
        date=_as_str(meta["date"]), updated=_as_str(meta["updated"]),
        query=_as_str(meta["query"]), answer=" ".join(_as_str(meta["answer"]).split()),
        cta=cta, offer=_as_str(meta["offer"]),
        sources=sources, faq=faq,
        noindex=bool(meta.get("noindex", False)),
        body_md=body, path=path,
    )


@lru_cache(maxsize=1)
def load_posts() -> tuple[BlogPost, ...]:
    """Every post under ``es/`` and ``en/``, sorted by date descending then slug.

    Cached: the files ship inside the image and cannot change under a running
    process. Tests that write fixtures call ``load_posts.cache_clear()``.
    """
    posts: list[BlogPost] = []
    for lang in LANGS:
        folder = BLOG_DIR / lang
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            if path.name == "README.md":
                continue
            posts.append(_parse_post(path, lang))
    posts.sort(key=lambda p: (p.date, p.slug), reverse=True)
    return tuple(posts)


def posts_for_lang(lang: str) -> tuple[BlogPost, ...]:
    return tuple(p for p in load_posts() if p.lang == lang)


def post_by_slug(slug: str) -> BlogPost | None:
    """Look a post up by slug. The slug is compared against the loaded set, so a
    traversal attempt like ``../../secrets`` simply does not match anything --
    the filesystem is never consulted with caller-supplied text."""
    for p in load_posts():
        if p.slug == slug:
            return p
    return None


def sibling(post: BlogPost) -> BlogPost | None:
    """The other language of the same ``pair``, when it exists on disk."""
    for p in load_posts():
        if p.pair == post.pair and p.lang != post.lang:
            return p
    return None


# --------------------------------------------------------------------------- #
# Markdown subset -> HTML
# --------------------------------------------------------------------------- #

_LINK_RE = re.compile(r"\[([^\]]+)\]\((/[^)\s]*)\)")
_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITAL_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")


def _inline(text: str) -> str:
    """Escape first, then re-introduce the handful of inline forms we allow.

    Order matters: code spans are protected before emphasis so that a `*` inside
    backticks is not read as italics. Links are restricted to **site-relative**
    hrefs starting with ``/`` -- WRITER.md requires relative links, and refusing
    anything else here means a stray absolute URL cannot smuggle a scheme in.
    """
    out = html.escape(text, quote=False)

    spans: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        spans.append(m.group(1))
        return f"\x00{len(spans) - 1}\x00"

    out = _CODE_RE.sub(_stash, out)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = _ITAL_RE.sub(r"<em>\1</em>", out)
    out = _LINK_RE.sub(r'<a href="\2">\1</a>', out)

    for i, code in enumerate(spans):
        out = out.replace(f"\x00{i}\x00", f"<code>{code}</code>")
    return out


def _table(rows: list[str]) -> str:
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    head, body = cells[0], cells[2:]  # cells[1] is the --- separator
    out = ['<div class="table-wrap"><table><thead><tr>']
    out += [f"<th>{_inline(c)}</th>" for c in head]
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def render_markdown(md: str, lang: str = "en") -> str:
    """Render the subset. Anything not recognised becomes a paragraph, escaped.

    One fence is special: ``kern-figure`` holds JSON that becomes an inline SVG
    diagram. It is still not raw HTML -- the payload is parsed as data and the
    markup is authored by ``webapp.blog_figures``, so an article can ask for a
    chart without being able to inject a tag.
    """
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    list_items: list[str] = []
    list_tag: str | None = None
    table_rows: list[str] = []
    i = 0

    def flush_para() -> None:
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if list_items:
            items = "".join(f"<li>{_inline(x)}</li>" for x in list_items)
            out.append(f"<{list_tag}>{items}</{list_tag}>")
            list_items.clear()
            list_tag = None

    def flush_table() -> None:
        if table_rows:
            out.append(_table(table_rows))
            table_rows.clear()

    def flush_all() -> None:
        flush_para(); flush_list(); flush_table()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_all()
            info = stripped[3:].strip().lower()
            i += 1
            block: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            payload = "\n".join(block)
            if info == "kern-figure":
                out.append(render_figure(payload, lang))
            else:
                out.append("<pre><code>" + html.escape(payload, quote=False) + "</code></pre>")
            continue

        if not stripped:
            flush_all()
            i += 1
            continue

        m = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if m:
            flush_all()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            flush_para(); flush_list()
            table_rows.append(stripped)
            i += 1
            continue
        flush_table()

        if stripped.startswith("> "):
            flush_para(); flush_list()
            quote: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(f"<blockquote><p>{_inline(' '.join(quote))}</p></blockquote>")
            continue

        m = re.match(r"^[-*]\s+(.*)$", stripped)
        if m:
            flush_para()
            if list_tag not in (None, "ul"):
                flush_list()
            list_tag = "ul"
            list_items.append(m.group(1))
            i += 1
            continue

        m = re.match(r"^\d+\.\s+(.*)$", stripped)
        if m:
            flush_para()
            if list_tag not in (None, "ol"):
                flush_list()
            list_tag = "ol"
            list_items.append(m.group(1))
            i += 1
            continue

        flush_list()
        para.append(stripped)
        i += 1

    flush_all()
    return "\n".join(out)


def body_without_h1(post: BlogPost) -> str:
    """The article body with its leading H1 dropped: the page shell prints the
    title itself, and two H1s on one document is the kind of thing a GEO page
    cannot afford."""
    return re.sub(r"\A\s*#\s+.*?(\n|$)", "", post.body_md, count=1)

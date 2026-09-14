"""Server-side rendering for the GEO blog: the two indexes and the article page.

Same visual shell as ``webapp/stocky_alternative_page.py`` (Inter + JetBrains
Mono, ``--ink``/``--accent``, teal) so the blog does not read as a bolted-on
subsite.

Three decisions worth stating because they are easy to get wrong later:

- **JSON-LD is authored here, not via ``src/seo/schema_gen.py``.** That module
  authors Product/Offer for client catalogs; an article is ``Article`` +
  ``FAQPage`` + ``BreadcrumbList``, and reusing the catalog generator would emit
  Product markup for a blog post.
- **Canonical is per-language and never cross-language.** ES canonicalises to its
  own ES path, EN to its own. The pair is joined by ``hreflang``, not by
  collapsing one into the other; ``x-default`` is English because the AI crawlers
  and the two highest-intent landings are English.
- **No price is printed here.** ``webapp.offers`` owns prices, and the CTA links
  to the offer's own page rather than baking a figure into eleven articles that
  would then need redeploying every time a price moves.
"""
from __future__ import annotations

import json
import os
from html import escape

from webapp.blog_figures import FIGURE_CSS, render_figure
from webapp.blog_loader import (
    STOCKY_EN_PATH,
    BlogPost,
    body_without_h1,
    render_markdown,
    sibling,
)

DEFAULT_BASE_URL = "https://linchpin.fly.dev"

_STR = {
    "es": {
        "kicker": "Blog", "index_title": "Blog — Kern",
        "index_lede": "Metodo de inventario explicado contra el codigo que lo corre. Sin precios, sin casos de exito inventados.",
        "read": "Leer", "other": "English", "answers": "Responde a",
        "cta_demo": "Diagnóstico gratis", "cta_diag": "Diagnostico de Arranque",
        "updated": "Actualizado", "sources": "Fuentes", "faq": "Preguntas frecuentes",
    },
    "en": {
        "kicker": "Blog", "index_title": "Blog — Kern",
        "index_lede": "Inventory method explained against the code that runs it. No prices, no invented case studies.",
        "read": "Read", "other": "Espanol", "answers": "Answers",
        "cta_demo": "Free diagnostic", "cta_diag": "Start-up Diagnostic",
        "updated": "Updated", "sources": "Sources", "faq": "FAQ",
    },
}

_CSS = """
  :root{--ink:#080b11;--panel:#111722;--line:#1e2733;--txt:#e7eef6;--txt-2:#c4cfdb;
    --muted:#9aa7b6;--faint:#5e6b7a;--accent:#4fd1c5;--accent-bright:#5eead4;
    --accent-soft:rgba(79,209,197,.14);--accent-bd:rgba(79,209,197,.45);
    --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;--r:13px}
  *{box-sizing:border-box}
  body{margin:0;background:var(--ink);color:var(--txt);font-family:var(--sans);font-size:16px;line-height:1.65;-webkit-font-smoothing:antialiased;
    background-image:radial-gradient(1100px 520px at 10% -8%,rgba(79,209,197,.09),transparent 60%)}
  a{color:var(--accent-bright);text-decoration:none}
  a:hover{text-decoration:underline}
  .wrap{max-width:760px;margin:0 auto;padding:0 22px}
  header{border-bottom:1px solid var(--line);background:rgba(8,11,17,.7);backdrop-filter:blur(10px)}
  header .wrap{display:flex;align-items:center;justify-content:space-between;height:60px;max-width:1080px}
  .brand{display:flex;align-items:center;gap:9px;font:700 17px/1 var(--mono)}
  .brand .d{color:var(--accent)}
  header nav{display:flex;gap:18px;align-items:center;font-size:14px;color:var(--txt-2)}
  header nav a{color:var(--txt-2)}
  main{padding:48px 0 72px}
  .kicker{font:600 12px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin-bottom:14px}
  h1{font-size:clamp(28px,4.4vw,42px);line-height:1.15;letter-spacing:-.02em;margin:0 0 18px}
  h2{font-size:24px;line-height:1.25;letter-spacing:-.01em;margin:38px 0 12px}
  h3{font-size:18px;margin:26px 0 8px}
  .lede{color:var(--txt-2);font-size:18px;margin:0 0 26px}
  .answer{border-left:2px solid var(--accent-bd);background:var(--accent-soft);
    padding:14px 18px;border-radius:0 var(--r) var(--r) 0;margin:0 0 28px;color:var(--txt)}
  .meta{font:500 13px/1.5 var(--mono);color:var(--faint);margin:0 0 30px}
  .meta a{color:var(--muted)}
  article p{margin:0 0 16px}
  article ul,article ol{margin:0 0 16px;padding-left:22px}
  article li{margin:0 0 7px}
  code{font-family:var(--mono);font-size:.9em;background:var(--panel);border:1px solid var(--line);
    border-radius:6px;padding:1px 5px}
  pre{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:14px 16px;overflow-x:auto}
  pre code{background:none;border:0;padding:0;font-size:13px;line-height:1.5}
  blockquote{margin:0 0 18px;padding:2px 0 2px 16px;border-left:2px solid var(--line-2,#283341);color:var(--txt-2)}
  table{width:100%;border-collapse:collapse;margin:0 0 20px;font-size:14px;display:block;overflow-x:auto}
  th,td{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
  th{background:var(--panel);font-weight:600;color:var(--txt-2)}
  .cta{margin:44px 0 0;padding:22px;border:1px solid var(--accent-bd);border-radius:var(--r);background:var(--accent-soft)}
  .cta a.btn{display:inline-block;margin:8px 10px 0 0;padding:11px 18px;border-radius:9px;
    background:var(--accent);color:#04231f;font-weight:700;font-size:14px}
  .cta a.btn.ghost{background:transparent;border:1px solid var(--accent-bd);color:var(--accent-bright)}
  .srcs{margin:34px 0 0;font:500 13px/1.7 var(--mono);color:var(--faint)}
  .srcs code{font-size:12px}
  ul.posts{list-style:none;padding:0;margin:0}
  ul.posts li{border-top:1px solid var(--line);padding:20px 0}
  ul.posts h2{margin:0 0 6px;font-size:20px}
  ul.posts p{margin:0 0 6px;color:var(--txt-2);font-size:15px}
  ul.posts .q{font:500 12px/1.5 var(--mono);color:var(--faint)}
  footer{border-top:1px solid var(--line);padding:26px 0;color:var(--faint);font-size:13px}
"""


def public_base_url() -> str:
    return (os.environ.get("PUBLIC_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _nav(lang: str) -> str:
    if lang == "en":
        return (
            '<nav><a href="/blog/en">Blog</a><a href="/demo/en">Diagnostic</a>'
            '<a href="/paquetes">Packages</a><a href="/stocky-alternative">Stocky</a></nav>'
        )
    return (
        '<nav><a href="/blog">Blog</a><a href="/demo">Diagnóstico</a>'
        '<a href="/paquetes">Paquetes</a><a href="/stocky-alternative">Stocky</a></nav>'
    )


def _shell(*, lang: str, title: str, description: str, head_extra: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<meta name="description" content="{escape(description)}">
<link rel="icon" href="data:,">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
{head_extra}
<style>{_CSS}{FIGURE_CSS}</style>
</head>
<body>
<header><div class="wrap"><a class="brand" href="/">kern<span class="d">.</span></a>{_nav(lang)}</div></header>
<main><div class="wrap">{body}</div></main>
<footer><div class="wrap">Kern — inventory method, cited to the code that runs it.</div></footer>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# hreflang / canonical
# --------------------------------------------------------------------------- #

def hreflang_hrefs(post: BlogPost) -> dict[str, str]:
    """``{"es": url, "en": url}`` for the pair, absolute.

    ``stocky-alt`` is the documented exception: its English half is the existing
    ``/stocky-alternative`` landing, not a ``/blog/`` URL.
    """
    base = public_base_url()
    sib = sibling(post)
    hrefs: dict[str, str] = {post.lang: f"{base}{post.url_path}"}
    if sib is not None:
        hrefs[sib.lang] = f"{base}{sib.url_path}"
    elif post.pair == "stocky-alt" and post.lang == "es":
        hrefs["en"] = f"{base}{STOCKY_EN_PATH}"
    return hrefs


def _alternate_links(post: BlogPost) -> str:
    hrefs = hreflang_hrefs(post)
    out = [f'<link rel="canonical" href="{escape(hrefs[post.lang], quote=True)}">']
    for lang in ("es", "en"):
        if lang in hrefs:
            out.append(f'<link rel="alternate" hreflang="{lang}" href="{escape(hrefs[lang], quote=True)}">')
    if "en" in hrefs:
        out.append(f'<link rel="alternate" hreflang="x-default" href="{escape(hrefs["en"], quote=True)}">')
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# JSON-LD
# --------------------------------------------------------------------------- #

def article_jsonld(post: BlogPost) -> str:
    base = public_base_url()
    url = f"{base}{post.url_path}"
    graph = [
        {
            "@type": "Article",
            "headline": post.title,
            "description": post.description,
            "inLanguage": post.lang,
            "datePublished": post.date,
            "dateModified": post.updated,
            "mainEntityOfPage": {"@type": "WebPage", "@id": url},
            "author": {"@type": "Organization", "name": "Kern"},
            "publisher": {"@type": "Organization", "name": "Kern"},
        },
        {
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": f.q,
                 "acceptedAnswer": {"@type": "Answer", "text": f.a}}
                for f in post.faq
            ],
        },
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Blog",
                 "item": f"{base}/blog" if post.lang == "es" else f"{base}/blog/en"},
                {"@type": "ListItem", "position": 2, "name": post.title, "item": url},
            ],
        },
    ]
    payload = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)
    # No </script> can appear inside a JSON string literal here, but escape the
    # sequence anyway: it is the one thing that would break out of the block.
    return payload.replace("</", "<\\/")


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #

def _cta_block(post: BlogPost) -> str:
    s = _STR[post.lang]
    demo = f'<a class="btn" href="/demo?source=blog-{escape(post.slug, quote=True)}">{s["cta_demo"]}</a>'
    diag = f'<a class="btn ghost" href="/paquetes/{escape(post.offer, quote=True)}">{s["cta_diag"]}</a>'
    if post.cta == "demo":
        return f'<div class="cta">{demo}</div>'
    if post.cta == "diagnostico":
        return f'<div class="cta">{diag}</div>'
    return f'<div class="cta">{demo}{diag}</div>'


def _sources_block(post: BlogPost) -> str:
    s = _STR[post.lang]
    items = " · ".join(f"<code>{escape(x['ref'])}</code>" for x in post.sources)
    return f'<p class="srcs">{s["sources"]}: {items}</p>'


def _index_icons(lang: str) -> str:
    """A quiet warehouse strip under the lede. The blog is a wall of prose and
    nothing on the index said "inventory" at a glance; six stroked glyphs on one
    grid fix that without pretending to be data."""
    caption = ("Lo que estas paginas miden, y de donde sale cada numero."
               if lang == "es" else
               "What these pages measure, and where each number comes from.")
    return render_figure(json.dumps({"type": "icons", "label": caption}), lang)

def render_blog_article_html(post: BlogPost) -> str:
    s = _STR[post.lang]
    hrefs = hreflang_hrefs(post)
    other = "en" if post.lang == "es" else "es"
    other_link = (
        f' · <a href="{escape(hrefs[other].replace(public_base_url(), ""), quote=True)}">{s["other"]}</a>'
        if other in hrefs else ""
    )
    head = (
        _alternate_links(post)
        + f'\n<script type="application/ld+json">{article_jsonld(post)}</script>'
        + ('\n<meta name="robots" content="noindex">' if post.noindex else "")
    )
    body = (
        f'<div class="kicker">{s["kicker"]}</div>'
        f"<h1>{escape(post.title)}</h1>"
        f'<div class="answer">{escape(post.answer)}</div>'
        f'<p class="meta">{s["answers"]}: “{escape(post.query)}” · '
        f'{s["updated"]} {escape(post.updated)}{other_link}</p>'
        f"<article>{render_markdown(body_without_h1(post), post.lang)}</article>"
        f"{_cta_block(post)}"
        f"{_sources_block(post)}"
    )
    return _shell(lang=post.lang, title=f"{post.title} | Kern",
                  description=post.description, head_extra=head, body=body)


def render_blog_index_html(lang: str, posts: tuple[BlogPost, ...]) -> str:
    s = _STR[lang]
    base = public_base_url()
    other_path = "/blog/en" if lang == "es" else "/blog"
    head = (
        f'<link rel="canonical" href="{base}{"/blog" if lang == "es" else "/blog/en"}">\n'
        f'<link rel="alternate" hreflang="es" href="{base}/blog">\n'
        f'<link rel="alternate" hreflang="en" href="{base}/blog/en">\n'
        f'<link rel="alternate" hreflang="x-default" href="{base}/blog/en">'
    )
    items = []
    for p in posts:
        if p.noindex:
            continue
        items.append(
            f'<li><h2><a href="{p.url_path}">{escape(p.title)}</a></h2>'
            f"<p>{escape(p.description)}</p>"
            f'<div class="q">{s["answers"]}: “{escape(p.query)}”</div></li>'
        )
    body = (
        f'<div class="kicker">{s["kicker"]}</div>'
        f"<h1>{escape(s['index_title'].split(' — ')[0])}</h1>"
        f'<p class="lede">{escape(s["index_lede"])}</p>'
        f'<p class="meta"><a href="{other_path}">{s["other"]}</a></p>'
        f"{_index_icons(lang)}"
        f'<ul class="posts">{"".join(items)}</ul>'
    )
    return _shell(lang=lang, title=s["index_title"], description=s["index_lede"],
                  head_extra=head, body=body)

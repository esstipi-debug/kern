"""Server-side rendering for the GEO blog: the two indexes and the article page.

Same visual system as ``webapp/stocky_alternative_page.py`` (Inter + JetBrains
Mono, ``--ink``/``--accent``, teal) so the blog does not read as a bolted-on
subsite. The index is a card list; the article is a reading column with a
labelled answer, a warm CTA that matches /demo, and a language switch in the
header -- not a wall of unstyled prose.

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
        "kicker": "Blog",
        "index_title": "Blog — Kern",
        "index_h1": "Inventario, explicado",
        "index_lede": (
            "Método de inventario explicado contra el código que lo corre. "
            "Sin precios, sin casos de éxito inventados."
        ),
        "read": "Leer",
        "other": "English",
        "answers": "Responde a",
        "cta_kicker": "Siguiente paso",
        "cta_demo": "Escanea tu CSV",
        "cta_diag": "Diagnóstico de Arranque",
        "cta_demo_copy": "El mismo análisis, contra tu export. Nada se escribe en tu ERP.",
        "cta_diag_copy": "Un sprint de dos semanas sobre tus números, no un dashboard nuevo.",
        "cta_both_copy": (
            "Escanea primero. Si el ranking vale una reunión, el Diagnóstico es el paso pago."
        ),
        "updated": "Actualizado",
        "sources": "Fuentes",
        "faq": "Preguntas frecuentes",
        "answer_label": "La respuesta",
        "footer": "Kern — método de inventario, citado contra el código que lo corre.",
        "lang_label": "Idioma",
        "skip": "Saltar al contenido",
    },
    "en": {
        "kicker": "Blog",
        "index_title": "Blog — Kern",
        "index_h1": "Inventory, explained",
        "index_lede": (
            "Inventory method explained against the code that runs it. "
            "No prices, no invented case studies."
        ),
        "read": "Read",
        "other": "Español",
        "answers": "Answers",
        "cta_kicker": "Next step",
        "cta_demo": "Scan your CSV",
        "cta_diag": "Start-up Diagnostic",
        "cta_demo_copy": "The same analysis, against your export. Nothing writes back to your ERP.",
        "cta_diag_copy": "A two-week sprint on your numbers, not another dashboard.",
        "cta_both_copy": (
            "Scan first. If the ranking is worth a meeting, the Diagnostic is the paid step."
        ),
        "updated": "Updated",
        "sources": "Sources",
        "faq": "FAQ",
        "answer_label": "The answer",
        "footer": "Kern — inventory method, cited to the code that runs it.",
        "lang_label": "Language",
        "skip": "Skip to content",
    },
}

_CSS = """
  :root{--ink:#080b11;--panel:#111722;--panel-2:#0f141d;--line:#1e2733;--line-2:#283341;
    --txt:#e7eef6;--txt-2:#c4cfdb;--muted:#9aa7b6;--faint:#5e6b7a;
    --accent:#4fd1c5;--accent-bright:#5eead4;--accent-soft:rgba(79,209,197,.14);
    --accent-bd:rgba(79,209,197,.45);
    --accent-warm:#ffb545;--accent-warm-2:#ff8a5c;--accent-warm-deep:#b5601f;
    --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;--r:14px}
  *{box-sizing:border-box}
  html{scroll-behavior:smooth}
  body{margin:0;background:var(--ink);color:var(--txt);font-family:var(--sans);font-size:16px;line-height:1.65;
    -webkit-font-smoothing:antialiased;
    background-image:radial-gradient(1100px 520px at 10% -8%,rgba(79,209,197,.10),transparent 60%),
      radial-gradient(900px 620px at 110% 0%,rgba(120,90,255,.07),transparent 55%);
    background-attachment:fixed}
  a{color:var(--accent-bright);text-decoration:none}
  a:hover{text-decoration:underline}
  a:focus-visible,button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  .skip{position:absolute;left:-999px;top:8px;background:var(--accent);color:#04231f;padding:8px 12px;border-radius:8px;z-index:20;font-weight:700}
  .skip:focus{left:8px}
  .wrap{max-width:760px;margin:0 auto;padding:0 22px}
  .wrap.wide{max-width:880px}
  header{position:sticky;top:0;z-index:10;border-bottom:1px solid var(--line);
    background:rgba(8,11,17,.82);backdrop-filter:blur(14px)}
  header .wrap{display:flex;align-items:center;justify-content:space-between;gap:16px;
    height:62px;max-width:1080px}
  .brand{display:flex;align-items:center;gap:9px;font:700 17px/1 var(--mono);color:var(--txt);text-decoration:none}
  .brand:hover{text-decoration:none}
  .brand .d{color:var(--accent)}
  .head-end{display:flex;align-items:center;gap:18px;margin-left:auto}
  header nav.site{display:flex;gap:18px;align-items:center;font-size:14px}
  header nav.site a{color:var(--txt-2);text-decoration:none}
  header nav.site a:hover{color:var(--txt);text-decoration:none}
  header nav.site a[aria-current="page"]{color:var(--accent-bright)}
  .lang{display:inline-flex;border:1px solid var(--line-2);border-radius:999px;overflow:hidden;flex:none}
  .lang a{padding:6px 11px;font:600 11px/1 var(--mono);letter-spacing:.08em;color:var(--muted);text-decoration:none}
  .lang a:hover{color:var(--txt);text-decoration:none;background:var(--panel)}
  .lang a[aria-current="true"]{background:var(--accent-soft);color:var(--accent-bright)}
  main{padding:52px 0 88px}
  .kicker{font:600 12px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin-bottom:14px}
  h1{font-size:clamp(30px,4.6vw,46px);font-weight:800;line-height:1.12;letter-spacing:-.03em;margin:0 0 16px}
  h2{font-size:1.35rem;font-weight:700;line-height:1.25;letter-spacing:-.015em;margin:42px 0 12px}
  h3{font-size:1.05rem;font-weight:700;margin:28px 0 8px}
  .lede{color:var(--txt-2);font-size:1.15rem;margin:0 0 28px;max-width:42em}
  .crumb{display:flex;gap:8px;align-items:center;font:500 12.5px/1 var(--mono);color:var(--faint);margin:0 0 22px;flex-wrap:wrap}
  .crumb a{color:var(--muted)}
  .crumb span.sep{opacity:.5}
  .crumb .now{color:var(--txt-2);max-width:42ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .answer{border:1px solid var(--accent-bd);background:linear-gradient(180deg,var(--accent-soft),rgba(79,209,197,.05));
    padding:18px 20px 16px;border-radius:var(--r);margin:0 0 22px;color:var(--txt)}
  .answer .answer-k{font:600 11px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--accent-bright);margin:0 0 10px}
  .answer p{margin:0;font-size:1.02rem;line-height:1.6}
  .meta{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:0 0 34px}
  .chip{display:inline-flex;align-items:center;gap:7px;font:500 12px/1.4 var(--mono);color:var(--txt-2);
    background:var(--panel);border:1px solid var(--line-2);border-radius:999px;padding:6px 11px}
  .chip i{width:6px;height:6px;border-radius:50%;background:var(--accent-bright);flex:none}
  article p{margin:0 0 16px;color:var(--txt-2)}
  article p:first-child{color:var(--txt)}
  article ul,article ol{margin:0 0 16px;padding-left:22px;color:var(--txt-2)}
  article li{margin:0 0 7px}
  article strong{color:var(--txt);font-weight:600}
  article a{text-underline-offset:3px}
  code{font-family:var(--mono);font-size:.88em;background:var(--panel);border:1px solid var(--line);
    border-radius:6px;padding:1px 5px;color:var(--accent-bright)}
  pre{background:var(--panel-2);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;overflow-x:auto}
  pre code{background:none;border:0;padding:0;font-size:13px;line-height:1.55;color:var(--txt-2)}
  blockquote{margin:0 0 20px;padding:4px 0 4px 16px;border-left:2px solid var(--accent-bd);color:var(--txt-2)}
  .table-wrap{overflow-x:auto;margin:0 0 22px;border:1px solid var(--line);border-radius:var(--r)}
  table{width:100%;min-width:520px;border-collapse:collapse;font-size:14px;margin:0}
  th,td{border-bottom:1px solid var(--line);padding:10px 12px;text-align:left;vertical-align:top}
  th{background:var(--panel);font-weight:600;color:var(--txt-2);font-size:12.5px}
  tr:last-child td{border-bottom:0}
  .cta{margin:52px 0 0;padding:26px 24px;border:1px solid var(--accent-bd);border-radius:16px;
    background:linear-gradient(165deg,rgba(79,209,197,.12),rgba(15,20,29,.4))}
  .cta .cta-k{font:600 11px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--accent-bright);margin:0 0 8px}
  .cta p{margin:0 0 16px;color:var(--txt-2);max-width:42em}
  .cta-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  a.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;font:600 14px/1 var(--sans);
    padding:12px 20px;border-radius:999px;border:1px solid transparent;text-decoration:none;
    transition:transform 80ms ease-out,box-shadow 80ms ease-out,filter 120ms ease-out}
  a.btn:hover{text-decoration:none;filter:saturate(1.08)}
  a.btn.primary{background:linear-gradient(150deg,var(--accent-warm),var(--accent-warm-2));color:#2a1200;
    box-shadow:0 5px 0 var(--accent-warm-deep)}
  a.btn.primary:active{transform:translateY(5px);box-shadow:0 0 0 var(--accent-warm-deep)}
  a.btn.ghost{background:transparent;color:var(--txt);border-color:var(--line-2)}
  a.btn.ghost:hover{border-color:var(--accent-bd);background:var(--accent-soft)}
  .srcs{margin:36px 0 0;padding:18px 20px;border:1px solid var(--line);border-radius:var(--r);background:var(--panel-2)}
  .srcs .srcs-k{font:600 11px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--faint);margin:0 0 12px}
  .srcs ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
  .srcs li{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;font:500 13px/1.5 var(--mono);color:var(--muted)}
  .srcs .kind{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);
    border:1px solid var(--line-2);border-radius:999px;padding:2px 7px}
  .icon-panel{margin:8px 0 36px}
  .icon-panel .fig{background:var(--panel);padding:20px 16px 14px}
  ul.posts{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:14px}
  ul.posts li{margin:0;padding:0;border:0}
  a.card{display:block;background:var(--panel);border:1px solid var(--line);border-radius:16px;
    padding:22px 22px 18px;text-decoration:none;color:inherit;
    transition:border-color .15s ease, transform .15s ease, background .15s ease}
  a.card:hover{text-decoration:none;border-color:var(--accent-bd);transform:translateY(-2px);background:#141c28}
  a.card .q{display:inline-block;font:500 11.5px/1.4 var(--mono);letter-spacing:.02em;color:var(--accent-bright);
    background:var(--accent-soft);border:1px solid var(--accent-bd);border-radius:999px;padding:5px 10px;margin:0 0 12px}
  a.card h2{margin:0 0 8px;font-size:1.28rem;letter-spacing:-.02em;color:var(--txt)}
  a.card p{margin:0 0 14px;color:var(--txt-2);font-size:.98rem}
  a.card .go{display:flex;justify-content:space-between;align-items:center;gap:12px;
    font:500 12.5px/1 var(--mono);color:var(--muted)}
  a.card .go span.read{color:var(--accent-bright)}
  footer{border-top:1px solid var(--line);padding:28px 0 36px;color:var(--faint);font-size:13px}
  footer .wrap{max-width:1080px;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:center}
  footer nav{display:flex;gap:16px}
  footer nav a{color:var(--muted)}
  @media (max-width:640px){
    header .wrap{height:auto;padding:12px 16px;flex-wrap:wrap}
    .head-end{width:100%;margin-left:0;justify-content:space-between;flex-wrap:wrap}
    header nav.site{gap:14px}
    main{padding:36px 0 64px}
    h1{font-size:clamp(26px,8vw,34px)}
    a.card{padding:18px}
  }
  @media (prefers-reduced-motion: reduce){
    html{scroll-behavior:auto}
    a.card,a.btn{transition:none}
    a.card:hover{transform:none}
  }
"""


def public_base_url() -> str:
    return (os.environ.get("PUBLIC_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _rel(url: str) -> str:
    base = public_base_url()
    if url.startswith(base):
        rest = url[len(base):]
        return rest if rest else "/"
    return url


def _lang_switch(*, current: str, es_href: str, en_href: str) -> str:
    s = _STR[current]
    es_cur = ' aria-current="true"' if current == "es" else ""
    en_cur = ' aria-current="true"' if current == "en" else ""
    return (
        f'<nav class="lang" aria-label="{escape(s["lang_label"])}">'
        f'<a href="{escape(es_href, quote=True)}"{es_cur}>ES</a>'
        f'<a href="{escape(en_href, quote=True)}"{en_cur}>EN</a>'
        "</nav>"
    )


def _nav() -> str:
    return (
        '<nav class="site" aria-label="Kern">'
        '<a href="/blog" aria-current="page">Blog</a>'
        '<a href="/demo">Demo</a>'
        '<a href="/paquetes">Paquetes</a>'
        "</nav>"
    )


def _shell(
    *,
    lang: str,
    title: str,
    description: str,
    head_extra: str,
    body: str,
    lang_switch: str,
    wide: bool = False,
) -> str:
    s = _STR[lang]
    wrap = "wrap wide" if wide else "wrap"
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<meta name="description" content="{escape(description)}">
<meta property="og:title" content="{escape(title)}">
<meta property="og:description" content="{escape(description)}">
<link rel="icon" href="data:,">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
{head_extra}
<style>{_CSS}{FIGURE_CSS}</style>
</head>
<body>
<a class="skip" href="#content">{escape(s["skip"])}</a>
<header><div class="wrap"><a class="brand" href="/"><span class="d">&#9672;</span> Kern</a><div class="head-end">{_nav()}{lang_switch}</div></div></header>
<main id="content"><div class="{wrap}">{body}</div></main>
<footer><div class="wrap"><span>{escape(s["footer"])}</span>
<nav><a href="/blog">Blog</a><a href="/demo">Demo</a><a href="/paquetes">Paquetes</a></nav>
</div></footer>
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
    demo = (
        f'<a class="btn primary" href="/demo?source=blog-{escape(post.slug, quote=True)}">'
        f'{s["cta_demo"]}</a>'
    )
    diag = (
        f'<a class="btn ghost" href="/paquetes/{escape(post.offer, quote=True)}">'
        f'{s["cta_diag"]}</a>'
    )
    if post.cta == "demo":
        copy, buttons = s["cta_demo_copy"], demo
    elif post.cta == "diagnostico":
        copy, buttons = s["cta_diag_copy"], f'<a class="btn primary" href="/paquetes/{escape(post.offer, quote=True)}">{s["cta_diag"]}</a>'
    else:
        copy, buttons = s["cta_both_copy"], demo + diag
    return (
        f'<section class="cta">'
        f'<div class="cta-k">{s["cta_kicker"]}</div>'
        f"<p>{escape(copy)}</p>"
        f'<div class="cta-row">{buttons}</div>'
        "</section>"
    )


def _sources_block(post: BlogPost) -> str:
    s = _STR[post.lang]
    items = []
    for src in post.sources:
        kind = escape(src.get("kind", ""))
        ref = escape(src["ref"])
        items.append(f'<li><span class="kind">{kind}</span><code>{ref}</code></li>')
    return (
        f'<section class="srcs"><div class="srcs-k">{s["sources"]}</div>'
        f'<ul>{"".join(items)}</ul></section>'
    )


def _index_icons(lang: str) -> str:
    """A quiet warehouse strip under the lede. The blog is a wall of prose and
    nothing on the index said "inventory" at a glance; six stroked glyphs on one
    grid fix that without pretending to be data."""
    caption = ("Lo que estas páginas miden, y de dónde sale cada número."
               if lang == "es" else
               "What these pages measure, and where each number comes from.")
    return f'<div class="icon-panel">{render_figure(json.dumps({"type": "icons", "label": caption}), lang)}</div>'


def _article_lang_switch(post: BlogPost) -> str:
    hrefs = hreflang_hrefs(post)
    es_href = _rel(hrefs["es"]) if "es" in hrefs else "/blog"
    en_href = _rel(hrefs["en"]) if "en" in hrefs else "/blog/en"
    return _lang_switch(current=post.lang, es_href=es_href, en_href=en_href)


def _body_after_answer(post: BlogPost) -> str:
    """Drop the leading paragraph when it is the same text as ``answer``.

    The shell already prints that paragraph in the labelled callout. Repeating
    it as the first body paragraph made every article open twice.
    """
    md = body_without_h1(post).lstrip()
    first, sep, rest = md.partition("\n\n")
    if not sep:
        return md
    if " ".join(first.split()) == " ".join(post.answer.split()):
        return rest.lstrip()
    return md


def render_blog_article_html(post: BlogPost) -> str:
    s = _STR[post.lang]
    index_path = "/blog" if post.lang == "es" else "/blog/en"
    head = (
        _alternate_links(post)
        + '\n<meta property="og:type" content="article">'
        + f'\n<script type="application/ld+json">{article_jsonld(post)}</script>'
        + ('\n<meta name="robots" content="noindex">' if post.noindex else "")
    )
    body = (
        f'<nav class="crumb" aria-label="Breadcrumb">'
        f'<a href="{index_path}">Blog</a><span class="sep">/</span>'
        f'<span class="now">{escape(post.title)}</span></nav>'
        f'<div class="kicker">{s["kicker"]}</div>'
        f"<h1>{escape(post.title)}</h1>"
        f'<aside class="answer"><div class="answer-k">{s["answer_label"]}</div>'
        f"<p>{escape(post.answer)}</p></aside>"
        f'<div class="meta">'
        f'<span class="chip"><i></i>{s["answers"]}: {escape(post.query)}</span>'
        f'<span class="chip">{s["updated"]} {escape(post.updated)}</span>'
        f"</div>"
        f"<article>{render_markdown(_body_after_answer(post), post.lang)}</article>"
        f"{_cta_block(post)}"
        f"{_sources_block(post)}"
    )
    return _shell(
        lang=post.lang,
        title=f"{post.title} | Kern",
        description=post.description,
        head_extra=head,
        body=body,
        lang_switch=_article_lang_switch(post),
    )


def render_blog_index_html(lang: str, posts: tuple[BlogPost, ...]) -> str:
    s = _STR[lang]
    base = public_base_url()
    head = (
        f'<link rel="canonical" href="{base}{"/blog" if lang == "es" else "/blog/en"}">\n'
        f'<link rel="alternate" hreflang="es" href="{base}/blog">\n'
        f'<link rel="alternate" hreflang="en" href="{base}/blog/en">\n'
        f'<link rel="alternate" hreflang="x-default" href="{base}/blog/en">\n'
        '<meta property="og:type" content="website">'
    )
    items = []
    for p in posts:
        if p.noindex:
            continue
        items.append(
            f'<li><a class="card" href="{p.url_path}">'
            f'<div class="q">{s["answers"]}: {escape(p.query)}</div>'
            f"<h2>{escape(p.title)}</h2>"
            f"<p>{escape(p.description)}</p>"
            f'<div class="go"><time datetime="{escape(p.updated, quote=True)}">'
            f'{s["updated"]} {escape(p.updated)}</time>'
            f'<span class="read">{s["read"]} &rarr;</span></div></a></li>'
        )
    body = (
        f'<div class="kicker">{s["kicker"]}</div>'
        f"<h1>{escape(s['index_h1'])}</h1>"
        f'<p class="lede">{escape(s["index_lede"])}</p>'
        f"{_index_icons(lang)}"
        f'<ul class="posts">{"".join(items)}</ul>'
    )
    return _shell(
        lang=lang,
        title=s["index_title"],
        description=s["index_lede"],
        head_extra=head,
        body=body,
        lang_switch=_lang_switch(
            current=lang,
            es_href="/blog",
            en_href="/blog/en",
        ),
        wide=True,
    )

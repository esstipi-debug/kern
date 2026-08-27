"""robots.txt, sitemap.xml and the two llms.txt feeds.

Kept out of ``webapp/app.py`` on purpose: the app module is already long and is
edited by everything, so the blog's SEO surface lives here and ``app.py`` only
gains four thin routes.

``generate_llms_txt`` is imported from ``src.seo.llms_txt`` -- the module, not the
``src.seo`` package -- because the package's ``__init__`` reaches for optional
extras and ``webapp.app`` has to import cleanly at boot with only the base deps.
"""
from __future__ import annotations

from xml.sax.saxutils import escape as xml_escape

from src.seo.llms_txt import LlmsPage, SiteInfo, generate_llms_txt
from webapp.blog_loader import BlogPost, load_posts
from webapp.blog_page import public_base_url

#: Crawlers that answer questions rather than only ranking pages. They get an
#: explicit Allow on the blog and the llms feeds, which is the whole point of
#: writing GEO pages: being quotable, not merely indexed.
AI_AGENTS = ("GPTBot", "PerplexityBot", "Google-Extended", "ClaudeBot", "CCBot")

#: Static pages that belong in the sitemap alongside the articles.
STATIC_PATHS = (
    "/", "/blog", "/blog/en", "/demo", "/paquetes",
    "/stocky-alternative", "/one-plan", "/how-it-works",
)


def _indexed(posts: tuple[BlogPost, ...] | None = None) -> list[BlogPost]:
    return [p for p in (posts if posts is not None else load_posts()) if not p.noindex]


def build_robots_txt() -> str:
    base = public_base_url()
    lines = ["User-agent: *", "Allow: /", ""]
    for agent in AI_AGENTS:
        lines += [
            f"User-agent: {agent}",
            "Allow: /blog",
            "Allow: /llms.txt",
            "Allow: /llms.es.txt",
            "",
        ]
    lines += [f"Sitemap: {base}/sitemap.xml", ""]
    return "\n".join(lines)


def build_sitemap_xml(posts: tuple[BlogPost, ...] | None = None) -> str:
    base = public_base_url()
    indexed = _indexed(posts)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path in STATIC_PATHS:
        out.append(f"  <url><loc>{xml_escape(base + path)}</loc></url>")
    for p in indexed:
        out.append(
            f"  <url><loc>{xml_escape(base + p.url_path)}</loc>"
            f"<lastmod>{xml_escape(p.updated)}</lastmod></url>"
        )
    out.append("</urlset>")
    return "\n".join(out) + "\n"


def _llms_pages(lang: str, posts: tuple[BlogPost, ...] | None = None) -> list[LlmsPage]:
    base = public_base_url()
    return [
        LlmsPage(
            product_id=p.slug,
            title=p.title,
            url=f"{base}{p.url_path}",
            description=p.description,
            section="Blog",
        )
        for p in _indexed(posts)
        if p.lang == lang
    ]


def build_llms_txt(lang: str, posts: tuple[BlogPost, ...] | None = None) -> str:
    """The machine index. English is the default feed (``/llms.txt``) because
    ``x-default`` is English for the same reason: the AI crawlers are."""
    base = public_base_url()
    if lang == "en":
        site = SiteInfo(
            name="Kern",
            summary="Inventory method for SMB distributors and DTC brands, explained against the code that runs it.",
            context=(
                "Every article states its rule, the columns it needs and the module it comes from. "
                "No prices and no client results appear in article bodies."
            ),
            homepage_url=base,
        )
    else:
        site = SiteInfo(
            name="Kern",
            summary="Metodo de inventario para distribuidores e importadores, explicado contra el codigo que lo corre.",
            context=(
                "Cada articulo declara su regla, las columnas que necesita y el modulo del que sale. "
                "Los cuerpos no llevan precios ni resultados de clientes."
            ),
            homepage_url=base,
        )
    return generate_llms_txt(site, _llms_pages(lang, posts))

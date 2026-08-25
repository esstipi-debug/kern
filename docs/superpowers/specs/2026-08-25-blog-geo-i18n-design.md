# Blog GEO bilingüe (ES + EN) — design spec

**Date:** 2026-08-25
**Status:** plan locked; posts authored by a writer agent (Claude); renderer is a follow-up PR
**Owner of this spec:** infra / product
**Owner of post bodies:** Claude, following `documentation/blog/WRITER.md`

## 1. Goal

A public blog on the existing Kern site that:

1. Ranks on Google for ICP money-queries in **Spanish and English**.
2. Is citable by Perplexity, ChatGPT Search, and Google AI Overviews.
3. Converts to a `/demo` email (`source=blog-<slug>`) or a Diagnóstico CTA.
4. Ships both languages as **first-class pages**, not an afterthought translation.

This is option **C** from the GEO blog plan: markdown in the repo, HTML
server-rendered, plus `llms.txt` / raw `.md` / JSON-LD. LinkedIn and video stay
distribution; this blog is the canonical, crawlable article.

## 2. Non-goals

- CMS, Substack, Ghost, Notion, a `blog.` subdomain.
- Client-side markdown (`marked.js` as on `/operator`) — crawlers will not see the body.
- A Nosilo blog. Nosilo may be named once as the control plane inside a Kern post.
- Literal translation. Each language owns different queries and "compared to" lists.
- Writer-agent edits to `webapp/` unless a follow-up explicitly asks for the renderer.
- Rewriting `/stocky-alternative` as a blog post (that URL already owns the EN query).

## 3. Division of labour

| Who | Owns | Does not own |
|---|---|---|
| **This spec + `documentation/blog/`** | IA, URLs, frontmatter contract, briefs, §10 denylist, DoD | Finished article prose |
| **Claude (writer)** | `documentation/blog/es/*.md` and `documentation/blog/en/*.md` | FastAPI routes, JSON-LD, CSS, sitemap |
| **Follow-up implementation PR** | Loader, SSR, `/llms.txt`, `/robots.txt`, `/sitemap.xml`, `/demo?source=` | Inventing new slugs or queries |

A post file that fails the frontmatter contract is not "almost done". It is unpublished.

## 4. URL and language model

**One topic = one `pair` id = two URLs (except Stocky EN).**

| pair | ES slug (`/blog/{slug}`) | EN slug (`/blog/{slug}`) |
|---|---|---|
| `excess-obsolete` | `stock-muerto` | `dead-stock` |
| `stocky-alt` | `alternativa-stocky` | **existing** `/stocky-alternative` (not under `/blog`) |
| `minmax-erp` | `min-max-odoo` | `erp-minmax-reorder` |
| `safety-stock` | `stock-seguridad` | `safety-stock` |
| `qa-gate` | `compuerta-qa` | `inventory-ai-qa-gate` |
| `excel-vs-method` | `excel-vs-metodo` | `excel-inventory-method` |

Slugs are **globally unique**. Language is a field on the post, never a path prefix
like `/es/blog/...` in v1 (avoids a site-wide i18n rewrite). Indexes:

| Route | Language | Role |
|---|---|---|
| `GET /blog` | es | Default human index (matches `/paquetes`) |
| `GET /blog/en` | en | English index (matches Stocky / one-plan ICP) |
| `GET /blog/{slug}` | inferred from post | Article HTML |
| `GET /blog/{slug}.md` | inferred | Raw source for AI crawlers |

**hreflang** on every article:

```html
<link rel="alternate" hreflang="es" href="{PUBLIC_BASE_URL}/blog/{es_slug}">
<link rel="alternate" hreflang="en" href="{PUBLIC_BASE_URL}/blog/{en_slug}">
<link rel="alternate" hreflang="x-default" href="{PUBLIC_BASE_URL}/blog/{en_slug}">
```

For `stocky-alt`, the EN href is `{PUBLIC_BASE_URL}/stocky-alternative`, not a
`/blog/` URL.

**x-default = English.** AI crawlers and the two highest-intent English landings
are EN. Spanish is always tagged `hreflang="es"`. Revisit if Search Console shows
ES dominating.

**Canonical:** `PUBLIC_BASE_URL` (today `https://linchpin.fly.dev`) + the path of
*this* language. Never canonicalise ES → EN or vice versa.

**Transcreation, not translation.** Shared: method, code paths, §10. Different:
primary query, examples, competitor set (Odoo/importador vs Shopify/DTC), CTA
microcopy.

## 5. On-disk layout

```
documentation/blog/
  README.md                 # map
  WRITER.md                 # standing orders for Claude
  CHECKLIST.md              # DoD the writer runs on itself
  TEMPLATE.es.md
  TEMPLATE.en.md
  briefs/                   # what to write (not published)
    00-catalog.md
    01-excess-obsolete.md
    ...
  es/                       # published Spanish posts (writer fills)
  en/                       # published English posts (writer fills)
```

The renderer (follow-up) reads **only** `es/` and `en/`. Briefs and templates are
never mounted as public routes.

## 6. Frontmatter contract

Required keys (loader fails closed):

```yaml
pair: excess-obsolete          # stable id; must match the brief
lang: es                       # es | en
slug: stock-muerto             # == filename without .md; ^[a-z0-9-]+$
title: "..."
description: "..."             # <= 160 chars; reused as llms.txt one-liner
date: 2026-08-25               # ISO date, first publish
updated: 2026-08-25
query: "stock muerto inventario"
answer: >                      # 40-80 words; THE answer; first on-page paragraph
  ...
cta: demo                      # demo | diagnostico | both
offer: diagnostico-arranque    # slug in webapp.offers; prices NEVER in the body
sources:                       # >= 1
  - kind: code
    ref: src/excess_obsolete.py
  - kind: book
    ref: "Vandeput, Inventory Optimization (2020), ch. 4"
faq:                           # >= 3, ideally 4-6
  - q: "..."
    a: "..."
noindex: false
```

Optional: `persona` (`owner` | `ops` | `cfo`), `related_pairs` (list of pair ids).

Rules:

- `slug` == filename. `lang` == parent folder (`es/` or `en/`).
- `kind: code` refs MUST be paths that exist in the repo at merge time.
- `kind: book` refs MUST be a real node/source in `knowledge/scm-books/` (title +
  chapter or section). The writer looks them up; they do not invent ISBNs.
- HTML inside markdown is **rejected** by the renderer (XSS + claim smuggling).
- If `noindex: true`, omit from sitemap and `llms.txt` (drafts).

## 7. Renderer (follow-up PR — locked decisions)

- **SSR in FastAPI**, same shell as `webapp/stocky_alternative_page.py`
  (Inter + JetBrains / IBM Plex Mono, `--ink/--accent`, teal `#5EEAD4`).
- Markdown → HTML: stdlib subset (H1–H3, p, ul/ol, links, `code`, blockquote,
  strong/em, GFM tables). **No raw HTML.** PyYAML already core (frontmatter).
  Do not add `markdown` to the `[web]` extra unless the subset proves too thin.
- JSON-LD in the HTML: `Article` + `FAQPage` + `BreadcrumbList`. Do **not** reuse
  `src/seo/schema_gen.py` (that module authors Product/Offer for client catalogs).
- Import `generate_llms_txt` from `src.seo.llms_txt` (the module, not `src.seo`).
- Boot-safe: no optional-extra imports on `webapp.app` → blog modules.
- CTA prices and Stripe/Calendly/mailto: `webapp.offers` only, same as Stocky.
- `/demo` must read `?source=` and send it to `POST /api/leads` (today defaults
  to `"demo"`; allowed charset is already `[\w.\-]{,40}`).

Routes the follow-up must add:

| Route | Body |
|---|---|
| `GET /blog` | ES index |
| `GET /blog/en` | EN index |
| `GET /blog/{slug}` | SSR article |
| `GET /blog/{slug}.md` | raw file, `text/markdown` |
| `GET /llms.txt` | EN machine index (AI default) |
| `GET /llms.es.txt` | ES machine index |
| `GET /robots.txt` | Allow `/`, sitemap, Allow GPTBot/PerplexityBot/Google-Extended on `/blog` and llms files |
| `GET /sitemap.xml` | home, both indexes, every indexed slug, `/demo`, `/paquetes`, `/stocky-alternative`, `/one-plan`, `/how-it-works` |

Nav: add Blog to `/paquetes`, `/demo`, `/stocky-alternative`, `/one-plan`. Not to
`/console` or `/operator`.

## 8. GEO writing shape (enforced in briefs, not in code)

On-page order:

1. H1 + `answer` (first paragraph, no throat-clearing).
2. Operational definition (what is measured, which columns).
3. Method, with formula or numbered steps, cited.
4. Compared with what (bible §2 — different set per language).
5. What Kern does **not** do.
6. FAQ from frontmatter (do not duplicate by hand in the body unless the
   renderer does not yet exist; until then, include an `## FAQ` that matches
   frontmatter).
7. Single CTA.

## 9. v1 scope = 6 pairs (12 public URLs, 11 markdown files)

Stocky EN is the 12th URL and already lives in `webapp/stocky_alternative_page.py`.
Claude writes **11** markdown files. See `documentation/blog/briefs/00-catalog.md`.

Do not add a 7th pair until a distinct query is named. Recycle LinkedIn/video
toward these slugs; do not fork new URLs for the same question.

## 10. Tests the follow-up PR must land

Mirror `tests/test_stocky_alternative_page.py`:

- 200 / 404 / path-traversal 404
- JSON-LD parseable, `hreflang` pair present, canonical host from `PUBLIC_BASE_URL`
- `.md` response equals file bytes
- `is_valid_llms_txt()` on both `/llms.txt` and `/llms.es.txt`
- sitemap contains every `noindex: false` slug and `/stocky-alternative`
- denylist §10 over all `es/` + `en/` bodies
- every `sources.kind: code` path exists

## 11. Success (90 days after renderer is live)

- All indexed slugs appear in a `site:` query.
- At least one `/blog/...` or `/stocky-alternative` citation in Perplexity for a
  pair's primary query (manual monthly check).
- `POST /api/leads` records with `source` starting `blog-` > 0.

If indexed and zero leads: rewrite the pillar CTA/query, do not add a CMS.

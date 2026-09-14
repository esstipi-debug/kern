# Follow-up: implement the blog renderer

Not this PR. Track against
`docs/superpowers/specs/2026-08-25-blog-geo-i18n-design.md`.

Suggested branch name: `cursor/blog-ssr-llms-f9a1` (or the then-current prefix).

## Slice 1

- Load `documentation/blog/es/*.md` and `en/*.md` (ignore `README.md`).
- Validate frontmatter (fail tests if a published file is invalid).
- SSR `/blog`, `/blog/en`, `/blog/{slug}`, `/blog/{slug}.md`.
- Shell = Stocky visual system.
- One fixture post may be needed if Claude has not landed pair 1 yet; prefer
  waiting for `es/stock-muerto.md` rather than committing lorem.

## Slice 2

- `/robots.txt`, `/sitemap.xml`, `/llms.txt`, `/llms.es.txt`.
- `hreflang` + canonical.
- Nav Blog links.
- `/demo?source=` → `POST /api/leads`.
- Pair `stocky-alt`: ES article hreflang EN href = `/stocky-alternative`.

## Slice 3

- `tests/test_blog_claims.py` denylist from `CHECKLIST.md` §10.
- `sources.kind: code` path existence.

Do not invent slugs. If a file is missing, the index simply omits it.

# Blog renderer — shipped

Tracked against `docs/superpowers/specs/2026-08-25-blog-geo-i18n-design.md`.
The three slices below landed in PR #283. Later PRs own visual polish and
the production Fly deploy (a merge to `main` does not push the image).

## Slice 1 — done

- Load `documentation/blog/es/*.md` and `en/*.md` (ignore `README.md`).
- Validate frontmatter (fail tests if a published file is invalid).
- SSR `/blog`, `/blog/en`, `/blog/{slug}`, `/blog/{slug}.md`.
- Shell = Stocky visual system.

## Slice 2 — done

- `/robots.txt`, `/sitemap.xml`, `/llms.txt`, `/llms.es.txt`.
- `hreflang` + canonical.
- Nav Blog links on `/paquetes`, `/demo`, `/stocky-alternative`, `/one-plan`.
- `/demo?source=` → `POST /api/leads`.
- Pair `stocky-alt`: ES article hreflang EN href = `/stocky-alternative`.

## Slice 3 — done

- `tests/test_blog_claims.py` denylist from `CHECKLIST.md` §10.
- `sources.kind: code` path existence.

Do not invent slugs. If a file is missing, the index simply omits it.

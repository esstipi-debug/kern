# Kern public blog (ES + EN)

Crawlable articles on the Kern site. **Not** LinkedIn. **Not** Nosilo.

Live routes (same FastAPI app as `/paquetes` and `/demo`):

| Route | What it is |
|---|---|
| `/blog` | Spanish index |
| `/blog/en` | English index |
| `/blog/{slug}` | SSR article |
| `/blog/{slug}.md` | raw markdown for AI crawlers |
| `/llms.txt` / `/llms.es.txt` | machine indexes |
| `/sitemap.xml` / `/robots.txt` | crawl surface |

Host today: `https://linchpin.fly.dev` (`PUBLIC_BASE_URL`). A merge to `main` is not a deploy; Fly is a manual `fly deploy --build-secret nosilo_token=...`.

| Path | What it is | Who writes it |
|---|---|---|
| `docs/superpowers/specs/2026-08-25-blog-geo-i18n-design.md` | URLs, hreflang, renderer, tests | already written |
| `WRITER.md` | Standing orders for the writer agent | already written |
| `CHECKLIST.md` | Definition of done per post | already written |
| `TEMPLATE.es.md` / `TEMPLATE.en.md` | Copy-paste skeleton | already written |
| `briefs/` | What to write for each pair | already written |
| `es/*.md` | Spanish posts (published) | **Claude** |
| `en/*.md` | English posts (published) | **Claude** |

The renderer reads **only** `es/` and `en/`. Files there are live source: valid frontmatter, answer-first, cited, §10-clean.

**Do not put finished posts in `briefs/`.** Briefs are instructions. Posts are articles.

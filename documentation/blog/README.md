# Kern public blog (ES + EN)

Crawlable articles on the Kern site. **Not** LinkedIn. **Not** Nosilo.

| Path | What it is | Who writes it |
|---|---|---|
| `docs/superpowers/specs/2026-08-25-blog-geo-i18n-design.md` | URLs, hreflang, renderer, tests | already written |
| `WRITER.md` | Standing orders for the writer agent | already written |
| `CHECKLIST.md` | Definition of done per post | already written |
| `TEMPLATE.es.md` / `TEMPLATE.en.md` | Copy-paste skeleton | already written |
| `briefs/` | What to write for each pair | already written |
| `es/*.md` | Spanish posts (published) | **Claude** |
| `en/*.md` | English posts (published) | **Claude** |

Until the follow-up renderer PR lands, files in `es/` and `en/` are the source of
truth sitting in git. They are still written as if they were live: valid
frontmatter, answer-first, cited, §10-clean.

**Do not put finished posts in `briefs/`.** Briefs are instructions. Posts are
articles.

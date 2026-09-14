# Prompt to paste into Claude (writer agent)

Use this as the full task message. Replace the batch if needed.

---

You are the writer for Kern's public GEO blog. You do **not** change FastAPI,
slugs, or briefs except to fill `documentation/blog/es/` and `documentation/blog/en/`.

Read, in order:

1. `documentation/blog/WRITER.md`
2. `documentation/blog/CHECKLIST.md`
3. `documentation/blog/briefs/00-catalog.md`
4. The brief(s) for the pair(s) below
5. Every code file those briefs name
6. `documentation/KERN_BRAND_PRODUCT_BIBLE.md` §2 and §10

Then write the markdown files the brief lists. Use `TEMPLATE.es.md` / `TEMPLATE.en.md`.
Run the checklist mentally against each file. Transcreate; do not translate
sentence-by-sentence.

**This session, write pair(s):** `excess-obsolete` (both languages).

If a book citation cannot be found with `python examples/query_knowledge.py --search`,
omit the book and keep the code citation.

Do not add a 7th slug. Do not type prices. Do not create `en/` for `stocky-alt`.

---

Batches (run as separate Claude sessions):

| Session | Pairs |
|---|---|
| 1 | `excess-obsolete` |
| 2 | `stocky-alt` (ES only) |
| 3 | `safety-stock` |
| 4 | `minmax-erp` |
| 5 | `qa-gate` |
| 6 | `excel-vs-method` |

Renderer / `webapp/` is a **different** PR (`docs/superpowers/specs/2026-08-25-blog-geo-i18n-design.md`).

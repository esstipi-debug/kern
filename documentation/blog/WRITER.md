# Writer standing orders (Claude)

You write the **article bodies**. You do not design the blog, pick new slugs, or
edit FastAPI.

Read in this order, then stop reading and write:

1. This file.
2. `CHECKLIST.md`.
3. The brief for the pair you were asked to write (`briefs/0N-*.md`).
4. Every `kind: code` file the brief names (open the real file; do not guess).
5. The named book/chapter via `python examples/query_knowledge.py --search "..."`
   or `--explain`. If the graph has no such node, **drop the claim**. Do not cite
   a book you could not locate.
6. `documentation/KERN_BRAND_PRODUCT_BIBLE.md` §2 (compared-to) and **§10**
   (banned claims).

Copy `TEMPLATE.es.md` / `TEMPLATE.en.md` into `es/<slug>.md` and `en/<slug>.md`.
Filenames **are** the slugs in the brief. Do not invent URLs.

## What you are producing

For each assigned pair, **two articles** (Stocky EN is the exception: do not
create `en/stocky-alternative.md`; the live page is `/stocky-alternative`).

They share `pair`, method, and sources. They are **not** translations:

| | Español | English |
|---|---|---|
| Default ICP | PyME LatAm/España, Odoo o Excel, importador/distribuidor | DTC / Shopify / spreadsheet, US-UK-AU |
| Voice | Español LATAM, voseo neutral (like the content calendar) | Plain professional US English; no hype |
| Compared-to | Excel, min/max Odoo, consultor, chatbot | Excel, Stocky/Netstock-class SaaS, chatbot, min/max ERP |
| CTA microcopy | "Escaneá tu CSV" / "Agendá el diagnóstico" | "Scan your CSV" / "Book the diagnostic" |

## Shape of every post

1. YAML frontmatter (all required keys — see the design spec).
2. H1 = `title`.
3. First paragraph = `answer` verbatim (40–80 words). Models cite this block.
4. Method, columns, formula — cited to code or book+chapter.
5. Compared-with-what.
6. What Kern does not do.
7. `## FAQ` whose Q/A **match** the frontmatter `faq:` list (until the renderer
   paints FAQ from YAML only).
8. One CTA. No newsletter. No "subscribe".

Length: **900–1,400 words** of body per language, not counting YAML.

## Hard rules

- If you cannot point a sentence at a repo file or a graph source, delete it.
- Never put a price in the markdown. The offer slug in frontmatter is enough;
  the page renderer injects price from `webapp.offers`.
- Never put a client dollar amount, a testimonial, or a "% autonomous" figure.
- Rival prices, if mentioned, are "market anchor / list price, verified Jul 2026"
  — not "they charge you X today".
- Examples: label `e.g.` / `ej.` / `estimado` / `illustrative`.
- No raw HTML. No `<script>`, no `<iframe>`, no inline styles.
- No "AI predicts the future", "state of the art AI", "fully autonomous",
  "Shopify integration", "Amazon integration", "we sync your Mercado Libre
  inventory", "replaces your analysts", "CEO dashboard that runs the company".
- Kern **prepares**; a human signs irreversible writeback. Three of four
  GuidedOutcome endings need a human (`OPTIONS`, `HANDOFF`, `ESCALATED`). The
  allowed line is: *of the four possible endings, three stop on a human by
  design*. Not "3 out of 4 times it asks permission" (frequency unmeasured).
- QA-gate: "if QA fails, there is no deliverable". Point at the orchestrator
  contract, not at a made-up accuracy %.
- Stocky: positioning as an alternative **model** and migration helper. **Not**
  a Shopify/Stocky connector. Shutdown date is 31 Aug 2026 (already on the EN
  landing). Do not invent Shopify API behaviour.
- Nosilo: at most one sentence, as "the domain-agnostic control plane under
  writeback/guided outcomes". Not the H1, not the slug, not the query.

## Sources

Prefer, in order:

1. The exact Python module the brief names (quote the real rule: thresholds,
   formula, column names).
2. A books-graph hit with title + chapter/section.
3. The brand bible for compared-to framing — not for made-up stats.

Column names in examples must match templates or job `prepare()`:

- Stock scan: `product_id, on_hand, daily_demand, unit_cost, days_since_last_sale`
  (`documentation/templates/stock_template.csv`).

## CTA mapping

| `cta` value | Writer closing line (ES) | Writer closing line (EN) |
|---|---|---|
| `demo` | Empujá a `/demo` (escaneo de stock o lead time). | Push `/demo` (stock or lead-time scan). |
| `diagnostico` | Empujá Diagnóstico de Arranque, sin precio. | Push Start-up Diagnostic, no price. |
| `both` | Demo first, diagnostic second. | Same. |

Use markdown links to `/demo`, `/paquetes/diagnostico-arranque`,
`/stocky-alternative` as the brief says. Relative links only (site-absolute
paths starting `/`).

## Order of work when asked "write pair N"

1. Spanish file first (`es/...`), then English (`en/...`), unless the brief
   says otherwise (Stocky: Spanish only).
2. Run `CHECKLIST.md` against both files.
3. Do not open a PR that also "improves" the spec, the briefs, or the engine.

## If blocked

- Missing graph node: delete the citation; keep the code citation.
- Brief conflicts with §10: **§10 wins**. Note it in the PR/commit message.
- You want a 7th article: stop. Do not add slugs. Ask.

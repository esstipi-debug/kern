# Brief 01 — pair `excess-obsolete`

**Claude writes:** `documentation/blog/es/stock-muerto.md` and `documentation/blog/en/dead-stock.md`.

## Queries

| | ES | EN |
|---|---|---|
| Primary | stock muerto inventario | dead stock cash tied up in inventory |
| Secondary | capital inmovilizado stock | excess and obsolete inventory |
| Not this article | liquidación contingente (Sprint), markdown pricing | contingent liquidation fee |

## Persona

- ES: dueño/importador que sospecha plata en la bodega y vive en Excel.
- EN: DTC/ops lead who talks about "cash trapped in dead stock" in public.

## Answer (writer rewrites, must keep these facts)

Dead vs excess vs healthy is a **classification of on-hand**, not a vibe.

From `src/excess_obsolete.py` (read it; do not paraphrase from memory):

- Inputs per SKU: `product_id`, `on_hand`, `daily_demand`, `unit_cost`, optional `days_since_last_sale` (`None` ≠ 0: unknown idle time does **not** fire the idleness rule).
- Defaults: `target_cover_days=90`, `dead_threshold_days=180`.
- **Dead:** `daily_demand <= 0` **or** `days_since_last_sale >= 180` (when idle is known). All on-hand counts as at-risk units.
- **Excess:** still moving, but days-of-cover (`on_hand / daily_demand`) > 90. At-risk units = on-hand minus `90 * daily_demand`.
- **Healthy:** the rest.
- `excess_value` = at-risk units × `unit_cost`. Ranking is by that value descending.
- Recommended actions are bilingual in the module (`liquidar / …` vs `liquidate / …`).

Template columns: `documentation/templates/stock_template.csv`.

## Required sections

1. Definition split: muerto/dead vs excedido/excess vs sano/healthy.
2. Worked **illustrative** SKU (label it). Use numbers like the template (800 units, 0 demand, 220 days idle → dead) — say `ej.` / `e.g.`.
3. Column minimum vs optional idle field, and why defaulting idle to 0 would hide the dead SKU (the module comment documents this trap).
4. Compared-to:
   - ES: Excel without a cover rule; "lo veo a ojo"; ERP that does not size cash.
   - EN: dashboard that charts aging but does not emit a cash ranking with a stated rule.
5. What Kern does not do: no ERP write on the scan; no client $ case study; scan is read-only.
6. FAQ (minimum):
   - Minimum columns?
   - Difference dead vs excess?
   - Does the scan write to my ERP/Shopify?
   - Are the $ in the article a real client?
   - What is the next paid step? (Diagnóstico — no price in the body)

## Sources (must appear in frontmatter)

- `kind: code` `src/excess_obsolete.py`
- Optional book: only if `query_knowledge.py` finds a real E&O / obsolete-inventory chapter. If not, code-only is valid.

## CTA

`cta: demo` · `offer: diagnostico-arranque` · link `/demo`. Mention that the public demo is the same family of analysis as the start of Diagnóstico de Arranque.

## Landmines

- Do not invent a client recovery amount.
- Do not change 90 / 180 in prose unless you re-read the defaults.
- Do not call zero-demand SKUs "excess"; the code classifies them **dead**.
- Do not claim Mercado Libre / Shopify inventory sync.

## Transcreation notes

ES can talk "importador, contenedor, capital atado". EN can talk "DTC, cash flow trap, overstock after a demand spike". Same formula.

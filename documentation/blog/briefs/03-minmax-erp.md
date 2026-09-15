# Brief 03 — pair `minmax-erp`

**Claude writes:** `documentation/blog/es/min-max-odoo.md` and
`documentation/blog/en/erp-minmax-reorder.md`.

## Queries

| | ES | EN |
|---|---|---|
| Primary | min max Odoo reorden | ERP min max reorder point |
| Secondary | reglas de reabastecimiento Odoo | min-max inventory policy vs (s,Q) |
| Not this article | full Odoo Apps listing; connector setup wizard | Shopify Stocky (pair 2) |

## Persona

- ES: pyme on Odoo whose min/max were typed once and never revisited.
- EN: any SMB ERP / spreadsheet min-max (NetSuite-ish, "the ERP already does
  reorder"). Do **not** require Odoo in the EN H1; Odoo can be one example.

## Core claim (bible §2.6 + engine)

Min/max in the ERP is a **static rule**. It does not:

- forecast the demand pattern,
- size safety stock from **σ_e** (forecast-error std),
- simulate before recommending,
- stage a reversible write.

Kern does not replace Odoo/the ERP as system of record. It computes a governed
policy and, if writing back, uses dry-run + approval + rollback
(`nosilo_core.writeback` — one sentence max, not the topic).

Read before writing:

- `src/policies.py` — (s,Q) / (R,S) exist; min/max is what the client already has.
- `jobs/odoo_replenishment.py` (ES) / `jobs/excel_replenishment.py` (both) —
  describe **what the job actually does**, not a fantasy connector.
- Do not claim a live Odoo write happens in the public `/demo`. Demo is CSV.

## Required sections

1. What min and max usually mean in an ERP (reorder when on-hand < min, up to max).
2. Where that rule breaks: second warehouse, intermittent SKU, lead time change.
3. What a governed policy adds: forecast → σ_e → safety stock → s or S, cited.
4. Compared-to: Excel min/max; native ERP rule; hiring a planner who still types min/max.
5. What Kern does not do: silent overwrite of Odoo min/max; "set and forget autonomy".
6. FAQ:
   - Does Kern replace Odoo?
   - Will it change my min/max without asking?
   - Is min/max the same as a reorder point s?
   - What file do I need to get a first policy?
   - ES-only: ¿trabajan con implementadores Odoo? (yes as a channel — no partner
     names unless already in `documentation/ACQUISITION_PLAYBOOK.md`, and even
     then do not imply a live endorsement contract).

## Sources

- `kind: code` `src/policies.py` and/or `src/safety_stock.py` and/or
  `src/forecasting.py`
- Book: Vandeput *Inventory Optimization* (2020) ch. 2–5 (EOQ / SS / (s,Q) / (R,S))
  — confirm chapter via the graph before citing a chapter number.

## CTA

`cta: diagnostico` · `offer: diagnostico-arranque`
ES may also mention Growth as "ciclo sobre Odoo" without typing the monthly
price. Link `/paquetes/diagnostico-arranque`. Demo link allowed as a lighter
first step (`/demo`).

## Landmines

- No "we sync Odoo automatically 24/7" as a shipped Control Tower (plan 3.0).
- `odoo_replenishment` writes only through writeback — say dry-run / approval.
- Do not cite Odoo partner phone numbers in a blog post.
- EN: do not pretend Kern is an Odoo-only product.

## Transcreation notes

ES H1 can name Odoo. EN H1 should name **ERP min/max** so it can rank beyond
Odoo (still mention Odoo once as an example).

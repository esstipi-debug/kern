# v1 catalog — 6 pairs

Write in this order unless a later instruction says otherwise. Each row is one
brief. Claude produces the markdown files in the last column.

| # | pair | ES file | EN file | Primary query ES | Primary query EN | CTA |
|---|---|---|---|---|---|---|
| 1 | `excess-obsolete` | `es/stock-muerto.md` | `en/dead-stock.md` | stock muerto inventario | dead stock cash tied up | demo |
| 2 | `stocky-alt` | `es/alternativa-stocky.md` | **none** (live `/stocky-alternative`) | alternativa a Stocky Shopify | stocky alternative (already ranked) | both |
| 3 | `minmax-erp` | `es/min-max-odoo.md` | `en/erp-minmax-reorder.md` | min max Odoo reorden | ERP min max reorder point | diagnostico |
| 4 | `safety-stock` | `es/stock-seguridad.md` | `en/safety-stock.md` | calcular stock de seguridad | how to calculate safety stock | demo |
| 5 | `qa-gate` | `es/compuerta-qa.md` | `en/inventory-ai-qa-gate.md` | IA inventario sin fuente | inventory AI quality gate | diagnostico |
| 6 | `excel-vs-method` | `es/excel-vs-metodo.md` | `en/excel-inventory-method.md` | inventario en Excel | inventory planning in Excel | demo |

**11 markdown files.** Do not add pair 7.

Related live pages the writer may link, never duplicate:

- `/demo` — email-gated scan (stock / lead time)
- `/paquetes/diagnostico-arranque`
- `/paquetes/starter-fundamentos`
- `/stocky-alternative` (EN)
- `/one-plan` (EN AU/NZ; do not cannibalise — do not retarget that query here)

Suggested writing batches for Claude:

1. Pair 1 both languages (product already behind `/demo` stock).
2. Pair 2 Spanish only + hreflang note pointing at `/stocky-alternative`.
3. Pair 4 both (evergreen, highest AI-cite potential).
4. Pairs 3, 5, 6.

Sources of truth for claims: the brief + the cited code + bible §10.

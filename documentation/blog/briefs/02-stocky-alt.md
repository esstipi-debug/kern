# Brief 02 — pair `stocky-alt`

**Claude writes:** `documentation/blog/es/alternativa-stocky.md` **only**.

**Do not create** `en/stocky-alternative.md` or `en/alternativa-stocky.md`.
English already ships as `GET /stocky-alternative`
(`webapp/stocky_alternative_page.py`). That page owns the query
"stocky alternative".

In the Spanish frontmatter, still set `pair: stocky-alt` and `lang: es`.
The follow-up renderer will hreflang ES → `/stocky-alternative`.

## Queries

| | ES | EN (do not write) |
|---|---|---|
| Primary | alternativa a Stocky Shopify | stocky alternative (live page) |
| Secondary | Stocky cierra 2026, reemplazo forecast reorden | shutdown 31 Aug 2026 |

## Facts that must match the live EN page

Read `webapp/stocky_alternative_page.py` before writing Spanish:

- Shopify removed Stocky from the App Store 2 Feb 2026 (no new installs).
- Full shutdown **31 August 2026**.
- Stocky did three jobs: demand forecast from sales history, reorder-point / PO suggestions, low-stock alerts. A replacement must cover all three.
- Kern is **not** a Shopify app install and does not need ongoing Shopify permissions. CSV / spreadsheet, reversible dry-run writeback.
- Entry is a one-time diagnostic, not "another $49–599/mo dashboard" as the default path. Ongoing engagement is optional.
- Trust: cited methodology + staged reversible restock; nothing live without approval.
- Disclaimer already on the EN page: Kern is independent, not affiliated with Shopify; "Stocky" is Shopify's product.

## Spanish angle (not a translation of the EN H1)

EN H1 is "Shopify is killing Stocky…". ES readers may not run Shopify. Frame:

- Qué era Stocky y por qué hay una ola de búsqueda (hecho de mercado, no integración).
- Qué hay que reemplazar (las tres funciones).
- Por qué "instalar otra app de inventario" te deja operándola (bible §2 SaaS).
- Kern como **modelo + operador**, CSV, sin conector Shopify.
- CTA: demo y/o Diagnóstico.

If the reader is on Odoo/Excel and never used Stocky, one short paragraph:
this wave is still the same job (forecast + reorder + alert), even off Shopify.

## Required sections

1. Shutdown facts (dates).
2. The three functions to replace.
3. Compared-to: other dashboards (name categories, rival **list-price anchors**
   only as in bible §2, labelled ancla de mercado Jul 2026 — Netstock / Inventory
   Planner / Prediko class, not a live quote).
4. What Kern does not do: no Shopify integration, no Stocky importer, no
   "we keep your Stocky data in sync".
5. FAQ (ES):
   - ¿Cuándo cierra Stocky?
   - ¿Tengo que instalar una app en Shopify?
   - ¿Kern reemplaza forecast + punto de reorden + alerta?
   - ¿Es otra suscripción mensual?
   - ¿Escribe en mi tienda?

## Sources

- `kind: code` — point at something real you actually describe, e.g.
  `src/forecasting.py` (σ_e), `src/safety_stock.py`, `jobs/inventory_optimization.py`
  if you explain the restock plan. Do not cite `stocky_alternative_page.py` as a
  methodology source (it is marketing HTML).
- Book: Vandeput safety stock / forecast error only if you use those ideas.

## CTA

`cta: both` · `offer: diagnostico-arranque` · links `/demo` and
`/paquetes/diagnostico-arranque`. Optional: `/stocky-alternative` for the EN
original ("versión en inglés").

## Landmines

- No "integración con Shopify/Stocky".
- Window is lead-gen framing, not a revenue promise for August.
- Do not invent Stocky feature details beyond the three listed on the EN page.
- Chequeo de migración ($350–400) is a funnel step in the bible; **do not type
  the price** in the post. If you mention a self-serve migration check, no
  dollar figure — renderer/offers owns prices.

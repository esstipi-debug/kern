# Brief 04 — pair `safety-stock`

**Claude writes:** `documentation/blog/es/stock-seguridad.md` and
`documentation/blog/en/safety-stock.md`.

Highest AI-cite potential. Write like a reference page a model can quote.

## Queries

| | ES | EN |
|---|---|---|
| Primary | calcular stock de seguridad | how to calculate safety stock |
| Secondary | z sigma lead time inventario | z score safety stock formula |
| Must win | σ_e vs σ de la demanda | sigma_e vs raw demand std |

## Persona

Ops / demand planner / founder who Googled the formula. Skeptical of "AI safety stock".

## Facts from code (read the files)

`src/safety_stock.py` — Vandeput (2020) *Inventory Optimization* **Chapter 4**:

- `Ss = z_alpha * sigma_d * sqrt(tau)` (eq. 4.3 in the module docstring).
- `z_alpha = Phi^{-1}(alpha)`; `cycle_service_level` in (0, 1) exclusive.
- `tau` (`risk_periods`): lead time L for (s,Q); R+L for (R,S).
- Cycle service level ≠ fill rate (do not conflate; fill rate is later chapters /
  other modules — if you mention fill rate, cite the real module or omit).

`src/forecasting.py` — the engine's demand input is **not** raw σ of sales:

- `error_std` is **σ_e**, std of one-step-ahead forecast errors.
- Comment in file: theoretically correct dispersion for safety stock is σ_e,
  citing Vandeput *Data Science for Supply Chain Forecasting* **Sec. 4.2.5**
  (note: forecasting docstring says 2021 for that book). Confirm via graph;
  if the year disagrees, cite as the module does and do not invent a third year.

Intermittent demand: Croston exists (`forecast_demand` / ADI threshold 1.32 in
`src/forecasting.py`). One short subsection: the normal formula is the wrong
default for lumpy SKUs — do not expand into a full Croston tutorial.

Gamma / Ch. 9: `SafetyStockResult.distribution` can be `'gamma'`. Mention **only**
if you read that branch; otherwise stay on the normal formula and say skewed
demand is a different model, not "z times sigma".

## Required sections

1. The formula, with each symbol defined.
2. **σ_e vs σ of demand** — this is the article's reason to exist. Excel
   `STDEV` of sales is the common mistake.
3. What `tau` is under continuous vs periodic review (one paragraph).
4. Worked illustrative numbers (label them). Pick round z (e.g. ~1.65 for 95%)
   but compute honestly or say "illustrative".
5. Compared-to: Excel STDEV; ERP min/max; chatbot that emits a SS number
   with no σ definition.
6. What Kern does not do: predict the future; claim a service level it did not
   compute from `alpha`; write SS into the ERP from this blog post.
7. FAQ:
   - Is safety stock the same as reorder point? (No: ROP = demand over L + SS.)
   - 95% of what — cycle service vs fill rate?
   - Why not use the standard deviation of weekly sales?
   - What if lead time is also random? (If the stochastic-LT module is more than
     a slogan, cite it; else: "separate model, not this formula" and stop.)
   - Minimum history? (`src/forecasting.py` documents n=6 as a floor for
     purchase-sizing — read the comment; do not invent another n).

## Sources

- `kind: code` `src/safety_stock.py`
- `kind: code` `src/forecasting.py`
- `kind: book` Vandeput Inventory Optimization (2020), ch. 4
- `kind: book` Vandeput Data Science for Supply Chain Forecasting, sec. 4.2.5
  (only if the graph or the forecasting docstring is your trail)

## CTA

`cta: demo` · `offer: diagnostico-arranque` · `/demo`
The demo does not have to compute SS on the stock scan; be honest: the scan
quantifies cash in E&O; SS policy is the Diagnóstico / Starter job. Do not
imply `/demo` prints z×σ.

## Landmines

- Do not say Kern "always uses 95%". Default service level in the engine is a
  real default — if you mention it, **read it from code** (`0.95` appears as a
  generic default in CLAUDE.md / registry). Label it as the engine default, not
  a customer result.
- Do not mix fill rate and cycle service level.
- No "AI predicts demand perfectly so you need no safety stock".

## Transcreation notes

Formula and σ_e story are identical. ES examples: import lead time in days.
EN examples: supplier weeks, Shopify weekly sales export.

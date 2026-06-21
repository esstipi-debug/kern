# Inventory Optimization — Demo Co

## Executive summary

Analyzed **8 SKUs**. Recommended inventory investment is **$40,000** against a budget of **$40,000**, at a **95%** cycle service level. 2 SKU(s) show high forecast bias and 2 are intermittent (review recommended).

> Safety stock scaled to **61%** to fit the budget. Raise the cap to $46,616 to fund it fully.

## Recommended policy per SKU

| SKU | Method | Policy | Forecast/period | Order qty (Q*) | Order-up-to (S) | Reorder point (s) | Safety stock | Inv. value | Status |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| SKU-A | ses | (s, Q) | 108.8 | 261 | — | 135 | 26.7 | $7,850 | High bias — review |
| SKU-B | ses | (s, Q) | 258.1 | 897 | — | 594 | 79.1 | $5,277 | High bias — review |
| SKU-C | croston | (R, S) | 18.5 | — | 86 | 67 | 49.0 | $6,991 | Intermittent — review |
| SKU-D | ses | (s, Q) | 45.3 | 217 | — | 55 | 9.7 | $3,547 | On track |
| SKU-E | ses | (s, Q) | 366.5 | 1,381 | — | 1,185 | 88.1 | $4,670 | On track |
| SKU-F | croston | (R, S) | 6.6 | — | 45 | 38 | 25.3 | $5,711 | Intermittent — review |
| SKU-G | ses | (s, Q) | 79.2 | 234 | — | 133 | 54.3 | $7,719 | On track |
| SKU-H | ses | (s, Q) | 138.8 | 490 | — | 163 | 24.2 | $4,850 | On track |

## Findings & flags

- **SKU-A** — forecast bias +2.0 (|bias| ≥ 2). The forecast is consistently off; review the demand history or method before trusting the policy.
- **SKU-B** — forecast bias +3.3 (|bias| ≥ 2). The forecast is consistently off; review the demand history or method before trusting the policy.
- **SKU-C** — intermittent demand, forecast via Croston. Lumpy demand makes a periodic (R,S) review more robust than a fixed reorder point.
- **SKU-F** — intermittent demand, forecast via Croston. Lumpy demand makes a periodic (R,S) review more robust than a fixed reorder point.

## Methodology

- **Forecast:** per-SKU demand history is forecast with simple exponential smoothing (or Croston for intermittent demand), exposing the forecast-error standard deviation σₑ used for safety stock (Vandeput 2021, §4.2.5).
- **Safety stock:** `SS = z · σₑ · √L`, with `z` from the target cycle service level.
- **Order quantity:** Economic Order Quantity `Q* = √(2·D·K/H)` for continuous-review (s,Q); periodic (R,S) order-up-to `S = μ·(L+R) + SS` for intermittent SKUs.
- **Reorder point:** `s = μ·L + SS`.
- **Budget:** when a cap is set, safety stock is scaled across the portfolio to fit while preserving cycle-stock economics.
- Models from Vandeput (2020), *Inventory Optimization: Models and Simulations*.

## Assumptions

- Cycle service level: **95.0%**
- Holding cost: **25%** of unit cost per year
- Fixed order cost (K): **$75**
- Demand bucketed into **52 periods/year**; lead time taken from the data (or a default where absent).

_Generated from the client's demand data. Figures are decision support; validate cost and lead-time inputs against your systems before ordering._
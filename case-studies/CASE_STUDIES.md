# Case Studies & Exercises

Worked examples aligned with **Vandeput (2020)**, *Inventory Optimization: Models and Simulations*.

> Previous marketing case studies in this file were placeholders. Use the exercises below with the Python implementation.

---

## Exercise 1 — EOQ (§2.2.4)

**Given:** D = 1000 units/year, k = 50 €/order, h = 1.75 €/unit/year

```bash
python -c "
from src.eoq import compute_eoq
r = compute_eoq(1000, 1.75, 50)
print(f'Q*={r.order_quantity:.0f}, C*={r.optimal_total_cost:.0f}')
"
```

**Expected:** Q* ≈ 239, C* ≈ 418

---

## Exercise 2 — Safety stock (Table 4.1)

**Given:** μ_d = 100, σ_d = 25, α = 95%

```bash
python -c "
from src.safety_stock import safety_stock, service_level_factor
print('z =', round(service_level_factor(0.95), 2))
print('Ss =', round(safety_stock(25, 0.95, 1).safety_stock))
print('Inventory =', round(100 + safety_stock(25, 0.95, 1).safety_stock))
"
```

**Expected:** z ≈ 1.64, inventory ≈ 141

---

## Exercise 3 — (R,S) vs average on-hand (§3.3, §5.3)

Compare order-up-to **S** with simulated mean on-hand for long lead time:

```bash
python examples/run_part1_part2.py --product SKU-A --lead-time 4 --simulate
```

Observe: **S** is much higher than mean on-hand — do not treat S as “target stock on shelf”.

---

## Exercise 4 — Your SKU

Replace `data/sample_demand.csv` with your weekly/monthly demand history and run:

```bash
python examples/run_part1_part2.py --product YOUR-SKU --simulate
```

Calibrate `--holding-cost`, `--order-cost`, and `--lead-time` from finance and supplier data (§2.1, §3.1).

---

## Exercise 5 — Newsvendor muffins (§11.3)

```bash
python -c "
from src.newsvendor import muffin_pmf, optimal_newsvendor_discrete
r = optimal_newsvendor_discrete(muffin_pmf(), price=6, unit_cost=2, salvage_value=1)
print(f'Q*={r.optimal_quantity:.0f}, profit={r.expected_profit:.2f}')
"
```

**Expected:** Q* = 4, profit ≈ 6.00 EUR

---

## Exercise 6 — Serial GSM (§10.4)

```bash
python -c "
from src.multi_echelon import optimize_serial_gsm
b = optimize_serial_gsm([4,3,2], 100, 25, [1,2,4], 0.95, 1.0)
print(b.risk_periods, round(b.total_holding_cost))
"
```

**Expected:** (4, 0, 6), cost ≈ 485

---

## Exercise 7 — Safety stock: naive % rule vs statistical formula (§4.2-4.3)

**Given:** mu_d = 100, sigma_d = 25 (the Table 4.1 scenario), target cycle service level = 95%.

```bash
python -c "
from src.safety_stock import achieved_service_level, safety_stock
naive_ss = 0.20 * 100
achieved = achieved_service_level(naive_ss, 25, 1)
correct_ss = safety_stock(25, 0.95, 1).safety_stock
print(f'Naive 20-percent-of-demand rule: Ss={naive_ss:.0f}, achieves {achieved*100:.1f} percent service level (target 95 percent)')
print(f'Correct formula: Ss={correct_ss:.0f}')
"
```

**Expected:**
```
Naive 20-percent-of-demand rule: Ss=20, achieves 78.8 percent service level (target 95 percent)
Correct formula: Ss=41
```
-- the common "hold 20% of average demand" rule of thumb looks like a reasonable buffer but only achieves 78.8% service level against a 95% target.

---

## Exercise 8 — EOQ: round-lot vs economic order quantity (Section 2.2.4, 2.4)

**Given:** D = 1000 units/year, k = 50 EUR/order, h = 1.75 EUR/unit/year (the Exercise 1 scenario). Naive: order in a round lot of 500 units.

```bash
python -c "
from src.eoq import compute_eoq, total_cost, cost_ratio_vs_optimal
opt = compute_eoq(1000, 1.75, 50)
naive = total_cost(500, 1000, 1.75, 50)
ratio = cost_ratio_vs_optimal(500, opt.order_quantity)
print(f'Q*={opt.order_quantity:.0f}, C*={opt.optimal_total_cost:.0f}')
print(f'Naive Q=500 cost={naive:.0f}, ratio={ratio:.3f}')
"
```

**Expected:**
```
Q*=239, C*=418
Naive Q=500 cost=538, ratio=1.285
```
-- ordering in a round lot instead of the EOQ-optimal quantity costs ~28.5% more per year for identical demand and cost parameters.

---

## Exercise 9 — Multi-echelon: no risk-pooling vs GSM-optimized (Section 10.4)

**Given:** the Exercise 6 scenario (lead times [4,3,2], mu=100, sigma=25, holding costs [1,2,4], review period 1.0, 95% service level). Naive: all safety stock held at the customer-facing node (no risk-pooling across the chain) -- already pinned by this repo's own `test_gsm_case4_all_downstream_cost`.

```bash
python -c "
from src.multi_echelon import evaluate_serial_allocation, optimize_serial_gsm
naive = evaluate_serial_allocation((0,0,10), [4,3,2], 100, 25, [1,2,4], 0.95, 1.0, case_id=4)
best = optimize_serial_gsm([4,3,2], 100, 25, [1,2,4], 0.95, 1.0)
print(f'Naive (all-downstream) risk_periods={naive.risk_periods}, cost={round(naive.total_holding_cost)}')
print(f'GSM-optimal risk_periods={best.risk_periods}, cost={round(best.total_holding_cost)}')
print(f'Reduction: {(1 - best.total_holding_cost/naive.total_holding_cost)*100:.1f} percent')
"
```

**Expected:**
```
Naive (all-downstream) risk_periods=(0, 0, 10), cost=520
GSM-optimal risk_periods=(4.0, 0.0, 6.0), cost=485
Reduction: 6.7 percent
```
-- pooling risk across the chain instead of stacking it all at the customer-facing node cuts holding cost by ~6.7%.

---

## Exercise 10 — Forecasting: naive persistence vs classification-routed method, backtested on real M5 data (Ch. 9; Makridakis, Spiliotis & Assimakopoulos 2022)

Requires local M5 competition data (not committed to this repo -- large,
Kaggle-sourced, same convention as every other external dataset here). Copy
`sales_train_evaluation.csv` and `calendar.csv` into
`data/kaggle/m5/m5/datasets/` (from a Kaggle download, or from another local
checkout that already has them) before running:

```bash
python scripts/benchmark_forecast_m5.py --sample-size 100 --seed 42
```

**Expected:**
```
SKUs scored: 100 (skipped 0 all-zero-history/actuals SKUs)
Per-SKU average -- Naive:     MAE=1.260  WAPE=1.213
Per-SKU average -- Linchpin:  MAE=1.015  WAPE=1.251
Per-SKU average WAPE improvement: -3.2 percent
Demand-weighted (pooled) -- Naive:     MAE=1.260  WAPE=0.901
Demand-weighted (pooled) -- Linchpin:  MAE=1.015  WAPE=0.726
Demand-weighted WAPE improvement: 19.4 percent
```

Linchpin's classification-routed forecast (99/100 sampled SKUs were
intermittent, routed to Croston's method) is ~19.4% more accurate than naive
last-value persistence in absolute terms (MAE). The demand-weighted WAPE
improvement (19.4%) reflects the same underlying error reduction -- with
every SKU scored over an identical 28-day window, pooled MAE equals the
per-SKU average, and demand-weighted WAPE improvement reduces to the same
ratio, so these two figures aren't independent corroboration, just two
equivalent views of one result.

The genuinely independent contrast is against the per-SKU-average WAPE
(unweighted mean of each SKU's own WAPE), which shows the opposite direction
(-3.2%): a known artifact of that specific metric on intermittent demand,
not a real weakness. A handful of near-zero-actual SKUs get an inflated
relative error from Croston's correctly-nonzero forecast, while naive's
"predict zero" hits a WAPE ceiling of 1.0 on those same SKUs -- exactly why
the real M5 competition itself does not score entries on plain per-series
WAPE, and why the demand-weighted number is the one to trust.

Note on reproducibility: the exact figures above depend on the environment.
They were captured on pandas 3.0.x with the base install (no `statsforecast`
extra) -- `df.sample()`'s RNG can select a different 100-SKU sample on a
different pandas major version, and `forecast_demand(method="auto")` would
route through AutoETS/TSB instead of Croston/SES if the `statsforecast`
extra is installed. Re-running this exact command in a different environment
may shift the precise numbers; the ~19% Linchpin margin (MAE, demand-weighted
WAPE) is the stable takeaway, not the last decimal place.

---

## Further reading in the book

| Scenario | Chapter |
|----------|---------|
| Volume discounts | §2.5.3 |
| Fill rate vs cycle SL | Ch. 7 |
| Optimal service level | Ch. 8 |
| Non-normal demand | Ch. 9 |
| Multi-echelon network | Ch. 10 |
| Newsvendor / perishables | Ch. 11 |

Official code snippets: [supchains.com/resources-invopt](https://supchains.com/resources-invopt)

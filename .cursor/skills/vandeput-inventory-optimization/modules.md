# Module map — Vandeput (2020)

## Part I–II (Ch. 1–5)

### `src/eoq.py`
- `compute_eoq(D, h, k)` → Q*, optimal cost
- `total_cost(Q, D, h, k)`
- Power-of-2 review period rounding

### `src/safety_stock.py`
- `service_level_factor(alpha)` → z
- `safety_stock(sigma_d, alpha, tau)` → Ss, inventory level

### `src/policies.py`
- `continuous_review_sq(...)` → (s, Q)
- `periodic_review_rs(...)` → (R, S)
- Uses `demand_over_risk_period` when `lead_time_std > 0`

### `src/simulation.py`
- `simulate_rs_policy(S, L, R, ...)`
- `simulate_sq_policy(s, Q, L, ...)`
- `lost_sales=True` on RS for §5.3.2
- Backorders on net inventory; returns cycle SL and period SL

### `src/export.py`
- `write_summary_csv`, `write_policy_comparison`
- Used by `examples/run_complete.py --export`

### `src/data_loader.py`
- `load_demand_csv`, `demand_stats`, `annualize_demand`

## Part III (Ch. 6–8)

### `src/risk_period.py`
- `demand_over_risk_period(mu, sigma, L, sigma_L, R)` → μ_x, σ_x

### `src/fill_rate.py`
- `normal_loss`, `fill_rate_from_inventory`, `safety_stock_for_fill_rate`
- `inverse_standard_loss` (polynomial or solver)

### `src/cost_optimization.py`
- `optimal_cycle_service_level_rs/sq` → α* = 1 − hR/b or 1 − hQ/(bD)
- `optimize_rs_policy`, `optimize_sq_policy`
- `compare_review_periods`

## Part IV (Ch. 9–13)

### `src/distributions.py`
- `select_distribution(data)` — skewness rule γ₁ > σ/μ
- `fit_gamma`, `safety_stock_gamma`, `gamma_loss`, `gamma_loss_inverse`

### `src/multi_echelon.py`
- `serial_gsm_cases(L, R)` — 2^(n−1) all-or-nothing patterns
- `optimize_serial_gsm` — min Σ(Ss_i · h_i)
- `echelon_inventory`, `echelon_orders`
- `simulate_serial_gsm` — discrete echelon simulation (§10.5)

### `src/newsvendor.py`
- `optimal_newsvendor_discrete(pmf, price, cost, salvage)`
- `muffin_pmf()` — Table 11.1
- `critical_ratio(cu, co)`

### `src/discrete_demand.py`
- `histogram_pmf`, `kde_pmf`, `scott_bandwidth`
- `DiscretePMF.cdf`, `.ppf`

### `src/simulation_opt.py`
- `simulate_rs_cost` — holding + ordering + backorder
- `find_best_safety_stock`, `find_best_safety_stock_smart_start`
- `optimize_rs_simulation` — scipy bounded search on Ss

## Examples

| Script | Chapters |
|--------|----------|
| `examples/run_part1_part2.py` | 2–5 |
| `examples/run_part3.py` | 7–8 |
| `examples/run_part4.py` | 9–13 |
| `examples/run_complete.py` | 1–13 + CSV export |

## Tests

```
tests/test_eoq.py
tests/test_safety_stock.py
tests/test_simulation.py
tests/test_fill_rate.py
tests/test_cost_optimization.py
tests/test_lead_time.py
tests/test_distributions.py
tests/test_multi_echelon.py
tests/test_newsvendor.py
tests/test_simulation_opt.py
```

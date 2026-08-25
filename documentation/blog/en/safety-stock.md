---
pair: safety-stock
lang: en
slug: safety-stock
title: "How to calculate safety stock, and the two ways the sigma goes wrong"
description: "Ss = z x sigma x sqrt(tau), every symbol read off the module that computes it, plus the two dispersion mistakes that quietly resize the buffer."
date: 2026-08-25
updated: 2026-08-25
query: "how to calculate safety stock"
answer: >
  Compute safety stock as Ss = z x sigma x sqrt(tau). Take z from the inverse
  normal at the cycle service level you are prepared to state, take sigma from
  the standard deviation of your one-step forecast errors rather than of your
  sales, and take tau as the number of periods you stay exposed: the lead time
  alone under continuous review, lead time plus review period when you order on
  a calendar.
cta: demo
offer: diagnostico-arranque
sources:
  - kind: code
    ref: src/safety_stock.py
  - kind: code
    ref: src/forecasting.py
  - kind: code
    ref: src/risk_period.py
  - kind: code
    ref: src/fill_rate.py
  - kind: book
    ref: "Vandeput, Inventory Optimization (Models and Simulations) — Safety Stock, Ch. 4"
  - kind: book
    ref: "Boylan & Syntetos, Intermittent Demand Forecasting — Croston's Method, Ch. 6.5"
faq:
  - q: "Where does 1.65 come from?"
    a: "From service_level_factor() in src/safety_stock.py, which is Phi^{-1}(alpha) evaluated with scipy's norm.ppf. At alpha = 0.95 it returns 1.6449, and it rejects any alpha outside the open interval (0, 1): a service level of 1 asks for an infinite buffer."
  - q: "Does a 95% cycle service level mean 95% of orders ship complete?"
    a: "No. Cycle service level is the probability of not stocking out during a cycle. Fill rate is the share of demand served from stock: src/fill_rate.py computes beta = 1 - Us / dc and sizes its buffer from the inverse normal loss function, not from Phi^{-1}(alpha)."
  - q: "Will ordering more often reduce my safety stock?"
    a: "Yes, and src/risk_period.py says by how much: tau = mean_lead_time + review_period. A shorter review period is a shorter tau, and the buffer moves with its square root, so halving tau cuts the buffer by about 29%, not by half."
  - q: "Should every SKU carry the same service level?"
    a: "No, and the engine does not assume it. jobs/inventory_optimization.py takes a per-product service_levels map, or derives one from the ABC-XYZ class with differentiate_by_class. tune_service_level() then nudges that level toward an observed fill-rate target, bounded to [0.50, 0.999]."
  - q: "How much sales history before this number means anything?"
    a: "Six periods. MIN_PERIODS_FOR_POLICY = 6 in src/forecasting.py: n periods give n-1 one-step errors, and the std of five errors already carries a relative standard error near 35%. Both entry points that size a purchase gate on that constant."
noindex: false
---

# How to calculate safety stock, and the two ways the sigma goes wrong

Compute safety stock as Ss = z x sigma x sqrt(tau). Take z from the inverse normal at the cycle service level you are prepared to state, take sigma from the standard deviation of your one-step forecast errors rather than of your sales, and take tau as the number of periods you stay exposed: the lead time alone under continuous review, lead time plus review period when you order on a calendar.

## The three inputs, and which one is contested

`src/safety_stock.py` does it in one line — equation 4.3 of chapter 4 in Vandeput's *Inventory Optimization*:

```
Ss = z_alpha * sigma_d * sqrt(tau)
```

- **`z_alpha`** — `service_level_factor()`, or `Phi^{-1}(alpha)`: the inverse standard normal at your cycle service level. It rejects any `alpha` outside the open interval (0, 1).
- **`sigma_d`** — dispersion of demand per period. The contested one; the next two sections are about it.
- **`tau`** (`risk_periods`) — how many periods you stay exposed, in the same unit as `sigma_d`.

It is also only half a reorder point: `src/policies.py` builds the continuous-review trigger as `s = mu_x + Ss`.

## Sigma is forecast error, not sales variation

The header of `src/forecasting.py` states the problem: **historically the dispersion was the raw sample std of demand. That is only correct for stationary demand.** The module hands the engine `error_std` instead — sigma_e, the standard deviation of one-step-ahead forecast errors.

If a SKU trends or has a season, a competent forecast follows it and the leftover errors are small; `=STDEV(weekly units)` does not know a forecast exists, so it charges the whole pattern to risk.

The module keeps the receipt. `ForecastResult.sigma_source` is derived rather than stored, so it cannot drift from the numbers it describes: `forecast_error_oos` when sigma_e came from errors the model never saw, `forecast_error` from in-sample residuals, `demand_std_fallback` when the raw demand std stood in, and `unavailable` when neither is finite and positive. That last label splits in two inside `to_engine_inputs`: a dispersion that is not a real number — NaN or infinite — raises, because a zero there would print "no buffer needed" as a confident fact; two finite dispersions that are merely zero are a measured flat history, and it passes `sigma = 0.0` on purpose. `jobs/qa.py` polices the difference: `unavailable` with a positive buffer does not ship.

## In-sample sigma is optimistic, and the module says by how much

In-sample residuals are what is left after a model has bent toward those exact points, so their spread is narrower than the errors you will live through. `rolling_origin_error_std` measures the honest version: fit on the history before each of the last twelve periods, forecast one step, keep the error.

Its docstring records when that matters. SES, Croston and moving average run on fixed smoothing constants, so their fitted values are already one-step-ahead predictions — within about 6% of the rolling-origin estimate. AutoETS fits parameters to the series and, on a seasonal one, understated the true one-step error by 17%. A 95% band sized off that is not a 95% band.

## Tau: how often you look is part of the risk

The docstring pins it: `tau` is the lead time L for a continuous-review (s, Q) policy, R + L for a periodic-review (R, S) one. `src/risk_period.py` computes `tau = mean_lead_time + review_period`, so continuous review is where the review period is zero. Order on a calendar and a Tuesday spike stays invisible until the next review.

Illustrative figures, a DTC brand reading weekly units off its sales export, supplier three weeks out:

| Input | Value (e.g.) |
|---|---|
| `demand_std_per_period` (sigma_e) | 25 units/week |
| Lead time L | 3 weeks |
| `cycle_service_level` (alpha) | 0.95 |
| `z_alpha = Phi^{-1}(0.95)` | 1.6449 |
| `risk_periods` (tau), (s, Q) policy | 3 |

`Ss = 1.6449 x 25 x sqrt(3) = 71 units`.

Move to a two-week ordering calendar and `tau = R + L = 5`: the buffer becomes 92 units, twenty-one bought by the calendar alone. Push service to 0.99 on the continuous policy and `z_alpha` becomes 2.3263, the buffer 101 units, about 40% more for four points. And if `STDEV` of weekly units read 34 where the forecast error was 25, the same formula returns 97 units: twenty-six extra (e.g.) against a pattern the forecast had already seen.

## When the SKU sells in bursts

The formula assumes normally distributed demand. A SKU that sells in bursts is not that, and `src/forecasting.py` tests before picking a method: an average demand interval (total periods over periods with demand) at or above `INTERMITTENT_ADI_THRESHOLD = 1.32`, the Syntetos-Boylan cut, takes the SKU off the smoothing default. That means Croston on the built-in path (Boylan & Syntetos, *Intermittent Demand Forecasting*, ch. 6.5), TSB with the modern forecaster in `src/forecasting_auto.py` installed. Skew is a third problem: `src/demand_variability.py` fits a gamma, where `Ss = iota_gamma - mu_x`, which no z describes.

## Compared with what

**Your spreadsheet.** The formula fits in one cell; that was never the hard part. What a cell cannot carry is provenance — which sigma went in, measured how — and it does not recompute itself on Monday.

**A planning SaaS in the Stocky or Netstock class.** The maths is in there; the gap is the default. The correct dispersion tends to be an optional setting rather than the one you get, and the tool still waits for you to open it.

**ERP min/max rules.** A min and a max are a policy's *output*, not a policy: no alpha, no tau, no sigma, so nothing is left to turn when service slips.

**A chatbot over your export.** A round figure with a fluent rationale, no definition of the sigma behind it, and a different figure tomorrow.

## What Kern does not do

**It does not predict demand.** The forecast carries its own bias, MAE and error std, and the buffer is sized against that error.

**It does not size a policy on a history that cannot support one.** `MIN_PERIODS_FOR_POLICY = 6` is, in the module's words, a refusal threshold and not a tuning knob: below it `jobs/inventory_optimization.py` marks the SKU `insufficient_history` and returns no quantity.

**It does not claim a service level it did not compute.** `achieved_service_level()` is the analytic inverse of `safety_stock()`: hand it a buffer, it returns the alpha that buffer buys.

**It does not write safety stock into your systems from a blog post.** Kern prepares the work and a human signs anything irreversible; of the four possible endings, three stop on a human by design.

The 0.95 above is the engine default (`service_level: float = 0.95` in `jobs/inventory_optimization.py`), not a recommendation.

## FAQ

### Where does 1.65 come from?

From `service_level_factor()` in `src/safety_stock.py`, which is `Phi^{-1}(alpha)` evaluated with scipy's `norm.ppf`. At alpha = 0.95 it returns 1.6449, and it rejects any alpha outside the open interval (0, 1): a service level of 1 asks for an infinite buffer.

### Does a 95% cycle service level mean 95% of orders ship complete?

No. Cycle service level is the probability of not stocking out during a cycle. Fill rate is the share of demand served from stock: `src/fill_rate.py` computes `beta = 1 - Us / dc` and sizes its buffer from the inverse normal loss function, not from `Phi^{-1}(alpha)`.

### Will ordering more often reduce my safety stock?

Yes, and `src/risk_period.py` says by how much: `tau = mean_lead_time + review_period`. A shorter review period is a shorter tau, and the buffer moves with its square root, so halving tau cuts the buffer by about 29%, not by half.

### Should every SKU carry the same service level?

No, and the engine does not assume it. `jobs/inventory_optimization.py` takes a per-product `service_levels` map, or derives one from the ABC-XYZ class with `differentiate_by_class`. `tune_service_level()` then nudges that level toward an observed fill-rate target, bounded to [0.50, 0.999].

### How much sales history before this number means anything?

Six periods. `MIN_PERIODS_FOR_POLICY = 6` in `src/forecasting.py`: n periods give n-1 one-step errors, and the std of five errors already carries a relative standard error near 35%. Both entry points that size a purchase gate on that constant.

## Next step

Sizing safety stock is [Start-up Diagnostic](/paquetes/diagnostico-arranque) work; the public scan does not print z times sigma. What it does print is the other half of the formula: [scan your receipts CSV](/demo) and it returns each supplier's observed lead time against the one they declared, with deviation and p90. That observed mean is the L inside your tau — all of it under continuous review, L + R once you add your ordering calendar — and that deviation is the `sigma_L` term.

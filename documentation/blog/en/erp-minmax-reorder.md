---
pair: minmax-erp
lang: en
slug: erp-minmax-reorder
title: "ERP min/max reorder points: the inputs a static rule never updates"
description: "A min/max reorder point freezes four inputs the day it is typed. What the ERP rule actually executes, and how a governed policy recomputes each one."
date: 2026-08-25
updated: 2026-08-25
query: "ERP min max reorder point"
answer: >
  A min/max reorder point is a number, not a policy. It freezes four inputs the
  day someone types it: a demand estimate, a lead time, a service level, and a
  dispersion. Nothing in the ERP re-derives them when reality moves. A governed
  policy recomputes each one from history, sizes the buffer from forecast error,
  and stops short of the write.
cta: diagnostico
offer: diagnostico-arranque
sources:
  - kind: code
    ref: jobs/excel_replenishment_job.py
  - kind: code
    ref: src/policies.py
  - kind: code
    ref: src/demand_variability.py
  - kind: code
    ref: src/safety_stock.py
  - kind: code
    ref: src/risk_period.py
  - kind: code
    ref: src/forecasting.py
  - kind: code
    ref: src/connectors/odoo.py
  - kind: book
    ref: "Vandeput, Inventory Optimization (Models and Simulations) — Ch. 4 (Safety Stock), Ch. 5 (Continuous Review Policy), Ch. 6 (Risk Period, Stochastic Lead Time)"
faq:
  - q: "How much sales history do I need before Kern will size anything?"
    a: "Six observed demand periods. MIN_PERIODS_FOR_POLICY = 6 in src/forecasting.py is a refusal threshold: below it the sigma estimate is more uncertain than a third of its own value, and the honest output is 'not enough history'."
  - q: "My sheet has both a reorder point and a weekly demand column. Which one wins?"
    a: "Demand. The job selects demand-cover whenever a demand column exists, even with a reorder point beside it, and covers cover_periods of demand instead of a multiple of the threshold."
  - q: "We have purchase orders already in transit. Does the plan subtract them?"
    a: "No, and the code says so rather than hiding it: quantities are computed from on-hand only, so a brand with inbound stock can be told to over-order. Subtract before approving."
  - q: "Do I have to connect anything to get a first number?"
    a: "No. A sheet with SKU, on-hand and either a reorder point or a demand per period is enough, and the public demo reads an exported CSV without writing anywhere."
noindex: false
---

# ERP min/max reorder points: the inputs a static rule never updates

A min/max reorder point is a number, not a policy. It freezes four inputs the day someone types it: a demand estimate, a lead time, a service level, and a dispersion. Nothing in the ERP re-derives them when reality moves. A governed policy recomputes each one from history, sizes the buffer from forecast error, and stops short of the write.

## The four numbers frozen inside a min

Unpack what a min of 360 units contains and the problem names itself. Someone multiplied an expected weekly demand by a lead time, added a cushion whose size implied a service level, and sized it against an assumed spread of outcomes. Four inputs, one output — and only the output gets stored. When the supplier slips from four weeks to six, the record still reads 360.

That is the distance between a threshold and a policy: a policy keeps its inputs and can be re-derived when one moves. A min cannot be re-derived, only retyped.

## What the rule literally executes

Kern reproduces the rule before replacing it, so start with that code. In `jobs/excel_replenishment_job.py`, `reorder-point` mode sets the target at `reorder_point * order_up_to_factor` — the factor defaults to 2.0 — and orders the gap **only** once on-hand has fallen below the reorder point; above it, the quantity is zero.

`demand-cover` mode differs in a way worth knowing. It takes over as soon as the sheet carries a demand column, whether or not a reorder point sits beside it, and its target is `demand_per_period * cover_periods`, eight by default. It has no trigger at all: anything under target is topped up. Two modes, both faithful, neither able to say where its own number came from.

## Three failures that stay invisible

**A blank cell.** No alert exists for a rule that was never written: nothing fires, and the row reads like a healthy one. The job refuses that reading. A blank reorder point or demand cell never coalesces to zero; the row is excluded, counted in `n_unplanned`, and reported under the heading "Rows the plan could NOT cover."

**Two rows for one SKU.** Split a product across locations and the sheet grows a second line. The job fails closed there: a duplicate SKU raises before any planning happens, because the write-back cell for that product would be ambiguous. One threshold per SKU is an assumption the rule makes silently and the code makes loudly.

**An item that sells six times a year.** Its mean describes nothing. `src/forecasting.py` measures the average demand interval and, past `INTERMITTENT_ADI_THRESHOLD = 1.32` — the Syntetos-Boylan cutoff — routes to Croston or TSB instead of assuming a normal curve. A min/max applies one arithmetic to the daily seller and the annual one alike.

## Recomputing the number, one step at a time

`src/forecasting.py` produces `error_std`: sigma of the one-step-ahead forecast error, the dispersion safety stock is meant to consume rather than the raw standard deviation of demand, with `sigma_source` recording which of the two reached the engine. Below `MIN_PERIODS_FOR_POLICY = 6` observed periods it produces none — the comment there calls it a refusal threshold, not a tuning knob.

`src/risk_period.py` widens the window: tau is the lead time under (s,Q), lead time plus review period under (R,S); mean demand across it is `mu_d * tau`; and the spread combines both sources of uncertainty as `sigma_x = sqrt(tau * sigma_d^2 + sigma_L^2 * mu_d^2)`.

Two formulas live in this repository and only one governs. `src/policies.py` asks `safety_stock_risk_period` in `src/demand_variability.py` for it, and that function's normal branch is `Ss = z_alpha * sigma_x`: z applied to the dispersion of the entire risk period, with `z_alpha = Phi^-1(alpha)` from `service_level_factor` in `src/safety_stock.py`. That same file carries Vandeput's equation 4.3, `Ss = z_alpha * sigma_d * sqrt(tau)`, which returns an identical number only while sigma_L is zero, since sigma_x then reduces to exactly `sigma_d * sqrt(tau)`. Once the lead time varies the two part company, and the first is what runs. From there `s = mu_x + Ss` under (s,Q), with Q from the EOQ, or `S = mu_x + Ss` under (R,S) — chapters 4, 5 and 6 of Vandeput, *Inventory Optimization (Models and Simulations)*.

Illustrative figures only (e.g.): a direct-to-consumer brand whose best-selling refill averages 90 units a week at a forecast-error sigma of 28, at a 97.5% cycle service level (`z ≈ 1.960`).

| Scenario | tau | sigma_L | mu_x | sigma_x | Ss | s or S |
|---|---|---|---|---|---|---|
| The min already typed in | assumed | ignored | — | — | — | 360 |
| (s,Q), 4-week lead time | 4 wk | 0 | 360 | 56.0 | 110 | 470 |
| (s,Q), same lead, sigma_L 1.5 wk | 4 wk | 1.5 wk | 360 | 146.2 | 286 | 646 |
| (R,S), weekly review, 4-week lead | 5 wk | 0 | 450 | 62.6 | 123 | 573 |

Row one is the typed min: four weeks of average demand, zero buffer. Row three is the expensive one — admitting the lead time itself moves nearly triples the buffer, the term the single-period formula has no slot for.

## Where each alternative stops

| Alternative | What it does well | Where it stops |
|---|---|---|
| A spreadsheet min/max | Free, immediate, legible to everyone | No memory: nothing records which assumptions produced the number |
| The ERP's own rule | Executes perfectly, every day, without fatigue | Runs the same threshold forever; cannot learn the supplier changed |
| Inventory planning SaaS (Netstock/Stocky class) | A real tool with the right models inside | The method steps arrive as optional settings, and you still operate it |
| A demand planner | Runs the method properly, exercises judgement | Knowledge leaves when the person does, and SKU 4,000 never gets touched |
| A chatbot over your inventory export | Fluent, instant, feels like an answer | A confident number with no file behind it, and a different one next run |

Kern's claim is narrower: recompute the threshold, name the file it came from, hand it back for a person to apply.

## The part that stays manual

Nothing gets overwritten quietly. The plan is staged as a dry-run changeset, and the inputs it rests on are staged alongside it as guard cells — stock and reorder signal, restated as no-op edits — so if either moved between staging and approval, the commit-time drift check refuses rather than applying a stale plan. Approval, backup, atomic write and rollback follow, through `nosilo_core.writeback`, the safe-staging write plane inside `nosilo_core`, the domain-agnostic control plane under writeback and guided outcomes.

Odoo is one example of that path, not a requirement: in `src/connectors/odoo.py`, `stage_restock` prepares a reorder-point update against `stock.warehouse.orderpoint.product_min_qty` and explicitly does not write. None of this is set-and-forget either — of the four possible endings, three stop on a human by design.

## FAQ

### How much sales history do I need before Kern will size anything?

Six observed demand periods. `MIN_PERIODS_FOR_POLICY = 6` in `src/forecasting.py` is a refusal threshold: below it the sigma estimate is more uncertain than a third of its own value, and the honest output is "not enough history".

### My sheet has both a reorder point and a weekly demand column. Which one wins?

Demand. The job selects `demand-cover` whenever a demand column exists, even with a reorder point beside it, and covers `cover_periods` of demand instead of a multiple of the threshold.

### We have purchase orders already in transit. Does the plan subtract them?

No, and the code says so rather than hiding it: quantities are computed from on-hand only, so a brand with inbound stock can be told to over-order. Subtract before approving.

### Do I have to connect anything to get a first number?

No. A sheet with SKU, on-hand and either a reorder point or a demand per period is enough, and the public demo reads an exported CSV without writing anywhere.

## Where to start

Book the [Start-up Diagnostic](/paquetes/diagnostico-arranque) to run the method against your own SKUs, arithmetic visible, thin histories named instead of filled in. If a lighter first step suits better, [scan your CSV](/demo) — read-only, nothing written.

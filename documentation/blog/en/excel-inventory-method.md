---
pair: excel-vs-method
lang: en
slug: excel-inventory-method
title: "Where inventory planning in Excel actually breaks for a DTC brand"
description: "A workbook computes cover days correctly. What it cannot keep is where a threshold came from, a blank that is not a zero, and the right to refuse."
date: 2026-08-25
updated: 2026-08-25
query: "inventory planning in Excel"
answer: >
  A workbook computes cover days and reorder points correctly. What it cannot do
  is record which threshold produced a class, keep a blank cell from being read
  as a zero, or refuse to answer when the history is too short. Those three are
  the policy, not the arithmetic. Kern runs them and writes the result back into
  the same file.
cta: demo
offer: diagnostico-arranque
sources:
  - kind: code
    ref: src/excess_obsolete.py
  - kind: code
    ref: jobs/excess_obsolete_job.py
  - kind: code
    ref: webapp/demo_scan.py
  - kind: code
    ref: src/forecasting.py
  - kind: code
    ref: src/connectors/excel.py
  - kind: code
    ref: jobs/intake.py
  - kind: code
    ref: documentation/templates/stock_template.csv
faq:
  - q: "Our stock lives in a Google Sheet, not an .xlsx. Does that work?"
    a: "Export it. The intake reads CSV or Excel by file extension, and on the Excel path it finds the header by scoring every sheet and row for known column names, so a cover page with the data on sheet two still maps. The writeback connector is separate: it edits a workbook file, not a live Google Sheet."
  - q: "Will the free scan tell me what to reorder?"
    a: "No. It runs excess-and-obsolete, ABC and financial KPIs over one snapshot; ABC is exact, and XYZ degenerates because a snapshot carries no time series. Sizing a purchase needs demand history, and below six observed periods the honest output is not enough history, never a quantity."
  - q: "My workbook has macros, a pivot and a chart. What survives a write?"
    a: "An .xlsm is opened with its VBA project preserved, so the macros are still there afterwards and are never executed: running them needs a live Excel over COM. Values, formulas and styles round-trip, but charts can degrade, hence the byte-exact copy written beside the file first."
  - q: "We hold stock at two 3PL warehouses. Does the scan handle that?"
    a: "No. The snapshot carries one row per SKU, so the split is invisible: the same SKU can be short at one node and long at the other, and no column says which way to move it. That is multi-echelon planning, the work of a later package."
noindex: false
---

# Where inventory planning in Excel actually breaks for a DTC brand

A workbook computes cover days and reorder points correctly. What it cannot do is record which threshold produced a class, keep a blank cell from being read as a zero, or refuse to answer when the history is too short. Those three are the policy, not the arithmetic. Kern runs them and writes the result back into the same file.

## The arithmetic your sheet already gets right

Start with what a spreadsheet does perfectly well. From one stock snapshot in the five columns of `documentation/templates/stock_template.csv` — `product_id`, `on_hand`, `daily_demand`, `unit_cost`, `days_since_last_sale` — the excess-and-obsolete classification follows from four rules:

- cover is `on_hand / daily_demand`, infinite when demand is zero;
- **dead** when `daily_demand` is zero, or a *recorded* idle time reaches the idle threshold;
- **excess** when cover exceeds the cover target (90 days by default), by `on_hand − target × daily_demand` units;
- **healthy** otherwise.

Four illustrative rows from a direct-to-consumer skincare catalogue (not a measured client):

| `product_id` | `on_hand` | `daily_demand` | `days_since_last_sale` | Cover | Class | Units too many |
|---|---|---|---|---|---|---|
| GLOW-SERUM-30 | 900 | 6 | 3 | 150 d | excess | 360 |
| REFILL-POUCH | 300 | 5 | 2 | 60 d | healthy | 0 |
| GIFT-SET-22 | 250 | 0.4 | *blank* | 625 d | excess | 214 |
| TRAVEL-KIT-A | 400 | 0 | *blank* | n/a | dead | 400 |

Every cell there is a formula an operator can write. The arithmetic is not the problem.

## Where the threshold came from

Two constants decided that table and neither appears in it. The cover target is 90 days. The idle threshold in the public scan is 365 days rather than the engine's 180, and the reason sits beside the constant in `webapp/demo_scan.py`: the promised output is "no movement in 12+ months", and at 180 days a seasonal business throws false positives that no human is standing by to filter on a first-contact scan.

Move the threshold and row three changes its mind: had GIFT-SET-22 carried a recorded 400 days since its last sale, it would be dead rather than excess, and all 250 units would be written off instead of 214. The workbook stores the answer, not which threshold produced it.

## A blank that is not a zero

Two rows above have a blank `days_since_last_sale`, and the blank is load-bearing. In `jobs/excess_obsolete_job.py` it becomes `None`, not `0.0` — never recording a last sale is not the same as selling today — so an unknown idle time does not fire the idle rule. A sheet that fills blanks with zero reads those rows as sold today: the reading that most flatters them.

That same intake refuses a blank `daily_demand` outright, naming the SKUs and the consequence: read as zero it marks the SKU dead, and dead writes off the entire on-hand quantity. `src/forecasting.py` refuses a non-finite demand history for the same reason — `NaN < 0` is false, so a naive sign check waves it through.

## Which dispersion it used

Safety stock is sized from the dispersion of the **forecast error**, not of sales. `ForecastResult.sigma_source` records which one reached the engine, with exactly four values: out-of-sample error, in-sample error, raw demand standard deviation standing in, or unavailable. A worksheet has no column for that, so the question never gets asked.

The gap between the first two is measured, not asserted. The out-of-sample figure walks the origin forward: fit on history up to a period, predict one step, record the miss. In-sample residuals are what remains after a model has bent toward those exact points, so they run narrow — measured in this repository, 17% understated for AutoETS on a seasonal series. A 95% band sized off that is not a 95% band.

## The refusal

A spreadsheet always returns something. Six observed periods give five one-step errors, and the standard deviation of five errors carries a relative standard error near 35%: more uncertain than a third of its own value. So `MIN_PERIODS_FOR_POLICY` stops the money path below six periods, and the output becomes "not enough history", never a quantity. A refusal threshold, not a tuning knob.

The public scan has the same reflex at the other end: if any of its three jobs flags an issue in `verify()`, or a headline number is not finite, the scan is QA-failed and no artifact is written.

## Writing into the workbook instead of replacing it

None of this asks you to leave the file. `src/connectors/excel.py` exposes a workbook through the same store surface the ERP connectors use, so signed approvals, idempotency and audit come from `nosilo_core`, the domain-agnostic control plane under writeback. A sheet is the entity and a cell is the field; a helper turns "SKU-002, column Reorder Point, 90" into `D5`, finding the header row within the first twenty rows.

What it refuses matters more than what it writes. Before committing it re-reads the live file and aborts if any staged cell no longer holds the value it was staged against, so a colleague saving mid-change is never overwritten. It copies the byte-exact original beside the file, then saves to a temporary file and swaps it in one move. Duplicate keys fail closed instead of picking a row. `.xlsm` macros are preserved and never executed. If a process dies between the swap and the audit record, the orphaned backup is the tripwire: the next commit for that key refuses.

Then a person sees the before and the after and signs. Of the four possible endings, three stop on a human by design.

## Compared with what

**The sheet on its own** — the workbook a DTC operator keeps and the 3PL never sees. It computes; it does not remember, and one person holds the formulas.

**A Netstock-class planning SaaS.** You get the tool, the correct method stays an optional setting most users never change, and you still operate it.

**Min/max rules in the ERP or the 3PL portal.** Static thresholds, no demand pattern behind them, no record of who moved them. Kern leaves them as the execution arm.

**A chatbot over your export.** A plausible paragraph, no threshold you can read, no source you can check.

Kern is none of those four: the method running against the file you already keep, with a human signing anything irreversible.

## What Kern does not do

It does not ask you to leave Excel — the deliverables **are** Excel, plus the report and the chart. It does not run your macros, it is not a WMS, and there is no Shopify or Amazon inventory integration. The scan and the connector are separate paths; the scan writes nothing back.

## FAQ

### Our stock lives in a Google Sheet, not an .xlsx. Does that work?

Export it. The intake reads CSV or Excel by file extension, and on the Excel path it finds the header by scoring every sheet and row for known column names, so a cover page with the data on sheet two still maps. The writeback connector is separate: it edits a workbook file, not a live Google Sheet.

### Will the free scan tell me what to reorder?

No. It runs excess-and-obsolete, ABC and financial KPIs over one snapshot; ABC is exact, and XYZ degenerates because a snapshot carries no time series. Sizing a purchase needs demand history, and below six observed periods the honest output is not enough history, never a quantity.

### My workbook has macros, a pivot and a chart. What survives a write?

An `.xlsm` is opened with its VBA project preserved, so the macros are still there afterwards and are never executed: running them needs a live Excel over COM. Values, formulas and styles round-trip, but charts can degrade, hence the byte-exact copy written beside the file first.

### We hold stock at two 3PL warehouses. Does the scan handle that?

No. The snapshot carries one row per SKU, so the split is invisible: the same SKU can be short at one node and long at the other, and no column says which way to move it. That is multi-echelon planning, the work of a later package.

## Next step

Send the export you already have — one snapshot, five columns. [Scan your CSV or workbook in the demo](/demo): it returns your own stock under the rules above, read-only. Running them over your real time series, with the QA gate on the deliverable, is where the [Start-up Diagnostic](/paquetes/diagnostico-arranque) begins.

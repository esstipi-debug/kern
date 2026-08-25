---
pair: excess-obsolete
lang: en
slug: dead-stock
title: "Dead stock: how to size the cash tied up in inventory that stopped moving"
description: "Dead, excess and healthy are a classification of on-hand stock with stated thresholds, not a judgement call. The rule, the columns, and the cash ranking."
date: 2026-08-25
updated: 2026-08-25
query: "dead stock cash tied up in inventory"
answer: >
  Dead stock is on-hand inventory with zero daily demand, or with a known idle
  time at or past the dead threshold. Excess stock still moves but holds more
  days of cover than the target. Healthy is the rest. Each at-risk SKU is sized
  as at-risk units times unit cost, and the list is ranked by that cash figure,
  so the answer is an ordered dollar ranking rather than an opinion.
cta: demo
offer: diagnostico-arranque
sources:
  - kind: code
    ref: src/excess_obsolete.py
  - kind: code
    ref: documentation/templates/stock_template.csv
faq:
  - q: "What is the minimum set of columns?"
    a: "product_id, on_hand and daily_demand are the minimum. unit_cost is what turns units into a cash ranking, and days_since_last_sale is optional. Those five are the columns in documentation/templates/stock_template.csv."
  - q: "What is the difference between dead and excess?"
    a: "Dead has stopped moving: daily demand at or below zero, or a known idle time at or past 180 days. Excess is still moving but carries more than the 90-day cover target. A SKU with zero demand is never classified excess, it is dead."
  - q: "Does the scan write anything to my ERP or store?"
    a: "No. The scan reads a CSV you export and returns a classification and a ranking. There is no writeback in the E&O path, and there is no Shopify or Amazon inventory integration."
  - q: "Are the dollar figures in this article from a real client?"
    a: "No. Every number here is illustrative and built from the example rows in the repository template. There are no client results, recovery amounts or testimonials in this article."
  - q: "What is the paid step after the free scan?"
    a: "The Start-up Diagnostic. The public demo runs the same family of analysis that the diagnostic begins with, so the scan tells you whether the paid step is worth booking before you book it."
noindex: false
---

# Dead stock: how to size the cash tied up in inventory that stopped moving

Dead stock is on-hand inventory with zero daily demand, or with a known idle time at or past the dead threshold. Excess stock still moves but holds more days of cover than the target. Healthy is the rest. Each at-risk SKU is sized as at-risk units times unit cost, and the list is ranked by that cash figure, so the answer is an ordered dollar ranking rather than an opinion.

## What you are measuring

Most inventory conversations stall because "dead stock" is used as a feeling. Someone walks the warehouse, points at a pallet nobody has touched since the last peak, and calls it dead. The next person calls the same pallet safety stock. Neither reading can be checked, so the argument is settled by whoever is more senior.

The alternative is to make it a classification with stated thresholds. In `src/excess_obsolete.py` every SKU lands in exactly one of three buckets, and the rule that put it there is written down:

- **Dead** — `daily_demand <= 0`, **or** a known idle time at or past `dead_threshold_days`, which defaults to 180. All on-hand counts as at-risk.
- **Excess** — still moving, but days of cover (`on_hand / daily_demand`) is greater than `target_cover_days`, which defaults to 90. At-risk units are the on-hand above that target.
- **Healthy** — everything else. Zero at-risk units.

The important part is not the specific numbers. It is that they are parameters with defaults you can read, argue with and change, rather than a threshold living in one analyst's head.

## How it is calculated

Days of cover is `on_hand / daily_demand`. When demand is zero or not finite, cover is infinite, which is the honest representation of a SKU that will never sell through on current behaviour.

At-risk units differ by bucket. For a dead SKU, every unit on hand is at risk, because there is no demand to consume any of it. For an excess SKU, only the amount above the cover target is at risk:

```
cover        = on_hand / daily_demand
dead         : at_risk_units = on_hand
excess       : at_risk_units = on_hand - 90 * daily_demand
at_risk_cash = at_risk_units * unit_cost
```

The ranking is `at_risk_cash` descending. That ordering is the deliverable. A list of SKUs sorted alphabetically, or by units, tells you nothing about where to start; sorted by the cash each one is holding, the first five rows are the meeting.

Three illustrative SKUs, built from the example rows in `documentation/templates/stock_template.csv` (e.g. only, not client data):

| SKU | On hand | Daily demand | Idle days | Unit cost | Cover | Class | At-risk cash |
|---|---|---|---|---|---|---|---|
| A | 800 | 0 | 220 | $8.00 | infinite | dead | $6,400 |
| B | 900 | 5 | 12 | $12.50 | 180 d | excess | $5,625 |
| C | 150 | 3.5 | 10 | $12.50 | 42.9 d | healthy | $0 |

SKU A fires the dead rule twice over: demand is zero and idle time is past 180 days. All 800 units are at risk. SKU B is healthy in the sense that it sells, and still holds twice the cover target, so 450 units above the target are at risk. SKU C is inside the target and contributes nothing.

Note the ordering. SKU B has more units on hand than SKU A, and it still ranks second, because the ranking is cash and not volume.

## The optional column that changes the answer

`days_since_last_sale` is optional, and it defaults to `None` rather than `0`. That distinction is doing real work, and the module comment says why:

> It used to default to 0.0, i.e. "sold today" — the reading that most flatters the SKU, and the one that keeps a genuinely dead one out of the count.

If an unknown idle time were read as zero, every SKU with a missing value would look like it sold today, and the idleness rule would never fire for exactly the rows where you have the least information. Unknown now simply does not trigger the rule. A SKU with missing idle time can still be classified dead, but only through the demand test, not by accident.

The practical consequence for your export: if you can supply idle time, supply it. If you cannot, leave it empty rather than filling zeros. A zero is a claim; an empty cell is the truth.

## Compared with what

**A dashboard that charts inventory aging.** Aging buckets show you a distribution: how much sits in 0–30 days, 30–60, 90-plus. That is a picture, not a decision. It does not state a rule that separates dead from excess, and it does not emit a cash ranking, so two people can look at the same chart and disagree about what to liquidate. The classification above commits to a rule and produces an ordered list.

**A spreadsheet without a cover rule.** Excel can compute days of cover in one column. What it usually does not carry is a stated target, a stated dead threshold, and a consistent treatment of missing idle data across every row and every month. The formula is easy; the discipline of applying the same one twice is what erodes.

**An ERP with min/max thresholds.** Min and max tell the system when to reorder. They do not size the cash already sitting above the target, and they do not classify a zero-demand SKU as anything at all, because a SKU with no demand simply never trips a reorder point. It stays quietly on hand.

**A chatbot over your inventory export.** It will produce a fluent paragraph about your dead stock. What it will not do is commit to a threshold you can read, or return the same classification twice for the same file.

## What Kern does not do

The scan reads the CSV you export. It does not write to your ERP, it does not update your store, and it is not an inventory integration with Shopify or Amazon. The E&O path takes a file, classifies it and returns a deliverable with the ranking and a recommended action per class.

The recommended actions are deliberately blunt and come from the module itself: liquidate, return to vendor or write off for dead; stop buying, redistribute or promote to draw down for excess; monitor and take no action for healthy. They are prompts for a decision, not the decision.

More generally, Kern prepares work and a human signs anything irreversible. Of the four possible endings to a guided outcome, three stop on a human by design.

This article also carries no client results. Every figure above is illustrative and derived from the repository's own example template.

## FAQ

### What is the minimum set of columns?

`product_id`, `on_hand` and `daily_demand` are the minimum. `unit_cost` is what turns units into a cash ranking, and `days_since_last_sale` is optional. Those five are the columns in `documentation/templates/stock_template.csv`.

### What is the difference between dead and excess?

Dead has stopped moving: daily demand at or below zero, or a known idle time at or past 180 days. Excess is still moving but carries more than the 90-day cover target. A SKU with zero demand is never classified excess, it is dead.

### Does the scan write anything to my ERP or store?

No. The scan reads a CSV you export and returns a classification and a ranking. There is no writeback in the E&O path, and there is no Shopify or Amazon inventory integration.

### Are the dollar figures in this article from a real client?

No. Every number here is illustrative and built from the example rows in the repository template. There are no client results, recovery amounts or testimonials in this article.

### What is the paid step after the free scan?

The Start-up Diagnostic. The public demo runs the same family of analysis that the diagnostic begins with, so the scan tells you whether the paid step is worth booking before you book it.

## Next step

Export the five columns above and [scan your file in the demo](/demo). It returns the classification and the cash ranking for your own SKUs, read-only, and it is the same family of analysis the [Start-up Diagnostic](/paquetes/diagnostico-arranque) begins with.

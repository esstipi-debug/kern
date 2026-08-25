---
pair: excess-obsolete
lang: en
slug: dead-stock
title: "Dead stock: the cash ranking your inventory export can already produce"
description: "Dead, excess and healthy are three buckets with one rule each. The rule, the columns, and why the biggest cash sink is rarely the pallet you stare at."
date: 2026-08-25
updated: 2026-08-25
query: "dead stock cash tied up in inventory"
answer: >
  Dead stock is on-hand inventory with zero daily demand, or a known idle time
  at or past the dead threshold. Excess still sells but carries more days of
  cover than the target. Healthy is the rest. Each bucket sizes its own at-risk
  units, multiplies them by unit cost, and the output is a list ranked by that
  cash figure rather than by how much floor space the pile occupies.
cta: demo
offer: diagnostico-arranque
sources:
  - kind: code
    ref: src/excess_obsolete.py
  - kind: code
    ref: documentation/templates/stock_template.csv
faq:
  - q: "Can I run this on a Shopify or Amazon inventory export?"
    a: "You can run it on any CSV you export yourself, from any system, as long as it carries the columns. There is no Shopify or Amazon inventory integration: you produce the file, the scan reads it."
  - q: "My demand figures are monthly, not daily. Does that break it?"
    a: "It breaks the cover number, which is why it matters. Cover is on_hand divided by daily_demand, and the dead threshold is in days. Convert to a per-day rate before you upload, or every cover figure comes out roughly thirty times too long."
  - q: "Does the SKU with the most units always rank first?"
    a: "No, and that is the point. Ranking is at-risk units times unit cost. A cheap pallet of 1,200 dead units can rank below 460 excess units of something expensive, which is the opposite of what walking the warehouse suggests."
  - q: "What happens to rows with no unit cost?"
    a: "unit_cost defaults to zero in SkuStock, so their at-risk cash computes to zero and they sink to the bottom of the ranking regardless of how bad they are. Supply unit cost or the ranking is meaningless."
  - q: "Is this the same as an ABC analysis?"
    a: "No. ABC ranks SKUs by how much value moves through them. This ranks by how much value has stopped moving. A high-turn A item contributes nothing here; a forgotten C item can top the list."
noindex: false
---

# Dead stock: the cash ranking your inventory export can already produce

Dead stock is on-hand inventory with zero daily demand, or a known idle time at or past the dead threshold. Excess still sells but carries more days of cover than the target. Healthy is the rest. Each bucket sizes its own at-risk units, multiplies them by unit cost, and the output is a list ranked by that cash figure rather than by how much floor space the pile occupies.

## Three buckets, one rule each

The reason dead stock never gets dealt with is that nobody can say where it starts. Ask three people to point at it and you get three different pallets, and the one that wins is whoever argues hardest. There is no way to check any of the three answers, so the conversation repeats next quarter.

`src/excess_obsolete.py` replaces the argument with three rules you can read:

- **Dead** — `daily_demand <= 0`, **or** a known idle time at or past `dead_threshold_days`, default 180. Every unit on hand is at risk.
- **Excess** — still selling, but `on_hand / daily_demand` exceeds `target_cover_days`, default 90. Only the units above that target are at risk.
- **Healthy** — everything else, contributing zero.

Two things follow from writing it this way. A SKU with zero demand can never be classified excess, however healthy the rest of its numbers look, because the demand test runs first. And the thresholds are arguments with defaults, so "should 180 be 120 for us?" becomes a decision somebody makes on the record instead of a habit nobody wrote down.

## Sizing the cash, not the units

Days of cover is `on_hand / daily_demand`, and it goes to infinity when demand is zero or non-finite. That is the correct answer, not a bug: a SKU with no demand will not sell through on current behaviour, and any finite cover figure would be a polite fiction.

At-risk units depend on the bucket, then cost turns them into a ranking:

```
cover        = on_hand / daily_demand
dead         : at_risk_units = on_hand
excess       : at_risk_units = on_hand - 90 * daily_demand
at_risk_cash = at_risk_units * unit_cost
rank by at_risk_cash, descending
```

Three illustrative rows, in the shape of `documentation/templates/stock_template.csv` (e.g. only, not from any client):

| SKU | On hand | Daily demand | Idle days | Unit cost | Cover | Bucket | At-risk cash |
|---|---|---|---|---|---|---|---|
| Seasonal apparel, prior run | 1,240 | 0 | 310 | $4.20 | infinite | dead | $5,208 |
| Hero product, post-spike | 640 | 2 | 40 | $18.00 | 320 d | excess | $8,280 |
| Core replenishment item | 300 | 6 | 5 | $9.00 | 50 d | healthy | $0 |

```kern-figure
{
 "type": "buckets",
 "items": [
  {
   "label": "Excess",
   "tone": "excess",
   "units": "460 un.",
   "cash": "$8,280",
   "value": 8280
  },
  {
   "label": "Dead",
   "tone": "dead",
   "units": "1,240 un.",
   "cash": "$5,208",
   "value": 5208
  },
  {
   "label": "Healthy",
   "tone": "healthy",
   "units": "0 un.",
   "cash": "$0",
   "value": 900
  }
 ],
 "label": "The same three SKUs, ordered by at-risk cash: the excess row outranks the dead one. Illustrative figures, not client data."
}
```

Look at which row wins. The apparel is unambiguously dead: no demand, idle for ten months, 1,240 units of it. It is also the pallet anyone walking the warehouse would point at. And it ranks **second**, because 460 excess units of an $18 hero product hold more cash than 1,240 units of $4.20 apparel.

That inversion is the whole argument for computing this instead of eyeballing it. The obvious pile and the expensive pile are different piles, and volume is what your eyes measure while cash is what your bank measures.

The hero product is also the more uncomfortable finding, because it is still selling. Nobody flags a SKU that ships every day. It bought 320 days of cover during a demand spike that has since ended, and 250 of those days are cash sitting still.

## The column most exports leave out

`days_since_last_sale` is optional and defaults to `None`, not to `0`. The module comment explains why that distinction was worth the code:

> It used to default to 0.0, i.e. "sold today" — the reading that most flatters the SKU, and the one that keeps a genuinely dead one out of the count.

Read an unknown idle time as zero and every row missing that field looks like it sold this morning. The idleness rule then never fires precisely where your data is thinnest, which is where dead stock hides. Under the current behaviour, unknown simply does not trigger the rule: such a SKU can still be classified dead, but only by failing the demand test, never by accident.

For your export this means one thing. Supply idle time when you have it, and leave the cell empty when you do not. A zero is a claim about the SKU. An empty cell is the truth about your data.

## Why the aging report does not answer this

**Inventory aging dashboards** show a distribution: how much value sits in each age band. That is a picture of the past, not a decision about the present. No stated rule separates dead from excess, no cash ranking comes out the other side, and two people reading the same chart can disagree about what to liquidate without either being wrong.

**A spreadsheet with a cover column** gets the arithmetic right. What it rarely keeps is a stated cover target, a stated dead threshold, and one consistent treatment of missing idle data applied to every row this month and again next month. The formula is trivial; running the same one twice is the part that erodes.

**Stocky, Netstock-class SaaS and similar tools** will classify slow movers. The question worth asking of any of them is which threshold produced the classification and whether you can see it. A rule you can read is a rule you can argue with; a rule you cannot read is a vendor's opinion with a chart around it.

**A chatbot pointed at your export** returns a fluent paragraph about your dead stock. It will not commit to a threshold you can inspect, and it will not return the same classification twice for the same file.

## What the scan does not do

It reads the CSV you export and returns a classification plus a ranking. It does not write to your ERP, it does not touch your storefront, and it is not an inventory integration with Shopify or Amazon. The E&O path takes a file, classifies it, and hands back a deliverable.

The recommended action per bucket comes from the module and stays deliberately blunt: liquidate, return to vendor or write off for dead; stop buying, redistribute or promote to draw down for excess; monitor and do nothing for healthy. Those are prompts for a decision. Kern prepares the work and a human signs anything irreversible: of the four possible endings to a guided outcome, three stop on a human by design.

No client results appear anywhere on this page. Every figure above is illustrative and shaped after the repository's own example template.

## FAQ

### Can I run this on a Shopify or Amazon inventory export?

You can run it on any CSV you export yourself, from any system, as long as it carries the columns. There is no Shopify or Amazon inventory integration: you produce the file, the scan reads it.

### My demand figures are monthly, not daily. Does that break it?

It breaks the cover number, which is why it matters. Cover is `on_hand` divided by `daily_demand`, and the dead threshold is in days. Convert to a per-day rate before you upload, or every cover figure comes out roughly thirty times too long.

### Does the SKU with the most units always rank first?

No, and that is the point. Ranking is at-risk units times unit cost. A cheap pallet of 1,200 dead units can rank below 460 excess units of something expensive, which is the opposite of what walking the warehouse suggests.

### What happens to rows with no unit cost?

`unit_cost` defaults to zero in `SkuStock`, so their at-risk cash computes to zero and they sink to the bottom of the ranking regardless of how bad they are. Supply unit cost or the ranking is meaningless.

### Is this the same as an ABC analysis?

No. ABC ranks SKUs by how much value moves through them. This ranks by how much value has stopped moving. A high-turn A item contributes nothing here; a forgotten C item can top the list.

## Next step

Export `product_id`, `on_hand`, `daily_demand`, `unit_cost` and `days_since_last_sale`, then [scan your file in the demo](/demo). It returns the buckets and the cash ranking for your own SKUs, read-only, and it is the same family of analysis the [Start-up Diagnostic](/paquetes/diagnostico-arranque) opens with.

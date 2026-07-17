# Kern — Cold Outreach Email Templates (First-Client Playbook)

> Working asset, not a committed deliverable. Companion to
> `documentation/MONETIZATION_BRIEF.md` and the GTM research captured in project
> memory `kern-gtm-first-client-research-2026-07-16`. Targets the primary ICP
> (US/UK/AU, $1-15M Shopify/Amazon retailers) in English — the buyer's language.
> A LATAM/Spanish variant is a quick adaptation of the same structure if needed.

## The one rule that makes this work

**Every `[bracketed]` field must come from a REAL Kern run against the
prospect's OWN public data, done before you write the email.** Never invent a
number. The whole tactic (audit-as-icebreaker) only works because the finding
is true and specific — a fabricated number is not just dishonest, it's also
the single fastest way to get caught and burn the list. If you haven't run the
diagnostic against a prospect yet, use the **Variant B (soft-offer)** email
instead of Variant A.

### Pre-send checklist (per prospect, do this first)
1. Confirm the prospect fits the ICP: $1-15M revenue, Shopify or Amazon FBA,
   visible public storefront/catalog.
2. Run Kern's `price_intelligence` or `inventory_optimization` analysis
   locally against their public product pages / catalog (offline, manual —
   this does not need the webapp).
3. Pull out exactly ONE finding with a real dollar figure or SKU count. Don't
   list five findings — one sharp number beats a report dump in a cold email.
4. Fill in the variables below. If you don't have a real finding yet for this
   prospect, use Variant B, not Variant A.

---

## Sequence overview

| # | Send day | Variant | Subject line pattern | Purpose | Primary CTA |
|---|---|---|---|---|---|
| 1 | Day 0 | A (real finding) or B (soft offer) | Number + store name, <40 chars | Icebreaker — earn one reply | 15-min call or "want the full breakdown?" |
| 2 | Day 4 | Follow-up — new angle | Short question, no "follow up" | Re-surface with a DIFFERENT angle, not "just checking in" | Same as email 1 |
| 3 | Day 9 | Breakup | Direct, low-pressure | Give a clean exit, last chance | Reply or opt-out |

**Cadence rule from the research:** 50-70% of replies come from touch 2 or 3,
not touch 1. Don't skip the follow-ups, and don't send all three from a
mail-merge tool that timestamps identically — space them out like a human
would.

**Sending mechanics:**
- Send Tue-Thu, morning in the recipient's timezone.
- Plain text, from your own real inbox — no HTML template, no tracking
  pixels, no "sent via [ESP]" footer. This is a personal email, not a
  newsletter.
- One prospect at a time. Don't BCC or mail-merge blast the list — the whole
  tactic depends on it reading as individually researched, because it is.
- No attachments on email 1 (a PDF behind a click is friction — put the
  finding directly in the email body).

---

## Variant A — Email 1: the real-finding icebreaker (use when you have a genuine number)

**Subject line options** (pick one, all under 40 characters, all contain the
number, none contain "free"/"urgent"/"reminder"):
- `3 SKUs bleeding margin at [Store Name]`
- `[Store Name]: $[X,XXX] in excess stock`
- `Noticed something on [Store Name]'s catalog`

**Body:**

```
Hi [First Name],

I ran a quick pricing/inventory scan on [Store Name]'s public catalog —
found [specific finding, e.g. "3 SKUs priced 12-18% below your two closest
competitors" or "$[X,XXX] tied up in excess safety stock across [N] SKUs"].

Method: [one plain-language sentence — e.g. "compared your listed prices
against [Competitor A] and [Competitor B] on the same SKUs" or "compared
your current reorder points against 90 days of implied sell-through"].
Nothing fancy, just a straight comparison — happy to show you exactly how
I got the number.

I do this kind of pricing/inventory diagnostic for stores your size
([$1-15M] range) — a two-week sprint, one deliverable, no subscription.
If this is something you're actively tracking, I have time Tuesday or
Thursday this week for 15 minutes to walk through what I found.

[Your name]
```

**Why this shape:** leads with the finding, not "AI" or a pitch — 2026 buyer
trust data shows quantified, methodology-transparent first touches beat
generic pitches, and personalized-audit-as-icebreaker outreach reports far
higher reply rates than a feature-list pitch. The methodology sentence matters
more than polish: it's the "prove you're not guessing" line.

---

## Variant B — Email 1: soft offer (use when you haven't run a real diagnostic on this prospect yet)

**Subject line options:**
- `Quick pricing check for [Store Name]?`
- `Is [Store Name] leaving margin on the table?`

**Body:**

```
Hi [First Name],

I help stores in the [$1-15M] range catch pricing gaps and excess-stock
problems before they show up in the P&L. Rather than pitch you cold, I'd
rather just show you something real: I built a free scan that checks your
listed prices against a couple of named competitors and flags anything off.

Takes 2 minutes, no signup beyond an email: [link to
/paquetes/diagnostico-posicion-precios or the live demo scan once it's
confirmed working in prod].

If it turns something up worth a closer look, the full diagnostic (all your
SKUs, not just a couple) is a two-week sprint, one deliverable, no
subscription. If not, no hard feelings — you'll have learned something for
free either way.

[Your name]
```

**When to use B instead of A:** any time you're prospecting faster than you
can run manual diagnostics, or as the entry point for a colder, larger batch
before you invest the time in A's personalized finding for the ones who
engage. B converts lower but scales further — use A for your hand-picked
top 10-15, B for the rest of the 20-30 list.

---

## Email 2 (Day 4) — new angle, not "just following up"

**Subject line options:**
- `One more thing on [Store Name]`
- `Different angle on [Store Name]'s [SKUs/pricing]`

**Body:**

```
Hi [First Name],

Following up briefly — one thing I didn't mention last time: [a SECOND,
different observation, e.g. "the same scan flagged your [category] line as
having the widest price variance vs. competitors — usually a sign the
pricing hasn't been revisited in a while" or a relevant, timely hook like
"saw Shopify's killing Stocky at the end of August — if that's what you've
been using for reorder points, might be worth a second opinion before then"].

Still happy to walk through it in 15 minutes if useful. If the timing's
off, just let me know and I'll leave it there.

[Your name]
```

**Why a new angle, not a bump:** "just checking in" emails get ignored because
they add no new information. A second data point or a timely hook (the Stocky
shutdown is a real, dated angle right now) gives the prospect a fresh reason
to open and reply.

---

## Email 3 (Day 9) — the breakup, low-pressure

**Subject line options:**
- `Last one from me, [First Name]`
- `Closing the loop on [Store Name]`

**Body:**

```
Hi [First Name],

Don't want to clutter your inbox — this is my last note on this. If the
pricing/inventory scan I mentioned isn't a priority right now, totally
understand.

If it ever becomes one, the offer stands: [link to the diagnostic package
page]. Either way, wishing you a good [quarter/season] at [Store Name].

[Your name]
```

**Why this works:** a genuine, no-pressure exit often gets more replies than
another pitch — it removes the "them chasing me" dynamic and reads as
respectful of their time, which is itself a small trust signal.

---

## Optional companion: LinkedIn sequence (same prospect, parallel channel)

Run this ALONGSIDE the email sequence, not instead of it — research shows
profile-visit-then-message sequences convert meaningfully better than a cold
DM alone.

1. **Days 1-3 (before any email or DM):** visit the prospect's LinkedIn
   profile, react to or comment genuinely on 1-2 of their recent posts. No
   pitch, no DM yet.
2. **Day 4, first DM** (after the visible engagement above):
   ```
   Hi [First Name] — enjoyed your post on [specific topic]. Quick
   question: is pricing/inventory optimization something you're actively
   working on at [Store Name] right now, or more of a back-burner thing?
   ```
   Note: acknowledge → one specific value insight → open question. NEVER
   pitch in the first message — it's the single biggest reply-killer per the
   2026 outreach data.
3. **If they reply "actively working on it" or similar:** THEN share the
   finding from Variant A, framed as "since you mentioned it — I actually ran
   a quick scan on your catalog and found [X]."

---

## A/B tests worth running once you have 15-20 sends

- **Subject line:** number-first (`3 SKUs bleeding margin...`) vs.
  store-name-first (`[Store Name]: 3 SKUs...`) — track open rate.
- **CTA framing:** "15 minutes this week" (specific ask) vs. "want the full
  breakdown?" (open-ended) — track reply rate.
- **Variant A vs. B** on a matched pair of similar prospects — track reply
  AND close rate, not just replies (B may reply more but convert less).

## Metrics to track (put this in a simple spreadsheet, one row per prospect)

| Field | Notes |
|---|---|
| Prospect / store name | |
| ICP fit confirmed? | revenue band, platform, catalog size |
| Diagnostic run? (Y/N) | if Y, which variant (A/B) to send |
| Email 1 sent (date) | |
| Email 1 opened / replied? | if your inbox shows read receipts / if they reply |
| Email 2 sent (date) | |
| Email 3 sent (date) | |
| LinkedIn touch status | visited / commented / DM sent / replied |
| Outcome | no reply / interested / call booked / declined |
| If call booked → pilot structure offered | $300-1,500 paid diagnostic-first, or full-price money-back pilot |

**Review cadence:** after every batch of 10 sends, look at what got replies vs.
silence — the finding/hook that worked is worth reusing verbatim on the next
batch, not reinvented each time.

---

## Reminders from the research (don't skip these)

- Apply the same personalized-finding logic to Upwork proposals if you post/
  respond there: open by mirroring the client's own stated problem, cite the
  metric, 200-350 words, end with two concrete time slots. Never list AI tool
  names in the proposal.
- Do NOT post any of this content inside EcommerceFuel, r/ecommerce,
  r/AmazonSeller, or r/FulfillmentByAmazon — those communities actively punish
  vendor pitching. If you engage there, it's 90% genuine help for weeks before
  any mention of Kern, never a version of these templates.
- The moment someone replies "interested," move straight to a low-risk pilot
  structure (small paid diagnostic that upsells only if it finds real value,
  or a full-price money-back pilot) — don't try to close the full $2-18k
  package cold off one reply.

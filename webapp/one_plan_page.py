"""Server-rendered HTML for GET /one-plan.

The English-language AU/NZ agency landing page. Where /stocky-alternative
targets a specific search wave (Shopify retiring its Stocky app), /one-plan
carries the durable agency positioning: Kern as a *fractional planning team*
that works demand, stock, purchasing and pricing as a single plan, instead of
another point-tool app to log into.

Deliberately English + its own minimal shell (not webapp/paquetes_page.py's
Spanish nav/copy), reusing the SAME visual system as
webapp/stocky_alternative_page.py (dark/teal, Inter + JetBrains Mono) so a
visitor clicking through to /paquetes, /stocky-alternative or /demo never hits a
jarring style change.

CTAs point at the SAME two real offers the stocky page uses --
"diagnostico-arranque" (the low-commitment first step) and "starter-fundamentos"
(the ongoing engagement) -- both structured, fixed-scope data from
webapp/offers.py. No new pricing is authored anywhere on this page.

Two hard constraints are baked in on purpose (both are enforced by
tests/test_one_plan_page.py against the *rendered* HTML, not the templates):

  1. Banned words. A brand/compliance list (certified / audit-grade / 10x /
     "digital twin" / "operate your whole|entire chain" / EXCEED / ISO claims)
     must never appear in the output. This module avoids every one of them,
     including in negations (e.g. we say Kern "never physically operates
     anything", never the literal banned phrase).

  2. Fractional-team economics. The wrapper-A framing compares Kern's price to a
     *loaded full-time planner hire* (~USD 100-120k/yr), NEVER to the visitor's
     existing $100-300/mo point-tool app. The ratio is computed on the price
     THIS page actually CTAs into -- Starter at USD 900/mo (~USD 10,800/yr),
     i.e. on the order of a tenth of a loaded salary. It is NOT "~40-50%" (that
     figure only ever applied to the Scale/Retainer tiers, and an earlier draft
     wrongly computed it on a since-repriced Growth price -- do not reintroduce
     it).

Every claim here is either a mechanism (something true of the code/engagement,
stated as fact) or an outcome (a client KPI, always hedged -- "designed to",
"aims to", never a promised number, until a founding client proves it).
"""

from __future__ import annotations

import json
from html import escape

from webapp.offers import (
    Offer,
    is_safe_external_url,
    resolve_agendar_cta,
    resolve_pagar_cta,
)

# --- Positioning spine (promise B), verbatim -------------------------------
# The em dash is written as the &mdash; entity so this source file stays ASCII
# (repo convention) while rendering as a real "--" glyph in the browser. The
# same constant is imported by tests/test_one_plan_page.py, so the H1 and the
# assertion can never drift apart.
H1_PROMISE_B = (
    "Demand, stock, purchasing and pricing &mdash; one plan that stops fighting itself."
)

# --- Fractional-team economics (see module docstring, constraint 2) --------
# Restates the SHIPPED Starter floor price (authoritative full string is shown
# verbatim in the offer panel below, straight from webapp/offers.py). Kept as
# named constants so the ~10% framing is auditable, not a magic figure.
_STARTER_MONTHLY_USD = 900
_STARTER_ANNUAL_USD = _STARTER_MONTHLY_USD * 12  # ~10,800
_PLANNER_LOADED_LOW_USD = 100  # in thousands/yr
_PLANNER_LOADED_HIGH_USD = 120  # in thousands/yr

# --- English/US-formatted price + cadence display, page-local only ---------
# webapp/offers.py's Offer.price/cadence are Spanish-language, European-
# decimal-formatted strings (e.g. "USD 1.500-2.500 unico"). That module stays
# untouched -- it is the Spanish canonical catalog, used correctly by
# webapp/paquetes_page.py. This page is English-only, so it restates the SAME
# real numbers already in Offer.price/Offer.cadence for just the two offers
# used here, translated into English/US-decimal formatting. No new figures.
#
#   diagnostico-arranque.price   == "USD 1.500-2.500 unico"
#   diagnostico-arranque.cadence == "Unico, sprint de 2 semanas"
#   starter-fundamentos.price    == "USD 900/mes (piso ~500 SKUs, +$40/mes
#                                     cada bloque de 250 SKUs, techo $1.500)"
#   starter-fundamentos.cadence  == "Mensual, alcance variable por catalogo"
_DIAGNOSTICO_PRICE_EN = "USD 1,500-2,500, one-time"
_DIAGNOSTICO_CADENCE_EN = "One-time, 2-week sprint"
_STARTER_PRICE_EN = (
    "USD 900/month (from ~500 SKUs, plus USD 40/month per extra 250-SKU "
    "block, capped at USD 1,500/month)"
)
_STARTER_CADENCE_EN = "Monthly, scope varies by catalog size"

_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kern &mdash; one supply-chain plan for demand, stock, purchasing &amp; pricing</title>
<meta name="description" content="Demand, stock, purchasing and pricing worked as a single plan by a fractional planning team. Kern plans, coordinates and recommends; your ERP executes and a human approves every change.">
<link rel="icon" href="data:,">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script type="application/ld+json">{faq_jsonld}</script>
<style>
  :root{{
    --ink:#080b11; --panel:#111722; --panel-2:#0f141d;
    --line:#1e2733; --line-2:#283341;
    --txt:#e7eef6; --txt-2:#c4cfdb; --muted:#9aa7b6; --faint:#5e6b7a;
    --accent:#4fd1c5; --accent-bright:#5eead4; --accent-soft:rgba(79,209,197,.14); --accent-bd:rgba(79,209,197,.45);
    --warn:#f5b942; --warn-soft:rgba(245,185,66,.12); --warn-bd:rgba(245,185,66,.4);
    --accent-warm:#ffb545; --accent-warm-2:#ff8a5c; --accent-warm-deep:#b5601f;
    --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
    --r:13px;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--ink);color:var(--txt);font-family:var(--sans);font-size:15px;line-height:1.6;-webkit-font-smoothing:antialiased;
    background-image:radial-gradient(1100px 520px at 10% -8%,rgba(79,209,197,.09),transparent 60%),radial-gradient(900px 620px at 110% 0%,rgba(120,90,255,.06),transparent 55%);background-attachment:fixed}}
  a{{color:var(--accent-bright);text-decoration:none}}
  .wrap{{max-width:880px;margin:0 auto;padding:0 22px}}
  header{{border-bottom:1px solid var(--line);background:rgba(8,11,17,.7);backdrop-filter:blur(10px)}}
  header .wrap{{display:flex;align-items:center;justify-content:space-between;height:60px;max-width:1080px}}
  .brand{{display:flex;align-items:center;gap:9px;font:700 17px/1 var(--mono)}}
  .brand .d{{color:var(--accent)}}
  header nav{{display:flex;gap:18px;align-items:center;font-size:14px;color:var(--txt-2)}}
  h1{{font-size:clamp(1.9rem,1.3rem+2.4vw,3rem);font-weight:800;letter-spacing:-.02em;margin:0 0 .3em;line-height:1.15}}
  h2{{font-size:1.35rem;font-weight:700;margin:0 0 .5em;letter-spacing:-.01em}}
  h3{{font-size:1.05rem;font-weight:700;margin:0 0 .3em}}
  .eyebrow{{font:600 12px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--accent-bright)}}
  .muted{{color:var(--muted)}} .sub{{color:var(--txt-2)}}
  section{{padding:34px 0}}
  section + section{{border-top:1px solid var(--line)}}
  .panel{{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:22px}}
  .btn{{display:inline-flex;align-items:center;justify-content:center;gap:8px;font:600 14px/1 var(--sans);padding:12px 20px;border-radius:999px;border:1px solid transparent;cursor:pointer;transition:transform 80ms ease-out,box-shadow 80ms ease-out,filter 120ms ease-out}}
  .btn-primary{{background:linear-gradient(150deg,var(--accent-warm),var(--accent-warm-2));color:#2a1200;box-shadow:0 6px 0 var(--accent-warm-deep)}}
  .btn-primary:hover{{filter:saturate(1.08)}}
  .btn-primary:active{{transform:translateY(6px);box-shadow:0 0 0 var(--accent-warm-deep)}}
  .btn-primary:focus-visible{{outline:2px solid var(--accent-warm);outline-offset:2px}}
  .btn-ghost{{background:transparent;color:var(--txt);border-color:var(--line-2)}}
  .btn-ghost:hover{{border-color:var(--accent-bd);background:var(--accent-soft)}}
  .btn-ghost:active{{transform:translateY(2px)}}
  .row{{display:flex;gap:10px;flex-wrap:wrap;align-items:center}}
  .tension{{display:inline-flex;align-items:center;gap:9px;background:var(--warn-soft);border:1px solid var(--warn-bd);color:var(--warn);border-radius:999px;padding:8px 16px;font:600 13px/1 var(--mono);margin-bottom:20px}}
  .tension .dot{{width:7px;height:7px;border-radius:50%;background:var(--warn)}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:16px}}
  ul.check,ul.cross{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}}
  ul.check li,ul.cross li{{padding-left:26px;position:relative;color:var(--txt-2)}}
  ul.check li::before{{content:"\\2713";position:absolute;left:0;top:0;color:var(--accent-bright);font-weight:700}}
  ul.cross li::before{{content:"\\2715";position:absolute;left:0;top:0;color:var(--muted);font-weight:700}}
  .faq-item{{padding:16px 0;border-bottom:1px solid var(--line)}}
  .faq-item:last-child{{border-bottom:none}}
  .faq-item h3{{color:var(--txt)}}
  .faq-item p{{color:var(--txt-2);margin:.4em 0 0}}
  .cta-final{{text-align:center;padding:40px 0}}
  footer{{border-top:1px solid var(--line);padding:26px 0;color:var(--faint);font-size:13px}}
  footer .wrap{{max-width:1080px}}
  @media(max-width:600px){{.grid2{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <span class="brand"><span class="d">&#9672;</span> Kern</span>
    <nav><a href="/demo">Free scan</a><a href="/paquetes">Packages</a><a href="/">Home</a></nav>
  </div>
</header>
<main class="wrap">
"""

_FOOT = """
<footer><div class="wrap">Kern is an independent supply-chain planning team for AU/NZ inventory-heavy retailers and distributors. Kern plans, coordinates and recommends; your ERP/MES executes and a human approves every change. Serving Australia and New Zealand.</div></footer>
</main>
</body>
</html>
"""

# The three deal-killer objections, answered with mechanisms (facts about how the
# engagement works), never with promised outcomes. See module docstring.
_FAQ: tuple[tuple[str, str], ...] = (
    (
        "You have zero paying clients -- why should I trust this?",
        "We are not asking you to trust it on faith. That is exactly why the entry "
        "point is a one-time, fixed-scope diagnostic rather than a contract: you see "
        "what Kern finds in your own data first, with every number cited against a "
        "documented method (safety stock, EOQ, ABC-XYZ classification), and decide "
        "from there. Nothing is written to your live systems without your explicit "
        "approval, so the downside of trying it is bounded.",
    ),
    (
        "Isn't this just a black box?",
        "No. Every recommendation is grounded and cited against a published "
        "methodology, not an opaque model output you have to take on faith. You can "
        "trace each number back to the inputs and the formula behind it, and any "
        "change to stock levels or reorder points is staged as a reversible dry run "
        "before anything is committed -- so you review the reasoning, not just the "
        "answer.",
    ),
    (
        "My ERP or inventory app already does this.",
        "Most inventory apps do one slice -- a forecast here, a reorder point there -- "
        "and leave demand, stock, purchasing and pricing to argue with each other "
        "across separate screens. Kern's job is the opposite: work those four as a "
        "single plan so they stop contradicting each other, then hand execution back "
        "to the ERP you already run. It sits alongside your ERP/MES and coordinates "
        "the decisions; it does not replace the system that executes them.",
    ),
)


def _faq_jsonld() -> str:
    # Escape "</" so a future FAQ answer can never accidentally close the
    # surrounding <script> tag early (content here is hardcoded, not user
    # input, but this is the standard safe-embedding technique regardless).
    return json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": q,
                    "acceptedAnswer": {"@type": "Answer", "text": a},
                }
                for q, a in _FAQ
            ],
        }
    ).replace("</", "<\\/")


def _cta_buttons(offer: Offer) -> str:
    agendar = resolve_agendar_cta(offer)
    pagar = resolve_pagar_cta(offer)
    return (
        f'<a class="btn btn-primary" href="{escape(pagar.href)}">{escape(pagar.label)}</a>'
        f'<a class="btn btn-ghost" href="{escape(agendar.href)}">{escape(agendar.label)}</a>'
    )


def render_one_plan_html(offer_starter: Offer, offer_diagnostico: Offer) -> str:
    faq_items = "".join(
        f'<div class="faq-item"><h3>{escape(q)}</h3><p>{escape(a)}</p></div>' for q, a in _FAQ
    )
    demo_scan_href = "/demo"
    if is_safe_external_url(demo_scan_href):  # defensive no-op; kept for parity with other CTAs
        pass

    body = (
        _HEAD.format(faq_jsonld=_faq_jsonld())
        + f"""
<section style="padding-top:44px">
  <div class="tension"><span class="dot"></span>Still firefighting stockouts and overstock?</div>
  <span class="eyebrow">Fractional supply-chain planning &middot; Australia &amp; New Zealand</span>
  <h1>{H1_PROMISE_B}</h1>
  <p class="sub" style="max-width:64ch;font-size:1.05rem">
    Most teams run demand in one spreadsheet, stock in another, purchasing by feel and
    pricing in a third tool &mdash; and the four quietly contradict each other. Kern
    works them as a single plan, so the plan stops fighting itself. It is designed to
    cut the tug-of-war between over-ordering and running out, not to promise a number
    before your own data has shown one.
  </p>
  <div class="row" style="margin-top:22px">{_cta_buttons(offer_diagnostico)}</div>
</section>

<section>
  <span class="eyebrow">How we work</span>
  <h2 style="margin-top:10px">A fractional planning team, not another app to log into</h2>
  <p class="sub" style="max-width:64ch">
    A full-time demand or inventory planner is roughly a
    USD&nbsp;{_PLANNER_LOADED_LOW_USD}&ndash;{_PLANNER_LOADED_HIGH_USD}k/year hire once
    you load salary, tooling and ramp-up &mdash; and one person still can't hold demand,
    stock, purchasing and pricing in their head at the same time. Kern gives you that
    planning function as an ongoing engagement instead: the Starter tier runs
    USD&nbsp;{_STARTER_MONTHLY_USD}/month (about
    USD&nbsp;{_STARTER_ANNUAL_USD:,}/year), a fraction of a full-time hire's loaded
    salary &mdash; on the order of a tenth. You are renting a planning team's output,
    not paying to recruit, train and retain one.
  </p>
  <ul class="check" style="margin-top:16px;max-width:64ch">
    <li>One plan across demand, stock, purchasing and pricing &mdash; reconciled, not four screens that disagree</li>
    <li>Every recommendation cited against a documented method, so you can check the math</li>
    <li>Reversible dry-run staging first &mdash; nothing hits your live system without a human approving it</li>
    <li>Fixed-scope pricing you can start and stop, not a full-time headcount you have to carry</li>
  </ul>
</section>

<section>
  <span class="eyebrow">What we do / what we don't</span>
  <h2 style="margin-top:10px">Kern plans the work. Your systems and your team run it.</h2>
  <p class="sub" style="max-width:64ch">
    Kern is a decision-and-design layer. In plain terms: we run plan-source-deliver;
    your ERP/MES executes; a human signs. It decides, plans, coordinates, stages and
    recommends &mdash; it never executes, ships, buys or physically operates anything on
    its own.
  </p>
  <div class="grid2">
    <div class="panel">
      <h3>Kern's job</h3>
      <ul class="check" style="margin-top:10px">
        <li>Decide: what to buy, how much, when, at what price</li>
        <li>Plan: demand, stock, purchasing and pricing as one reconciled plan</li>
        <li>Coordinate the trade-offs between the four so they stop fighting</li>
        <li>Stage every change as a reversible dry run for review</li>
        <li>Recommend, with the reasoning and citations attached</li>
      </ul>
    </div>
    <div class="panel">
      <h3>Not Kern's job</h3>
      <ul class="cross" style="margin-top:10px">
        <li>Executing transactions in your ERP &mdash; your ERP does that</li>
        <li>Placing the purchase order or paying a supplier</li>
        <li>Moving, picking or shipping physical stock</li>
        <li>Running your warehouse or machines &mdash; your MES/WMS does that</li>
        <li>Approving its own recommendations &mdash; a human always signs</li>
      </ul>
    </div>
  </div>
</section>

<section>
  <span class="eyebrow">Two ways to start</span>
  <h2 style="margin-top:10px">Pick based on how much certainty you want first</h2>
  <div class="panel" style="margin-top:16px">
    <h3>{escape(offer_diagnostico.name)}</h3>
    <p class="sub" style="margin:6px 0 14px">{escape(_DIAGNOSTICO_PRICE_EN)} &middot; {escape(_DIAGNOSTICO_CADENCE_EN)}
      &mdash; the lowest-commitment way to see what Kern finds in your own data before
      committing to anything ongoing.
      <a href="/paquetes/{escape(offer_diagnostico.slug)}">Full details &rarr;</a></p>
    <div class="row">{_cta_buttons(offer_diagnostico)}</div>
  </div>
  <div class="panel" style="margin-top:14px">
    <h3>{escape(offer_starter.name)}</h3>
    <p class="sub" style="margin:6px 0 14px">{escape(_STARTER_PRICE_EN)} &middot; {escape(_STARTER_CADENCE_EN)}
      &mdash; the ongoing engagement: demand, stock, purchasing and pricing worked as one
      plan every cycle, with what-if scenario testing.
      <a href="/paquetes/{escape(offer_starter.slug)}">Full details &rarr;</a></p>
    <div class="row">{_cta_buttons(offer_starter)}</div>
  </div>
  <p class="sub" style="margin-top:10px;font-size:13px">Priced and billed in USD via Stripe.</p>
  <p class="sub" style="margin-top:16px">
    Not ready for either? Run the <a href="{demo_scan_href}">free self-serve scan</a> on
    your own data first &mdash; no commitment, no card required.
  </p>
</section>

<section>
  <span class="eyebrow">FAQ</span>
  <h2 style="margin-top:10px">The questions worth asking first</h2>
  <div style="margin-top:12px">{faq_items}</div>
</section>

<section class="cta-final" style="border-top:1px solid var(--line)">
  <h2>One plan, instead of four that argue.</h2>
  <p class="sub" style="max-width:52ch;margin:8px auto 22px">
    Start with the fixed-scope diagnostic, see the reasoning in your own data, and
    decide from there.
  </p>
  <div class="row" style="justify-content:center">{_cta_buttons(offer_diagnostico)}</div>
</section>
"""
        + _FOOT
    )
    return body

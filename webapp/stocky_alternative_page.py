"""Server-rendered HTML for GET /stocky-alternative.

A dedicated, English-language landing page targeting the "stocky alternative"
search wave: Shopify delisted its native Stocky forecasting/PO app from the
App Store in Feb 2026 and is shutting it down entirely on 2026-08-31, pushing
an active pool of merchants to search for a replacement right now (see the
GTM research captured in project memory as of 2026-07-16).

Deliberately English + its own minimal shell (not webapp/paquetes_page.py's
Spanish nav/copy) since this page's audience is the English-speaking
Shopify/Amazon ICP, not the Spanish-language commercial-package buyer the rest
of the site targets. Reuses the SAME visual system (dark/teal, Inter +
JetBrains Mono) so a visitor who clicks through to /paquetes or /demo doesn't
hit a jarring style change.

CTAs point at the two offers that actually replace what Stocky did (demand
forecasting + reorder points + PO suggestions): "starter-fundamentos" (the
ongoing replacement) and "diagnostico-arranque" (the low-commitment first
step) -- both structured, fixed-scope data from webapp/offers.py, no new
pricing invented here.
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

_SHUTDOWN_DATE = "August 31, 2026"

_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stocky Alternative for Shopify Merchants (2026) | Kern</title>
<meta name="description" content="Shopify is shutting down Stocky on August 31, 2026. Here is what actually replaces its demand forecasting and reorder-point recommendations -- and why a one-time audit beats another monthly dashboard.">
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
  .btn{{display:inline-flex;align-items:center;justify-content:center;gap:8px;font:600 14px/1 var(--sans);padding:12px 20px;border-radius:999px;border:1px solid transparent;cursor:pointer;transition:transform .15s,box-shadow .15s}}
  .btn-primary{{background:linear-gradient(150deg,var(--accent-bright),var(--accent));color:#06201d;box-shadow:0 10px 26px -12px rgba(79,209,197,.6)}}
  .btn-primary:hover{{transform:translateY(-1px);box-shadow:0 16px 32px -12px rgba(79,209,197,.85)}}
  .btn-ghost{{background:transparent;color:var(--txt);border-color:var(--line-2)}}
  .btn-ghost:hover{{border-color:var(--accent-bd);background:var(--accent-soft)}}
  .row{{display:flex;gap:10px;flex-wrap:wrap;align-items:center}}
  .deadline{{display:inline-flex;align-items:center;gap:9px;background:var(--warn-soft);border:1px solid var(--warn-bd);color:var(--warn);border-radius:999px;padding:8px 16px;font:600 13px/1 var(--mono);margin-bottom:20px}}
  .deadline .dot{{width:7px;height:7px;border-radius:50%;background:var(--warn)}}
  table{{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:8px}}
  thead th{{text-align:left;padding:10px 12px;color:var(--muted);font-weight:600;border-bottom:1px solid var(--line-2)}}
  tbody td{{padding:12px;border-bottom:1px solid var(--line);color:var(--txt-2);vertical-align:top}}
  tbody tr:last-child td{{border-bottom:none}}
  .col-kern{{color:var(--txt);font-weight:600;background:rgba(79,209,197,.05)}}
  ul.check{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}}
  ul.check li{{padding-left:26px;position:relative;color:var(--txt-2)}}
  ul.check li::before{{content:"\\2713";position:absolute;left:0;top:0;color:var(--accent-bright);font-weight:700}}
  .faq-item{{padding:16px 0;border-bottom:1px solid var(--line)}}
  .faq-item:last-child{{border-bottom:none}}
  .faq-item h3{{color:var(--txt)}}
  .faq-item p{{color:var(--txt-2);margin:.4em 0 0}}
  .cta-final{{text-align:center;padding:40px 0}}
  footer{{border-top:1px solid var(--line);padding:26px 0;color:var(--faint);font-size:13px}}
  footer .wrap{{max-width:1080px}}
  @media(max-width:600px){{table{{font-size:12.5px}} thead th,tbody td{{padding:8px 6px}}}}
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
<footer><div class="wrap">Kern is an independent supply-chain analysis tool, not affiliated with or endorsed by Shopify Inc. "Stocky" is Shopify's own product; this page compares publicly documented functionality, not confidential information.</div></footer>
</main>
</body>
</html>
"""

_FAQ: tuple[tuple[str, str], ...] = (
    (
        "When does Stocky actually shut down?",
        f"Shopify removed Stocky from the App Store on February 2, 2026 (no new installs), "
        f"and will fully shut it down on {_SHUTDOWN_DATE}. If you already have it installed, "
        f"it keeps working until that date -- then it stops.",
    ),
    (
        "What did Stocky actually do that I need to replace?",
        "Three things: demand forecasting from your sales history, reorder-point / "
        "purchase-order suggestions, and low-stock alerts. Any replacement needs to cover "
        "all three, not just one -- a forecasting tool with no PO suggestion is only half "
        "the job.",
    ),
    (
        "Do I need to install a new Shopify app?",
        "No. Kern runs against a CSV export of your sales/inventory data (or your own "
        "spreadsheet, staged back in as a reversible dry-run edit) -- no app install, no "
        "ongoing Shopify permissions to grant.",
    ),
    (
        "Is this another $49-599/month subscription like the other Stocky alternatives?",
        "Not by default. Most Stocky alternatives are recurring dashboards. Kern's entry "
        "point is a one-time, fixed-scope diagnostic (no subscription) -- you get a "
        "citation-backed report and a reversible restock plan, not a login you have to keep "
        "paying for. An ongoing engagement is available if you want continuous monitoring, "
        "but it's a choice, not the only option.",
    ),
    (
        "What makes the output trustworthy if I've never used Kern before?",
        "Every number in the deliverable is grounded and cited against a documented "
        "methodology (safety stock, EOQ, ABC-XYZ classification) -- not a black-box model "
        "output you have to take on faith. Any restock or reorder-point change is staged as "
        "a reversible dry run first; nothing writes to your live system without your "
        "explicit approval.",
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


def render_stocky_alternative_html(offer_starter: Offer, offer_diagnostico: Offer) -> str:
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
  <div class="deadline"><span class="dot"></span>Stocky shuts down {escape(_SHUTDOWN_DATE)}</div>
  <span class="eyebrow">Shopify Stocky Migration</span>
  <h1>Shopify is killing Stocky. Here's what actually replaces it.</h1>
  <p class="sub" style="max-width:62ch;font-size:1.05rem">
    Stocky was pulled from the App Store in February 2026 and shuts down for good on
    {escape(_SHUTDOWN_DATE)}. If it was doing your demand forecasting, reorder points,
    or purchase-order suggestions, you need a replacement before then -- not another
    black-box dashboard, a diagnostic you can actually check the math on.
  </p>
  <div class="row" style="margin-top:22px">{_cta_buttons(offer_diagnostico)}</div>
</section>

<section>
  <span class="eyebrow">What Stocky did</span>
  <h2 style="margin-top:10px">The three jobs any replacement has to cover</h2>
  <ul class="check" style="margin-top:16px;max-width:62ch">
    <li><b>Demand forecasting</b> from your historical sales, per SKU</li>
    <li><b>Reorder-point / purchase-order suggestions</b> -- what to buy, how much, when</li>
    <li><b>Low-stock alerts</b> before you actually run out</li>
  </ul>
  <p class="sub" style="margin-top:16px;max-width:62ch">
    A lot of "Stocky alternatives" only really cover the first one. Kern's
    <a href="/paquetes/starter-fundamentos">Starter package</a> is built to cover all
    three, plus what-if scenario testing and Excel-native reorder staging if you're not
    ready to move off spreadsheets entirely.
  </p>
</section>

<section>
  <span class="eyebrow">How the options compare</span>
  <h2 style="margin-top:10px">One-time diagnostic vs. another monthly dashboard</h2>
  <div class="panel" style="padding:0;overflow-x:auto">
    <table>
      <thead><tr><th>&nbsp;</th><th>Stocky (retiring)</th><th>Typical alternative</th><th class="col-kern">Kern</th></tr></thead>
      <tbody>
        <tr><td>Cost</td><td>Free (built into Shopify)</td><td>$49-599/month recurring</td><td class="col-kern">One-time diagnostic, no subscription required</td></tr>
        <tr><td>Setup</td><td>Already installed</td><td>New app install + permissions</td><td class="col-kern">CSV export -- no app, no new Shopify permissions</td></tr>
        <tr><td>Reorder writeback</td><td>Manual, in Shopify admin</td><td>Varies by vendor</td><td class="col-kern">Reversible dry-run staged into your own sheet/system first</td></tr>
        <tr><td>Methodology</td><td>Not published</td><td>Usually a black box</td><td class="col-kern">Cited against a documented model (safety stock, EOQ, ABC-XYZ)</td></tr>
        <tr><td>Commitment</td><td>None (it's shutting down anyway)</td><td>Monthly lock-in</td><td class="col-kern">Start with a fixed-scope diagnostic, no ongoing commitment required</td></tr>
      </tbody>
    </table>
  </div>
</section>

<section>
  <span class="eyebrow">Two ways to start</span>
  <h2 style="margin-top:10px">Pick based on how much certainty you want first</h2>
  <div class="panel" style="margin-top:16px">
    <h3>{escape(offer_diagnostico.name)}</h3>
    <p class="sub" style="margin:6px 0 14px">{escape(offer_diagnostico.price)} &middot; {escape(offer_diagnostico.cadence)}
      &mdash; the lowest-commitment way to see what Kern finds in your own data before
      deciding on anything ongoing.
      <a href="/paquetes/{escape(offer_diagnostico.slug)}">Full details &rarr;</a></p>
    <div class="row">{_cta_buttons(offer_diagnostico)}</div>
  </div>
  <div class="panel" style="margin-top:14px">
    <h3>{escape(offer_starter.name)}</h3>
    <p class="sub" style="margin:6px 0 14px">{escape(offer_starter.price)} &middot; {escape(offer_starter.cadence)}
      &mdash; the direct, ongoing replacement for what Stocky did: forecasting, reorder
      points, and PO suggestions, plus what-if scenario testing.</p>
    <div class="row">{_cta_buttons(offer_starter)}</div>
  </div>
  <p class="sub" style="margin-top:16px">
    Not ready for either? Run the <a href="{demo_scan_href}">free self-serve demo</a> on
    your own data first -- no commitment, no card required.
  </p>
</section>

<section>
  <span class="eyebrow">FAQ</span>
  <h2 style="margin-top:10px">Common questions about the migration</h2>
  <div style="margin-top:12px">{faq_items}</div>
</section>

<section class="cta-final" style="border-top:1px solid var(--line)">
  <h2>{escape(_SHUTDOWN_DATE)} isn't far off.</h2>
  <p class="sub" style="max-width:52ch;margin:8px auto 22px">
    Whichever way you go, don't wait until Stocky actually stops working to figure out
    the replacement.
  </p>
  <div class="row" style="justify-content:center">{_cta_buttons(offer_diagnostico)}</div>
</section>
"""
        + _FOOT
    )
    return body

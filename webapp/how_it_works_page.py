"""Server-rendered HTML for GET /how-it-works.

An internal/onboarding page (not a sales page -- see
docs/superpowers/specs/2026-07-19-how-it-works-page-design.md): what Kern
does, its 48 capabilities under two donut-chart lenses (domain area / SCOR
Digital Standard process), how it's grounded and adapted per client, and its
alignment with SCOR/CPIM/CLTD/CSCP/SCPro/CPSM/ISO 9001/28000.

Reuses the SAME dark/teal visual system as webapp/stocky_alternative_page.py
(Inter + JetBrains Mono, --ink/--panel/--accent tokens) for visual
consistency across the site, but is otherwise a fully self-contained page
(its own <style> block, per this codebase's per-page convention -- see
paquetes_page.py/pricing_page.py/tower_page.py, none of which share a CSS
file either).

No charting library: donuts are hand-built inline SVG (stroke-dasharray/
stroke-dashoffset arcs). All interactivity (lens tabs, click-to-expand tool
lists, expandable cards/accordion) lives in webapp/static/how_it_works.js,
loaded via a <script src> tag -- confirmed compatible with the base CSP
(webapp/security.py's csp_for_path() only special-cases /console and
/static/prototype; this route gets the strict default, which already allows
'self'-hosted scripts).
"""

from __future__ import annotations

import math
from html import escape
from typing import Sequence

_DONUT_COLORS: tuple[str, ...] = (
    "#4fd1c5",  # accent
    "#5eead4",  # accent-bright
    "#f5b942",  # warn
    "#8b7cf6",
    "#f47174",
    "#63b3ed",
    "#68d391",
    "#f6ad55",
    "#b794f4",
)


def _donut_svg(
    segments: Sequence[tuple[str, int]],
    *,
    element_id: str,
    size: int = 240,
    stroke_width: int = 36,
) -> str:
    """Render an accessible inline SVG donut chart.

    `segments` is an ordered sequence of (label, count) pairs; count must be
    >= 0 and the total must be > 0. Each segment becomes one <circle> arc
    carrying data-label/data-count/data-pct attributes (read by
    static/how_it_works.js for click-to-filter) plus a native <title> so a
    hover tooltip works with zero JS.
    """
    if any(count < 0 for _, count in segments):
        raise ValueError("segment counts must be non-negative")
    total = sum(count for _, count in segments)
    if total <= 0:
        raise ValueError("donut segments must sum to a positive total")

    radius = (size - stroke_width) / 2
    circumference = 2 * math.pi * radius
    center = size / 2

    cumulative = 0.0
    arcs: list[str] = []
    for i, (label, count) in enumerate(segments):
        color = _DONUT_COLORS[i % len(_DONUT_COLORS)]
        fraction = count / total
        dash = fraction * circumference
        gap = circumference - dash
        offset = -cumulative
        cumulative += dash
        pct = round(fraction * 100)
        safe_label = escape(str(label))
        arcs.append(
            f'<circle class="donut-seg" data-label="{safe_label}" data-count="{escape(str(count))}" '
            f'data-pct="{escape(str(pct))}" tabindex="0" cx="{escape(f"{center:.3f}")}" cy="{escape(f"{center:.3f}")}" r="{escape(f"{radius:.3f}")}" '
            f'fill="none" stroke="{escape(color)}" stroke-width="{escape(str(stroke_width))}" '
            f'stroke-dasharray="{escape(f"{dash:.3f}")} {escape(f"{gap:.3f}")}" stroke-dashoffset="{escape(f"{offset:.3f}")}" '
            f'transform="rotate(-90 {escape(f"{center:.3f}")} {escape(f"{center:.3f}")})">'
            f"<title>{safe_label}: {escape(str(count))} ({escape(str(pct))}%)</title>"
            "</circle>"
        )

    return (
        f'<svg class="donut" id="{escape(element_id)}" viewBox="0 0 {escape(str(size))} {escape(str(size))}" '
        f'width="{escape(str(size))}" height="{escape(str(size))}" role="img" aria-label="Donut chart">'
        + "".join(arcs)
        + f'<text x="{escape(f"{center:.3f}")}" y="{escape(f"{center:.3f}")}" class="donut-total" text-anchor="middle" '
        f'dominant-baseline="middle">{escape(str(total))}</text>'
        + "</svg>"
    )


from webapp.how_it_works_data import (  # noqa: E402
    CERTIFICATIONS,
    DOMAIN_AREA_ORDER,
    HONEST_GAPS,
    ISO_9001_CLAUSES,
    ISO_28000_ELEMENTS,
    SCOR_BUCKET_ORDER,
    TOOLS,
    CertCoverage,
    IsoClause,
    tally_by_domain_area,
    tally_by_scor_bucket,
)

_LEVEL_FILL: dict[str, int] = {"High": 4, "Medium-high": 3, "Partial": 2}


def _expandable_card(title: str, summary: str, detail_html: str, *, card_id: str) -> str:
    return (
        f'<button type="button" class="card-toggle" data-target="{escape(card_id)}" '
        f'aria-expanded="false">'
        f"<h3>{escape(title)}</h3><p class=\"sub\">{escape(summary)}</p>"
        "</button>"
        f'<div class="card-detail" id="{escape(card_id)}" hidden>{detail_html}</div>'
    )


def _coverage_bar(cert: CertCoverage, *, bar_id: str) -> str:
    filled = _LEVEL_FILL.get(cert.level, 1)
    segments = "".join(
        f'<span class="bar-seg{" filled" if i < filled else ""}"></span>' for i in range(4)
    )
    covered_items = "".join(f"<li>{escape(item)}</li>" for item in cert.covered)
    gap_items = "".join(f"<li>{escape(item)}</li>" for item in cert.gaps)
    return (
        f'<button type="button" class="cert-toggle" data-target="{escape(bar_id)}" '
        f'aria-expanded="false">'
        f'<span class="cert-name">{escape(cert.name)}</span>'
        f'<span class="cert-body">{escape(cert.body)}</span>'
        f'<span class="cert-bar" aria-hidden="true">{segments}</span>'
        f'<span class="cert-level">{escape(cert.level)}</span>'
        "</button>"
        f'<div class="cert-detail" id="{escape(bar_id)}" hidden>'
        f"<h4>Covered</h4><ul class=\"check\">{covered_items}</ul>"
        f"<h4>Gaps</h4><ul class=\"gap\">{gap_items}</ul>"
        "</div>"
    )


def _iso_accordion_row(clause: IsoClause, *, row_id: str) -> str:
    return (
        f'<button type="button" class="iso-toggle" data-target="{escape(row_id)}" '
        f'aria-expanded="false">'
        f'<span class="iso-clause">{escape(clause.clause)}</span>'
        '<span class="iso-chevron" aria-hidden="true">&#9662;</span>'
        "</button>"
        f'<div class="iso-detail" id="{escape(row_id)}" hidden>'
        f"<p>{escape(clause.kern_behavior)}</p></div>"
    )


def _stepper(stages: Sequence[tuple[str, str]]) -> str:
    items = "".join(
        f'<li class="step"><span class="step-name">{escape(name)}</span>'
        f'<span class="step-detail">{escape(detail)}</span></li>'
        for name, detail in stages
    )
    return f'<ol class="stepper">{items}</ol>'


_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>How Kern Works | Kern</title>
<meta name="description" content="How Kern's 48 supply-chain capabilities work: what feeds them, how they adapt to a client's context, and how they align with SCOR, CPIM, CLTD, CSCP, SCPro, CPSM, and ISO 9001/28000.">
<link rel="icon" href="data:,">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root{
    --ink:#080b11; --panel:#111722; --panel-2:#0f141d;
    --line:#1e2733; --line-2:#283341;
    --txt:#e7eef6; --txt-2:#c4cfdb; --muted:#9aa7b6; --faint:#5e6b7a;
    --accent:#4fd1c5; --accent-bright:#5eead4; --accent-soft:rgba(79,209,197,.14); --accent-bd:rgba(79,209,197,.45);
    --warn:#f5b942; --warn-soft:rgba(245,185,66,.12); --warn-bd:rgba(245,185,66,.4);
    --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
    --r:13px;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--ink);color:var(--txt);font-family:var(--sans);font-size:15px;line-height:1.6;-webkit-font-smoothing:antialiased;
    background-image:radial-gradient(1100px 520px at 10% -8%,rgba(79,209,197,.09),transparent 60%),radial-gradient(900px 620px at 110% 0%,rgba(120,90,255,.06),transparent 55%);background-attachment:fixed}
  a{color:var(--accent-bright);text-decoration:none}
  .wrap{max-width:920px;margin:0 auto;padding:0 22px}
  header{border-bottom:1px solid var(--line);background:rgba(8,11,17,.7);backdrop-filter:blur(10px)}
  header .wrap{display:flex;align-items:center;justify-content:space-between;height:60px;max-width:1080px}
  .brand{display:flex;align-items:center;gap:9px;font:700 17px/1 var(--mono)}
  .brand .d{color:var(--accent)}
  header nav{display:flex;gap:18px;align-items:center;font-size:14px;color:var(--txt-2)}
  h1{font-size:clamp(1.9rem,1.3rem+2.4vw,3rem);font-weight:800;letter-spacing:-.02em;margin:0 0 .3em;line-height:1.15}
  h2{font-size:1.35rem;font-weight:700;margin:0 0 .5em;letter-spacing:-.01em}
  h3{font-size:1.05rem;font-weight:700;margin:0 0 .3em}
  h4{font-size:.85rem;font-weight:700;margin:14px 0 6px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
  .eyebrow{font:600 12px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--accent-bright)}
  .muted{color:var(--muted)} .sub{color:var(--txt-2)}
  section{padding:34px 0}
  section + section{border-top:1px solid var(--line)}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:22px}
  ul.check{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
  ul.check li{padding-left:22px;position:relative;color:var(--txt-2);font-size:13.5px}
  ul.check li::before{content:"\\2713";position:absolute;left:0;top:0;color:var(--accent-bright);font-weight:700}
  ul.gap{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
  ul.gap li{padding-left:22px;position:relative;color:var(--txt-2);font-size:13.5px}
  ul.gap li::before{content:"\\2013";position:absolute;left:0;top:0;color:var(--warn)}
  footer{border-top:1px solid var(--line);padding:26px 0;color:var(--faint);font-size:13px}
  footer .wrap{max-width:1080px}

  /* -- stepper -- */
  .stepper{list-style:none;margin:22px 0 0;padding:0;display:flex;gap:0;flex-wrap:wrap;counter-reset:step}
  .stepper .step{flex:1 1 150px;padding:14px 16px;border:1px solid var(--line-2);border-radius:var(--r);background:var(--panel-2);position:relative;counter-increment:step}
  .stepper .step::before{content:counter(step);position:absolute;top:-10px;left:14px;background:var(--accent);color:#06201d;font:700 11px/18px var(--mono);width:18px;height:18px;border-radius:50%;text-align:center}
  .step-name{display:block;font:700 14px/1.2 var(--sans);color:var(--txt);margin-bottom:6px}
  .step-detail{display:block;font-size:12.5px;color:var(--txt-2)}

  /* -- lens tabs + donuts -- */
  .lens-tabs{display:flex;gap:8px;margin:18px 0 14px}
  .lens-tab{font:600 13px/1 var(--sans);padding:9px 16px;border-radius:999px;border:1px solid var(--line-2);background:transparent;color:var(--txt-2);cursor:pointer}
  .lens-tab.active{background:var(--accent-soft);border-color:var(--accent-bd);color:var(--txt)}
  .lens-panel[hidden]{display:none}
  .donut-row{display:flex;gap:28px;flex-wrap:wrap;align-items:flex-start}
  .donut{flex:0 0 auto}
  .donut-seg{cursor:pointer;transition:opacity .15s}
  .donut-seg:hover, .donut-seg:focus{opacity:.8;outline:none}
  .donut-total{fill:var(--txt);font:700 28px/1 var(--mono)}
  .tool-list{flex:1 1 260px;min-width:220px}
  .tool-list[hidden]{display:none}
  .tool-list .bucket-block[hidden]{display:none}
  .tool-list h4{margin-top:0}
  .tool-list .tool-row{padding:8px 0;border-bottom:1px solid var(--line)}
  .tool-list .tool-row:last-child{border-bottom:none}
  .tool-list .tool-key{font:600 13px/1.3 var(--mono);color:var(--accent-bright)}
  .tool-list .tool-desc{display:block;font-size:12.5px;color:var(--txt-2);margin-top:2px}
  .donut-legend{flex:0 1 240px;min-width:200px;display:flex;flex-direction:column;gap:4px;margin:0;padding:0}
  .legend-item{display:flex;align-items:center;gap:9px;width:100%;text-align:left;background:transparent;border:1px solid transparent;border-radius:8px;padding:6px 9px;cursor:pointer;color:var(--txt-2);font:500 13px/1.3 var(--sans)}
  .legend-item:hover{background:var(--panel);border-color:var(--line-2);color:var(--txt)}
  .legend-swatch{flex:0 0 auto;width:11px;height:11px;border-radius:3px}
  .legend-label{flex:1 1 auto}
  .legend-count{flex:0 0 auto;font:600 12px/1 var(--mono);color:var(--muted)}

  /* -- expandable cards -- */
  .card-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-top:16px}
  .card-toggle{display:block;width:100%;text-align:left;background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:16px;cursor:pointer;color:inherit;font:inherit}
  .card-toggle:hover{border-color:var(--accent-bd)}
  .card-detail{background:var(--panel-2);border:1px solid var(--line);border-top:none;border-radius:0 0 var(--r) var(--r);padding:14px 16px;margin-top:-14px;font-size:13px;color:var(--txt-2)}
  .card-detail[hidden]{display:none}

  /* -- certification coverage bars -- */
  .cert-toggle{display:grid;grid-template-columns:80px 1fr 90px 100px;gap:12px;align-items:center;width:100%;text-align:left;background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:12px 16px;cursor:pointer;color:inherit;font:inherit;margin-top:8px}
  .cert-name{font:700 14px/1 var(--mono);color:var(--txt)}
  .cert-body{font-size:12px;color:var(--muted)}
  .cert-bar{display:flex;gap:3px}
  .bar-seg{width:16px;height:8px;border-radius:2px;background:var(--line-2)}
  .bar-seg.filled{background:var(--accent)}
  .cert-level{font-size:12.5px;color:var(--txt-2);text-align:right}
  .cert-detail{background:var(--panel-2);border:1px solid var(--line);border-top:none;padding:12px 16px 16px}
  .cert-detail[hidden]{display:none}

  /* -- ISO accordion -- */
  .iso-toggle{display:flex;justify-content:space-between;align-items:center;width:100%;text-align:left;background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:12px 16px;cursor:pointer;color:inherit;font:inherit;margin-top:6px}
  .iso-clause{font:600 13.5px/1.3 var(--sans)}
  .iso-detail{background:var(--panel-2);border:1px solid var(--line);border-top:none;padding:10px 16px 14px;font-size:13px;color:var(--txt-2)}
  .iso-detail[hidden]{display:none}

  @media(max-width:640px){.cert-toggle{grid-template-columns:1fr;gap:6px}}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <span class="brand"><span class="d">&#9672;</span> Kern</span>
    <nav><a href="/">Home</a><a href="/console">Live console</a></nav>
  </div>
</header>
<main class="wrap">
"""

_FOOT = """
<footer><div class="wrap">
  SCOR&reg; is a framework of the Association for Supply Chain Management (ASCM).
  APICS, CPIM, CSCP, and CLTD are ASCM certifications. SCPro is a CSCMP
  certification. CPSM is an Institute for Supply Management (ISM) certification.
  Kern is not affiliated with, endorsed by, or certified by ASCM, CSCMP, or ISM --
  this page shows how Kern's own capabilities relate to those public frameworks.
  Full technical detail: <code>documentation/KERN_NIVEL_REFERENCIA_SCM.md</code>.
</div></footer>
</main>
<script src="/static/how_it_works.js?v=2"></script>
</body>
</html>
"""


def _tool_list_html(order: Sequence[str], group_by: str, *, list_id: str) -> str:
    """One <div data-bucket="..."> block per bucket in `order`, each hidden by
    default and unhidden by static/how_it_works.js when the matching donut
    segment is clicked. `group_by` is "domain_area" or "scor_bucket"."""
    blocks: list[str] = []
    for bucket in order:
        rows = "".join(
            f'<div class="tool-row"><span class="tool-key">{escape(t.label)}</span>'
            f'<span class="tool-desc">{escape(t.one_liner)}</span></div>'
            for t in TOOLS
            if getattr(t, group_by) == bucket
        )
        blocks.append(f'<div class="bucket-block" data-bucket="{escape(bucket)}" hidden>{rows}</div>')
    return f'<div class="tool-list" id="{escape(list_id)}" hidden>{"".join(blocks)}</div>'


def _donut_legend(segments: Sequence[tuple[str, int]], *, donut_id: str) -> str:
    """A clickable legend beside a donut: color swatch + bucket name + count,
    one row per segment, colors matching _donut_svg's _DONUT_COLORS order.
    Clicking a row reveals the same bucket the matching donut segment would
    (static/how_it_works.js binds .legend-item via data-donut/data-bucket) --
    it is also a far larger hit target than the donut's thin stroke ring."""
    rows = "".join(
        f'<button type="button" class="legend-item" data-donut="{escape(donut_id)}" '
        f'data-bucket="{escape(label)}">'
        f'<span class="legend-swatch" style="background:{escape(_DONUT_COLORS[i % len(_DONUT_COLORS)])}"></span>'
        f'<span class="legend-label">{escape(label)}</span>'
        f'<span class="legend-count">{escape(str(count))}</span>'
        "</button>"
        for i, (label, count) in enumerate(segments)
    )
    return f'<div class="donut-legend" role="list" aria-label="Chart legend">{rows}</div>'


def render_how_it_works_html() -> str:
    domain_tally = tally_by_domain_area()
    scor_tally = tally_by_scor_bucket()
    tool_count = escape(str(len(TOOLS)))

    stepper_html = _stepper([
        ("Brief", "A plain-language request, optionally with a data file attached."),
        ("Classify", f"The orchestrator matches the brief's intent to one of {tool_count} registered tools."),
        ("Run", "The matched tool's own prepare -> run pipeline executes against the data provided."),
        ("QA", "The tool's own QA gate checks the result. If QA fails, nothing ships -- zero deliverables."),
        ("Deliver", "A grounded, cited deliverable -- or, if execution wasn't safe, ranked options, a "
                     "prepared handoff, or an escalation."),
    ])

    domain_segments = [(area, domain_tally[area]) for area in DOMAIN_AREA_ORDER]
    scor_segments = [(bucket, scor_tally[bucket]) for bucket in SCOR_BUCKET_ORDER]
    domain_donut = _donut_svg(domain_segments, element_id="donut-domain")
    scor_donut = _donut_svg(scor_segments, element_id="donut-scor")
    domain_legend = _donut_legend(domain_segments, donut_id="donut-domain")
    scor_legend = _donut_legend(scor_segments, donut_id="donut-scor")
    domain_list = _tool_list_html(DOMAIN_AREA_ORDER, "domain_area", list_id="donut-domain-list")
    scor_list = _tool_list_html(SCOR_BUCKET_ORDER, "scor_bucket", list_id="donut-scor-list")

    guided_donut = _donut_svg(
        [("EXECUTED", 1), ("OPTIONS", 1), ("HANDOFF", 1), ("ESCALATED", 1)], element_id="donut-guided"
    )

    grounding_cards = "".join([
        _expandable_card(
            "Knowledge graph", "33 curated SCM sources + the codebase itself",
            "<p>Every deliverable carries L3 citations, gated by <code>citation_gate</code> "
            "(minimum 2 citations, max 2 hops, an EXCLUDED_CONCEPTS false-friend filter) so a "
            "result is never grounded in an off-topic source.</p>",
            card_id="card-knowledge",
        ),
        _expandable_card(
            "Client profiles", "Per-client cost/capacity parameters that persist",
            "<p>Holding rate, order cost, service level, lead time, warehouse capacity -- "
            "asked once, stored under <code>clients/&lt;slug&gt;/profile.json</code>, and "
            "merged into every later run so the same brief produces a client-specific answer "
            "instead of a generic one.</p>",
            card_id="card-profiles",
        ),
        _expandable_card(
            "QA gate", "\"QA fails => no deliverable\"",
            "<p>Enforced in one place by the orchestrator. A result that fails its own tool's "
            "QA check is refused, not shipped.</p>",
            card_id="card-qa",
        ),
        _expandable_card(
            "Optional LLM layer", "Works with or without one",
            "<p>The deterministic engine is the core. An optional LLM provider sharpens intent "
            "routing and the narrative summary when configured, but every model in the engine "
            "runs the same with or without it.</p>",
            card_id="card-llm",
        ),
    ])

    cert_bars = "".join(
        _coverage_bar(cert, bar_id=f"cert-{cert.name.lower()}") for cert in CERTIFICATIONS
    )
    iso_9001_rows = "".join(
        _iso_accordion_row(clause, row_id=f"iso9001-{i}") for i, clause in enumerate(ISO_9001_CLAUSES)
    )
    iso_28000_rows = "".join(
        _iso_accordion_row(clause, row_id=f"iso28000-{i}") for i, clause in enumerate(ISO_28000_ELEMENTS)
    )
    gap_rows = "".join(
        f'<div class="panel" style="margin-top:10px">'
        f"<h3>{escape(gap.name)}</h3>"
        f'<p class="sub">{escape(gap.current_state)}</p>'
        f'<p class="muted" style="font-size:12.5px;margin-top:6px">Asked for by: {escape(gap.standard)}</p>'
        "</div>"
        for gap in HONEST_GAPS
    )

    return (
        _HEAD
        + f"""
<section style="padding-top:44px">
  <span class="eyebrow">How Kern Works</span>
  <h1>A brief goes in. A grounded, QA-gated deliverable comes out.</h1>
  <p class="sub" style="max-width:64ch;font-size:1.05rem">
    Kern is an agentic supply-chain engine: {tool_count} agent-routable capabilities behind one
    pipeline, grounded in a knowledge graph of 33 curated sources, adapted to each client's own
    cost and capacity parameters.
  </p>
  {stepper_html}
</section>

<section>
  <span class="eyebrow">{tool_count} capabilities, two lenses</span>
  <h2 style="margin-top:10px">The same {tool_count} tools, grouped two ways</h2>
  <div class="lens-tabs" role="tablist">
    <button type="button" class="lens-tab active" data-lens="domain" role="tab" aria-selected="true">By domain area</button>
    <button type="button" class="lens-tab" data-lens="scor" role="tab" aria-selected="false">By SCOR Digital Standard process</button>
  </div>
  <div class="lens-panel" data-lens="domain">
    <div class="donut-row">{domain_donut}{domain_legend}{domain_list}</div>
  </div>
  <div class="lens-panel" data-lens="scor" hidden>
    <div class="donut-row">{scor_donut}{scor_legend}{scor_list}</div>
    <p class="sub" style="margin-top:14px;max-width:64ch">
      <b>Transform</b> (production/manufacturing execution) is Kern's thinnest SCOR category by
      design -- Kern is a planning and decision-support engine, not a manufacturing execution
      system (MES).
    </p>
  </div>
  <p class="muted" style="font-size:12.5px;margin-top:10px">Click a legend entry (or a donut segment) to see its tools. Each tool sits in exactly one bucket per lens.</p>
</section>

<section>
  <span class="eyebrow">Grounding &amp; adaptation</span>
  <h2 style="margin-top:10px">How it's fed, and how it adapts to your context</h2>
  <div class="card-grid">{grounding_cards}</div>
</section>

<section>
  <span class="eyebrow">Never-unprotected</span>
  <h2 style="margin-top:10px">Every result is one of four outcomes</h2>
  <div class="donut-row">
    {guided_donut}
    <div class="tool-list" style="min-width:220px">
      <div class="tool-row"><span class="tool-key">EXECUTED</span><span class="tool-desc">The agent did it autonomously.</span></div>
      <div class="tool-row"><span class="tool-key">OPTIONS</span><span class="tool-desc">Ranked choices for a human to pick.</span></div>
      <div class="tool-row"><span class="tool-key">HANDOFF</span><span class="tool-desc">A prepared, ready-to-approve next step.</span></div>
      <div class="tool-row"><span class="tool-key">ESCALATED</span><span class="tool-desc">Routed to a human with an SLA.</span></div>
    </div>
  </div>
  <p class="muted" style="font-size:12.5px;margin-top:10px">
    Structural -- the four possible outcome shapes -- not a measured run-frequency split.
  </p>
</section>

<section>
  <span class="eyebrow">Standards &amp; certifications</span>
  <h2 style="margin-top:10px">How this maps to SCOR and five SCM certifications</h2>
  <p class="sub" style="max-width:64ch">
    SCOR Digital Standard's own <b>Orchestrate</b> category -- added for the "digital" layer:
    twins, analytics, agents, resilience -- is where Kern's agentic guarantees (the QA gate,
    never-unprotected, signed staged writeback) land, ahead of what most commercial suites do
    here.
  </p>
  <h3 style="margin-top:22px">Certification coverage</h3>
  <p class="sub">A tool can touch more than one certification's body of knowledge at once, so
    this is a coverage level per certification, not a tool count.</p>
  <div>{cert_bars}</div>
  <h3 style="margin-top:26px">ISO 9001 alignment</h3>
  <div>{iso_9001_rows}</div>
  <h3 style="margin-top:22px">ISO 28000 alignment</h3>
  <div>{iso_28000_rows}</div>
  <h3 style="margin-top:26px">Honest gaps</h3>
  <p class="sub">What Kern does not cover yet, and which standard asks for it.</p>
  <div>{gap_rows}</div>
</section>
"""
        + _FOOT
    )

"""Server-rendered HTML for GET /paquetes and GET /paquetes/{slug}.

/paquetes renders the 7-offer grid straight from webapp/offers.py (structured
data only -- no prose duplicated here). /paquetes/{slug} renders a shell that
fetches the real one-pager markdown from the mounted /paquetes-docs/ and
converts it client-side with the already-vendored marked.min.js, the same
pattern proven by webapp/static/operator/index.html.
"""

from __future__ import annotations

from html import escape

from webapp.offers import (
    Offer,
    is_safe_external_url,
    is_safe_same_origin_or_external_url,
    resolve_agendar_cta,
    resolve_pagar_cta,
)
from webapp.operator_profile import OperatorProfile

_HEAD = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="icon" href="data:,">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root{{
    --ink:#080b11; --panel:#111722; --panel-2:#0f141d;
    --line:#1e2733; --line-2:#283341;
    --txt:#e7eef6; --txt-2:#c4cfdb; --muted:#9aa7b6; --faint:#5e6b7a;
    --accent:#4fd1c5; --accent-bright:#5eead4; --accent-soft:rgba(79,209,197,.14); --accent-bd:rgba(79,209,197,.45);
    --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
    --r:13px;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--ink);color:var(--txt);font-family:var(--sans);font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;
    background-image:radial-gradient(1100px 520px at 10% -8%,rgba(79,209,197,.09),transparent 60%),radial-gradient(900px 620px at 110% 0%,rgba(120,90,255,.06),transparent 55%);background-attachment:fixed}}
  a{{color:var(--accent-bright);text-decoration:none}}
  .wrap{{max-width:1080px;margin:0 auto;padding:0 22px}}
  header{{border-bottom:1px solid var(--line);background:rgba(8,11,17,.7);backdrop-filter:blur(10px)}}
  header .wrap{{display:flex;align-items:center;justify-content:space-between;height:60px}}
  .brand{{display:flex;align-items:center;gap:9px;font:700 17px/1 var(--mono)}}
  .brand .d{{color:var(--accent)}}
  header nav{{display:flex;gap:18px;align-items:center;font-size:14px;color:var(--txt-2)}}
  h1{{font-size:clamp(1.7rem,1.2rem+1.8vw,2.4rem);font-weight:800;letter-spacing:-.02em;margin:0}}
  h2{{font-size:1.1rem;font-weight:700;margin:0 0 4px}}
  .eyebrow{{font:600 12px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--accent-bright)}}
  .muted{{color:var(--muted)}} .sub{{color:var(--txt-2)}}
  section{{padding:38px 0}}
  .panel{{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:22px}}
  .btn{{display:inline-flex;align-items:center;justify-content:center;gap:8px;font:600 14px/1 var(--sans);padding:11px 18px;border-radius:999px;border:1px solid transparent;cursor:pointer;transition:transform .15s,box-shadow .15s}}
  .btn-primary{{background:linear-gradient(150deg,var(--accent-bright),var(--accent));color:#06201d;box-shadow:0 10px 26px -12px rgba(79,209,197,.6)}}
  .btn-primary:hover{{transform:translateY(-1px);box-shadow:0 16px 32px -12px rgba(79,209,197,.85)}}
  .btn-ghost{{background:transparent;color:var(--txt);border-color:var(--line-2)}}
  .btn-ghost:hover{{border-color:var(--accent-bd);background:var(--accent-soft)}}
  .row{{display:flex;gap:10px;flex-wrap:wrap;align-items:center}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:26px}}
  @media(max-width:780px){{.grid{{grid-template-columns:1fr}}}}
  .card{{display:flex;flex-direction:column;gap:8px;background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:20px 20px 18px;transition:.18s}}
  .card:hover{{border-color:var(--accent-bd);transform:translateY(-2px)}}
  .card h3{{font-size:1.05rem;font-weight:700;margin:0}}
  .price{{font:700 15px/1 var(--mono);color:var(--accent-bright)}}
  .cadence{{font-size:12.5px;color:var(--faint)}}
  .field{{font-size:13.5px;color:var(--txt-2);line-height:1.5}}
  .field b{{color:var(--txt);font-weight:600}}
  .card-cta{{margin-top:8px;display:flex;gap:8px;flex-wrap:wrap}}
  .card-cta .btn{{padding:9px 14px;font-size:13px}}
  .detail-link{{margin-top:2px;font-size:13px;font-weight:600;color:var(--accent-bright)}}
  .operator{{display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap}}
  .operator img{{width:76px;height:76px;border-radius:50%;object-fit:cover;border:1px solid var(--line-2)}}
  .operator .avatar-fallback{{width:76px;height:76px;border-radius:50%;background:var(--panel-2);border:1px solid var(--line-2);display:flex;align-items:center;justify-content:center;font:700 22px/1 var(--mono);color:var(--faint)}}
  .doc{{padding-top:8px}}
  .doc h1{{font-size:1.9rem;font-weight:800;letter-spacing:-.02em;margin:.2em 0 .5em}}
  .doc h2{{font-size:1.25rem;font-weight:700;margin:1.6em 0 .5em;padding-bottom:.3em;border-bottom:1px solid var(--line)}}
  .doc h3{{font-size:1.05rem;font-weight:700;margin:1.3em 0 .4em}}
  .doc p{{margin:.7em 0;color:var(--txt-2)}}
  .doc strong{{color:var(--txt)}}
  .doc ul,.doc ol{{margin:.6em 0;padding-left:1.3em;color:var(--txt-2)}}
  .doc li{{margin:.3em 0}}
  .doc hr{{border:none;border-top:1px solid var(--line);margin:2em 0}}
  .doc table{{border-collapse:collapse;width:100%;font-size:13.5px;margin:1em 0}}
  .doc thead th{{background:var(--panel-2);text-align:left;padding:9px 12px;border-bottom:1px solid var(--line-2)}}
  .doc tbody td{{padding:9px 12px;border-bottom:1px solid var(--line);color:var(--txt-2)}}
  .doc code{{font-family:var(--mono);font-size:.86em;background:var(--panel);border:1px solid var(--line);padding:.1em .4em;border-radius:5px;color:var(--accent)}}
  .doc pre{{background:var(--panel-2);border:1px solid var(--line);border-radius:10px;padding:14px 16px;overflow:auto}}
  .doc pre code{{background:none;border:none;padding:0}}
  .loading{{color:var(--muted);padding:30px 0}}
  .err{{color:#ff8b81;background:var(--panel);border:1px solid var(--line-2);border-radius:10px;padding:16px}}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <span class="brand"><span class="d">&#9672;</span> Kern</span>
    <nav><a href="/paquetes">Paquetes</a><a href="/demo">Demo</a><a href="/">Dashboard</a></nav>
  </div>
</header>
<main class="wrap">
"""

_FOOT = """
</main>
</body>
</html>
"""


def _cta_buttons(offer: Offer) -> str:
    agendar = resolve_agendar_cta(offer)
    pagar = resolve_pagar_cta(offer)
    return (
        f'<a class="btn btn-primary" href="{escape(pagar.href)}">{escape(pagar.label)}</a>'
        f'<a class="btn btn-ghost" href="{escape(agendar.href)}">{escape(agendar.label)}</a>'
    )


def _operator_block(profile: OperatorProfile) -> str:
    if profile.photo_url and is_safe_same_origin_or_external_url(profile.photo_url):
        avatar = f'<img src="{escape(profile.photo_url)}" alt="{escape(profile.name)}">'
    else:
        initial = escape(profile.name[:1].upper() or "?")
        avatar = f'<div class="avatar-fallback">{initial}</div>'
    links = []
    if profile.linkedin_url and is_safe_external_url(profile.linkedin_url):
        links.append(f'<a href="{escape(profile.linkedin_url)}" target="_blank" rel="noopener">LinkedIn &#8599;</a>')
    if profile.email:
        links.append(f'<a href="mailto:{escape(profile.email)}">{escape(profile.email)}</a>')
    links_html = " &middot; ".join(links) if links else '<span class="muted">TODO-OPERADOR: agregar contacto</span>'
    return (
        '<section><div class="grid-h eyebrow" style="margin-bottom:14px">Quien firma</div>'
        '<div class="panel operator">'
        f"{avatar}"
        "<div>"
        f'<h2>{escape(profile.name)}</h2>'
        f'<p class="field" style="max-width:60ch">{escape(profile.bio)}</p>'
        f'<p class="field" style="margin-top:6px">{links_html}</p>'
        "</div></div></section>"
    )


def render_index_html(offers: tuple[Offer, ...], profile: OperatorProfile) -> str:
    cards = []
    for offer in offers:
        cards.append(
            '<div class="card">'
            f'<h3>{escape(offer.name)}</h3>'
            f'<div class="price">{escape(offer.price)}</div>'
            f'<div class="cadence">{escape(offer.cadence)}</div>'
            f'<p class="field"><b>Recibe:</b> {escape(offer.recibe)}</p>'
            f'<p class="field"><b>Para quien:</b> {escape(offer.para_quien)}</p>'
            f'<div class="card-cta">{_cta_buttons(offer)}</div>'
            f'<a class="detail-link" href="/paquetes/{escape(offer.slug)}">Ver detalle &rarr;</a>'
            "</div>"
        )
    body = (
        _HEAD.format(title="Paquetes - Kern")
        + '<section style="padding-bottom:6px">'
        + '<span class="eyebrow">Paquetes comerciales</span>'
        + '<h1 style="margin-top:12px">Kern produce, vos vendes y firmas</h1>'
        + '<p class="sub" style="max-width:70ch;margin-top:10px">8 paquetes '
        + "(7 de alcance fijo, 1 de precio contingente), cada uno un conjunto de "
        + "herramientas del motor -- nunca una sola tool suelta. "
        + "Cada resultado sale citado contra la base de conocimiento y pasa un gate de QA "
        + "antes de entregarse.</p>"
        + f'<div class="grid">{"".join(cards)}</div>'
        + "</section>"
        + _operator_block(profile)
        + _FOOT
    )
    return body


def render_offer_html(offer: Offer, profile: OperatorProfile) -> str:
    cta_html = _cta_buttons(offer)
    body = (
        _HEAD.format(title=f"{offer.name} - Kern")
        + '<section style="padding-bottom:10px">'
        + '<span class="eyebrow">Paquete comercial</span>'
        + f'<h1 style="margin-top:12px">{escape(offer.name)}</h1>'
        + '<div class="row" style="margin-top:12px">'
        + f'<span class="price">{escape(offer.price)}</span>'
        + f'<span class="cadence">{escape(offer.cadence)}</span>'
        + "</div>"
        + f'<div class="row" style="margin-top:16px">{cta_html}</div>'
        + "</section>"
        + '<section style="padding-top:0">'
        + '<div class="doc panel" id="content"><div class="loading">Cargando&hellip;</div></div>'
        + "</section>"
        + '<section style="padding-top:0"><div class="row">'
        + f'{cta_html}'
        + '<a class="btn btn-ghost" href="/paquetes">&larr; Volver a paquetes</a>'
        + "</div></section>"
        + '<script src="/static/operator/vendor/marked.min.js"></script>'
        + "<script>"
        + "marked.setOptions({gfm:true, breaks:false});"
        + f'fetch("/paquetes-docs/{offer.md_file}").then(function(r){{'
        + "if(!r.ok)throw new Error(r.status); return r.text();"
        + "}).then(function(md){"
        + 'document.getElementById("content").innerHTML = marked.parse(md);'
        + "}).catch(function(e){"
        + 'document.getElementById("content").innerHTML = '
        + '\'<div class="err">No se pudo cargar el one-pager (\'+e.message+\').</div>\';'
        + "});"
        + "</script>"
        + _FOOT
    )
    return body

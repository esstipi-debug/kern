"""ISO catalog HTTP surface and the MONITOR-style order-evidence panel.

Read-only. Boot-safe: this module imports ``src.iso_catalog`` (stdlib + the
committed JSON) and FastAPI. No optional-extra deps.

Routes:
  GET  /api/iso          — { stats, normas, ... } same pack as catalogo.json
  POST /api/detectar     — contratoPost from tests/fixtures/iso/detectar.ejemplo.json
  GET  /api/iso/orden    — native floor evidence for the open order's signals
  GET  /iso              — panel "Evidencia ISO de esta orden"
"""

from __future__ import annotations

from html import escape
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.iso_catalog import (
    AVISO_DETECCION,
    AVISO_ORDEN,
    PerfilInvalido,
    SenalesOrden,
    catalogo_publico,
    detectar,
    evidencia_de_orden,
    parse_perfil,
)

router = APIRouter(tags=["iso"])


@router.get("/api/iso")
def api_iso() -> dict:
    """The committed pack. Same content MONITOR (or a job) would cache."""
    return catalogo_publico()


@router.post("/api/detectar")
def api_detectar(body: Any = Body(...)) -> dict:
    """Applicability + evidence potential. Never a conformity ruling."""
    try:
        perfil = parse_perfil(body)
    except PerfilInvalido as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return detectar(perfil)


@router.get("/api/iso/orden")
def api_iso_orden(
    ciclo: bool = Query(True, description="La orden ya tiene ciclos de maquina"),
    scrap: bool = Query(True, description="La orden ya tiene piezas buenas / no conformes"),
    kg: bool = Query(True, description="La orden ya tiene kg (o kWh/kg)"),
    tkm: bool = Query(False, description="Hay t·km del 3PL o de la flota"),
    modulos: str = Query("smartpro,monitor,energy,huella"),
) -> dict:
    mods = tuple(part.strip().lower() for part in modulos.split(",") if part.strip())
    unknown = [m for m in mods if m not in {"smartpro", "monitor", "energy", "huella", "cmms", "cei"}]
    if unknown:
        raise HTTPException(status_code=400, detail=f"modulo kern desconocido: {unknown[0]}")
    return evidencia_de_orden(
        SenalesOrden(
            tiene_ciclo=ciclo,
            tiene_scrap=scrap,
            tiene_kg=kg,
            tiene_tkm=tkm,
            modulos=mods or ("smartpro", "monitor"),
        )
    )


@router.get("/iso", response_class=HTMLResponse)
def iso_page() -> HTMLResponse:
    panel = evidencia_de_orden(
        SenalesOrden(
            tiene_ciclo=True,
            tiene_scrap=True,
            tiene_kg=True,
            tiene_tkm=False,
            modulos=("smartpro", "monitor", "energy", "huella"),
        )
    )
    return HTMLResponse(render_iso_html(panel))


def render_iso_html(panel: dict) -> str:
    """Server-rendered MONITOR panel. The demo order already has cycle/scrap/kg."""
    evidencias = panel.get("evidencias") or []
    cards = "".join(_card(ev, native=True) for ev in evidencias)
    fuera = panel.get("fueraDeTramo") or {}
    gap = _card(fuera, native=False) if fuera else ""
    huecos = "".join(f"<li>{escape(str(h))}</li>" for h in (panel.get("huecos") or []))
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Evidencia ISO de esta orden · Kern</title>
<meta name="description" content="Evidencia de piso ISO para la orden abierta. No es un dictamen de conformidad.">
<link rel="icon" href="data:,">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root{{
    --ink:#080b11; --panel:#111722; --panel-2:#0f141d;
    --line:#1e2733; --txt:#e7eef6; --txt-2:#c4cfdb; --muted:#9aa7b6;
    --accent:#4fd1c5; --accent-bright:#5eead4; --accent-soft:rgba(79,209,197,.14);
    --warn:#f5b942; --warn-soft:rgba(245,185,66,.12); --bad:#f0564a;
    --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
    --r:13px;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--ink);color:var(--txt);font-family:var(--sans);font-size:15px;line-height:1.6;
    background-image:radial-gradient(1100px 520px at 10% -8%,rgba(79,209,197,.09),transparent 60%)}}
  a{{color:var(--accent-bright);text-decoration:none}}
  .wrap{{max-width:960px;margin:0 auto;padding:0 22px}}
  header{{border-bottom:1px solid var(--line);background:rgba(8,11,17,.75);backdrop-filter:blur(10px)}}
  header .wrap{{display:flex;align-items:center;justify-content:space-between;height:58px}}
  .brand{{font:700 16px/1 var(--mono)}} .brand span{{color:var(--accent)}}
  header nav{{display:flex;gap:16px;font-size:13px;color:var(--txt-2)}}
  h1{{font-size:clamp(1.6rem,1.2rem+1.8vw,2.3rem);font-weight:800;letter-spacing:-.02em;margin:.2em 0}}
  h2{{font-size:1.15rem;margin:0 0 .5em}}
  .eyebrow{{font:600 11px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--accent-bright)}}
  .aviso{{background:var(--warn-soft);border:1px solid rgba(245,185,66,.4);border-radius:var(--r);padding:12px 14px;color:var(--txt-2);margin:16px 0}}
  .resumen{{font-size:1.05rem;color:var(--txt);margin:0 0 18px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}}
  .card{{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:16px}}
  .card.gap{{border-color:rgba(245,185,66,.45);background:var(--warn-soft)}}
  .chip{{display:inline-block;font:600 11px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;padding:4px 8px;border-radius:999px;background:var(--accent-soft);color:var(--accent-bright);margin-bottom:8px}}
  .chip.gap{{background:var(--warn-soft);color:var(--warn)}}
  .limite{{color:var(--muted);font-size:13px;margin-top:10px}}
  ul.ev{{margin:8px 0 0;padding-left:18px;color:var(--txt-2)}}
  section{{padding:28px 0}}
  section + section{{border-top:1px solid var(--line)}}
  label{{display:block;font-size:12px;color:var(--muted);margin:10px 0 4px}}
  input,textarea{{width:100%;background:var(--panel-2);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:8px 10px;font:13px/1.4 var(--mono)}}
  button{{margin-top:14px;background:var(--accent);color:#04221d;border:0;border-radius:999px;padding:10px 18px;font:600 14px var(--sans);cursor:pointer}}
  pre{{background:var(--panel-2);border:1px solid var(--line);border-radius:var(--r);padding:12px;overflow:auto;font:12px/1.45 var(--mono);color:var(--txt-2)}}
  .muted{{color:var(--muted)}}
</style>
</head>
<body>
<header><div class="wrap">
  <div class="brand">Kern <span>· ISO</span></div>
  <nav>
    <a href="/api/iso">GET /api/iso</a>
    <a href="/operator">Operador</a>
    <a href="/">Inicio</a>
  </nav>
</div></header>
<main class="wrap">
<section>
  <div class="eyebrow">MONITOR · orden abierta</div>
  <h1>Evidencia ISO de esta orden</h1>
  <p class="aviso">{escape(panel.get("aviso") or AVISO_ORDEN)}</p>
  <p class="resumen">{escape(panel.get("resumen") or "")}</p>
  <p class="muted">Senales en la orden: ciclo, scrap y kg. No hay t·km del 3PL.</p>
  <div class="grid">
    {cards}
    {gap}
  </div>
  <ul class="ev" style="margin-top:16px">{huecos}</ul>
</section>
<section>
  <h2>Detectar aplicabilidad para una planta</h2>
  <p class="muted">{escape(AVISO_DETECCION)}</p>
  <form id="detectar-form">
    <label for="sectores">sectores</label>
    <input id="sectores" name="sectores" value="automotriz">
    <label for="clientePide">clientePide</label>
    <input id="clientePide" name="clientePide" value="iatf, pcf">
    <label for="logistica">logistica</label>
    <input id="logistica" name="logistica" value="3pl, rti, export">
    <label for="planta">planta</label>
    <input id="planta" name="planta" value="muestreo, maestro-sku">
    <label for="kern">kern</label>
    <input id="kern" name="kern" value="smartpro, monitor, energy, huella">
    <label for="texto">texto</label>
    <textarea id="texto" name="texto" rows="2">El OEM pide IATF y PCF.</textarea>
    <button type="submit">POST /api/detectar</button>
  </form>
  <pre id="detectar-out">El resultado aparece aqui. No es un dictamen de conformidad.</pre>
</section>
</main>
<script>
(function () {{
  function list(id) {{
    return document.getElementById(id).value.split(",").map(function (s) {{ return s.trim(); }}).filter(Boolean);
  }}
  document.getElementById("detectar-form").addEventListener("submit", async function (ev) {{
    ev.preventDefault();
    var body = {{
      sectores: list("sectores"),
      clientePide: list("clientePide"),
      logistica: list("logistica"),
      planta: list("planta"),
      kern: list("kern"),
      texto: document.getElementById("texto").value
    }};
    var out = document.getElementById("detectar-out");
    out.textContent = "Consultando…";
    try {{
      var r = await fetch("/api/detectar", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(body)
      }});
      var data = await r.json();
      out.textContent = JSON.stringify(data, null, 2);
    }} catch (err) {{
      out.textContent = String(err);
    }}
  }});
}})();
</script>
</body>
</html>
"""


def _card(card: dict, *, native: bool) -> str:
    codigo = escape(str(card.get("codigo") or ""))
    claim = escape(str(card.get("claimPermitido") or ""))
    limite = escape(str(card.get("limite") or ""))
    ev = "".join(f"<li>{escape(str(item))}</li>" for item in (card.get("evidenciasKern") or []))
    klass = "card" if native else "card gap"
    chip = '<div class="chip">evidencia nativa</div>' if native else '<div class="chip gap">fuera de este tramo</div>'
    ev_block = f'<ul class="ev">{ev}</ul>' if ev else ""
    return (
        f'<article class="{klass}">{chip}<h3>{codigo}</h3>'
        f"<p>{claim}</p>{ev_block}"
        f'<p class="limite">{limite}</p></article>'
    )

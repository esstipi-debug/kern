"""ISO catalog HTTP surface: catalog browser, plant detector, order panel.

Read-only. Boot-safe: this module imports ``src.iso_catalog`` (stdlib + the
committed JSON) and FastAPI. No optional-extra deps.

Routes:
  GET  /api/iso          — { stats, normas, ... } same pack as catalogo.json
  POST /api/detectar     — contratoPost from tests/fixtures/iso/detectar.ejemplo.json
  GET  /api/iso/orden    — native floor evidence for the open order's signals
  GET  /iso              — catalog (PARA-KERN style) + detector + order evidence
"""

from __future__ import annotations

from html import escape
from typing import Any, Mapping

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


def render_iso_html(panel: Mapping[str, Any], catalog: Mapping[str, Any] | None = None) -> str:
    """Catalog browser + plant detector + MONITOR order panel.

    The demo order already has cycle/scrap/kg and no t·km. Catalog cards load
    from GET /api/iso (same JSON PARA-KERN.html embeds).
    """
    packed = catalog or catalogo_publico()
    evidencias = panel.get("evidencias") or []
    cards = "".join(_card(ev, native=True) for ev in evidencias)
    fuera = panel.get("fueraDeTramo") or {}
    gap = _card(fuera, native=False) if fuera else ""
    huecos = "".join(f"<li>{escape(str(h))}</li>" for h in (panel.get("huecos") or []))
    stats = packed.get("stats") or {}
    modulos = packed.get("modulos") or []
    dominios = packed.get("dominiosScm") or []
    mod_opts = "".join(
        f'<option value="{escape(str(m.get("id") or ""))}">{escape(str(m.get("nombre") or m.get("id") or ""))}</option>'
        for m in modulos
        if isinstance(m, Mapping)
    )
    dom_opts = "".join(
        f'<option value="{escape(str(d.get("dominio") or ""))}">{escape(str(d.get("nombre") or d.get("dominio") or ""))}</option>'
        for d in dominios
        if isinstance(d, Mapping)
    )
    total = escape(str(stats.get("total") or "53"))
    nativo = escape(str(stats.get("nativo") or "18"))
    parcial = escape(str(stats.get("evidenciaParcial") or "31"))
    cadena = escape(str(stats.get("supplyChain") or "46"))
    inventario = escape(str(stats.get("cadenasProductivas") or "38"))
    return _PAGE.replace("%%CARDS%%", cards).replace("%%GAP%%", gap).replace(
        "%%HUECOS%%", huecos
    ).replace("%%AVISO_ORDEN%%", escape(str(panel.get("aviso") or AVISO_ORDEN))).replace(
        "%%RESUMEN%%", escape(str(panel.get("resumen") or ""))
    ).replace("%%AVISO_DETECCION%%", escape(AVISO_DETECCION)).replace(
        "%%MOD_OPTS%%", mod_opts
    ).replace("%%DOM_OPTS%%", dom_opts).replace("%%TOTAL%%", total).replace(
        "%%NATIVO%%", nativo
    ).replace("%%PARCIAL%%", parcial).replace("%%CADENA%%", cadena).replace(
        "%%PRODUCTIVAS%%", inventario
    )


def _card(card: Mapping[str, Any], *, native: bool) -> str:
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


_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Catalogo ISO · Kern</title>
<meta name="description" content="Catalogo ISO de operaciones y cadena. Evidencia de piso, no un dictamen de conformidad.">
<link rel="icon" href="data:,">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root{
    --ink:#080b11; --panel:#111722; --panel-2:#0f141d;
    --line:#1e2733; --txt:#e7eef6; --txt-2:#c4cfdb; --muted:#9aa7b6;
    --accent:#4fd1c5; --accent-bright:#5eead4; --accent-soft:rgba(79,209,197,.14);
    --warn:#f5b942; --warn-soft:rgba(245,185,66,.12); --bad:#f0564a;
    --ok-soft:rgba(63,185,80,.12);
    --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
    --r:13px;
  }
  *{box-sizing:border-box}
  html{scroll-behavior:smooth}
  body{margin:0;background:var(--ink);color:var(--txt);font-family:var(--sans);font-size:15px;line-height:1.6;
    background-image:radial-gradient(1100px 520px at 10% -8%,rgba(79,209,197,.09),transparent 60%)}
  a{color:var(--accent-bright);text-decoration:none}
  .wrap{max-width:1080px;margin:0 auto;padding:0 22px}
  header{border-bottom:1px solid var(--line);background:rgba(8,11,17,.75);backdrop-filter:blur(10px);position:sticky;top:0;z-index:8}
  header .wrap{display:flex;align-items:center;justify-content:space-between;height:58px;gap:16px}
  .brand{font:700 16px/1 var(--mono);white-space:nowrap} .brand span{color:var(--accent)}
  header nav{display:flex;gap:14px;font-size:13px;color:var(--txt-2);flex-wrap:wrap;justify-content:flex-end}
  h1{font-size:clamp(1.5rem,1.2rem+1.6vw,2.15rem);font-weight:800;letter-spacing:-.02em;margin:.2em 0}
  h2{font-size:1.15rem;margin:0 0 .5em}
  h3{font-size:1.02rem;margin:.2em 0 .4em}
  .eyebrow{font:600 11px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--accent-bright)}
  .aviso{background:var(--warn-soft);border:1px solid rgba(245,185,66,.4);border-radius:var(--r);padding:12px 14px;color:var(--txt-2);margin:16px 0}
  .resumen{font-size:1.05rem;color:var(--txt);margin:0 0 18px}
  .stats{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 18px}
  .stat{background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:6px 12px;font:600 12px/1.2 var(--mono);color:var(--txt-2)}
  .stat b{color:var(--accent-bright);font-weight:700}
  .filters{display:grid;gap:10px;grid-template-columns:1.4fr 1fr 1fr 1fr;margin:0 0 14px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
  .catalog{display:flex;flex-direction:column;gap:10px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:16px}
  .card.gap{border-color:rgba(245,185,66,.45);background:var(--warn-soft)}
  .chip{display:inline-block;font:600 11px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;padding:4px 8px;border-radius:999px;background:var(--accent-soft);color:var(--accent-bright);margin:0 6px 8px 0}
  .chip.gap{background:var(--warn-soft);color:var(--warn)}
  .chip.parcial{background:var(--warn-soft);color:var(--warn)}
  .chip.fuera{background:rgba(240,86,74,.12);color:#f6a09a}
  .chip.alto{background:var(--ok-soft);color:#7ee787}
  .badges{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 8px}
  .b{font:600 11px/1 var(--mono);border:1px solid var(--line);border-radius:999px;padding:3px 8px;color:var(--muted)}
  .limite{color:var(--muted);font-size:13px;margin-top:10px}
  ul.ev{margin:8px 0 0;padding-left:18px;color:var(--txt-2)}
  section{padding:28px 0}
  section + section{border-top:1px solid var(--line)}
  label{display:block;font-size:12px;color:var(--muted);margin:10px 0 4px}
  input,textarea,select{width:100%;background:var(--panel-2);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:8px 10px;font:13px/1.4 var(--mono)}
  .form-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}
  button{margin-top:14px;background:var(--accent);color:#04221d;border:0;border-radius:999px;padding:10px 18px;font:600 14px var(--sans);cursor:pointer}
  button.ghost{background:transparent;color:var(--txt-2);border:1px solid var(--line);margin-left:8px}
  pre{background:var(--panel-2);border:1px solid var(--line);border-radius:var(--r);padding:12px;overflow:auto;font:12px/1.45 var(--mono);color:var(--txt-2)}
  details{margin-top:14px}
  summary{cursor:pointer;color:var(--muted);font-size:13px}
  .muted{color:var(--muted)}
  .code{font-family:var(--mono);font-weight:650;color:var(--accent-bright)}
  @media (max-width:900px){
    .filters,.form-grid{grid-template-columns:1fr}
    header .wrap{height:auto;padding:10px 22px;flex-direction:column;align-items:flex-start}
  }
</style>
</head>
<body>
<header><div class="wrap">
  <div class="brand">Kern <span>· ISO</span></div>
  <nav>
    <a href="#catalogo">Catalogo</a>
    <a href="#detectar">Detectar</a>
    <a href="#orden">Orden</a>
    <a href="/api/iso">GET /api/iso</a>
    <a href="/operator">Operador</a>
    <a href="/">Inicio</a>
  </nav>
</div></header>
<main class="wrap">
<section id="catalogo">
  <div class="eyebrow">paquete iso-operaciones-cadena · 1.0.0</div>
  <h1>Catalogo ISO de operaciones y cadena</h1>
  <p class="muted">53 normas mapeadas a SMARTPRO, MONITOR, ENERGY, huella, CMMS y CEI. Kern carga este catalogo y cruza lo que ya mide. No certifica.</p>
  <div class="stats">
    <span class="stat"><b>%%TOTAL%%</b> normas</span>
    <span class="stat"><b>%%NATIVO%%</b> evidencia nativa</span>
    <span class="stat"><b>%%PARCIAL%%</b> parcial</span>
    <span class="stat"><b>%%CADENA%%</b> supply chain</span>
    <span class="stat"><b>%%PRODUCTIVAS%%</b> cadena productiva</span>
  </div>
  <div class="filters">
    <div>
      <label for="iso-q">Buscar</label>
      <input id="iso-q" placeholder="ISO, transporte, PCF, IATF, inventario…">
    </div>
    <div>
      <label for="iso-mod">Modulo KERN</label>
      <select id="iso-mod">
        <option value="">Todos los modulos</option>
        %%MOD_OPTS%%
      </select>
    </div>
    <div>
      <label for="iso-dominio">Dominio SCM</label>
      <select id="iso-dominio">
        <option value="">Todos los dominios</option>
        %%DOM_OPTS%%
      </select>
    </div>
    <div>
      <label for="iso-cob">Cobertura</label>
      <select id="iso-cob">
        <option value="">Todas</option>
        <option value="nativo">nativo</option>
        <option value="evidencia-parcial">evidencia-parcial</option>
        <option value="fuera-de-alcance">fuera-de-alcance</option>
      </select>
    </div>
  </div>
  <p class="muted" id="iso-count">Cargando catalogo…</p>
  <div class="catalog" id="iso-list"></div>
  <p><button type="button" id="iso-dl">Descargar catalogo.json</button></p>
</section>
<section id="detectar">
  <div class="eyebrow">aplicabilidad · no conformidad</div>
  <h2>Detectar aplicabilidad para una planta</h2>
  <p class="muted">%%AVISO_DETECCION%%</p>
  <p class="muted">Cruza hechos (sector, lo que pide el cliente, logistica, practicas, modulos) contra reglas. Potencial alto no es certificado.</p>
  <form id="detectar-form">
    <div class="form-grid">
      <div>
        <label for="sectores">sectores</label>
        <input id="sectores" name="sectores" value="automotriz">
      </div>
      <div>
        <label for="clientePide">clientePide</label>
        <input id="clientePide" name="clientePide" value="iatf, pcf">
      </div>
      <div>
        <label for="logistica">logistica</label>
        <input id="logistica" name="logistica" value="3pl, rti, export">
      </div>
      <div>
        <label for="planta">planta</label>
        <input id="planta" name="planta" value="muestreo, maestro-sku">
      </div>
      <div>
        <label for="kern">kern</label>
        <input id="kern" name="kern" value="smartpro, monitor, energy, huella">
      </div>
    </div>
    <label for="texto">texto</label>
    <textarea id="texto" name="texto" rows="2">El OEM pide IATF y PCF.</textarea>
    <button type="submit">POST /api/detectar</button>
    <button type="button" class="ghost" id="detectar-vacio">Ficha vacia</button>
  </form>
  <div class="grid" id="detectar-cards" style="margin-top:16px"></div>
  <p class="muted" id="detectar-empty"></p>
  <details>
    <summary>JSON crudo de POST /api/detectar</summary>
    <pre id="detectar-out">El resultado aparece aqui. No es un dictamen de conformidad.</pre>
  </details>
</section>
<section id="orden">
  <div class="eyebrow">MONITOR · orden abierta</div>
  <h1>Evidencia ISO de esta orden</h1>
  <p class="aviso">%%AVISO_ORDEN%%</p>
  <p class="resumen">%%RESUMEN%%</p>
  <p class="muted">Senales en la orden: ciclo, scrap y kg. No hay t·km del 3PL.</p>
  <div class="grid">
    %%CARDS%%
    %%GAP%%
  </div>
  <ul class="ev" style="margin-top:16px">%%HUECOS%%</ul>
</section>
</main>
<script>
(function () {
  function list(id) {
    return document.getElementById(id).value.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
  }
  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function cobChip(cob) {
    if (cob === "nativo") return '<span class="chip">nativo</span>';
    if (cob === "evidencia-parcial") return '<span class="chip parcial">evidencia-parcial</span>';
    return '<span class="chip fuera">fuera-de-alcance</span>';
  }
  function potencialChip(p) {
    var n = Number(p) || 0;
    var label = n >= 0.5 ? "alto" : n >= 0.3 ? "medio" : "bajo";
    var klass = n >= 0.5 ? "chip alto" : n >= 0.3 ? "chip parcial" : "chip fuera";
    return '<span class="' + klass + '">potencial ' + label + '</span>';
  }
  var catalogo = null;
  var byId = {};

  function renderCatalog() {
    if (!catalogo) return;
    var query = document.getElementById("iso-q").value.trim().toLowerCase();
    var modulo = document.getElementById("iso-mod").value;
    var dominio = document.getElementById("iso-dominio").value;
    var cob = document.getElementById("iso-cob").value;
    var idsDominio = null;
    if (dominio) {
      var found = (catalogo.dominiosScm || []).find(function (d) { return d.dominio === dominio; });
      idsDominio = found ? (found.ids || []) : [];
    }
    var items = (catalogo.normas || []).filter(function (n) {
      var kern = n.kern || {};
      if (modulo && (kern.modulos || []).indexOf(modulo) < 0) return false;
      if (cob && kern.cobertura !== cob) return false;
      if (idsDominio && idsDominio.indexOf(n.id) < 0) return false;
      if (!query) return true;
      var blob = [n.codigo, n.nombre, n.resumen].concat(n.scm || []).concat(n.indicadores || []).join(" ").toLowerCase();
      return blob.indexOf(query) >= 0;
    });
    document.getElementById("iso-count").textContent = items.length + " de " + (catalogo.normas || []).length + " normas";
    document.getElementById("iso-list").innerHTML = items.map(function (n) {
      var kern = n.kern || {};
      var scm = (n.scm || []).slice(0, 3).map(function (s) { return '<span class="b">' + esc(s) + "</span>"; }).join("");
      var mods = (kern.modulos || []).map(function (m) { return '<span class="b">' + esc(m) + "</span>"; }).join("");
      var evid = (kern.evidenciasKern || []).join(" · ");
      return '<article class="card"><div class="code">' + esc(n.codigo) + ":" + esc(n.edicion) + "</div>" +
        '<div class="badges">' + cobChip(kern.cobertura) + scm + mods + "</div>" +
        "<p>" + esc(n.nombre) + ". " + esc(n.resumen) + "</p>" +
        '<p class="muted"><strong>Evidencia KERN:</strong> ' + esc(evid) + "</p>" +
        '<p class="limite"><strong>Limite:</strong> ' + esc(kern.limite || "") + "</p></article>";
    }).join("");
  }

  fetch("/api/iso").then(function (r) { return r.json(); }).then(function (data) {
    catalogo = data;
    (data.normas || []).forEach(function (n) { byId[n.id] = n; });
    renderCatalog();
  }).catch(function (err) {
    document.getElementById("iso-count").textContent = String(err);
  });

  ["iso-q", "iso-mod", "iso-dominio", "iso-cob"].forEach(function (id) {
    var el = document.getElementById(id);
    el.addEventListener(id === "iso-q" ? "input" : "change", renderCatalog);
  });

  document.getElementById("iso-dl").addEventListener("click", function () {
    if (!catalogo) return;
    var blob = new Blob([JSON.stringify(catalogo, null, 2)], { type: "application/json" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "catalogo-iso-kern.json";
    a.click();
  });

  function renderDetectadas(data) {
    var rows = data.detectadas || [];
    var cards = document.getElementById("detectar-cards");
    var empty = document.getElementById("detectar-empty");
    cards.innerHTML = "";
    empty.textContent = "";
    if (!rows.length) {
      empty.textContent = "Sin hechos suficientes: no se detecta ninguna norma. Completa sector, lo que pide el cliente o la logistica.";
      return;
    }
    cards.innerHTML = rows.map(function (row) {
      var norma = byId[row.id] || {};
      var kern = norma.kern || {};
      var huecos = (row.huecos || []).map(function (h) { return "<li>" + esc(h) + "</li>"; }).join("");
      var evid = (kern.evidenciasKern || []).slice(0, 3).map(function (e) { return "<li>" + esc(e) + "</li>"; }).join("");
      return '<article class="card">' + cobChip(kern.cobertura) + potencialChip(row.potencial) +
        "<h3>" + esc(row.codigo) + (norma.edicion ? ":" + esc(norma.edicion) : "") + "</h3>" +
        '<p class="muted">aplicabilidad ' + esc(row.aplicabilidad) + "</p>" +
        (evid ? '<ul class="ev">' + evid + "</ul>" : "") +
        (huecos ? '<ul class="ev">' + huecos + "</ul>" : "") +
        '<p class="limite">' + esc(kern.limite || "") + "</p></article>";
    }).join("");
  }

  document.getElementById("detectar-form").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    var body = {
      sectores: list("sectores"),
      clientePide: list("clientePide"),
      logistica: list("logistica"),
      planta: list("planta"),
      kern: list("kern"),
      texto: document.getElementById("texto").value
    };
    var out = document.getElementById("detectar-out");
    out.textContent = "Consultando…";
    try {
      var r = await fetch("/api/detectar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      var data = await r.json();
      out.textContent = JSON.stringify(data, null, 2);
      renderDetectadas(data);
    } catch (err) {
      out.textContent = String(err);
    }
  });

  document.getElementById("detectar-vacio").addEventListener("click", function () {
    ["sectores", "clientePide", "logistica", "planta", "kern"].forEach(function (id) {
      document.getElementById(id).value = "";
    });
    document.getElementById("texto").value = "";
    document.getElementById("detectar-form").requestSubmit();
  });
})();
</script>
</body>
</html>
"""

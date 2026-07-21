"""Server-rendered HTML for GET /tower -- the Control Tower dashboard tab
(Linchpin 3.0 PR-7, plan S5: "Webapp: tab Tower (eventos del dia, acciones T1
auto-ejecutadas y auditadas, pendientes T2 con boton de aprobacion TTL,
confiabilidad A4 por tool) + GET /api/events + POST /api/approvals/{id}").

Follows ``webapp/paquetes_page.py``'s exact pattern: a plain Python string
builder (no Jinja, no build step), served as ``HTMLResponse``. Same dark-
console aesthetic as ``webapp/static/operator/index.html`` and
``webapp/static/prototype/index.html``.

Data sourcing, by section:
  - **Eventos de hoy**: populated by a client-side ``fetch("/api/events")``
    (same "static shell + fetch" pattern as ``/paquetes/{slug}``'s
    client-side markdown fetch) so this module stays a pure string builder.
  - **T1 auto-ejecutado** / **T2 pendiente de aprobacion**: rendered
    SERVER-SIDE from ``scm_agent.autonomy.AutonomyLedger`` records the route
    handler (``webapp/app.py::tower_page``) already queried and passed in --
    the plan only asks for two new endpoints (``GET /api/events`` and
    ``POST /api/approvals/{id}``), so this avoids inventing a third
    (``GET /api/autonomy``) just to feed this page, the same way
    ``render_index_html`` takes ``OFFERS`` as a plain argument instead of
    fetching it.
  - **Confiabilidad A4 por tool**: a clearly-labeled placeholder. A4
    (``src/verify/backtest.py`` + ``src/verify/reliability.py``, plan S5) is
    PR-8's job, not this one -- this section says so instead of fabricating
    numbers (plan rule 14, "ningun cap silencioso").

The T2 "Aprobar" button POSTs to ``/api/approvals/{id}`` with the SAME
``X-API-Key`` the console (``/console``) already asks an operator to enter --
the localStorage key is reused verbatim (``linchpin_console_api_key``) so an
operator who already unlocked one admin surface does not have to re-enter it
here.
"""

from __future__ import annotations

from html import escape

from scm_agent.autonomy import AutonomyRecord
from scm_agent.autonomy_promotion import PromotionRecord

# How many T1 audit rows the page shows -- audit history keeps growing
# forever (scm_agent.autonomy.AutonomyLedger.list_all() is unbounded, same
# shape as jobs/digest_job.py's own list_all()-then-filter-in-Python
# precedent), so the route handler slices to the most recent rows before
# calling render_tower_html(); this constant is the single source of truth
# for how many so the caller and the page agree on it.
T1_DISPLAY_LIMIT = 25

_HEAD = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tower - Kern</title>
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
    --ok:#3fb950; --warn:#e3b341; --bad:#f0564a;
    --accent-warm:#ffb545; --accent-warm-2:#ff8a5c; --accent-warm-deep:#b5601f;
    --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
    --sans:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
    --r:13px;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--ink);color:var(--txt);font-family:var(--sans);font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;
    background-image:radial-gradient(1100px 520px at 10% -8%,rgba(79,209,197,.09),transparent 60%),radial-gradient(900px 620px at 110% 0%,rgba(120,90,255,.06),transparent 55%);background-attachment:fixed}
  a{color:var(--accent-bright);text-decoration:none}
  .wrap{max-width:1080px;margin:0 auto;padding:0 22px}
  header{border-bottom:1px solid var(--line);background:rgba(8,11,17,.7);backdrop-filter:blur(10px)}
  header .wrap{display:flex;align-items:center;justify-content:space-between;height:60px}
  .brand{display:flex;align-items:center;gap:9px;font:700 17px/1 var(--mono)}
  .brand .d{color:var(--accent)}
  header nav{display:flex;gap:18px;align-items:center;font-size:14px;color:var(--txt-2)}
  h1{font-size:clamp(1.7rem,1.2rem+1.8vw,2.4rem);font-weight:800;letter-spacing:-.02em;margin:0}
  h2{font-size:1.1rem;font-weight:700;margin:0 0 4px}
  .eyebrow{font:600 12px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--accent-bright)}
  .muted{color:var(--muted)} .sub{color:var(--txt-2)}
  section{padding:28px 0}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:20px}
  .btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;font:600 13.5px/1 var(--sans);padding:9px 16px;border-radius:999px;border:1px solid transparent;cursor:pointer;transition:transform 80ms ease-out,box-shadow 80ms ease-out,filter 120ms ease-out}
  .btn-primary{background:linear-gradient(150deg,var(--accent-warm),var(--accent-warm-2));color:#2a1200;box-shadow:0 4px 0 var(--accent-warm-deep)}
  .btn-primary:hover{filter:saturate(1.08)}
  .btn-primary:active{transform:translateY(4px);box-shadow:0 0 0 var(--accent-warm-deep)}
  .btn-primary:focus-visible{outline:2px solid var(--accent-warm);outline-offset:2px}
  .btn-primary:disabled{opacity:.5;cursor:default;transform:none;box-shadow:0 4px 0 var(--accent-warm-deep)}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .field-label{font:600 12px/1 var(--mono);letter-spacing:.05em;text-transform:uppercase;color:var(--faint)}
  .input{background:var(--panel-2);border:1px solid var(--line-2);border-radius:8px;color:var(--txt);padding:9px 11px;font:14px/1 var(--sans)}
  .input:focus{outline:none;border-color:var(--accent-bd)}
  table{border-collapse:collapse;width:100%;font-size:13.5px}
  thead th{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line-2);color:var(--faint);font:600 11.5px/1 var(--mono);text-transform:uppercase;letter-spacing:.05em}
  tbody td{padding:9px 10px;border-bottom:1px solid var(--line);color:var(--txt-2);vertical-align:top}
  tbody tr:last-child td{border-bottom:none}
  .chip{display:inline-flex;align-items:center;gap:5px;font:600 11px/1 var(--mono);padding:4px 9px;border-radius:999px;text-transform:uppercase;letter-spacing:.04em}
  .chip-t1{background:rgba(63,185,80,.14);color:var(--ok)}
  .chip-t2{background:rgba(227,179,65,.14);color:var(--warn)}
  .chip-t3{background:rgba(240,86,74,.14);color:var(--bad)}
  .chip-sev-high{background:rgba(240,86,74,.14);color:var(--bad)}
  .chip-sev-medium{background:rgba(227,179,65,.14);color:var(--warn)}
  .chip-sev-low{background:rgba(94,168,254,.14);color:#6ea8fe}
  .empty{color:var(--muted);font-size:13.5px;padding:6px 0}
  .err-msg{color:var(--bad);font-size:13px;margin-top:8px}
  .ok-msg{color:var(--ok);font-size:13px;margin-top:8px}
  .placeholder{border:1px dashed var(--line-2);border-radius:var(--r);padding:18px;color:var(--muted);font-size:13.5px;background:var(--panel-2)}
  code{font-family:var(--mono);font-size:.9em;background:var(--panel-2);border:1px solid var(--line);padding:.1em .4em;border-radius:5px;color:var(--accent)}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <span class="brand"><span class="d">&#9672;</span> Kern</span>
    <nav>
      <a href="/tower">Tower</a>
      <a href="/pricing">Pricing</a>
      <a href="/paquetes">Paquetes</a>
      <a href="/console">Consola</a>
      <a href="/">Dashboard</a>
    </nav>
  </div>
</header>
<main class="wrap">
"""

_FOOT = """
</main>
</body>
</html>
"""


def _tier_chip(tier: str) -> str:
    css = {"T1": "chip-t1", "T2": "chip-t2", "T3": "chip-t3"}.get(tier, "chip-t2")
    return f'<span class="chip {css}">{escape(tier)}</span>'


def _t1_rows(records: list[AutonomyRecord]) -> str:
    if not records:
        return '<p class="empty">Sin acciones T1 auto-ejecutadas todavia (todas las rutas configuradas en ' \
               '<code>config/event_routing.yaml</code> son T2 por ahora).</p>'
    rows = []
    for r in records:
        sku = escape(r.sku) if r.sku else "&mdash;"
        tool = escape(r.tool) if r.tool else "&mdash;"
        rows.append(
            "<tr>"
            f"<td>{_tier_chip(r.tier)}</td>"
            f"<td>{escape(r.event_type)}</td>"
            f"<td>{sku}</td>"
            f"<td>{tool}</td>"
            f'<td>{escape(r.summary)}</td>'
            f"<td>{escape(r.created_at.isoformat())}</td>"
            "</tr>"
        )
    return (
        '<table><thead><tr><th>Tier</th><th>Evento</th><th>SKU</th><th>Tool</th>'
        "<th>Resumen</th><th>Cuando</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _t2_rows(records: list[AutonomyRecord]) -> str:
    if not records:
        return '<p class="empty">No hay aprobaciones pendientes en este momento.</p>'
    rows = []
    for r in records:
        sku = escape(r.sku) if r.sku else "&mdash;"
        tool = escape(r.tool) if r.tool else "&mdash;"
        # Show the one-click approval deadline so the operator knows the window
        # closes -- a legacy row predating the TTL (NULL) shows a dash.
        expires = escape(r.expires_at.isoformat()) if r.expires_at else "&mdash;"
        rows.append(
            "<tr>"
            f"<td>{escape(r.event_type)}</td>"
            f"<td>{sku}</td>"
            f"<td>{tool}</td>"
            f'<td>{escape(r.summary)}</td>'
            f"<td>{escape(r.created_at.isoformat())}</td>"
            f"<td>{expires}</td>"
            "<td>"
            f'<button class="btn btn-primary approve-btn" data-approval-id="{escape(r.id)}" '
            'onclick="towerApprove(this)">Aprobar</button>'
            f'<div class="approve-result" data-approval-id="{escape(r.id)}"></div>'
            "</td>"
            "</tr>"
        )
    return (
        '<table><thead><tr><th>Evento</th><th>SKU</th><th>Tool</th><th>Resumen</th>'
        "<th>Pendiente desde</th><th>Vence</th><th>Accion</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _promotion_rows(records: list[PromotionRecord]) -> str:
    """Pending T2->T1 autonomy PROMOTIONS (Linchpin 3.0 PR-9, Golden Rule
    11) -- evidence-gated proposals from ``scm_agent.autonomy_promotion``
    awaiting a human's one-click approve/reject. T1->T2 DEGRADATIONS never
    appear here: they are applied immediately (no pending state) and show
    up in the T1 audit trail instead."""
    if not records:
        return '<p class="empty">No hay promociones de autonomia pendientes en este momento.</p>'
    rows = []
    for r in records:
        headlines = [
            f"{e.get('headline_precision'):.0%}" for e in r.evidence if e.get("headline_precision") is not None
        ]
        evidence_text = ", ".join(headlines) or "(ver rationale)"
        rows.append(
            "<tr>"
            f"<td>{escape(r.event_type)}</td>"
            f"<td>{escape(r.tool)}</td>"
            f"<td>{_tier_chip(r.from_tier)} &rarr; {_tier_chip(r.to_tier)}</td>"
            f"<td>{escape(evidence_text)}</td>"
            f'<td>{escape(r.rationale)}</td>'
            f"<td>{escape(r.created_at.isoformat())}</td>"
            "<td>"
            f'<button class="btn btn-primary promotion-approve-btn" data-promotion-id="{escape(r.id)}" '
            'onclick="towerApprovePromotion(this)">Aprobar</button> '
            f'<button class="btn promotion-reject-btn" data-promotion-id="{escape(r.id)}" '
            'onclick="towerRejectPromotion(this)">Rechazar</button>'
            f'<div class="approve-result" data-promotion-id="{escape(r.id)}"></div>'
            "</td>"
            "</tr>"
        )
    return (
        '<table><thead><tr><th>Evento</th><th>Tool</th><th>Tier</th><th>Evidencia (precision)</th>'
        "<th>Rationale</th><th>Propuesta desde</th><th>Accion</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def render_tower_html(
    *,
    t1_records: list[AutonomyRecord],
    t2_records: list[AutonomyRecord],
    promotion_records: list[PromotionRecord] | None = None,
) -> str:
    """Render the full ``/tower`` page. ``t1_records``/``t2_records`` are
    real ``scm_agent.autonomy.AutonomyRecord`` rows the caller already
    queried from ``AutonomyLedger`` (see module docstring for why this stays
    a plain-argument function rather than fetching them itself).
    ``promotion_records`` (PR-9, defaults to none pending) are real
    ``scm_agent.autonomy_promotion.PromotionRecord`` rows already queried
    from ``PromotionLedger.list_pending()`` -- same plain-argument
    convention."""
    promotion_records = promotion_records or []
    body = (
        _HEAD
        + '<section style="padding-bottom:6px">'
        + '<span class="eyebrow">Control Tower</span>'
        + '<h1 style="margin-top:12px">Sense &rarr; decide &rarr; execute &rarr; verify</h1>'
        + '<p class="sub" style="max-width:70ch;margin-top:10px">Monitores puros sobre el estado del sistema '
        + "emiten eventos, cada evento se rutea a una tool real por config, y cada resultado se ejecuta bajo "
        + "un tier de autonomia auditado (T1 automatico, T2 un click, T3 escalado a un humano).</p>"
        + "</section>"
        + '<section class="panel">'
        + '<div class="row" style="justify-content:space-between;align-items:flex-end">'
        + "<div><h2>Aprobaciones pendientes (T2)</h2>"
        + '<p class="muted" style="margin:2px 0 0;font-size:13px">Un click completa el pendiente via '
        + "<code>scm_agent.autonomy.acknowledge_pending</code>.</p></div>"
        + '<div><label class="field-label" for="tower-api-key">X-API-Key</label><br>'
        + '<input id="tower-api-key" class="input" type="password" autocomplete="off" spellcheck="false" '
        + 'placeholder="solo si este deploy la requiere" oninput="towerSaveApiKey(this.value)"></div>'
        + "</div>"
        + f'<div style="margin-top:14px">{_t2_rows(t2_records)}</div>'
        + "</section>"
        + '<section class="panel">'
        + "<h2>Promociones de autonomia pendientes (T2&rarr;T1, A4)</h2>"
        + '<p class="muted" style="margin:2px 0 14px;font-size:13px">Propuestas por '
        + "<code>scm_agent.autonomy_promotion.evaluate_promotion</code> con evidencia de "
        + "<code>src/verify/reliability.py</code> adjunta (Regla de Oro 11: la autonomia se gana con "
        + "evidencia, nunca por edicion manual de config) -- aprobar aplica el cambio real a "
        + "<code>config/event_routing.yaml</code>.</p>"
        + _promotion_rows(promotion_records)
        + "</section>"
        + '<section class="panel">'
        + "<h2>Ejecutado automaticamente (T1)</h2>"
        + '<p class="muted" style="margin:2px 0 14px;font-size:13px">Corrio en banda y ya se notifico -- esto es '
        + "el registro auditable, no una segunda notificacion.</p>"
        + _t1_rows(t1_records)
        + "</section>"
        + '<section class="panel">'
        + '<div class="row" style="justify-content:space-between;align-items:center">'
        + "<h2>Eventos de hoy</h2>"
        + '<span class="muted" id="tower-events-count" style="font-size:13px"></span>'
        + "</div>"
        + '<div id="tower-events" style="margin-top:14px" class="empty">Cargando&hellip;</div>'
        + "</section>"
        + '<section class="panel">'
        + "<h2>Confiabilidad por tool (A4)</h2>"
        + '<div class="placeholder" style="margin-top:12px">'
        + "TODO (PR-8, plan S5 fila A4): <code>src/verify/backtest.py</code> + "
        + "<code>src/verify/reliability.py</code> todavia no existen -- esta seccion mostrara MAPE/WAPE/bias "
        + "predicho vs. real por decision y confiabilidad por tool cuando esa PR aterrice. Ningun numero se "
        + "fabrica aca mientras tanto."
        + "</div>"
        + "</section>"
        + TOWER_SCRIPT
        + _FOOT
    )
    return body


# Defined as a module-level constant (referenced by render_tower_html above)
# rather than inlined at that call site, so the ~90-line script stays visually
# separate from the string-concatenation body -- matching
# webapp/paquetes_page.py's own end-of-body <script> placement for
# render_offer_html.
TOWER_SCRIPT = """
<script>
const TOWER_API_KEY_STORAGE_KEY = "linchpin_console_api_key"; // shared with /console on purpose

(function towerInit() {
  var saved = "";
  try { saved = localStorage.getItem(TOWER_API_KEY_STORAGE_KEY) || ""; } catch (e) {}
  var el = document.getElementById("tower-api-key");
  if (el && saved) el.value = saved;
})();

function towerSaveApiKey(value) {
  try { localStorage.setItem(TOWER_API_KEY_STORAGE_KEY, value || ""); } catch (e) {}
}

function towerApiKey() {
  var el = document.getElementById("tower-api-key");
  return el ? el.value.trim() : "";
}

// Events come from GET /api/events (server-controlled today -- only
// scm_agent monitors ever call EventLedger.emit()) but are rendered via
// innerHTML below for the table layout; escaping defends against any future
// path that lets event.type/severity/source/sku/ts or an approval summary
// carry attacker-influenced text, matching the XSS-prevention convention
// used elsewhere in this codebase (never inject unsanitized HTML).
function towerEscape(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

async function towerApprove(button) {
  var id = button.getAttribute("data-approval-id");
  var resultEl = document.querySelector('.approve-result[data-approval-id="' + id + '"]');
  button.disabled = true;
  if (resultEl) resultEl.innerHTML = "";
  var headers = {};
  var key = towerApiKey();
  if (key) headers["X-API-Key"] = key;
  try {
    var r = await fetch("/api/approvals/" + encodeURIComponent(id), { method: "POST", headers: headers });
    var data = await r.json().catch(function () { return {}; });
    if (!r.ok) {
      var msg = r.status === 401
        ? "API key invalida o faltante."
        : (data && data.detail) || ("Error " + r.status + " del servidor.");
      if (resultEl) resultEl.innerHTML = '<div class="err-msg">' + towerEscape(msg) + "</div>";
      button.disabled = false;
      return;
    }
    if (resultEl) resultEl.innerHTML = '<div class="ok-msg">Aprobado: ' + towerEscape(data.summary || "listo") + "</div>";
    button.textContent = "Aprobado";
  } catch (err) {
    if (resultEl) resultEl.innerHTML = '<div class="err-msg">Fallo de red: ' + towerEscape((err && err.message) || err) + "</div>";
    button.disabled = false;
  }
}

async function towerResolvePromotion(button, action) {
  var id = button.getAttribute("data-promotion-id");
  var resultEl = document.querySelector('.approve-result[data-promotion-id="' + id + '"]');
  var row = button.closest("tr");
  if (row) { row.querySelectorAll("button").forEach(function (b) { b.disabled = true; }); }
  if (resultEl) resultEl.innerHTML = "";
  var headers = {};
  var key = towerApiKey();
  if (key) headers["X-API-Key"] = key;
  try {
    var r = await fetch("/api/promotions/" + encodeURIComponent(id) + "/" + action, { method: "POST", headers: headers });
    var data = await r.json().catch(function () { return {}; });
    if (!r.ok) {
      var msg = r.status === 401
        ? "API key invalida o faltante."
        : (data && data.detail) || ("Error " + r.status + " del servidor.");
      if (resultEl) resultEl.innerHTML = '<div class="err-msg">' + towerEscape(msg) + "</div>";
      if (row) { row.querySelectorAll("button").forEach(function (b) { b.disabled = false; }); }
      return;
    }
    if (resultEl) resultEl.innerHTML = '<div class="ok-msg">' + towerEscape(data.summary || "listo") + "</div>";
    button.textContent = action === "approve" ? "Aprobado" : "Rechazado";
  } catch (err) {
    if (resultEl) resultEl.innerHTML = '<div class="err-msg">Fallo de red: ' + towerEscape((err && err.message) || err) + "</div>";
    if (row) { row.querySelectorAll("button").forEach(function (b) { b.disabled = false; }); }
  }
}

function towerApprovePromotion(button) { towerResolvePromotion(button, "approve"); }
function towerRejectPromotion(button) { towerResolvePromotion(button, "reject"); }

async function towerLoadEvents() {
  var listEl = document.getElementById("tower-events");
  var countEl = document.getElementById("tower-events-count");
  try {
    var r = await fetch("/api/events?limit=50");
    var data = await r.json().catch(function () { return {}; });
    if (!r.ok) {
      listEl.className = "err-msg";
      listEl.textContent = (data && data.detail) || ("Error " + r.status + " del servidor.");
      return;
    }
    var events = data.events || [];
    if (countEl) countEl.textContent = events.length + " evento(s)";
    if (events.length === 0) {
      listEl.className = "empty";
      listEl.textContent = "Sin eventos registrados todavia.";
      return;
    }
    var sevClass = { high: "chip-sev-high", medium: "chip-sev-medium", low: "chip-sev-low" };
    var rows = events.slice().reverse().map(function (e) {
      var sku = e.sku ? towerEscape(e.sku) : String.fromCharCode(8212);
      var sevCss = sevClass[String(e.severity || "").toLowerCase()] || "chip-sev-low";
      var sev = '<span class="chip ' + sevCss + '">' + towerEscape(e.severity || "?") + "</span>";
      return "<tr><td>" + towerEscape(e.type) + "</td><td>" + sev + "</td><td>" + sku + "</td><td>" +
        towerEscape(e.source) + "</td><td>" + towerEscape(e.ts) + "</td></tr>";
    }).join("");
    listEl.className = "";
    listEl.innerHTML = "<table><thead><tr><th>Tipo</th><th>Severidad</th><th>SKU</th>" +
      "<th>Origen</th><th>Cuando</th></tr></thead><tbody>" + rows + "</tbody></table>";
  } catch (err) {
    listEl.className = "err-msg";
    listEl.textContent = "Fallo de red: " + towerEscape((err && err.message) || err);
  }
}

towerLoadEvents();
</script>
"""

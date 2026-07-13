"""Server-rendered HTML for GET /pricing -- the Pricing dashboard tab
(Linchpin 3.0 PR-13, plan section 6.11 "Metricas del titan (dashboard, tab
Pricing)" + section 9's "Tabs nuevos: Pricing (posicion vs competidores,
frescura, cuarentena)").

Follows ``webapp/tower_page.py``'s exact pattern: a plain Python string
builder (no Jinja, no build step), same dark-console aesthetic as
``webapp/static/operator/index.html``/``webapp/static/prototype/index.html``,
same plain-argument convention (``render_pricing_html`` takes an already-
computed :class:`PricingSummary` rather than querying anything itself, so
this module stays a pure string builder and the route handler owns I/O).

Data sourcing: unlike Tower's T1/T2 tables (backed by a durable
``AutonomyLedger``), this PR does not add a persisted "last price-intel run"
store -- a run's :class:`~jobs.price_intelligence.PriceIntelReport` lives
only for the duration of one ``jobs.price_intelligence.run()`` call (the CLI
or the registered agent tool). ``webapp/app.py``'s route therefore renders
the honest empty state (``summary=None``) today, exactly the precedent
``tower_page.py`` already set for A4 ("ningun numero se fabrica aca
mientras tanto") -- wiring a persisted summary is future work, not a
fabricated number in the meantime.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class PricingSummary:
    """The handful of numbers this tab shows (plan section 6.11's metrics,
    scoped to what a single one-shot run's report already carries)."""

    client: str
    generated_at: str  # ISO 8601, already formatted by the caller
    n_products: int
    n_products_covered: int
    coverage_pct: float
    quarantine_rate: float
    avg_freshness_hours: float
    sla_hours: float
    tier_mix: dict[str, int]
    n_quarantined: int
    n_discarded: int
    n_skipped: int

    @staticmethod
    def from_report(report: object, *, client: str, generated_at: str) -> "PricingSummary":
        """Build a :class:`PricingSummary` from a real
        ``jobs.price_intelligence.PriceIntelReport`` -- kept as a thin
        adapter (rather than importing that module's type here) so this
        page module has no hard dependency on the pricing-intel package."""
        return PricingSummary(
            client=client, generated_at=generated_at,
            n_products=report.n_products, n_products_covered=report.n_products_covered,
            coverage_pct=report.coverage_pct, quarantine_rate=report.quarantine_rate,
            avg_freshness_hours=report.avg_freshness_hours, sla_hours=report.sla_hours,
            tier_mix=dict(report.tier_mix), n_quarantined=len(report.quarantined),
            n_discarded=len(report.discarded), n_skipped=len(report.skipped),
        )


_HEAD = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pricing - Kern</title>
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
  .grid-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:16px}
  @media(max-width:780px){.grid-stats{grid-template-columns:1fr 1fr}}
  .stat{background:var(--panel-2);border:1px solid var(--line-2);border-radius:10px;padding:14px 16px}
  .stat .v{font:700 22px/1.1 var(--mono);color:var(--accent-bright)}
  .stat .v.bad{color:var(--bad)} .stat .v.warn{color:var(--warn)} .stat .v.ok{color:var(--ok)}
  .stat .l{font-size:12px;color:var(--faint);margin-top:6px;text-transform:uppercase;letter-spacing:.04em}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  table{border-collapse:collapse;width:100%;font-size:13.5px}
  thead th{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line-2);color:var(--faint);font:600 11.5px/1 var(--mono);text-transform:uppercase;letter-spacing:.05em}
  tbody td{padding:9px 10px;border-bottom:1px solid var(--line);color:var(--txt-2);vertical-align:top}
  tbody tr:last-child td{border-bottom:none}
  .chip{display:inline-flex;align-items:center;gap:5px;font:600 11px/1 var(--mono);padding:4px 9px;border-radius:999px;text-transform:uppercase;letter-spacing:.04em}
  .chip-ok{background:rgba(63,185,80,.14);color:var(--ok)}
  .chip-warn{background:rgba(227,179,65,.14);color:var(--warn)}
  .placeholder{border:1px dashed var(--line-2);border-radius:var(--r);padding:18px;color:var(--muted);font-size:13.5px;background:var(--panel-2)}
  code{font-family:var(--mono);font-size:.9em;background:var(--panel-2);border:1px solid var(--line);padding:.1em .4em;border-radius:5px;color:var(--accent)}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <span class="brand"><span class="d">&#9672;</span> Kern</span>
    <nav>
      <a href="/pricing">Pricing</a>
      <a href="/tower">Tower</a>
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


def _stat(value: str, label: str, *, css: str = "") -> str:
    return f'<div class="stat"><div class="v {css}">{escape(value)}</div><div class="l">{escape(label)}</div></div>'


def _empty_state() -> str:
    return (
        '<section class="panel">'
        '<div class="placeholder">'
        "Sin corridas de Diagnostico de Posicion de Precios registradas todavia en este "
        "dashboard. Corre <code>python examples/run_price_intel.py --refs competitors.csv "
        '--client "Acme"</code> (o el paquete <a href="/paquetes/diagnostico-posicion-precios">'
        "Diagnostico de Posicion de Precios</a>) para generar la matriz de posicion, el reporte "
        "y el export del ledger. Este dashboard no fabrica ningun numero mientras tanto (regla "
        "de oro 14)."
        "</div>"
        "</section>"
    )


def _summary_sections(summary: PricingSummary) -> str:
    coverage_css = "ok" if summary.coverage_pct >= 0.60 else "bad"
    quarantine_css = "warn" if summary.quarantine_rate > 0 else "ok"
    freshness_css = "ok" if summary.avg_freshness_hours <= summary.sla_hours else "bad"
    stats = "".join([
        _stat(f"{summary.n_products}", "Productos en alcance"),
        _stat(f"{summary.coverage_pct * 100:.0f}%", "Cobertura (>=60% para embarcar)", css=coverage_css),
        _stat(f"{summary.quarantine_rate * 100:.0f}%", "Tasa de cuarentena", css=quarantine_css),
        _stat(f"{summary.avg_freshness_hours:.1f}h", f"Frescura (SLA {summary.sla_hours:.0f}h)", css=freshness_css),
    ])
    tier_rows = "".join(
        f"<tr><td>{escape(tier)}</td><td>{count}</td></tr>"
        for tier, count in sorted(summary.tier_mix.items())
    ) or '<tr><td colspan="2" class="muted">Sin observaciones aceptadas</td></tr>'
    return (
        '<section style="padding-bottom:6px">'
        f'<span class="eyebrow">Ultima corrida &middot; {escape(summary.client)}</span>'
        f'<h1 style="margin-top:12px">{escape(summary.generated_at)}</h1>'
        f'<div class="grid-stats">{stats}</div>'
        "</section>"
        '<section class="panel">'
        "<h2>Calidad de datos</h2>"
        '<p class="muted" style="margin:2px 0 14px;font-size:13px">Nunca se incluyen como si '
        "fueran confiables (regla de oro 14) -- reportadas aparte:</p>"
        "<table><thead><tr><th>Estado</th><th>Cantidad</th></tr></thead><tbody>"
        f'<tr><td><span class="chip chip-warn">Cuarentena</span></td><td>{summary.n_quarantined}</td></tr>'
        f'<tr><td><span class="chip chip-warn">Descartadas</span></td><td>{summary.n_discarded}</td></tr>'
        f'<tr><td><span class="chip chip-warn">Omitidas</span></td><td>{summary.n_skipped}</td></tr>'
        "</tbody></table>"
        "</section>"
        '<section class="panel">'
        "<h2>Mezcla por tier de adquisicion</h2>"
        '<p class="muted" style="margin:2px 0 14px;font-size:13px">Procedencia por dato (regla de '
        "oro 7) -- meta de largo plazo &gt;=70% L0+L1; L1-heavy es lo esperado sin credenciales "
        "L0 (MELI/Shopify/Amazon).</p>"
        "<table><thead><tr><th>Tier</th><th>Observaciones</th></tr></thead><tbody>"
        f"{tier_rows}"
        "</tbody></table>"
        "</section>"
    )


def render_pricing_html(summary: PricingSummary | None = None) -> str:
    """Render the full ``/pricing`` page. ``summary`` is a real,
    already-computed :class:`PricingSummary` the route handler built from a
    ``jobs.price_intelligence.PriceIntelReport`` (see
    :meth:`PricingSummary.from_report`) -- ``None`` renders the honest empty
    state (see module docstring)."""
    body = (
        _HEAD
        + '<section style="padding-bottom:0">'
        + '<span class="eyebrow">Pricing</span>'
        + '<h1 style="margin-top:12px">Posicion de precios vs. competencia</h1>'
        + '<p class="sub" style="max-width:70ch;margin-top:10px">Precio propio vs. cada competidor '
        + "confirmado, con procedencia por dato (tier de adquisicion, extractor, confianza, fecha) y "
        + "toda fila sospechosa marcada aparte -- nunca mezclada con las confiables.</p>"
        + "</section>"
        + (_summary_sections(summary) if summary is not None else _empty_state())
        + _FOOT
    )
    return body

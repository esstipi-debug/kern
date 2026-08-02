"""Prueba de concepto: el escaneo gratis renderizado como one-pager visual.

    python examples/run_free_scan_onepager.py --data data/sample_stock_snapshot.csv \
        --client "Distribuidora Demo"

Corre el MISMO job que examples/run_free_scan.py (jobs/excess_obsolete_job.py) y,
en vez de imprimir solo el mensaje de texto, arma una pagina HTML autocontenida.

Reparto deliberado (ver documentation/PRUEBA_ONEPAGER_INFOGRAPHIC.md):

- AntV Infographic renderiza SOLO el grafico de barras, y solo las barras: el
  titulo, la descripcion y la tabla de acciones son HTML propio.
- Motivo: los templates colorean por indice de item, no por dato, y el DSL no
  acepta color por item. Si el color no puede significar nada, no puede cargar
  la clasificacion. Aca todas las barras van del mismo color y la clasificacion
  viaja en la tabla, donde si es fiel al job.

Python solo emite texto; el SVG lo arma la libreria en el browser via CDN. No se
toca el motor ni se agregan dependencias Python.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import pandas as pd

from jobs.excess_obsolete_job import EOReport, prepare_records, run

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "webapp" / "static" / "onepager" / "index.html"

INFOGRAPHIC_VERSION = "0.2.19"

# Un solo color en todas las barras: el largo ya codifica la magnitud y el color
# no puede codificar la clasificacion (ver docstring). Repetido porque la paleta
# se recorre por indice de item.
BAR_COLOR = "#b45309"

# Aca si el color significa algo, porque lo pinta HTML propio contra el dato.
CLASS_STYLE = {
    "dead": ("Muerto", "#b45309", "#fef3c7"),
    "excess": ("Excedido", "#0f766e", "#ccfbf1"),
    "healthy": ("Sano", "#4b5563", "#f3f4f6"),
}


def build_chart_syntax(report: EOReport, top: int) -> str:
    """Sintaxis Infographic: solo las barras. Titulo y desc los pone el HTML."""
    lines = ["infographic chart-bar-plain-text", "data", "  values"]
    for entry in _top_lines(report, top):
        lines.append(f"    - label {entry.product_id}")
        lines.append(f"      value {entry.excess_value:.0f}")
    lines += ["theme", "  palette " + " ".join([BAR_COLOR] * 3)]
    return "\n".join(lines)


def _top_lines(report: EOReport, top: int) -> list:
    return [e for e in report.lines if e.excess_value > 0][:top]


def build_rows(report: EOReport, top: int) -> str:
    """Tabla de acciones en HTML propio, con la clasificacion tal cual la da el job."""
    rows = []
    for entry in _top_lines(report, top):
        label, ink, bg = CLASS_STYLE.get(entry.classification, CLASS_STYLE["healthy"])
        rows.append(
            "      <tr>"
            f'<td class="sku">{escape(entry.product_id)}</td>'
            f'<td><span class="tag" style="color:{ink};background:{bg}">{label}</span></td>'
            f'<td class="num">${entry.excess_value:,.0f}</td>'
            f'<td class="num">{entry.on_hand:,.0f}</td>'
            f"<td>{escape(entry.recommended_action)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_html(report: EOReport, client: str, source: str, top: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    shown = len(_top_lines(report, top))
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Escaneo de Stock Muerto - {escape(client)}</title>
<style>
  :root {{ --ink:#111827; --muted:#6b7280; --line:#e5e7eb; --risk:#b45309; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:40px 24px; background:#fff; color:var(--ink);
         font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif; }}
  main {{ max-width:820px; margin:0 auto; }}
  .eyebrow {{ margin:0; font-size:13px; letter-spacing:.08em; text-transform:uppercase;
             color:var(--muted); }}
  h1 {{ margin:6px 0 24px; font-size:28px; font-weight:600; }}
  h2 {{ margin:0 0 2px; font-size:17px; font-weight:600; }}
  .sub {{ margin:0 0 12px; font-size:14px; color:var(--muted); }}
  .headline {{ border:1px solid var(--line); border-radius:12px; padding:20px 24px;
              margin-bottom:36px; }}
  .figure {{ font-size:40px; font-weight:600; color:var(--risk); line-height:1.1; }}
  .figure small {{ display:block; margin-top:10px; font-size:16px; font-weight:400;
                  line-height:1.5; color:var(--muted); }}
  section {{ margin-bottom:36px; }}
  #chart {{ width:100%; height:{90 + shown * 46}px; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th {{ text-align:left; font-weight:500; font-size:12px; letter-spacing:.04em;
       text-transform:uppercase; color:var(--muted); padding:0 12px 8px 0;
       border-bottom:1px solid var(--line); }}
  td {{ padding:12px 12px 12px 0; border-bottom:1px solid var(--line);
       vertical-align:top; }}
  td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums;
                   white-space:nowrap; }}
  td.sku {{ font-weight:600; white-space:nowrap; }}
  .tag {{ display:inline-block; padding:2px 10px; border-radius:999px;
         font-size:12px; font-weight:500; white-space:nowrap; }}
  footer {{ margin-top:8px; padding-top:16px; border-top:1px solid var(--line);
           font-size:13px; color:var(--muted); }}
  code {{ font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12px; }}
</style>
</head>
<body>
<main>
  <p class="eyebrow">Escaneo de stock muerto</p>
  <h1>{escape(client)}</h1>

  <div class="headline">
    <div class="figure">${report.eo_value:,.0f}
      <small>{report.eo_pct_of_value * 100:.0f}% del valor de inventario esta atrapado en
      stock muerto o excedido: {report.n_dead} muertos + {report.n_excess} excedidos de
      {report.n_skus} SKUs analizados.</small>
    </div>
  </div>

  <section>
    <h2>Cash atrapado por SKU</h2>
    <p class="sub">En USD, los {shown} SKUs con mas capital inmovilizado.</p>
    <div id="chart"></div>
  </section>

  <section>
    <h2>Accion recomendada</h2>
    <p class="sub">Clasificacion y accion tal como las emite el motor.</p>
    <table>
      <thead>
        <tr>
          <th>SKU</th><th>Estado</th><th class="num">En riesgo</th>
          <th class="num">Unidades</th><th>Accion</th>
        </tr>
      </thead>
      <tbody>
{build_rows(report, top)}
      </tbody>
    </table>
  </section>

  <footer>
    Fuente: <code>{escape(source)}</code> &middot; motor:
    <code>jobs/excess_obsolete_job.py</code> &middot; umbral stock muerto:
    {report.dead_threshold_days:.0f} dias &middot; cobertura objetivo:
    {report.target_cover_days:.0f} dias &middot; generado {stamp}.
    Cada numero sale del job, sin edicion manual.
  </footer>
</main>

<script src="https://cdn.jsdelivr.net/npm/@antv/infographic@{INFOGRAPHIC_VERSION}/dist/infographic.min.js"></script>
<script>
  var el = document.getElementById('chart');
  var ig = new AntVInfographic.Infographic({{
    container: el, width: '100%', height: '100%', editable: false
  }});
  ig.render({_js_string(build_chart_syntax(report, top))});
</script>
</body>
</html>
"""


def _js_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return f"'{escaped}'"


def main() -> int:
    parser = argparse.ArgumentParser(description="One-pager visual del escaneo gratis.")
    parser.add_argument("--data", required=True, help="stock CSV (mismo formato que run_free_scan.py)")
    parser.add_argument("--client", default="Prospecto")
    parser.add_argument("--top", type=int, default=6, help="cuantos SKUs mostrar")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    report = run(prepare_records(pd.read_csv(args.data)))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(report, args.client, args.data, args.top), encoding="utf-8")

    print(build_chart_syntax(report, args.top))
    print(f"\n[one-pager en {out}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

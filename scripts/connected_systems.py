"""Genera documentation/CONNECTED_SYSTEMS.md desde lo que cada conector DECLARA.

"Kern lee X de Unleashed, deriva Y, no puede dar Z; escribe en Odoo con aprobacion"
nunca se escribe a mano: a mano envejece (el diagnostico del 2026-09-05 conto 11
conteos distintos de tools en los documentos). Este script lee ``SERVICE_COVERAGE``
(que campos de servicio da cada sistema y que significa cada numero) y
``WRITE_SURFACE`` (que escribe y con que compuerta) de cada modulo de
``src/connectors/`` y emite el markdown. ``tests/test_connector_write_surface.py``
exige que el archivo commiteado sea identico a lo generado.

    python scripts/connected_systems.py          # reescribe el .md
    python scripts/connected_systems.py --check  # sale 1 si el .md esta viejo
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connectors import COVERAGE_DERIVED, COVERAGE_MEASURED, WRITE_APPROVAL_GATED  # noqa: E402

OUT = ROOT / "documentation" / "CONNECTED_SYSTEMS.md"

#: Orden de publicacion: primero los sistemas de registro, despues transportistas,
#: despues fuentes secundarias y escritores de precio, ultimo el banco de pruebas.
MODULES = (
    ("odoo", "Odoo (ERP)"),
    ("unleashed", "Unleashed (ERP de inventario)"),
    ("cin7", "Cin7 Core (ERP de inventario)"),
    ("excel", "Planilla Excel del cliente"),
    ("shopify_inventory", "Shopify (inventario)"),
    ("starshipit", "Starshipit (transportista)"),
    ("sendle", "Sendle (transportista)"),
    ("shippit", "Shippit (transportista)"),
    ("olist", "Olist (dataset publico)"),
    ("http_client", "API REST generica de tienda"),
    ("mcp_source", "Otro servidor MCP como fuente"),
    ("odoo_prices", "Odoo (precios)"),
    ("shopify_prices", "Shopify (precios)"),
    ("meli_prices", "MercadoLibre (precios)"),
    ("meli_catalog", "MercadoLibre (catalogo oficial)"),
    ("simulator", "Tienda simulada (banco de pruebas)"),
)

_STATUS_ES = {COVERAGE_MEASURED: "medido", COVERAGE_DERIVED: "derivado"}
_FIELD_ES = {
    "delivered_qty": "cantidad entregada (fill rate)",
    "promised_date": "fecha prometida (OTIF)",
    "delivered_date": "fecha de entrega (OTIF)",
}


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def render() -> str:
    lines = [
        "# Connected systems: que lee y que escribe Kern en cada sistema",
        "",
        "> GENERADO por `python scripts/connected_systems.py` desde `SERVICE_COVERAGE` y",
        "> `WRITE_SURFACE` de cada modulo de `src/connectors/`. No editar a mano: el test",
        "> `tests/test_connector_write_surface.py` falla si este archivo no coincide con lo",
        "> que el codigo declara.",
        "",
        "Leer con esta clave. **Medido** es un dato que el sistema del cliente registra;",
        "**derivado** es un numero que Kern calcula a partir de lo registrado y dice como;",
        "**no disponible** es lo que ese sistema no tiene, y la frase dice que tiene en su",
        "lugar. Del lado de escritura no hay medias tintas: o **solo lectura**, o **escribe",
        "con aprobacion** (dry-run, aprobacion firmada, idempotencia y rollback).",
        "",
        "Una aclaracion que esta tabla no cubre: durante el onboarding asistido, el agente",
        "de setup puede mirar y operar la PANTALLA del cliente (`src/connectors/browser/`),",
        "acotado a una lista de dominios permitidos y con los datos sensibles tachados. Eso",
        "no pasa por el contrato de escritura de abajo porque no habla con ninguna API: es",
        "una persona-maquina usando la interfaz, con el cliente presente.",
        "",
        "## Resumen",
        "",
        "| Sistema | Fill rate | OTIF: prometida | OTIF: entrega | Escribe |",
        "|---|---|---|---|---|",
    ]
    sections: list[str] = []
    for mod_name, title in MODULES:
        mod = importlib.import_module(f"src.connectors.{mod_name}")
        cov = getattr(mod, "SERVICE_COVERAGE", None)
        ws = mod.WRITE_SURFACE
        if cov is not None:
            cells = [
                _STATUS_ES.get(cov.fields()[f].status, "no disponible")
                for f in ("delivered_qty", "promised_date", "delivered_date")
            ]
        else:
            cells = ["no lee pedidos"] * 3
        write_cell = "con aprobacion" if ws.mode == WRITE_APPROVAL_GATED else "solo lectura"
        lines.append(f"| {title} | {cells[0]} | {cells[1]} | {cells[2]} | {write_cell} |")

        sec = [f"## {title}", ""]
        if cov is not None:
            sec += [f"Fuente: `{_cell(cov.backend)}`", "", "| Campo | Estado | Campo del vendor | Que es el numero |", "|---|---|---|---|"]
            for field, fc in cov.fields().items():
                sec.append(
                    f"| {_FIELD_ES.get(field, field)} | {_STATUS_ES.get(fc.status, 'no disponible')} | "
                    f"`{_cell(fc.source)}` | {_cell(fc.means)} |"
                    if fc.source.strip()
                    else f"| {_FIELD_ES.get(field, field)} | no disponible | - | {_cell(fc.means)} |"
                )
            sec.append("")
        if ws.mode == WRITE_APPROVAL_GATED:
            sec.append("**Escribe, con aprobacion:**")
            sec += [f"- `{_cell(o)}`" for o in ws.objects]
            sec += ["", _cell(ws.means), ""]
        else:
            sec += [f"**Solo lectura.** {_cell(ws.means)}", ""]
        sections.append("\n".join(sec))
    lines += ["", *sections]
    return "\n".join(lines).rstrip("\n") + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera documentation/CONNECTED_SYSTEMS.md desde los conectores.")
    parser.add_argument("--check", action="store_true", help="no escribe; sale 1 si el archivo esta desactualizado")
    args = parser.parse_args()
    text = render()
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"{OUT.relative_to(ROOT)} esta desactualizado: correr python scripts/connected_systems.py")
            sys.exit(1)
        print("connected systems: al dia")
        return
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"escrito {OUT.relative_to(ROOT)} ({len(text.splitlines())} lineas)")


if __name__ == "__main__":
    main()

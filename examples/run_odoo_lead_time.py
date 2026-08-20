"""Salida #1 desde un Odoo Online vivo: plazo real vs. el que promete la ficha.

Cuatro campos (url, base, usuario, clave de API) y una lectura de SOLO LECTURA por
la External API de Odoo. Es el mismo camino que sirve ``POST /api/odoo-lead-time``
y desemboca en el MISMO motor que consume un CSV de recepciones subido a /demo:
el motor no se entera de donde vinieron las filas.

Existe porque hasta ahora esa ruta solo la llamaban los tests. Un cliente que
paga necesitaba curl y adivinar los nombres de los campos.

Usage:
    # Offline, sin credenciales ni red -- para ver la forma de la salida:
    python examples/run_odoo_lead_time.py --demo

    # Contra un Odoo real. La clave NO va por la linea de comandos (ver abajo):
    python examples/run_odoo_lead_time.py \\
        --url https://miempresa.odoo.com --db miempresa --login ops@miempresa.cl

    # Acotando el historico que se lee:
    python examples/run_odoo_lead_time.py --url ... --db ... --login ... --since 2026-01-01

LA CLAVE NO ES UN ARGUMENTO, Y ES A PROPOSITO. Un ``--api-key`` queda en el
historial del shell y, en una maquina compartida, a la vista de cualquiera que
corra ``ps``. Se lee de ``KERN_ODOO_API_KEY`` si esta, y si no se pide por
teclado sin eco. Es la misma regla que ya cumple el resto de esta ruta: la clave
no se registra, no se persiste y no viaja en ningun mensaje de error.

SOLO LECTURA. Este script no escribe en Odoo bajo ninguna circunstancia, ni con
aprobacion: no importa el plano de ``nosilo_core.writeback`` y el conector no
ejecuta un solo metodo que no sea de lectura.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connectors import odoo_connect  # noqa: E402
from src.connectors.odoo import demo_odoo_receipts  # noqa: E402
from webapp.demo_lead_time_scan import CTA_PATH, scan_from_deliveries  # noqa: E402

ENV_CLAVE = "KERN_ODOO_API_KEY"


def _obtener_clave() -> str:
    """De la variable de entorno, o del teclado sin eco. Nunca de argv."""
    desde_entorno = os.environ.get(ENV_CLAVE, "").strip()
    if desde_entorno:
        return desde_entorno
    if not sys.stdin.isatty():
        raise SystemExit(
            f"falta la clave de API: exporta {ENV_CLAVE} o corre esto en una terminal "
            "interactiva. No se acepta por argumento -- quedaria en el historial del shell."
        )
    return getpass.getpass("Clave de API de Odoo (no se muestra): ").strip()


def _mostrar_preflight(pre) -> None:
    print("\n=== Lo que se encontro del otro lado ===")
    print(f"  recepciones medibles : {pre.n_receipts}")
    print(f"  proveedores          : {pre.n_suppliers}")
    if pre.first_receipt:
        print(f"  rango de fechas      : {pre.first_receipt} -> {pre.last_receipt} "
              f"({pre.span_days} dias)")
    print(f"  con plazo declarado  : {pre.n_with_declared} proveedor(es)")
    descartes = {k: v for k, v in pre.dropped.items() if v}
    if descartes:
        print("  descartadas          : "
              + ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in descartes.items()))
    for aviso in pre.warnings:
        print(f"  aviso: {aviso}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plazo de entrega real vs. declarado, leido de un Odoo Online (solo lectura).",
    )
    parser.add_argument("--url", default="", help="https://miempresa.odoo.com (o solo el dominio)")
    parser.add_argument("--db", default="", help="la base; en Odoo Online suele ser el subdominio")
    parser.add_argument("--login", default="", help="el usuario con que entras a Odoo")
    parser.add_argument("--since", default="", help="fecha ISO: acota las recepciones que se leen")
    parser.add_argument("--demo", action="store_true",
                        help="corre offline contra un Odoo simulado; ignora los otros argumentos")
    args = parser.parse_args()

    if args.demo:
        print("=== Odoo simulado, offline: sin red y sin credenciales ===")
        # Los cuatro valores son los que `InMemoryOdooServer` acepta por defecto:
        # el punto del modo demo es la SALIDA, no adivinar credenciales.
        servidor = odoo_connect.InMemoryOdooServer(demo_odoo_receipts())
        creds = odoo_connect.OdooCredentials(
            url="https://demo.odoo.com", db="prod",
            login="ops@cliente.cl", api_key="clave-de-api",
        )
        entrada = odoo_connect.scan_input(creds, common=servidor, models=servidor)
    else:
        faltan = [n for n, v in (("--url", args.url), ("--db", args.db), ("--login", args.login)) if not v]
        if faltan:
            raise SystemExit(f"faltan {', '.join(faltan)} (o corre con --demo para verlo offline)")
        creds = odoo_connect.OdooCredentials(
            url=args.url, db=args.db, login=args.login, api_key=_obtener_clave(),
        )
        print(f"=== Conectando a {creds.host} (solo lectura) ===")
        entrada = odoo_connect.scan_input(creds, since=args.since.strip() or None)

    # 1) la conexion -----------------------------------------------------------
    if not entrada.check.ok:
        print(f"\nNo se pudo conectar [{entrada.check.reason}]")
        print(f"  {entrada.check.message}")
        if entrada.check.models:
            print(f"  modelos afectados: {', '.join(entrada.check.models)}")
        raise SystemExit(1)
    print(f"  conectado. Odoo {entrada.check.server_version or '(version no informada)'}")

    # 2) el preflight ----------------------------------------------------------
    _mostrar_preflight(entrada.preflight)
    if not entrada.preflight.ok:
        print("\nNo hay con que medir. Falta:")
        for falta in entrada.preflight.missing:
            print(f"  - {falta}")
        # Y NO se cae al campo `delay` de la ficha del proveedor: usarlo como
        # observacion compara la promesa consigo misma y devuelve brecha cero
        # sobre el defecto exacto que este escaneo viene a mostrar.
        raise SystemExit(2)

    # 3) el mismo motor que come un CSV ---------------------------------------
    scan = scan_from_deliveries(entrada.grouped, entrada.dropped, entrada.source_notes)
    if not scan.ok:
        print("\nEl QA del escaneo no paso, asi que no hay entregable:")
        for issue in scan.qa_issues:
            print(f"  - {issue}")
        raise SystemExit(2)

    h = scan.headline
    print("\n=== Plazo por proveedor ===")
    print(f"  {'Proveedor':<24} {'Recep.':>6} {'Declarado':>10} {'Observado':>10} {'Desv.':>7} {'Brecha':>8}")
    for s in scan.scores:
        def _n(v, ancho=10):
            return f"{v:>{ancho}.1f}" if v is not None else f"{'no medido':>{ancho}}"
        print(f"  {s.supplier:<24} {s.deliveries:>6} {_n(s.declared_lead_time)} "
              f"{_n(s.avg_lead_time)} {_n(s.lead_time_std, 7)} {_n(s.lead_time_gap, 8)}")

    print("\n=== Titular ===")
    print(f"  {h['worst_supplier']}: la ficha dice {h['worst_declared']:.0f} dias, "
          f"las recepciones dicen {h['worst_observed']:.0f}.")
    print(f"  {h['n_skus_to_recalculate']} SKU(s) tienen su stock de seguridad dimensionado "
          "contra el numero equivocado.")

    print("\n=== Hallazgos ===")
    for i, hallazgo in enumerate(scan.findings, start=1):
        print(f"  {i}. {hallazgo}")

    print(f"\nSiguiente paso: {CTA_PATH}")


if __name__ == "__main__":
    main()

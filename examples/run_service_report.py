"""Informe de servicio realizado, y cuanto de el no lo puede ver un ERP.

Corre la cadena de medicion dos veces sobre EL MISMO conjunto de pedidos: una con la fecha
en que la mercaderia llego al cliente, y otra con la fecha en que salio del deposito -- que
es lo unico que registran Unleashed, Cin7 Core y Odoo.

La diferencia entre los dos numeros no es un error de medicion: es el tramo del servicio
que el sistema del cliente no puede ver, y por lo tanto no puede gestionar.

    python examples/run_service_report.py --data data/kaggle/olist --as-of 2018-10-17

Los datos son el dataset publico de Olist (marketplace brasileno real). No vienen en el
repo -- ``data/`` esta gitignoreado -- asi que ``--data`` apunta a donde esten.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from src.connectors import observation_window_end, realized_service  # noqa: E402
from src.connectors.olist import DELIVERY_CARRIER, DELIVERY_CUSTOMER, OlistSource  # noqa: E402
from src.order_fulfillment import aggregate  # noqa: E402


def _pct(valor) -> str:
    return "no medido" if valor is None else f"{valor * 100:.1f}%"


def _plata(valor) -> str:
    return "no medida" if valor is None else f"${valor:,.0f}"


def _leer(carpeta: Path, modo: str, since: str | None):
    fuente = OlistSource(
        carpeta / "olist_orders_dataset.csv",
        carpeta / "olist_order_items_dataset.csv",
        delivery=modo,
    )
    return fuente.orders(since=since)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default="data/kaggle/olist", help="carpeta con los CSV")
    parser.add_argument("--since", default=None, help="leer solo compras desde esta fecha ISO")
    parser.add_argument("--as-of", default=None,
                        help="cierre de la ventana; por defecto se deriva de los datos")
    parser.add_argument("--top", type=int, default=10, help="cuantos SKU listar")
    args = parser.parse_args()

    carpeta = Path(args.data)
    if not (carpeta / "olist_orders_dataset.csv").exists():
        raise SystemExit(
            f"No encuentro los CSV de Olist en {carpeta}. Pasa --data con la carpeta que "
            "los contiene (data/ esta gitignoreado y no viene en el repo)."
        )

    por_llegada = _leer(carpeta, DELIVERY_CUSTOMER, args.since)
    por_despacho = _leer(carpeta, DELIVERY_CARRIER, args.since)

    # La MISMA ventana en las dos lecturas, y no es un detalle: cada una derivaria la suya
    # de la ultima fecha que ve, y la de despacho termina antes. Con ventanas distintas se
    # compararian dos cohortes distintas y parte de la brecha seria un artefacto.
    cierre = args.as_of or observation_window_end(por_llegada)

    llegada = realized_service(por_llegada, as_of=cierre)
    despacho = realized_service(por_despacho, as_of=cierre)
    cartera_ll = aggregate(llegada)
    cartera_de = aggregate(despacho)

    lineas = sum(len(o.lines) for o in por_llegada)
    print("=" * 78)
    print("SERVICIO REALIZADO -- lo que sus clientes efectivamente recibieron")
    print("=" * 78)
    print(f"  Pedidos leidos : {len(por_llegada):,}")
    print(f"  Lineas         : {lineas:,}")
    print(f"  SKU            : {len(llegada):,}")
    print(f"  Ventana        : hasta {cierre}")
    print()

    print("-" * 78)
    print("EL MISMO PERIODO, MEDIDO DE DOS FORMAS")
    print("-" * 78)
    print(f"  {'':<34}{'llegada al cliente':>20}{'salida del deposito':>22}")
    print(f"  {'OTIF (pedido perfecto)':<34}{_pct(cartera_ll.otif):>20}{_pct(cartera_de.otif):>22}")
    print(f"  {'fill rate (unidades servidas)':<34}"
          f"{_pct(cartera_ll.fill_rate):>20}{_pct(cartera_de.fill_rate):>22}")
    print(f"  {'lineas puntuadas':<34}"
          f"{cartera_ll.lines_scored:>20,}{cartera_de.lines_scored:>22,}")
    print()
    if cartera_ll.otif is not None and cartera_de.otif is not None:
        brecha = (cartera_de.otif - cartera_ll.otif) * 100
        fallados = int(round(brecha / 100 * (cartera_ll.lines_scored or 0)))
        print(f"  BRECHA: {brecha:.1f} puntos de OTIF que su ERP no puede ver.")
        print(f"  Son ~{fallados:,} lineas que salieron del deposito a tiempo y llegaron tarde.")
        if cartera_ll.otif:
            print(f"  Aproximadamente 1 de cada {1 / (1 - cartera_ll.otif):.0f} pedidos "
                  "llego despues de lo prometido.")
    print()

    print("-" * 78)
    print(f"PEOR SERVICIO MEDIDO, POR SKU (top {args.top} por venta perdida)")
    print("-" * 78)
    con_perdida = [r for r in llegada if (r.lost_sales_value or 0) > 0]
    con_perdida.sort(key=lambda r: r.lost_sales_value or 0, reverse=True)
    print(f"  {'sku':<36}{'fill':>8}{'otif':>10}{'perdida':>12}{'lineas':>8}")
    for r in con_perdida[: args.top]:
        print(f"  {r.product_id[:34]:<36}{_pct(r.fill_rate):>8}{_pct(r.otif):>10}"
              f"{_plata(r.lost_sales_value):>12}{r.lines_scored:>8}")
    print()
    print(f"  Venta perdida total medida: {_plata(cartera_ll.lost_sales_value)}")
    print()

    print("-" * 78)
    print("QUE HACE FALTA PARA QUE ESTO SE PUEDA MEDIR EN SU SISTEMA")
    print("-" * 78)
    from src.connectors.cin7 import SERVICE_COVERAGE as CIN7
    from src.connectors.olist import SERVICE_COVERAGE as OLIST
    from src.connectors.unleashed import SERVICE_COVERAGE as UNL

    # Se imprime la declaracion TAL CUAL, sin inferir nada de ella. Una version previa
    # deducia "llegada" o "salida" buscando una frase dentro del texto y se equivocaba en
    # la primera fuente -- que es justamente la fragilidad que este campo existe para
    # evitar. Lo que el cliente tiene que leer es lo que el adaptador declara, no lo que
    # un script dedujo de lo que el adaptador declara.
    for cov in (OLIST, UNL, CIN7):
        print(f"  {cov.backend}")
        print(f"      delivered_date -> {cov.delivered_date.means[:150]}")
        print()
    print("  Las tres llenan el MISMO campo y dos de ellas miden otra cosa: la salida del")
    print("  deposito. Esa distincion esta declarada en el codigo y la ejecuta un test")
    print("  (tests/test_connector_service_contract.py), no se asume.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

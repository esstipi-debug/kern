"""Construye los datasets que la bateria de 20 tareas pide bajo un ``data_root``.

Las comprobaciones T03 y T11 (``src/bateria/comprobaciones.py``) leen archivos con
nombre fijo bajo el ``Contexto.data_root``. Sin ellos salen ``SIN_DATO``, que suma
cero por diseno: falta de dato del cliente no es un bug.

Este script deriva lo que se puede derivar de datasets REALES ya presentes en
``data/kaggle/``. No fabrica ninguna columna: un marcador que sube con datos
inventados es peor que uno bajo.

    python scripts/build_bateria_datasets.py --out data/bateria

Despues:

    python -c "import src.bateria.comprobaciones; \
from src.bateria.scorer import Contexto, puntuar; \
p = puntuar(Contexto(data_root=__import__('pathlib').Path('data/bateria'))); \
print(p.puntos, p.porcentaje)"

## T03 -- sales_history.csv  (SE PUEDE)

Fuente: M5 (Walmart), tienda CA_1, extractos ya canonizados en
``data/kaggle/dsets/m5_CA_1_*.csv`` (date, product_id, quantity, unit_cost).
3.049 SKU con 64 meses cada uno, y ``unit_cost`` es el ``sell_price`` real de M5
(2.081 valores distintos entre 0,20 y 30,50) -- no un placeholder.

La unica transformacion es **agregar por mes**: la tarea pide 12 meses de ventas
por SKU, y el eje XYZ mide la variabilidad de la demanda MENSUAL. Alimentar el
clasificador con lineas de pedido diarias mide la dispersion del tamano de pedido,
que es otra cosa.

Superstore se descarto con el dato a la vista: mediana de 5 meses por SKU y solo 22
de 1.862 llegan a 12, ademas de que ``superstore_canonical.csv`` trae
``unit_cost = 1.0`` en todas las filas -- un ABC sobre eso ordena por unidades y se
rotula valor, que es exactamente lo que el criterio de T03 rechaza.

## T11 -- stock_on_hand.csv  (NO SE PUEDE)

Requiere ``on_hand`` por SKU: una FOTO de inventario. M5, Superstore, Olist y
DataCo son transaccionales -- registran lo que se vendio, nunca lo que quedo en
gondola. ``daily_demand`` si sale de las ventas, pero derivar ``on_hand`` de ellas
seria inventarlo. Ver ``--explicar-t11``.
"""

from __future__ import annotations

import argparse
import glob
from pathlib import Path

import pandas as pd

#: Extractos M5 de una sola tienda: mismo calendario y misma moneda para todos los SKU.
M5_GLOB = "data/kaggle/dsets/m5_CA_1_*.csv"

#: Lo que le falta a T11, en los terminos en que habria que pedirselo a un cliente.
T11_FALTA = """\
T11 necesita stock_on_hand.csv, y ningun dataset publico del repo lo tiene.

Columnas que el check exige (jobs/excess_obsolete_job.prepare):
  product_id     -- OK, cualquier dataset lo trae
  on_hand        -- FALTA. Unidades fisicas en stock hoy, por SKU. Es una foto de
                    inventario; ningun dataset transaccional la contiene.
  daily_demand   -- derivable de las ventas reales, no es el bloqueo

Opcionales, mejoran el veredicto:
  unit_cost               -- OK (M5 sell_price)
  days_since_last_sale    -- derivable de las ventas

Con un export de inventario del cliente (product_id + on_hand, una fila por SKU)
T11 se cierra sin tocar codigo.\
"""


def sales_history(m5_glob: str = M5_GLOB) -> pd.DataFrame:
    """Ventas mensuales por SKU con precio, desde los extractos M5 CA_1."""
    archivos = sorted(glob.glob(m5_glob))
    if not archivos:
        raise FileNotFoundError(
            f"no hay extractos M5 en {m5_glob}. Son gitignored: regeneralos o copialos "
            "antes de construir el dataset."
        )
    crudo = pd.concat([pd.read_csv(f) for f in archivos], ignore_index=True)
    crudo["date"] = pd.to_datetime(crudo["date"]).dt.to_period("M").dt.to_timestamp()
    mensual = (
        crudo.groupby(["product_id", "date"])
        .agg(quantity=("quantity", "sum"), unit_cost=("unit_cost", "mean"))
        .reset_index()
        .sort_values(["product_id", "date"])
    )
    return mensual[["date", "product_id", "quantity", "unit_cost"]]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/bateria", help="data_root de la bateria")
    ap.add_argument("--m5-glob", default=M5_GLOB)
    ap.add_argument("--explicar-t11", action="store_true", help="que falta para T11 y salir")
    args = ap.parse_args()

    if args.explicar_t11:
        print(T11_FALTA)
        return 0

    destino = Path(args.out)
    destino.mkdir(parents=True, exist_ok=True)
    ventas = sales_history(args.m5_glob)
    ruta = destino / "sales_history.csv"
    ventas.to_csv(ruta, index=False)

    meses = ventas.groupby("product_id")["date"].nunique()
    print(f"[T03] {ruta}: {len(ventas):,} filas, {meses.size:,} SKU, "
          f"{meses.min()}-{meses.max()} meses c/u, "
          f"{ventas.unit_cost.nunique():,} precios distintos")
    print(f"[T11] {destino / 'stock_on_hand.csv'}: NO generado, ver --explicar-t11")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

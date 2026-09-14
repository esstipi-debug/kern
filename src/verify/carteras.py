"""Carteras sinteticas por tamano de empresa, para verificar a escala real.

**Por que sinteticas y no un dataset real.** Los datasets del banco (M5, Olist,
DataCo, Superstore) traen el volumen pero no el control: no se puede pedirles
"dame exactamente 5.000 SKU, de los cuales 40 sin demanda y 25 sin costo". Y son
justo esos casos degenerados los que rompen a escala, porque en una cartera real
existen pero son raros -- lo bastante raros para no aparecer en una muestra de
dos SKU, lo bastante frecuentes para aparecer varias veces en cinco mil.

Estas carteras son deterministas por semilla, asi que un fallo a 5.000 SKU se
reproduce exactamente. No sustituyen al dataset real: lo complementan. El dato
real dice si Kern sirve para *este* cliente; esto dice si aguanta *un* cliente
de ese tamano.

Los tramos salen de recuentos de SKU tipicos por tamano de operacion, no de
potencias de diez elegidas por comodidad: una tienda con catalogo corto, un
distribuidor pyme, un retailer regional y una cadena nacional.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

SEMANAS_POR_DEFECTO = 52
INICIO = date(2024, 1, 1)


@dataclass(frozen=True)
class Tramo:
    """Un tamano de empresa y la cartera que le corresponde."""

    nombre: str
    n_skus: int
    descripcion: str
    #: Presupuesto de tiempo en segundos para el pipeline completo. Es un techo
    #: operativo, no una medicion: existe para que una regresion de rendimiento
    #: salga en rojo en vez de descubrirse en la demo de un cliente.
    presupuesto_s: float


#: Los cuatro tramos que la verificacion recorre. Anadir uno es barato; quitarlo
#: exige explicar que tamano de cliente deja de estar cubierto.
TRAMOS: tuple[Tramo, ...] = (
    Tramo("micro", 50, "tienda o e-commerce con catalogo corto", 20.0),
    Tramo("pyme", 500, "distribuidor o retailer de una plaza", 90.0),
    Tramo("mediana", 5_000, "retailer regional o mayorista", 600.0),
    Tramo("grande", 50_000, "cadena nacional o distribuidor multi-CD", 4_800.0),
)

TRAMOS_POR_NOMBRE = {t.nombre: t for t in TRAMOS}

# Proporciones de la mezcla. Suman menos de 1: el resto son SKU estables, que
# son la mayoria de cualquier catalogo real.
FRACCION_INTERMITENTE = 0.30
FRACCION_ESTACIONAL = 0.15
FRACCION_RUIDOSA = 0.10
# Degenerados: existen en todo maestro de articulos real -- altas recientes sin
# ventas, y articulos cuyo costo se perdio en la migracion. El job los aisla en
# skipped_zero_demand / skipped_zero_cost, y esa rama sólo se ejerce si la
# cartera los contiene.
FRACCION_SIN_DEMANDA = 0.008
FRACCION_SIN_COSTO = 0.005


#: Longitud del ciclo de clases. La clase de un SKU se decide por ``i % BLOQUE``
#: y NO por ``i / n``, para que el SKU numero 15 sea el mismo producto en una
#: cartera de 30 y en una de 5.000. Sin esa independencia, comparar dos tramos
#: no aisla nada: cambian a la vez el tamano y todos los datos, y una diferencia
#: de resultado no se puede atribuir a ninguno de los dos.
BLOQUE = 200


def _construir_ciclo() -> tuple[str, ...]:
    """El reparto de clases de un bloque, con los degenerados al principio.

    Se materializa una vez al importar en vez de calcularse por SKU: el
    generador lo consulta millones de veces en el tramo grande, y un ciclo
    explicito tambien es inspeccionable -- se puede imprimir y contar, que es
    justo lo que ``composicion()`` publica.

    Los dos degenerados ocupan los indices 0 y 1 de cada bloque aunque su
    fraccion no llegue a un SKU entero. Sin esa reserva, una cartera de 50 SKU
    no contiene ninguno con costo perdido (0,5% de 50 redondea a cero) y la
    rama que el job tiene para aislarlos no se ejerce nunca -- que es el modo de
    fallo que esta verificacion existe para atrapar.
    """
    ciclo = ["sin_demanda", "sin_costo"]
    n_sin_demanda = max(1, round(FRACCION_SIN_DEMANDA * BLOQUE))
    n_sin_costo = max(1, round(FRACCION_SIN_COSTO * BLOQUE))
    ciclo += ["sin_demanda"] * (n_sin_demanda - 1)
    ciclo += ["sin_costo"] * (n_sin_costo - 1)

    resto = BLOQUE - len(ciclo)
    n_interm = round(FRACCION_INTERMITENTE * BLOQUE)
    n_estac = round(FRACCION_ESTACIONAL * BLOQUE)
    n_ruido = round(FRACCION_RUIDOSA * BLOQUE)
    ciclo += ["intermitente"] * n_interm
    ciclo += ["estacional"] * n_estac
    ciclo += ["ruidosa"] * n_ruido
    ciclo += ["estable"] * (resto - n_interm - n_estac - n_ruido)

    if len(ciclo) != BLOQUE:
        raise AssertionError(f"el ciclo mide {len(ciclo)}, no {BLOQUE}")
    return tuple(ciclo)


CICLO = _construir_ciclo()


def _clase(i: int) -> str:
    """La clase del SKU numero ``i``, independiente del tamano de la cartera."""
    return CICLO[i % BLOQUE]


def _serie(clase: str, media: float, desv: float, semanas: int,
           rng: random.Random) -> list[int]:
    if clase == "sin_demanda":
        return [0] * semanas
    if clase == "intermitente":
        # Ocurrencia esparsa con la media conservada: el motor debe enrutarla a
        # Croston, no a suavizado exponencial.
        p = 0.32
        return [
            max(0, round(media / p + rng.gauss(0, desv))) if rng.random() < p else 0
            for _ in range(semanas)
        ]
    fase = rng.uniform(0, math.tau)
    deriva = rng.uniform(-0.004, 0.006) * media
    amplitud = desv * (0.45 if clase == "estacional" else 0.25)
    ruido = desv * (0.9 if clase == "ruidosa" else 0.65)
    out = []
    for i in range(semanas):
        estacion = math.sin((i / semanas) * math.tau + fase) * amplitud
        tendencia = deriva * i if clase == "estacional" else 0.0
        out.append(max(0, round(media + tendencia + estacion + rng.gauss(0, ruido))))
    return out


def generar_cartera(n_skus: int, *, semanas: int = SEMANAS_POR_DEFECTO,
                    semilla: int = 7) -> pd.DataFrame:
    """Una cartera de ``n_skus`` productos con ``semanas`` periodos cada uno.

    Devuelve el formato canonico que consume ``jobs.inventory_optimization.run``:
    ``date, product_id, quantity, unit_cost, lead_time_days``.

    La semilla se deriva del indice del SKU y no de un generador compartido, para
    que la serie de un SKU sea la misma aparezca en la cartera que aparezca. Sin
    eso, comparar el tramo de 500 con el de 5.000 no diria nada: cambiarian a la
    vez el tamano y todos los datos.
    """
    if n_skus <= 0:
        raise ValueError("n_skus debe ser > 0")
    if semanas <= 0:
        raise ValueError("semanas debe ser > 0")

    fechas = [(INICIO + timedelta(weeks=i)).isoformat() for i in range(semanas)]
    ids: list[str] = []
    cantidades: list[int] = []
    costos: list[float] = []
    leads: list[int] = []

    for i in range(n_skus):
        pid = f"SKU-{i:06d}"
        clase = _clase(i)
        rng = random.Random(semilla * 1_000_003 + i)
        media = rng.uniform(5.0, 400.0)
        desv = media * rng.uniform(0.15, 0.55)
        costo = 0.0 if clase == "sin_costo" else round(rng.uniform(0.4, 250.0), 3)
        lead = rng.choice((7, 7, 14, 14, 21, 30))
        serie = _serie(clase, media, desv, semanas, rng)

        ids.extend([pid] * semanas)
        cantidades.extend(serie)
        costos.extend([costo] * semanas)
        leads.extend([lead] * semanas)

    return pd.DataFrame({
        "date": fechas * n_skus,
        "product_id": ids,
        "quantity": cantidades,
        "unit_cost": costos,
        "lead_time_days": leads,
    })


def composicion(n_skus: int) -> dict[str, int]:
    """Cuantos SKU de cada clase trae una cartera de ese tamano.

    Lo publica la verificacion para que un tramo no pueda pasar por casualidad
    de no contener ningun degenerado.
    """
    conteo: dict[str, int] = {}
    for i in range(n_skus):
        c = _clase(i)
        conteo[c] = conteo.get(c, 0) + 1
    return conteo

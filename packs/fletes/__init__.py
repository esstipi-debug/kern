"""Fletes: compara los transportistas del propio cliente, ruta por ruta.

El tercer uso del core (despues de supply chain y source-to-pay). Lee el historial
de envios del cliente, mide costo por kg y puntualidad (OTD) por transportista y
ruta, y recomienda reasignar volumen con el ahorro calculado. No cotiza, no
reserva y no escribe en ningun sistema: es una capa de decision.

``loader``
    El CSV de envios con validacion estricta: una fila mala rechaza el archivo
    con fila, columna y motivo.
``engine``
    Puro, sin reloj, Decimal. Un transportista sin fechas tiene OTD vacio, nunca
    cero, y queda fuera del puntaje con su motivo a la vista.
``tool``
    Un ``register()`` lo pone en el ciclo prepare -> run -> qa -> deliver.

Importa solo stdlib y ``nosilo_core``: ``tests/test_pack_boundary.py`` lo exige.
"""

from .engine import (
    MIN_DIAS_ANUALIZAR,
    MIN_ENVIOS_DEFAULT,
    Celda,
    Comparacion,
    Pesos,
    Recomendacion,
    ResultadoRuta,
    comparar,
)
from .loader import Envio, LoadError, load_envios
from .tool import CARRIER_LANE_COMPARE, TOOL_KEY, build_fletes_tools, register

__all__ = [
    "CARRIER_LANE_COMPARE",
    "Celda",
    "Comparacion",
    "Envio",
    "LoadError",
    "MIN_DIAS_ANUALIZAR",
    "MIN_ENVIOS_DEFAULT",
    "Pesos",
    "Recomendacion",
    "ResultadoRuta",
    "TOOL_KEY",
    "build_fletes_tools",
    "comparar",
    "load_envios",
    "register",
]

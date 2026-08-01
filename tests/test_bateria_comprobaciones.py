"""Las comprobaciones deben poder decir NO. Un check que solo sabe aprobar no mide."""

from __future__ import annotations

import src.bateria.comprobaciones  # noqa: F401  -- registra las comprobaciones
from src.bateria.scorer import Contexto, Estado, puntuar

_CSV = "stock_on_hand.csv"

# product_id, on_hand, daily_demand, unit_cost, days_since_last_sale
_FILAS_CON_RIESGO = (
    "product_id,on_hand,daily_demand,unit_cost,days_since_last_sale\n"
    "SKU-MUERTO,500,0.0,12.5,400\n"  # sin venta hace >180d -> dead
    "SKU-EXCESO,900,1.0,8.0,10\n"  # 900 dias de cobertura -> excess
    "SKU-SANO,30,1.0,5.0,3\n"  # 30 dias -> healthy
)

_FILAS_TODO_SANO = (
    "product_id,on_hand,daily_demand,unit_cost,days_since_last_sale\n"
    "SKU-SANO-A,30,1.0,5.0,3\n"
    "SKU-SANO-B,45,2.0,7.0,5\n"
)


def _contexto(tmp_path, contenido: str) -> Contexto:
    (tmp_path / _CSV).write_text(contenido, encoding="utf-8")
    return Contexto(data_root=tmp_path)


def _t11(puntaje):
    return next(r for r in puntaje.resultados if r.tarea_id == "T11")


def test_t11_sin_dataset_es_sin_dato_no_fallo_de_codigo(tmp_path):
    """Falta de dato del cliente no puede leerse como bug del motor."""
    resultado = _t11(puntuar(Contexto(data_root=tmp_path)))
    assert resultado.estado is Estado.SIN_DATO
    assert _CSV in resultado.evidencia


def test_t11_cumple_cuando_la_lista_trae_dinero_cobertura_y_accion(tmp_path):
    resultado = _t11(puntuar(_contexto(tmp_path, _FILAS_CON_RIESGO)))
    assert resultado.estado is Estado.CUMPLE, resultado.evidencia
    assert resultado.detalle["n_en_riesgo"] == 2
    assert resultado.detalle["faltantes"] == []
    assert resultado.puntos == 1.0


def test_t11_sin_sku_en_riesgo_no_acredita_la_tarea(tmp_path):
    """Un dataset donde nada esta excedido no prueba que Kern sepa detectarlo."""
    resultado = _t11(puntuar(_contexto(tmp_path, _FILAS_TODO_SANO)))
    assert resultado.estado is Estado.SIN_DATO
    assert resultado.puntos == 0.0


def test_t11_suma_al_marcador_global(tmp_path):
    puntaje = puntuar(_contexto(tmp_path, _FILAS_CON_RIESGO))
    assert puntaje.puntos == 1.0
    assert puntaje.porcentaje == 5.0  # 1 de 20

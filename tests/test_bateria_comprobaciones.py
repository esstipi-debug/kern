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
    t11 = _t11(puntaje)
    assert t11.puntos == 1.0
    assert puntaje.puntos >= 1.0


def _resultado(puntaje, tarea_id: str):
    return next(r for r in puntaje.resultados if r.tarea_id == tarea_id)


# --- T01 / T02: estructurales, no dependen del dataset -----------------------


def test_t01_no_cumple_porque_el_entregable_no_lleva_banda():
    """La fuente: 'Sin banda de incertidumbre NO cuenta'.

    Si algun dia el job propaga ``error_std`` al entregable, este test se pone
    rojo y hay que reevaluar el veredicto -- que es exactamente lo que queremos.
    """
    r = _resultado(puntuar(), "T01")
    assert r.estado is Estado.NO_CUMPLE
    assert r.detalle["campos_de_dispersion"] == []
    assert "forecast" in r.detalle["campos"]


def test_t02_es_parcial_tiene_mape_pero_no_sesgo_ni_ciclo_previo():
    r = _resultado(puntuar(), "T02")
    assert r.estado is Estado.PARCIAL
    assert set(r.detalle["faltantes"]) == {
        "sesgo firmado",
        "comparacion contra el ciclo previo",
    }
    assert r.puntos == 0.5


# --- T03: segmentacion con servicio diferenciado -----------------------------

_CSV_VENTAS = "sales_history.csv"

# Costo unitario PLANO: el ABC ordena por unidades y se rotula valor anual.
_VENTAS_COSTO_PLANO = "product_id,quantity,unit_cost\n" + "".join(
    f"SKU-{i:02d},{10 + i * 7},1.0\n" for i in range(12) for _ in range(6)
)

# Costo unitario dispar: el ABC ordena por valor de verdad.
_VENTAS_COSTO_DISPAR = "product_id,quantity,unit_cost\n" + "".join(
    f"SKU-{i:02d},{10 + i * 7},{1.0 + i * 9.5}\n" for i in range(12) for _ in range(6)
)


def _ctx_ventas(tmp_path, contenido: str) -> Contexto:
    (tmp_path / _CSV_VENTAS).write_text(contenido, encoding="utf-8")
    return Contexto(data_root=tmp_path)


def test_t03_sin_ventas_es_sin_dato(tmp_path):
    assert _resultado(puntuar(Contexto(data_root=tmp_path)), "T03").estado is Estado.SIN_DATO


def test_t03_no_acredita_si_el_entregable_no_declara_su_base_de_valor(tmp_path):
    """Hoy ``AbcXyzReport`` no expone ``value_basis``, asi que el ABC podria estar
    ordenando por unidades con un costo placeholder y nadie lo sabria. Sin esa
    declaracion la tarea no se acredita: es PARCIAL, no CUMPLE.

    Cuando el entregable empiece a declararla, este test se pone rojo y hay que
    partirlo en los dos casos reales (ordena por valor / ordena por unidades).
    """
    r = _resultado(puntuar(_ctx_ventas(tmp_path, _VENTAS_COSTO_DISPAR)), "T03")
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.detalle["declara_base_de_valor"] is False
    assert "no declara su base de valor" in r.evidencia
    assert r.puntos == 0.5


def test_t03_segmenta_de_verdad_celdas_y_niveles_diferenciados(tmp_path):
    """Lo que si se puede acreditar hoy: hay mas de un nivel de servicio."""
    r = _resultado(puntuar(_ctx_ventas(tmp_path, _VENTAS_COSTO_PLANO)), "T03")
    assert len(r.detalle["niveles_de_servicio"]) > 1
    assert len(r.detalle["celdas"]) > 1

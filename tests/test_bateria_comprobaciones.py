"""Las comprobaciones deben poder decir NO. Un check que solo sabe aprobar no mide."""

from __future__ import annotations

import src.bateria.comprobaciones  # noqa: F401  -- registra las comprobaciones
from src.bateria.comprobaciones import _CandidatoOC, _veredicto_oc
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


# --- T04: politica con SS sensible al lead time ------------------------------


def test_t04_cumple_y_el_ss_reacciona_al_lead_time():
    """El filo del criterio es de comportamiento: duplicar sigma_L debe mover el SS."""
    r = _resultado(puntuar(), "T04")
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.detalle["reacciona_al_lead_time"] is True
    base = r.detalle["ss_sin_sigma_lt"]
    con = r.detalle["ss_con_sigma_lt"]
    assert all(b > a for a, b in zip(base, con, strict=True))


def test_t04_el_entregable_declara_si_sigma_lt_fue_medido_o_defaulteado():
    """Un SS calculado con sigma_L=0 por defecto no es lo mismo que uno medido."""
    assert _resultado(puntuar(), "T04").detalle["declara_fuente_de_sigma_lt"] is True


# --- T05: propuesta de OC por proveedor, descontando el en-transito ----------


def _candidato(**cambios) -> _CandidatoOC:
    """Un candidato que cumple TODO el criterio; los tests rompen una pieza por vez."""
    base = {
        "tool": "oc_ideal",
        "entregable": "jobs.oc_job.LineaOC",
        "campos": ("supplier", "sku", "order_qty", "required_date", "reason"),
        "descuenta_en_transito": True,
        "prueba_en_transito": "con 80 unidades emitidas la cantidad baja: [100] -> [20]",
    }
    return _CandidatoOC(**{**base, **cambios})


def test_t05_hoy_no_cumple_ningun_entregable_emite_una_oc_por_proveedor():
    """Veredicto real: ddmrp es el unico que netea el en-transito, pero lo que
    entrega es una senal de buffer por parte -- sin proveedor y sin fechas no hay
    OC. Si algun dia un entregable agrupa por proveedor, este test se pone rojo."""
    r = _resultado(puntuar(), "T05")
    assert r.estado is Estado.NO_CUMPLE, r.evidencia
    assert r.puntos == 0.0
    assert r.detalle["mejor"] == "ddmrp"
    por_tool = {c["tool"]: c for c in r.detalle["candidatos"]}
    assert por_tool["ddmrp"]["descuenta_en_transito"] is True
    assert por_tool["excel_replenishment"]["descuenta_en_transito"] is False
    assert "OC agrupada por proveedor" in por_tool["ddmrp"]["faltantes"]
    assert "fecha de pedido / llegada esperada" in por_tool["ddmrp"]["faltantes"]


def test_t05_cumple_con_oc_completa_que_netea_el_en_transito():
    r = _veredicto_oc((_candidato(),))
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t05_ignorar_el_en_transito_es_eliminatorio_aunque_la_oc_este_completa():
    """La fuente: 'si se ignoran las ordenes ya emitidas, sobre-compra y no cuenta'."""
    r = _veredicto_oc((
        _candidato(descuenta_en_transito=False, prueba_en_transito="no hay campo de en-transito"),
    ))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "en-transito" in r.evidencia


def test_t05_campo_de_en_transito_sin_sonda_no_se_acredita():
    """``None`` = hay campo pero nadie probo que se netee. No se regala el punto."""
    r = _veredicto_oc((
        _candidato(descuenta_en_transito=None, prueba_en_transito="sin sonda de comportamiento"),
    ))
    assert r.estado is Estado.NO_CUMPLE
    assert "sin sonda" in r.evidencia


def test_t05_es_parcial_cuando_solo_falta_una_pieza_de_la_oc():
    r = _veredicto_oc((_candidato(campos=("supplier", "sku", "order_qty", "reason")),))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "fecha de pedido / llegada esperada" in r.evidencia


def test_t05_gana_el_que_netea_aunque_otro_tenga_mas_campos():
    """Un entregable mas completo que sobre-compra no puede tapar al que sí netea."""
    completo_sin_neteo = _candidato(
        tool="completo", descuenta_en_transito=False, prueba_en_transito="ignora las OC abiertas"
    )
    netea_incompleto = _candidato(
        tool="netea", campos=("sku", "order_qty", "reason", "required_date")
    )
    r = _veredicto_oc((completo_sin_neteo, netea_incompleto))
    assert r.detalle["mejor"] == "netea"
    assert r.estado is Estado.PARCIAL

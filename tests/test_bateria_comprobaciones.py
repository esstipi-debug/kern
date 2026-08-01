"""Las comprobaciones deben poder decir NO. Un check que solo sabe aprobar no mide."""

from __future__ import annotations

import src.bateria.comprobaciones  # noqa: F401  -- registra las comprobaciones
from src.bateria.comprobaciones import (
    _CandidatoAjuste,
    _CandidatoCaja,
    _CandidatoLeadTime,
    _CandidatoMarkdown,
    _CandidatoOC,
    _CandidatoScorecard,
    _CandidatoServicio,
    _veredicto_ajuste,
    _veredicto_caja,
    _veredicto_lead_time,
    _veredicto_markdown,
    _veredicto_oc,
    _veredicto_scorecard,
    _veredicto_servicio,
)
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


def test_t06_no_cumple_ajusta_la_cantidad_pero_no_explicita_el_trade_off():
    """Veredicto real: hat_settlement mueve Q* al corte de descuento y compara el
    costo total contra el baseline, pero no dice cuanto stock extra se compra, ni
    la cobertura que agrega, ni si conviene. El plan de compra ni siquiera
    compara: redondea a MOQ/caja y el exceso solo sale de diferenciar corridas."""
    r = _resultado(puntuar(), "T06")
    assert r.estado is Estado.NO_CUMPLE, r.evidencia
    assert r.puntos == 0.0
    assert r.detalle["mejor"] == "hat_settlement"
    por_tool = {c["tool"]: c for c in r.detalle["candidatos"]}
    plan = por_tool["integrated_plan (purchase plan)"]
    assert plan["cuantifica_el_exceso"] is False
    assert "238 u de mas" in plan["prueba_exceso"]
    assert "projected_position" in plan["prueba_exceso"]  # coincide, pero no acredita
    hats = por_tool["hat_settlement"]
    assert hats["cuantifica_el_exceso"] is True
    assert set(hats["faltantes"]) == {
        "stock extra en unidades",
        "meses de cobertura que agrega",
        "recomendacion si/no",
    }


def _candidato_ajuste(**cambios) -> _CandidatoAjuste:
    """Un ajuste que cumple TODO el criterio; los tests rompen una pieza por vez."""
    base = {
        "tool": "ajuste_ideal",
        "entregable": "jobs.oc_job.LineaAjustada",
        "restriccion": "MOQ 250",
        "campos": ("order_qty", "delta_costo_total_usd", "exceso_unidades",
                   "cobertura_agregada_meses", "conviene"),
        "ajusta": True,
        "prueba_ajuste": "la cantidad sube de 50 a 250 u",
        "prueba_exceso": "el redondeo compra 200 u de mas (2.000 usd)",
    }
    return _CandidatoAjuste(**{**base, **cambios})


def test_t06_cumple_con_exceso_cobertura_y_recomendacion():
    r = _veredicto_ajuste((_candidato_ajuste(),))
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t06_redondear_sin_cuantificar_el_exceso_es_eliminatorio():
    """La fuente: \"'Redondear hacia arriba' sin cuantificar el exceso NO cuenta\"."""
    r = _veredicto_ajuste((
        _candidato_ajuste(campos=("order_qty", "cobertura_agregada_meses", "conviene")),
    ))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "sin cuantificar el exceso" in r.evidencia


def test_t06_es_parcial_cuando_solo_falta_la_cobertura():
    r = _veredicto_ajuste((
        _candidato_ajuste(campos=("order_qty", "exceso_unidades", "delta_costo_total_usd",
                                  "conviene")),
    ))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "meses de cobertura que agrega" in r.evidencia


def test_t06_no_ajustar_a_la_restriccion_tampoco_acredita():
    """Un entregable que cuantifica un exceso que nunca aplico no ajusto nada."""
    r = _veredicto_ajuste((
        _candidato_ajuste(ajusta=False, prueba_ajuste="la cantidad no se mueve con MOQ 250"),
    ))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "no se mueve" in r.evidencia


def test_t06_gana_el_que_cuantifica_aunque_otro_tenga_mas_campos():
    sin_cuantificar = _candidato_ajuste(
        tool="mudo", campos=("order_qty", "cobertura_agregada_meses", "conviene")
    )
    cuantifica = _candidato_ajuste(tool="cuantifica", campos=("order_qty", "exceso_unidades"))
    r = _veredicto_ajuste((sin_cuantificar, cuantifica))
    assert r.detalle["mejor"] == "cuantifica"
    assert r.estado is Estado.NO_CUMPLE


# --- T12: markdown con unidades y margen -------------------------------------


def _candidato_markdown(**cambios) -> _CandidatoMarkdown:
    """Un plan de markdown que cumple TODO; los tests rompen una pieza."""
    base = {
        "entregable": "jobs.markdown_job.LineaMarkdown",
        "campos": ("product_id", "precio_propuesto", "descuento_pct", "units_to_clear",
                   "margen_bruto_usd", "cobertura_liberada_meses"),
        "metodos": {"SKU-EXC": "elasticity"},
        "precios_propuestos": {"SKU-EXC": 12.68},
        "prueba_degradacion": "sin historial degrada a remate",
    }
    return _CandidatoMarkdown(**{**base, **cambios})


def test_t12_no_cumple_declara_el_supuesto_pero_no_trae_descuento_ni_margen():
    """Veredicto real: el metodo (elasticity / salvage_heuristic) viene declarado
    por linea, pero no hay % de descuento, ni margen bruto, ni cobertura liberada;
    y sin historial de precios el SKU muerto sale sin precio propuesto."""
    r = _resultado(puntuar(), "T12")
    assert r.estado is Estado.NO_CUMPLE, r.evidencia
    assert r.puntos == 0.0
    assert r.detalle["declara_el_supuesto"] is True
    assert r.detalle["metodos_declarados"]["SKU-EXC"] == "elasticity"
    assert r.detalle["precios_propuestos"]["SKU-MUERTO"] is None
    assert "margen bruto resultante en dinero" in r.detalle["faltantes"]
    # "recovered_value" contiene "cover" pero es recaudacion, no cobertura.
    assert "cobertura que se libera" in r.detalle["faltantes"]


def test_t12_cumple_con_precio_descuento_unidades_margen_y_cobertura():
    r = _veredicto_markdown(_candidato_markdown())
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t12_sin_declarar_el_supuesto_de_demanda_es_eliminatorio():
    """La fuente: 'Debe declarar el supuesto de respuesta de demanda usado'."""
    r = _veredicto_markdown(_candidato_markdown(metodos={"SKU-EXC": ""}))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "no declara con que supuesto" in r.evidencia


def test_t12_un_sku_sin_precio_propuesto_no_acredita_la_linea():
    """Un remate sin precio no es 'precio propuesto por SKU'."""
    r = _veredicto_markdown(_candidato_markdown(
        precios_propuestos={"SKU-EXC": 12.68, "SKU-MUERTO": None}
    ))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "precio propuesto por SKU" in r.evidencia


# --- T10: servicio realizado al cliente --------------------------------------


def _candidato_servicio(**cambios) -> _CandidatoServicio:
    """Un entregable de servicio que cumple TODO; los tests rompen una pieza."""
    base = {
        "entregable": "jobs.servicio_job.LineaServicio",
        "campos": ("sku", "otif_salida", "fill_rate_linea", "dias_quiebre", "venta_perdida_usd"),
        # 0.0 = no hay forma de sacar un fill rate sin pedidos: el numero es medido.
        "fill_rate_sin_pedidos": 0.0,
        "prueba_origen": "el fill rate se calcula sobre lineas de pedido despachadas",
    }
    return _CandidatoServicio(**{**base, **cambios})


def test_t10_no_cumple_el_servicio_reportado_es_el_del_modelo():
    """Veredicto real: sin una sola linea de pedido, el motor ya devuelve un fill
    rate del 99,6% a partir del safety stock. Eso es la beta que implica la
    politica, no el servicio realizado, y el criterio lo descalifica por nombre."""
    r = _resultado(puntuar(), "T10")
    assert r.estado is Estado.NO_CUMPLE, r.evidencia
    assert r.puntos == 0.0
    assert r.detalle["mide_lo_realizado"] is False
    assert r.detalle["fill_rate_sin_pedidos"] > 0.9
    assert "dias con quiebre por SKU" in r.detalle["faltantes"]


def test_t10_cumple_con_servicio_medido_quiebres_y_venta_perdida():
    r = _veredicto_servicio(_candidato_servicio())
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t10_un_fill_rate_que_sale_sin_pedidos_es_eliminatorio():
    """La fuente: reportar el nivel de servicio objetivo del modelo no cuenta."""
    r = _veredicto_servicio(_candidato_servicio(
        fill_rate_sin_pedidos=0.98,
        prueba_origen="el fill rate sale del safety stock, sin pedidos",
    ))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "el del modelo, no el realizado" in r.evidencia


def test_t10_es_parcial_cuando_solo_falta_la_venta_perdida():
    r = _veredicto_servicio(_candidato_servicio(
        campos=("sku", "otif_salida", "fill_rate_linea", "dias_quiebre")
    ))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "venta perdida estimada" in r.evidencia


# --- T09: scorecard de proveedores -------------------------------------------


def _candidato_scorecard(**cambios) -> _CandidatoScorecard:
    """Un panel que cumple TODO el criterio; los tests rompen una pieza."""
    base = {
        "entregable": "jobs.panel_job.PanelProveedores",
        "campos": ("supplier", "otif", "in_full_rate", "ppm", "price_variance_pct",
                   "marcado_para_accion"),
        "metricas": {
            "ACME": {"otif": 0.0, "fill_rate": 0.5, "rechazo": 5000.0},
            "BETA": {"otif": 1.0, "fill_rate": 1.0, "rechazo": 0.0},
        },
        "ranking": ("BETA", "ACME"),
        "trazable_a_las_oc": True,
        "prueba_trazabilidad": "cada nota lista las OC que la generaron",
    }
    return _CandidatoScorecard(**{**base, **cambios})


def test_t09_no_cumple_las_notas_no_son_trazables_a_las_oc():
    """Veredicto real: el panel calcula OTIF, fill rate y rechazo y ordena el
    ranking, pero las OC entran con id y ninguno sobrevive al reporte."""
    r = _resultado(puntuar(), "T09")
    assert r.estado is Estado.NO_CUMPLE, r.evidencia
    assert r.puntos == 0.0
    assert r.detalle["trazable_a_las_oc"] is False
    assert r.detalle["ranking"] == ["BETA", "ACME"]
    assert r.detalle["metricas"]["ACME"]["otif"] == 0.0
    assert set(r.detalle["faltantes"]) == {
        "desvio de precio % (OC vs facturado)",
        "los peores marcados para accion",
    }


def test_t09_cumple_con_las_cuatro_metricas_ranking_y_trazabilidad():
    r = _veredicto_scorecard(_candidato_scorecard())
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t09_sin_trazabilidad_es_eliminatorio_aunque_la_tabla_este_completa():
    """La fuente: 'Debe ser trazable a las OC concretas que generaron cada nota'."""
    r = _veredicto_scorecard(_candidato_scorecard(
        trazable_a_las_oc=False, prueba_trazabilidad="el id de OC no sobrevive al reporte"
    ))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "no son trazables" in r.evidencia


def test_t09_una_metrica_no_medida_en_todo_el_panel_no_acredita():
    """Un OTIF ``None`` es 'no lo medimos', no un 0%: no puede contar como medido."""
    r = _veredicto_scorecard(_candidato_scorecard(metricas={
        "ACME": {"otif": None, "fill_rate": 0.5, "rechazo": 5000.0},
        "BETA": {"otif": 1.0, "fill_rate": 1.0, "rechazo": 0.0},
    }))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "OTIF % por proveedor" in r.evidencia


# --- T08: lead time real por proveedor ---------------------------------------


def _candidato_lt(**cambios) -> _CandidatoLeadTime:
    """Un scorecard que cumple TODO el criterio; los tests rompen una pieza."""
    base = {
        "entregable": "jobs.lt_job.LeadTimePorProveedor",
        "campos": ("supplier", "avg_lead_time", "lead_time_p90", "gap_vs_lt_sistema",
                   "skus_a_recalcular_rop"),
        "lt_medido": {"ACME": 22.0, "BETA": 7.5},
        "lt_con_columna_declarada": {"ACME": 22.0, "BETA": 7.5},
    }
    return _CandidatoLeadTime(**{**base, **cambios})


def test_t08_no_cumple_mide_el_lt_pero_lo_pisa_el_declarado_y_no_trae_desvio():
    """Veredicto real: con solo fechas Kern mide 22 d para ACME; agregando una
    columna 'lead_time_days=7' del proveedor devuelve 7 y se pierde hasta la nota
    que decia como lo habia medido. Ademas no hay desvio/P90, ni comparacion
    contra el LT cargado, ni lista de SKU a recalcular."""
    r = _resultado(puntuar(), "T08")
    assert r.estado is Estado.NO_CUMPLE, r.evidencia
    assert r.puntos == 0.0
    assert r.detalle["lt_medido_de_las_fechas"]["ACME"] == 22.0
    assert r.detalle["lt_con_columna_declarada"]["ACME"] == 7.0
    assert r.detalle["prevalece_lo_medido"] is False
    assert "desvio o P90 del LT" in r.detalle["faltantes"]


def test_t08_cumple_con_desvio_gap_y_lista_de_recalculo():
    r = _veredicto_lead_time(_candidato_lt())
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t08_reportar_el_lt_declarado_es_eliminatorio():
    """La fuente: 'Reportar solo el LT nominal que dice el proveedor NO cuenta'."""
    r = _veredicto_lead_time(_candidato_lt(
        lt_medido={"ACME": 7.0, "BETA": 7.0}, lt_con_columna_declarada={"ACME": 7.0, "BETA": 7.0}
    ))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "no mide el lead time" in r.evidencia


def test_t08_es_parcial_cuando_solo_falta_el_desvio():
    r = _veredicto_lead_time(_candidato_lt(
        campos=("supplier", "avg_lead_time", "gap_vs_lt_sistema", "skus_a_recalcular_rop")
    ))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "desvio o P90 del LT" in r.evidencia


# --- T07: calendario de caja contra el tope ----------------------------------


def _candidato_caja(**cambios) -> _CandidatoCaja:
    """Un plan de caja que cumple TODO el criterio; los tests rompen una pieza."""
    base = {
        "entregable": "jobs.caja_job.LineaEgreso",
        "campos": ("semana_de_pago", "egreso_usd", "tope_usd", "deficit_usd",
                   "unidades_en_riesgo"),
        "pedido_usd": 1860.0,
        "tope_usd": 1000.0,
        "prioriza": True,
        "prueba_recorte": "se difieren primero las lineas de menor venta en riesgo",
    }
    return _CandidatoCaja(**{**base, **cambios})


def test_t07_no_cumple_total_de_compra_sin_fecha_de_pago():
    """Veredicto real: el plan de compra si compara contra el tope, pero devuelve
    un total sin calendario, y recorta con un escalar unico que raspa el buffer de
    todos por igual -- eso no es una lista ordenada de lineas a diferir."""
    r = _resultado(puntuar(), "T07")
    assert r.estado is Estado.NO_CUMPLE, r.evidencia
    assert r.puntos == 0.0
    assert r.detalle["prioriza_el_recorte"] is False
    assert r.detalle["deficit_usd"] == 860.0
    assert set(r.detalle["faltantes"]) == {
        "calendario de egresos por semana (fecha de pago)",
        "lista ordenada de lineas a recortar",
        "impacto en quiebre de cada recorte",
    }
    assert "sin ninguna fecha de pago" in r.evidencia


def test_t07_cumple_con_calendario_tope_prioridad_e_impacto():
    r = _veredicto_caja(_candidato_caja())
    assert r.estado is Estado.CUMPLE, r.evidencia
    assert r.puntos == 1.0


def test_t07_sin_fecha_de_pago_es_eliminatorio_aunque_compare_contra_el_tope():
    """La fuente: 'Un total de compra sin fecha de pago NO cuenta'."""
    r = _veredicto_caja(_candidato_caja(campos=("egreso_usd", "tope_usd", "unidades_en_riesgo")))
    assert r.estado is Estado.NO_CUMPLE
    assert r.puntos == 0.0
    assert "sin ninguna fecha de pago" in r.evidencia


def test_t07_recorte_parejo_no_es_lista_priorizada():
    """Raspar el buffer de todos por igual no es elegir que se difiere."""
    r = _veredicto_caja(_candidato_caja(
        prioriza=False, prueba_recorte="todos ceden la misma fraccion de su buffer"
    ))
    assert r.estado is Estado.PARCIAL, r.evidencia
    assert r.puntos == 0.5
    assert "lista ordenada de lineas a recortar" in r.evidencia


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

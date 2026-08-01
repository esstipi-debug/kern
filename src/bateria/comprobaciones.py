"""Las comprobaciones: una por tarea de software de la bateria.

Cada una corre la capa ``jobs/`` sobre un dataset real y verifica el
``criterio_de_aprobado`` **campo por campo**, no "a ojo". La evidencia que
devuelve tiene que alcanzar para defender el veredicto sin volver a correr nada.

Importar este modulo registra las comprobaciones en el scorer.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.bateria.scorer import Contexto, Estado, ResultadoTarea, comprobacion

#: Dataset de stock con el que se puntua T11. Sin el, la tarea es SIN_DATO.
_CSV_STOCK = "stock_on_hand.csv"

#: Dataset de historial de ventas por SKU (T03).
_CSV_VENTAS = "sales_history.csv"

#: Nombres con los que una banda de incertidumbre podria llegar al entregable.
_CAMPOS_DISPERSION = ("sigma", "std", "desvio", "p10", "p90", "percentil", "intervalo", "banda")

#: Nombres con los que el sesgo firmado podria llegar al entregable.
_CAMPOS_SESGO = ("bias", "sesgo")


def _campos_del_registro(clase: type) -> set[str]:
    """Campos que el entregable expone por item (lo que un cliente ve)."""
    return set(getattr(clase, "__dataclass_fields__", {}))


def _alguno_contiene(campos: set[str], agujas: tuple[str, ...]) -> list[str]:
    return sorted(c for c in campos if any(a in c.lower() for a in agujas))


def _cerrar_veredicto(
    tarea_id: str,
    *,
    faltantes: list[str],
    evidencia_cumple: str,
    evidencia_falta: str,
    detalle: dict[str, object],
) -> ResultadoTarea:
    """El tramo final compartido: sin faltantes CUMPLE, con uno PARCIAL, con mas NO_CUMPLE.

    Las condiciones eliminatorias de cada tarea se resuelven ANTES de llamar acá
    -- son propias del criterio y no se pueden promediar con las demas.
    """
    if not faltantes:
        return ResultadoTarea(tarea_id, Estado.CUMPLE, evidencia_cumple, detalle)
    estado = Estado.PARCIAL if len(faltantes) == 1 else Estado.NO_CUMPLE
    return ResultadoTarea(tarea_id, estado, evidencia_falta, detalle)


def _campos_planos(obj: object) -> set[str]:
    """Campos que el cliente ve por linea, aplanando UN nivel de anidamiento.

    ``DdmrpPart`` guarda lo interesante adentro de ``zones``/``signal``; mirar
    solo el nivel de arriba diria que la linea tiene tres campos y perderia el
    ``order_qty``. Devuelve ``signal.order_qty`` para que quede a la vista de
    donde sale cada requisito.
    """
    campos: set[str] = set()
    for nombre in _campos_del_registro(type(obj)):
        anidados = _campos_del_registro(type(getattr(obj, nombre)))
        campos |= {f"{nombre}.{c}" for c in anidados} if anidados else {nombre}
    return campos


@comprobacion("T01")
def t01_pronostico_con_banda(_: Contexto) -> ResultadoTarea:
    """T01 — pronostico base por SKU-periodo **con banda de incertidumbre**.

    La fuente es terminante: "Sin banda de incertidumbre NO cuenta: el stock de
    seguridad no se puede calcular con un punto solo". Es una comprobacion
    estructural sobre el entregable, no sobre el dato: si el registro por SKU no
    tiene donde poner una dispersion, ningun dataset lo arregla.
    """
    from jobs.forecast_job import SkuForecast

    campos = _campos_del_registro(SkuForecast)
    dispersion = _alguno_contiene(campos, _CAMPOS_DISPERSION)
    detalle = {
        "entregable": "jobs.forecast_job.SkuForecast",
        "campos": sorted(campos),
        "campos_de_dispersion": dispersion,
    }

    if dispersion:
        return ResultadoTarea(
            tarea_id="T01",
            estado=Estado.CUMPLE,
            evidencia=f"el registro por SKU expone dispersion: {', '.join(dispersion)}",
            detalle=detalle,
        )

    return ResultadoTarea(
        tarea_id="T01",
        estado=Estado.NO_CUMPLE,
        evidencia=(
            "el entregable devuelve el pronostico como punto: SkuForecast trae "
            "forecast/mae/mape/mase pero ningun sigma ni intervalo P10-P90. "
            "src.forecasting.ForecastResult SI calcula error_std y el job no lo "
            "propaga -- es cableado faltante, no capacidad faltante"
        ),
        detalle=detalle,
    )


@comprobacion("T02")
def t02_sesgo_y_precision(_: Contexto) -> ResultadoTarea:
    """T02 — medir precision **y sesgo firmado**, comparando contra el ciclo previo.

    El criterio exige tres cosas: MAPE, bias con signo, y comparacion contra el
    ciclo anterior (mejora o empeora). Un grafico de ventas explicitamente no
    cuenta.
    """
    from jobs.forecast_job import ForecastJobReport, SkuForecast

    por_sku = _campos_del_registro(SkuForecast)
    del_reporte = _campos_del_registro(ForecastJobReport)

    tiene_mape = bool(_alguno_contiene(por_sku, ("mape",)))
    sesgo = _alguno_contiene(por_sku | del_reporte, _CAMPOS_SESGO)
    ciclo_previo = _alguno_contiene(
        por_sku | del_reporte, ("previo", "anterior", "previous", "delta", "vs_")
    )

    faltantes = [
        nombre
        for nombre, ok in (
            ("MAPE por SKU", tiene_mape),
            ("sesgo firmado", bool(sesgo)),
            ("comparacion contra el ciclo previo", bool(ciclo_previo)),
        )
        if not ok
    ]
    detalle = {
        "entregable": "jobs.forecast_job.ForecastJobReport",
        "campos_por_sku": sorted(por_sku),
        "faltantes": faltantes,
    }

    if not faltantes:
        return ResultadoTarea(
            tarea_id="T02",
            estado=Estado.CUMPLE,
            evidencia="el entregable trae MAPE, sesgo firmado y comparacion contra el ciclo previo",
            detalle=detalle,
        )

    estado = Estado.PARCIAL if tiene_mape else Estado.NO_CUMPLE
    return ResultadoTarea(
        tarea_id="T02",
        estado=estado,
        evidencia=(
            f"el entregable sale sin: {', '.join(faltantes)}. "
            "src.forecasting.ForecastResult calcula bias y el job lo descarta; "
            "sin ciclo congelado guardado tampoco hay contra que comparar"
        ),
        detalle=detalle,
    )


#: sigma_L con el que se sondea la reaccion del safety stock en T04.
_SIGMA_LT_SONDA = 4.0


@comprobacion("T04")
def t04_politica_con_ss_sensible_al_lead_time(_: Contexto) -> ResultadoTarea:
    """T04 — politica por SKU: ROP, SS, lote y el nivel de servicio implicado.

    El filo del criterio es de comportamiento, no de forma: "El SS debe reaccionar
    a la variabilidad de lead time, no solo a la de demanda". Un entregable que
    trae los cuatro campos pero calcula el SS ignorando sigma_L no cumple.

    Se sondea con una serie sintetica porque la propiedad es del codigo, no del
    dato del cliente: se corre dos veces sobre la MISMA demanda, con y sin
    sigma_L, y el SS tiene que subir. Que la politica sirva sobre el catalogo
    real es otra pregunta, y la contesta T05.
    """
    import pandas as pd

    from jobs.inventory_optimization import run

    demanda = pd.DataFrame(
        {
            "product_id": ["A"] * 10 + ["B"] * 10,
            "date": pd.date_range("2025-01-01", periods=10).tolist() * 2,
            "quantity": [10, 12, 9, 11, 10, 13, 8, 12, 11, 10] * 2,
            "unit_cost": [5.0] * 10 + [9.0] * 10,
        }
    )
    sin_sigma = run(demanda)
    con_sigma = run(demanda, lead_time_stds=dict.fromkeys(("A", "B"), _SIGMA_LT_SONDA))

    campos = _campos_del_registro(type(sin_sigma.recommendations[0]))
    faltantes = [
        nombre
        for nombre, campo in (
            ("ROP en unidades", "reorder_point"),
            ("SS en unidades", "safety_stock"),
            ("nivel de servicio implicado", "service_level"),
        )
        if campo not in campos
    ]
    # El lote puede llegar como cantidad de pedido (s,Q) o como nivel objetivo (R,S).
    if not {"order_quantity", "order_up_to"} & campos:
        faltantes.append("lote sugerido")

    ss_base = [r.safety_stock for r in sin_sigma.recommendations]
    ss_sigma = [r.safety_stock for r in con_sigma.recommendations]
    reacciona = all(b > a for a, b in zip(ss_base, ss_sigma, strict=True))
    declara_fuente = "lead_time_std_source" in campos

    detalle = {
        "entregable": "jobs.inventory_optimization.SkuRecommendation",
        "faltantes": faltantes,
        "ss_sin_sigma_lt": ss_base,
        "ss_con_sigma_lt": ss_sigma,
        "reacciona_al_lead_time": reacciona,
        "declara_fuente_de_sigma_lt": declara_fuente,
    }

    if faltantes:
        return ResultadoTarea(
            tarea_id="T04",
            estado=Estado.NO_CUMPLE,
            evidencia=f"la politica sale sin: {', '.join(faltantes)}",
            detalle=detalle,
        )

    if not reacciona:
        return ResultadoTarea(
            tarea_id="T04",
            estado=Estado.PARCIAL,
            evidencia=(
                f"estan los cuatro campos, pero duplicar la variabilidad de lead time "
                f"(sigma_L={_SIGMA_LT_SONDA}) no mueve el safety stock: {ss_base} -> "
                f"{ss_sigma}. El SS solo reacciona a la variabilidad de demanda"
            ),
            detalle=detalle,
        )

    return ResultadoTarea(
        tarea_id="T04",
        estado=Estado.CUMPLE,
        evidencia=(
            f"ROP, SS, lote y nivel de servicio por SKU; con sigma_L={_SIGMA_LT_SONDA} "
            f"el SS sube de {ss_base[0]:.1f} a {ss_sigma[0]:.1f}"
            + (" y el entregable declara si sigma_L fue medido o defaulteado" if declara_fuente else "")
        ),
        detalle=detalle,
    )


#: Nombres con los que cada pieza de la OC podria llegar a la linea del entregable.
_CAMPOS_PROVEEDOR = ("supplier", "vendor", "proveedor")
_CAMPOS_SKU = ("sku", "part_id", "product_id", "item", "codigo")
_CAMPOS_CANTIDAD = ("qty", "quantity", "cantidad", "restock", "units")
_CAMPOS_FECHA = ("date", "fecha", "eta", "arriv", "llegada")
_CAMPOS_RAZON = ("reason", "razon", "motivo", "zone", "trigger", "priority", "classification")

#: Nombres con los que las ordenes ya emitidas podrian entrar al entregable.
_CAMPOS_EN_TRANSITO = ("transit", "transito", "on_order", "onorder", "incoming", "open_po")

#: Ordenes ya emitidas con las que se sondea el neteo en T05.
_ON_ORDER_SONDA = 80.0


@dataclass(frozen=True)
class _CandidatoOC:
    """Un entregable de reposicion, medido contra el criterio de T05.

    ``descuenta_en_transito`` es ``None`` cuando el entregable TIENE un campo de
    en-transito pero esta comprobacion no probo que lo netee: ahi no se acredita
    y la evidencia lo dice: hay que escribir la sonda, no asumir.
    """

    tool: str
    entregable: str
    campos: tuple[str, ...]
    descuenta_en_transito: bool | None
    prueba_en_transito: str

    @property
    def requisitos(self) -> dict[str, list[str]]:
        campos = set(self.campos)
        return {
            "OC agrupada por proveedor": _alguno_contiene(campos, _CAMPOS_PROVEEDOR),
            "SKU por linea": _alguno_contiene(campos, _CAMPOS_SKU),
            "cantidad por linea": _alguno_contiene(campos, _CAMPOS_CANTIDAD),
            "fecha de pedido / llegada esperada": _alguno_contiene(campos, _CAMPOS_FECHA),
            "razon de la linea": _alguno_contiene(campos, _CAMPOS_RAZON),
        }

    @property
    def faltantes(self) -> list[str]:
        return [nombre for nombre, hallados in self.requisitos.items() if not hallados]

    @property
    def cumplidos(self) -> int:
        return len(self.requisitos) - len(self.faltantes)

    def como_detalle(self) -> dict[str, object]:
        return {
            "tool": self.tool,
            "entregable": self.entregable,
            "campos": sorted(self.campos),
            "faltantes": self.faltantes,
            "descuenta_en_transito": self.descuenta_en_transito,
            "prueba_en_transito": self.prueba_en_transito,
        }


def _candidato_excel() -> _CandidatoOC:
    """``excel_replenishment``: el tool de reposicion sobre la planilla del cliente.

    El neteo se juzga estructuralmente porque el hueco es de entrada, no de
    calculo: ni la fila leida ni la linea emitida tienen donde recibir una OC
    abierta, asi que ningun dataset del cliente lo arregla. El propio docstring
    de ``run()`` lo declara como limitacion conocida.
    """
    from jobs.excel_replenishment_job import PlanillaRow, ReplenishmentLine

    entrada = _campos_del_registro(PlanillaRow)
    salida = _campos_del_registro(ReplenishmentLine)
    en_transito = _alguno_contiene(entrada | salida, _CAMPOS_EN_TRANSITO)
    return _CandidatoOC(
        tool="excel_replenishment",
        entregable="jobs.excel_replenishment_job.ReplenishmentLine",
        campos=tuple(sorted(salida)),
        descuenta_en_transito=None if en_transito else False,
        prueba_en_transito=(
            f"campos de en-transito hallados ({', '.join(en_transito)}) pero sin sonda de "
            "comportamiento: no se acredita hasta escribirla"
            if en_transito
            else f"la fila leida ({', '.join(sorted(entrada))}) y la linea emitida no tienen "
            "campo de en-transito: el job no tiene por donde recibir las OC ya emitidas"
        ),
    )


def _candidato_odoo() -> _CandidatoOC:
    """``odoo_replenishment``: reposicion contra el ERP, misma linea que el conector."""
    from src.connectors.replenish import ReplenishmentLine

    salida = _campos_del_registro(ReplenishmentLine)
    en_transito = _alguno_contiene(salida, _CAMPOS_EN_TRANSITO)
    return _CandidatoOC(
        tool="odoo_replenishment",
        entregable="src.connectors.replenish.ReplenishmentLine",
        campos=tuple(sorted(salida)),
        descuenta_en_transito=None if en_transito else False,
        prueba_en_transito=(
            f"campos de en-transito hallados ({', '.join(en_transito)}) pero sin sonda de "
            "comportamiento: no se acredita hasta escribirla"
            if en_transito
            else "la linea se arma sobre el on-hand disponible y no expone ningun campo de "
            "en-transito: las OC abiertas no entran al calculo"
        ),
    )


def _candidato_ddmrp() -> _CandidatoOC:
    """``ddmrp``: el unico que sí netea, y se prueba corriendolo.

    Se corre dos veces sobre las MISMAS partes, con y sin ordenes ya emitidas: la
    cantidad a pedir tiene que bajar. Es la propiedad que el criterio marca como
    eliminatoria ("si se ignoran las ordenes ya emitidas, sobre-compra y no cuenta").
    """
    import pandas as pd

    from jobs import ddmrp_job as job

    partes = pd.DataFrame(
        {
            "part_id": ["P1", "P2"],
            "adu": [10.0, 10.0],
            "dlt": [7.0, 7.0],
            "on_hand": [20.0, 20.0],
            "on_order": [0.0, 0.0],
            "qualified_demand": [5.0, 5.0],
        }
    )
    sin_oc = job.run(job.prepare_records(partes))
    con_oc = job.run(job.prepare_records(partes.assign(on_order=_ON_ORDER_SONDA)))
    qty_sin = [p.signal.order_qty for p in sin_oc.parts]
    qty_con = [p.signal.order_qty for p in con_oc.parts]
    netea = all(b < a for a, b in zip(qty_sin, qty_con, strict=True))
    return _CandidatoOC(
        tool="ddmrp",
        entregable="jobs.ddmrp_job.DdmrpPart",
        campos=tuple(sorted(_campos_planos(sin_oc.parts[0]))),
        descuenta_en_transito=netea,
        prueba_en_transito=(
            f"con {_ON_ORDER_SONDA:g} unidades ya emitidas la cantidad a pedir "
            f"{'baja' if netea else 'NO baja'}: {qty_sin} -> {qty_con}"
        ),
    )


def _veredicto_oc(candidatos: tuple[_CandidatoOC, ...]) -> ResultadoTarea:
    """Puntua T05 con el mejor candidato: primero el que netea, despues el mas completo."""
    mejor = max(candidatos, key=lambda c: (c.descuenta_en_transito is True, c.cumplidos))
    detalle: dict[str, object] = {
        "mejor": mejor.tool,
        "candidatos": [c.como_detalle() for c in candidatos],
    }

    if mejor.descuenta_en_transito is not True:
        sin_neteo = "; ".join(f"{c.tool}: {c.prueba_en_transito}" for c in candidatos)
        return ResultadoTarea(
            tarea_id="T05",
            estado=Estado.NO_CUMPLE,
            evidencia=(
                "ningun entregable de reposicion descuenta el en-transito, y el criterio es "
                f"terminante en que asi no cuenta (sobre-compra). {sin_neteo}"
            ),
            detalle=detalle,
        )

    return _cerrar_veredicto(
        "T05",
        faltantes=mejor.faltantes,
        evidencia_cumple=(
            f"{mejor.tool} emite la OC con proveedor, SKU, cantidad, fecha y razon por "
            f"linea, y descuenta el en-transito ({mejor.prueba_en_transito})"
        ),
        evidencia_falta=(
            f"{mejor.tool} descuenta el en-transito ({mejor.prueba_en_transito}) pero su "
            f"entregable sale sin: {', '.join(mejor.faltantes)}. Lo que llega es una lista de "
            f"reposicion por SKU ({', '.join(mejor.campos)}), no una OC por proveedor"
        ),
        detalle=detalle,
    )


@comprobacion("T05")
def t05_propuesta_de_oc_por_proveedor(_: Contexto) -> ResultadoTarea:
    """T05 — ciclo de reposicion: posicion de inventario -> propuesta de OC por proveedor.

    Dos filos distintos en el mismo criterio. Uno de forma: el entregable tiene
    que ser una OC **por proveedor** con SKU, cantidad, fecha requerida y la razon
    de cada linea. Otro de comportamiento y eliminatorio: "Debe descontar el
    en-transito: si se ignoran las ordenes ya emitidas, sobre-compra y no cuenta".

    Se miden los tres entregables de reposicion que Kern tiene hoy y se puntua el
    mejor. Ninguno depende de un dataset del cliente para contestar esto: el
    proveedor, la fecha y el campo de en-transito estan o no estan en el
    entregable.
    """
    return _veredicto_oc((_candidato_excel(), _candidato_odoo(), _candidato_ddmrp()))


#: Nombres con los que el entregable podria comparar contra la version sin ajustar.
_CAMPOS_COMPARACION = ("baseline", "sin_ajustar", "vs_", "delta", "ahorro", "saving")

#: Nombres con los que el sobrante de la restriccion podria estar rotulado.
_CAMPOS_EXCESO = ("exceso", "extra", "sobra", "surplus", "overbuy", "redondeo", "rounding")

#: Nombres con los que la cobertura agregada podria llegar al entregable. Ojo con
#: la aguja "cover" pelada: matchea "recovered_value" y "recovery_pct", que son
#: recaudacion, no cobertura -- por eso van anclados con guion bajo.
_CAMPOS_COBERTURA = ("_cover", "cover_", "cobertura", "meses", "months", "dio_days", "days_of")

#: Nombres con los que una recomendacion si/no podria llegar al entregable.
_CAMPOS_RECOMENDACION = ("go_nogo", "go_no_go", "verdict", "veredicto", "conviene", "aprobado")

#: Restricciones de proveedor con las que se sondea el ajuste en T06.
_MOQ_SONDA = 250.0
_MULTIPLO_SONDA = 48.0
_CORTE_SONDA_QTY = 1500.0
_CORTE_SONDA_COSTO = 9.2


@dataclass(frozen=True)
class _CandidatoAjuste:
    """Un entregable que ajusta la compra a una condicion del proveedor (T06)."""

    tool: str
    entregable: str
    restriccion: str
    campos: tuple[str, ...]
    ajusta: bool
    prueba_ajuste: str
    prueba_exceso: str

    @property
    def requisitos(self) -> dict[str, bool]:
        campos = set(self.campos)
        return {
            "ajusta a la restriccion del proveedor": self.ajusta,
            "comparacion de costo total contra la version sin ajustar": bool(
                _alguno_contiene(campos, _CAMPOS_COMPARACION)
            ),
            "stock extra en unidades": bool(_alguno_contiene(campos, _CAMPOS_EXCESO)),
            "meses de cobertura que agrega": bool(_alguno_contiene(campos, _CAMPOS_COBERTURA)),
            "recomendacion si/no": bool(_alguno_contiene(campos, _CAMPOS_RECOMENDACION)),
        }

    @property
    def cuantifica_el_exceso(self) -> bool:
        """El filo eliminatorio: redondear sin cuantificar el exceso no cuenta.

        Cualquiera de las dos formas sirve -- el costo total contra la version
        sin ajustar, o las unidades de mas -- pero alguna tiene que estar.
        """
        req = self.requisitos
        return (
            req["comparacion de costo total contra la version sin ajustar"]
            or req["stock extra en unidades"]
        )

    @property
    def faltantes(self) -> list[str]:
        return [nombre for nombre, ok in self.requisitos.items() if not ok]

    @property
    def cumplidos(self) -> int:
        return len(self.requisitos) - len(self.faltantes)

    def como_detalle(self) -> dict[str, object]:
        return {
            "tool": self.tool,
            "entregable": self.entregable,
            "restriccion": self.restriccion,
            "campos": sorted(self.campos),
            "faltantes": self.faltantes,
            "cuantifica_el_exceso": self.cuantifica_el_exceso,
            "prueba_ajuste": self.prueba_ajuste,
            "prueba_exceso": self.prueba_exceso,
        }


def _candidato_purchase_plan() -> _CandidatoAjuste:
    """El plan de compra integrado: la unica via que aplica MOQ y multiplo de caja.

    Se corre dos veces sobre la MISMA demanda, con y sin las condiciones del
    proveedor, y se mide el sobrante que produce el redondeo. Que la
    comprobacion tenga que DIFERENCIAR dos corridas para obtener ese numero es
    justamente la prueba de que el entregable no lo dice: el cliente recibe una
    sola corrida.
    """
    from src.sop_engine.demand_plan import NO_SHIFT_SOURCE, DemandPlanLine
    from src.sop_engine.engine import IntegratedPlanReport
    from src.sop_engine.purchase_plan import SkuPurchaseInputs, build_purchase_plan

    demanda = (DemandPlanLine("A", 100.0, 0.0, 100.0, NO_SHIFT_SOURCE, "sin shift de precio"),)
    comun = {"product_id": "A", "on_hand": 40.0, "unit_cost": 10.0, "reorder_point": 5.0,
             "incoming_po": 10.0}
    libre = SkuPurchaseInputs(**comun)
    restringido = SkuPurchaseInputs(
        **comun, minimum_order_quantity=_MOQ_SONDA, order_multiple=_MULTIPLO_SONDA
    )
    (crudo,), _ = build_purchase_plan(demanda, {"A": libre})
    (ajustado,), _ = build_purchase_plan(demanda, {"A": restringido})

    exceso_u = ajustado.recommended_order - crudo.recommended_order
    exceso_usd = exceso_u * ajustado.unit_cost
    campos_linea = _campos_del_registro(type(ajustado))
    # Un campo puede COINCIDIR con el exceso sin ser el exceso. Se busca, se
    # informa, y no acredita: lo que el criterio pide es explicitarlo.
    coincidencias = sorted(
        c for c in campos_linea
        if isinstance(getattr(ajustado, c), float) and abs(getattr(ajustado, c) - exceso_u) < 1e-9
    )
    return _CandidatoAjuste(
        tool="integrated_plan (purchase plan)",
        entregable="src.sop_engine.purchase_plan.PurchasePlanLine",
        restriccion=f"MOQ {_MOQ_SONDA:g} + multiplo de caja {_MULTIPLO_SONDA:g}",
        campos=tuple(sorted(campos_linea | _campos_del_registro(IntegratedPlanReport))),
        ajusta=ajustado.recommended_order > crudo.recommended_order,
        prueba_ajuste=(
            f"la cantidad sube de {crudo.recommended_order:g} a {ajustado.recommended_order:g} u "
            f"al aplicar MOQ {_MOQ_SONDA:g} y multiplo {_MULTIPLO_SONDA:g}"
        ),
        prueba_exceso=(
            f"el redondeo compra {exceso_u:g} u de mas ({exceso_usd:,.0f} usd) y ese numero solo "
            f"sale de diferenciar dos corridas"
            + (
                f"; {', '.join(coincidencias)} coincide numericamente pero esta documentado como "
                "posicion proyectada, no como el sobrante de la restriccion"
                if coincidencias else "; ningun campo lo expone"
            )
        ),
    )


def _candidato_hats_settlement() -> _CandidatoAjuste:
    """El plan reconciliado: la unica via que reacciona a un corte de descuento.

    Se liquida el MISMO SKU con un tarifario plano y con uno que trae el corte,
    bajo una politica de pesos del comprador (el sombrero cuyo objetivo es
    capturar descuentos por volumen). Si el corte es real, Q* tiene que saltar.
    """
    from jobs.hats_job import SettlementReport, run_settlement
    from src.eoq import PriceBreak
    from src.hats import HatConfig, build_inputs

    comun = {"sku": "A", "annual_demand": 5200.0, "mean_weekly": 100.0, "std_weekly": 20.0,
             "lead_time_weeks": 2.0, "unit_cost": 10.0, "config": HatConfig()}
    pesos = {"comprador": 1.0, "planner": 0.0, "cfo": 0.0, "comercial": 0.0}
    base = (PriceBreak(0.0, 10.0),)
    plano = build_inputs(price_breaks=base, **comun)
    con_corte = build_inputs(
        price_breaks=(*base, PriceBreak(_CORTE_SONDA_QTY, _CORTE_SONDA_COSTO)), **comun
    )
    sin = run_settlement({"inputs": (plano,), "weights": pesos}).settlements[0]
    con = run_settlement({"inputs": (con_corte,), "weights": pesos}).settlements[0]

    return _CandidatoAjuste(
        tool="hat_settlement",
        entregable="src.hat_council.Settlement",
        restriccion=f"corte de descuento a {_CORTE_SONDA_QTY:g} u ({_CORTE_SONDA_COSTO:g} usd/u)",
        campos=tuple(sorted(_campos_planos(con) | _campos_del_registro(SettlementReport))),
        ajusta=con.chosen.order_quantity > sin.chosen.order_quantity,
        prueba_ajuste=(
            f"con el corte a {_CORTE_SONDA_QTY:g} u el Q* pasa de "
            f"{sin.chosen.order_quantity:,.0f} a {con.chosen.order_quantity:,.0f} u"
        ),
        prueba_exceso=(
            f"value_vs_baseline_usd compara el costo total contra la version sin ajustar "
            f"({con.value_vs_baseline_usd:+,.0f} usd/anio), pero el entregable no trae el Q "
            f"sin ajustar: las {con.chosen.order_quantity - sin.chosen.order_quantity:,.0f} u "
            "de stock extra no estan escritas en ningun lado"
        ),
    )


def _veredicto_ajuste(candidatos: tuple[_CandidatoAjuste, ...]) -> ResultadoTarea:
    """Puntua T06 con el mejor candidato.

    Dos condiciones eliminatorias, en orden: ajustar de verdad a la restriccion
    (sin eso no hay tarea que puntuar, por completo que sea el resto del
    entregable) y cuantificar el exceso. Recien despues cuentan los faltantes.
    """
    mejor = max(candidatos, key=lambda c: (c.ajusta, c.cuantifica_el_exceso, c.cumplidos))
    detalle: dict[str, object] = {
        "mejor": mejor.tool,
        "candidatos": [c.como_detalle() for c in candidatos],
    }

    if not mejor.ajusta:
        return ResultadoTarea(
            tarea_id="T06",
            estado=Estado.NO_CUMPLE,
            evidencia=(
                "ningun entregable mueve la cantidad al aplicar la condicion del proveedor. "
                + "; ".join(f"{c.tool}: {c.prueba_ajuste}" for c in candidatos)
            ),
            detalle=detalle,
        )

    if not mejor.cuantifica_el_exceso:
        return ResultadoTarea(
            tarea_id="T06",
            estado=Estado.NO_CUMPLE,
            evidencia=(
                "los entregables redondean hacia arriba sin cuantificar el exceso, y el criterio "
                "dice que asi no cuenta. "
                + "; ".join(f"{c.tool}: {c.prueba_exceso}" for c in candidatos)
            ),
            detalle=detalle,
        )

    return _cerrar_veredicto(
        "T06",
        faltantes=mejor.faltantes,
        evidencia_cumple=(
            f"{mejor.tool} ajusta a {mejor.restriccion} ({mejor.prueba_ajuste}) y explicita el "
            f"trade-off completo: exceso, cobertura agregada y recomendacion si/no"
        ),
        evidencia_falta=(
            f"{mejor.tool} ajusta a {mejor.restriccion} ({mejor.prueba_ajuste}) pero el entregable "
            f"sale sin: {', '.join(mejor.faltantes)}. {mejor.prueba_exceso}"
        ),
        detalle=detalle,
    )


@comprobacion("T06")
def t06_ajuste_a_las_condiciones_del_proveedor(_: Contexto) -> ResultadoTarea:
    """T06 — ajustar la compra a MOQ, multiplos, minimo de flete y cortes de descuento.

    El criterio no se conforma con que la cantidad cambie: pide el trade-off en
    dinero -- cuanto stock extra se compra, cuantos meses de cobertura agrega, y
    si conviene o no. Y marca el atajo: "'Redondear hacia arriba' sin cuantificar
    el exceso NO cuenta".

    Kern parte la tarea en dos entregables que no se hablan: el plan de compra
    aplica MOQ y multiplo de caja, y el plan reconciliado reacciona al corte de
    descuento. Se sondean los dos corriendolos con y sin la restriccion, y se
    puntua el mejor. No se cuenta como recomendacion el bloque ``recommendations``
    del deck: son tres frases fijas ("aplicar Q*/SL*", "revisar el acta",
    "re-correr con el tarifario real"), iguales corra lo que corra, no un si/no
    sobre este trade-off. El minimo de flete no tiene implementacion en ningun
    lado: no hay candidato que sondear.
    """
    return _veredicto_ajuste((_candidato_purchase_plan(), _candidato_hats_settlement()))


#: Nombres con los que una fecha de pago o una semana de egreso podria llegar al entregable.
_CAMPOS_FECHA_PAGO = ("payment", "pago", "due", "vencimiento", "week", "semana", "calendar",
                      "calendario", "date", "fecha")

#: Nombres con los que el contraste contra el tope de caja podria llegar al entregable.
_CAMPOS_TOPE = ("budget", "presupuesto", "tope", "shortfall", "deficit", "investment")

#: Nombres con los que el impacto en quiebre del recorte podria llegar al entregable.
_CAMPOS_QUIEBRE = ("stockout", "quiebre", "at_risk", "en_riesgo", "lost_sale", "fill_rate")

#: Tope de caja con el que se sondea el recorte en T07 (por debajo de lo pedido).
_TOPE_SONDA = 1000.0


@dataclass(frozen=True)
class _CandidatoCaja:
    """El plan de compra enfrentado a un tope de caja (T07)."""

    entregable: str
    campos: tuple[str, ...]
    pedido_usd: float
    tope_usd: float
    prioriza: bool
    prueba_recorte: str

    @property
    def deficit_usd(self) -> float:
        return self.pedido_usd - self.tope_usd

    @property
    def requisitos(self) -> dict[str, bool]:
        campos = set(self.campos)
        return {
            "calendario de egresos por semana (fecha de pago)": bool(
                _alguno_contiene(campos, _CAMPOS_FECHA_PAGO)
            ),
            "excedente/deficit contra el tope": bool(_alguno_contiene(campos, _CAMPOS_TOPE)),
            "lista ordenada de lineas a recortar": self.prioriza,
            "impacto en quiebre de cada recorte": bool(_alguno_contiene(campos, _CAMPOS_QUIEBRE)),
        }

    @property
    def faltantes(self) -> list[str]:
        return [nombre for nombre, ok in self.requisitos.items() if not ok]

    def como_detalle(self) -> dict[str, object]:
        return {
            "entregable": self.entregable,
            "campos": sorted(self.campos),
            "faltantes": self.faltantes,
            "deficit_usd": self.deficit_usd,
            "prioriza_el_recorte": self.prioriza,
            "prueba_recorte": self.prueba_recorte,
        }


def _veredicto_caja(cand: _CandidatoCaja) -> ResultadoTarea:
    """Puntua T07. Eliminatorio, con las palabras de la fuente: un total de compra
    sin fecha de pago no cuenta -- sin calendario no hay nada que contrastar semana
    a semana, por bien que se compare el total contra el tope."""
    detalle: dict[str, object] = cand.como_detalle()
    if not cand.requisitos["calendario de egresos por semana (fecha de pago)"]:
        return ResultadoTarea(
            tarea_id="T07",
            estado=Estado.NO_CUMPLE,
            evidencia=(
                f"el plan de compra devuelve un total ({cand.pedido_usd:,.0f} usd) sin ninguna "
                f"fecha de pago: ningun campo del entregable ({', '.join(sorted(cand.campos))}) "
                f"lleva semana ni vencimiento, y el criterio dice que asi no cuenta. Contra el "
                f"tope si compara ({cand.deficit_usd:,.0f} usd de deficit), pero "
                f"{cand.prueba_recorte}"
            ),
            detalle=detalle,
        )

    return _cerrar_veredicto(
        "T07",
        faltantes=cand.faltantes,
        evidencia_cumple=(
            f"el entregable calendariza los egresos, los contrasta contra el tope "
            f"({cand.deficit_usd:,.0f} usd de deficit) y prioriza el recorte con impacto "
            f"en quiebre"
        ),
        evidencia_falta=(
            f"hay calendario de egresos, pero el entregable sale sin: "
            f"{', '.join(cand.faltantes)}. {cand.prueba_recorte}"
        ),
        detalle=detalle,
    )


@comprobacion("T07")
def t07_calendario_de_caja_contra_el_tope(_: Contexto) -> ResultadoTarea:
    """T07 — comprometer caja por semana contra el presupuesto y recortar si no entra.

    El criterio pide cuatro cosas y descalifica una: "Un total de compra sin fecha
    de pago NO cuenta". Kern tiene UNA sola via donde la compra se enfrenta a un
    tope -- ``allocate_under_budget``, que el plan de compra integrado expone como
    ``BudgetAllocation`` -- asi que se la sondea a ella: se corre el mismo plan sin
    tope y con un tope por debajo de lo pedido.

    Lo que el sondeo muestra es que el recorte existe pero es un ESCALAR: un unico
    ``safety_stock_scale`` que raspa el buffer de todos los SKU por igual. Eso no
    es "una lista ordenada de lineas a recortar": nadie eligio que se difiere
    primero, y no hay ningun numero de quiebre para elegirlo.
    """
    from src.constraints import BudgetAllocation
    from src.sop_engine.demand_plan import NO_SHIFT_SOURCE, DemandPlanLine
    from src.sop_engine.engine import IntegratedPlanReport
    from src.sop_engine.purchase_plan import SkuPurchaseInputs, build_purchase_plan

    demanda = (
        DemandPlanLine("A", 100.0, 0.0, 100.0, NO_SHIFT_SOURCE, "sin shift de precio"),
        DemandPlanLine("B", 200.0, 0.0, 200.0, NO_SHIFT_SOURCE, "sin shift de precio"),
    )
    entradas = {
        "A": SkuPurchaseInputs("A", on_hand=0.0, unit_cost=10.0, reorder_point=60.0),
        "B": SkuPurchaseInputs("B", on_hand=0.0, unit_cost=4.0, reorder_point=90.0),
    }
    lineas_libres, _ = build_purchase_plan(demanda, entradas)
    lineas_topadas, asignacion = build_purchase_plan(demanda, entradas, budget=_TOPE_SONDA)

    recortes = {
        libre.product_id: libre.reorder_buffer - topada.reorder_buffer
        for libre, topada in zip(lineas_libres, lineas_topadas, strict=True)
    }
    # Un recorte proporcional deja a todos con la MISMA fraccion del buffer: eso es
    # raspar parejo, no priorizar. Se mide sobre la fraccion, no sobre las unidades.
    fracciones = {
        topada.product_id: topada.reorder_buffer / libre.reorder_buffer
        for libre, topada in zip(lineas_libres, lineas_topadas, strict=True)
    }
    prioriza = len(set(round(f, 9) for f in fracciones.values())) > 1

    campos = (
        _campos_del_registro(type(lineas_topadas[0]))
        | _campos_del_registro(BudgetAllocation)
        | _campos_del_registro(IntegratedPlanReport)
    )
    return _veredicto_caja(_CandidatoCaja(
        entregable=(
            "src.sop_engine.purchase_plan.PurchasePlanLine + src.constraints.BudgetAllocation"
        ),
        campos=tuple(sorted(campos)),
        pedido_usd=asignacion.requested_investment,
        tope_usd=_TOPE_SONDA,
        prioriza=prioriza,
        prueba_recorte=(
            f"con un tope de {_TOPE_SONDA:,.0f} usd contra "
            f"{asignacion.requested_investment:,.0f} pedidos, el recorte es un escalar unico "
            f"(safety_stock_scale={asignacion.safety_stock_scale:.3f}): "
            f"{', '.join(f'{k} -{v:g} u' for k, v in recortes.items())}, todos ceden la misma "
            "fraccion de su buffer"
        ),
    ))


#: Nombres con los que el desvio del lead time podria llegar al entregable.
_CAMPOS_DESVIO_LT = ("std", "desvio", "sigma", "p90", "percentil", "variab")

#: Nombres con los que la comparacion contra el LT cargado podria llegar al entregable.
_CAMPOS_LT_SISTEMA = ("nominal", "sistema", "cargado", "planned", "declarad", "gap", "diferencia")

#: Nombres con los que la lista de parametros a recalcular podria llegar al entregable.
_CAMPOS_RECALCULO = ("recalc", "reorder_point", "safety_stock", "rop", "ss_", "parametro")

#: OC sintéticas con las que se sondea el lead time observado (T08).
_OC_SONDA_HEADER = "supplier,order_date,delivery_date,promised_date,quantity,received_qty,defects,unit_price"
_OC_SONDA_FILAS = (
    "ACME,2026-01-01,2026-01-21,2026-01-15,100,100,0,10",
    "ACME,2026-02-01,2026-02-25,2026-02-15,100,90,1,10",
    "BETA,2026-01-05,2026-01-12,2026-01-15,50,50,0,12",
    "BETA,2026-02-05,2026-02-13,2026-02-15,50,50,0,12",
)
#: Lead time "que dice el proveedor", inyectado como columna en la segunda corrida.
_LT_DECLARADO_SONDA = 7.0


@dataclass(frozen=True)
class _CandidatoLeadTime:
    """El scorecard de proveedores medido contra el criterio de T08."""

    entregable: str
    campos: tuple[str, ...]
    lt_medido: dict[str, float]
    lt_con_columna_declarada: dict[str, float]

    @property
    def mide_de_las_fechas(self) -> bool:
        """Gate: el LT sale de fecha OC vs recepcion, no de lo que declara el proveedor."""
        return bool(self.lt_medido) and any(
            lt != _LT_DECLARADO_SONDA for lt in self.lt_medido.values()
        )

    @property
    def prevalece_lo_medido(self) -> bool:
        """Con las dos fuentes presentes, el entregable tiene que quedarse con la medida."""
        return self.lt_con_columna_declarada == self.lt_medido

    @property
    def requisitos(self) -> dict[str, bool]:
        campos = set(self.campos)
        return {
            "LT promedio observado por proveedor": self.mide_de_las_fechas,
            "desvio o P90 del LT": bool(_alguno_contiene(campos, _CAMPOS_DESVIO_LT)),
            "el LT medido prevalece sobre el declarado": self.prevalece_lo_medido,
            "diferencia contra el LT cargado en el sistema": bool(
                _alguno_contiene(campos, _CAMPOS_LT_SISTEMA)
            ),
            "lista de SKU cuyo SS/ROP debe recalcularse": bool(
                _alguno_contiene(campos, _CAMPOS_RECALCULO)
            ),
        }

    @property
    def faltantes(self) -> list[str]:
        return [nombre for nombre, ok in self.requisitos.items() if not ok]

    def como_detalle(self) -> dict[str, object]:
        return {
            "entregable": self.entregable,
            "campos": sorted(self.campos),
            "faltantes": self.faltantes,
            "lt_medido_de_las_fechas": self.lt_medido,
            "lt_con_columna_declarada": self.lt_con_columna_declarada,
            "prevalece_lo_medido": self.prevalece_lo_medido,
        }


def _veredicto_lead_time(cand: _CandidatoLeadTime) -> ResultadoTarea:
    """Puntua T08. Eliminatorio: "Reportar solo el LT nominal que dice el proveedor
    NO cuenta" -- si el entregable no sabe medirlo de las fechas, no hay tarea."""
    detalle: dict[str, object] = cand.como_detalle()
    if not cand.mide_de_las_fechas:
        return ResultadoTarea(
            tarea_id="T08",
            estado=Estado.NO_CUMPLE,
            evidencia=(
                "el entregable no mide el lead time de fecha de OC contra fecha de recepcion: "
                f"devuelve {cand.lt_medido}, que es el LT declarado. El criterio dice que asi "
                "no cuenta"
            ),
            detalle=detalle,
        )

    fuga = (
        f"con una columna de LT declarado ({_LT_DECLARADO_SONDA:g} d) el entregable la prefiere "
        f"en silencio: {cand.lt_medido} -> {cand.lt_con_columna_declarada}, y hasta la nota "
        '"lead time measured as delivery_date minus order_date" desaparece'
        if not cand.prevalece_lo_medido else "el LT medido prevalece sobre el declarado"
    )
    return _cerrar_veredicto(
        "T08",
        faltantes=cand.faltantes,
        evidencia_cumple=(
            f"LT medido de las fechas por proveedor ({cand.lt_medido}), con desvio, diferencia "
            f"contra el LT cargado y la lista de SKU a recalcular"
        ),
        evidencia_falta=(
            f"el LT si sale de las fechas ({cand.lt_medido}), pero el entregable sale sin: "
            f"{', '.join(cand.faltantes)}. {fuga}"
        ),
        detalle=detalle,
    )


@comprobacion("T08")
def t08_lead_time_real_por_proveedor(_: Contexto) -> ResultadoTarea:
    """T08 — medir el lead time real y actualizar los parametros de planificacion.

    Se corre el scorecard de proveedores dos veces sobre las MISMAS OC: una con
    solo las fechas (OC y recepcion) y otra agregando una columna de lead time
    "que dice el proveedor". La primera prueba que Kern sabe medirlo; la segunda
    muestra que, cuando el export trae el declarado, lo prefiere sin avisar.

    El resto del criterio no depende del dato: el desvio/P90, la diferencia contra
    el LT cargado y la lista de SKU a recalcular estan o no estan en el entregable.
    Vale notar el corte del cableado: ``jobs.inventory_optimization`` ya sabe
    consumir ``lead_time_stds`` y declarar si sigma_L fue medido o defaulteado
    (ver T04), pero nadie mide ese desvio -- el scorecard solo promedia.
    """
    import tempfile
    from pathlib import Path

    from jobs import sourcing_job as job
    from src.supplier_scorecard import SupplierScore

    def _lt(lineas: tuple[str, ...], carpeta: Path, nombre: str) -> dict[str, float]:
        ruta = carpeta / nombre
        ruta.write_text("\n".join(lineas), encoding="utf-8")
        tarjetas = job.prepare(str(ruta))["scorecards"]
        return {c.supplier: c.avg_lead_time for c in tarjetas}

    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        medido = _lt((_OC_SONDA_HEADER, *_OC_SONDA_FILAS), carpeta, "solo_fechas.csv")
        declarado = _lt(
            (
                f"{_OC_SONDA_HEADER},lead_time_days",
                *(f"{fila},{_LT_DECLARADO_SONDA:g}" for fila in _OC_SONDA_FILAS),
            ),
            carpeta,
            "con_lt_declarado.csv",
        )

    return _veredicto_lead_time(_CandidatoLeadTime(
        entregable="src.supplier_scorecard.SupplierScore",
        campos=tuple(sorted(_campos_del_registro(SupplierScore))),
        lt_medido=medido,
        lt_con_columna_declarada=declarado,
    ))


#: Nombres con los que el desvio de precio (OC vs facturado) podria llegar al entregable.
_CAMPOS_DESVIO_PRECIO = ("price_variance", "desvio_precio", "price_gap", "price_dev", "billed")

#: Nombres con los que los peores del panel podrian quedar marcados para accion.
_CAMPOS_PEORES = ("peor", "worst", "accion", "action", "flag", "watchlist")

#: Identificador de OC que se inyecta para ver si el entregable lo conserva (T09).
_PREFIJO_OC_SONDA = "PO-000"


@dataclass(frozen=True)
class _CandidatoScorecard:
    """El panel de proveedores medido contra el criterio de T09."""

    entregable: str
    campos: tuple[str, ...]
    metricas: dict[str, dict[str, float | None]]
    ranking: tuple[str, ...]
    trazable_a_las_oc: bool
    prueba_trazabilidad: str

    def _medida_en_todos(self, metrica: str) -> bool:
        return bool(self.metricas) and all(
            m[metrica] is not None for m in self.metricas.values()
        )

    @property
    def requisitos(self) -> dict[str, bool]:
        campos = set(self.campos)
        return {
            "OTIF % por proveedor": self._medida_en_todos("otif"),
            "fill rate % por proveedor": self._medida_en_todos("fill_rate"),
            "tasa de rechazo % por proveedor": self._medida_en_todos("rechazo"),
            "desvio de precio % (OC vs facturado)": bool(
                _alguno_contiene(campos, _CAMPOS_DESVIO_PRECIO)
            ),
            "ranking ordenado del panel": len(self.ranking) == len(self.metricas) > 0,
            "los peores marcados para accion": bool(_alguno_contiene(campos, _CAMPOS_PEORES)),
        }

    @property
    def faltantes(self) -> list[str]:
        return [nombre for nombre, ok in self.requisitos.items() if not ok]

    def como_detalle(self) -> dict[str, object]:
        return {
            "entregable": self.entregable,
            "campos": sorted(self.campos),
            "faltantes": self.faltantes,
            "metricas": self.metricas,
            "ranking": list(self.ranking),
            "trazable_a_las_oc": self.trazable_a_las_oc,
            "prueba_trazabilidad": self.prueba_trazabilidad,
        }


def _veredicto_scorecard(cand: _CandidatoScorecard) -> ResultadoTarea:
    """Puntua T09. Eliminatorio: "Debe ser trazable a las OC concretas que generaron
    cada nota" -- una nota que nadie puede auditar no se puede llevar a la reunion
    con el proveedor, por completa que sea la tabla."""
    detalle: dict[str, object] = cand.como_detalle()
    if not cand.trazable_a_las_oc:
        return ResultadoTarea(
            tarea_id="T09",
            estado=Estado.NO_CUMPLE,
            evidencia=(
                f"las notas del panel ({cand.ranking}) no son trazables a las OC que las "
                f"generaron: {cand.prueba_trazabilidad}. El criterio lo exige explicitamente. "
                f"Ademas falta: {', '.join(cand.faltantes)}"
                if cand.faltantes else
                f"las notas del panel ({cand.ranking}) no son trazables a las OC que las "
                f"generaron: {cand.prueba_trazabilidad}"
            ),
            detalle=detalle,
        )

    return _cerrar_veredicto(
        "T09",
        faltantes=cand.faltantes,
        evidencia_cumple=(
            f"panel con OTIF, fill rate, desvio de precio y rechazo por proveedor, ranking "
            f"{list(cand.ranking)} con los peores marcados, trazable a las OC"
        ),
        evidencia_falta=(
            f"el panel es trazable a las OC, pero sale sin: {', '.join(cand.faltantes)}"
        ),
        detalle=detalle,
    )


@comprobacion("T09")
def t09_scorecard_de_proveedores(_: Contexto) -> ResultadoTarea:
    """T09 — scorecard de proveedores y ranking para la revision con el proveedor.

    Se corre el panel sobre OC sinteticas que llevan un identificador de OC
    (``po_number``), y despues se busca ese identificador en el reporte entero:
    si no aparece por ningun lado, la nota no se puede auditar contra las OC que
    la generaron. Es un sondeo de caja negra sobre el entregable, no una lectura
    de campos: si manana el reporte empieza a citar las OC, este check lo ve.

    El desvio de precio del criterio es OC contra facturado; el panel promedia
    UNA sola columna de precio, que no es lo mismo y no puede serlo con un solo
    numero por linea.
    """
    import tempfile
    from pathlib import Path

    from jobs import sourcing_job as job
    from src.mcdm import RankingResult
    from src.supplier_scorecard import SupplierScore

    header = f"po_number,{_OC_SONDA_HEADER}"
    filas = tuple(
        f"{_PREFIJO_OC_SONDA}{i},{fila}" for i, fila in enumerate(_OC_SONDA_FILAS, start=1)
    )
    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "oc_con_id.csv"
        ruta.write_text("\n".join((header, *filas)), encoding="utf-8")
        payload = job.prepare(str(ruta))
        reporte = job.run(payload["scorecards"], payload["prices"], notes=payload["notes"])

    ids = tuple(f"{_PREFIJO_OC_SONDA}{i}" for i in range(1, len(_OC_SONDA_FILAS) + 1))
    entero = repr(reporte)
    citadas = [oc for oc in ids if oc in entero]

    return _veredicto_scorecard(_CandidatoScorecard(
        entregable="jobs.sourcing_job.SourcingReport",
        campos=tuple(sorted(
            _campos_del_registro(SupplierScore)
            | _campos_del_registro(job.SourcingReport)
            | _campos_del_registro(RankingResult)
        )),
        metricas={
            c.supplier: {"otif": c.otif, "fill_rate": c.in_full_rate, "rechazo": c.ppm}
            for c in reporte.scorecards
        },
        ranking=tuple(reporte.ranking.ranking),
        trazable_a_las_oc=bool(citadas),
        prueba_trazabilidad=(
            f"las OC entran con id ({', '.join(ids)}) y ninguno sobrevive al reporte -- "
            "prepare arma el panel sobre columnas derivadas y descarta el identificador"
            if not citadas else f"el reporte cita {', '.join(citadas)}"
        ),
    ))


#: Nombres con los que el servicio REALIZADO podria llegar al entregable.
_CAMPOS_SERVICIO_REALIZADO = ("otif", "fill_rate", "realizado", "achieved", "actual_service")

#: Nombres con los que los dias de quiebre podrian llegar al entregable.
_CAMPOS_DIAS_QUIEBRE = ("stockout_day", "dias_quiebre", "days_out", "oos_days", "quiebre")

#: Nombres con los que la venta perdida podria llegar al entregable.
_CAMPOS_VENTA_PERDIDA = ("lost_sale", "venta_perdida", "perdida", "foregone", "unmet_value")

#: Politica sintetica con la que se muestra que el fill rate sale sin pedidos (T10).
_SS_SONDA = 50.0
_DEMANDA_CICLO_SONDA = 500.0
_SIGMA_CICLO_SONDA = 40.0


@dataclass(frozen=True)
class _CandidatoServicio:
    """Los numeros de servicio que Kern emite, medidos contra el criterio de T10."""

    entregable: str
    campos: tuple[str, ...]
    fill_rate_sin_pedidos: float
    prueba_origen: str

    @property
    def mide_lo_realizado(self) -> bool:
        """Gate: el numero tiene que venir de pedidos reales, no del modelo.

        Se prueba al reves, que es lo unico demostrable de una sola corrida: si el
        entregable puede emitir un fill rate SIN una sola linea de pedido, ese
        numero es el objetivo del modelo. El criterio lo descalifica por nombre.
        """
        return self.fill_rate_sin_pedidos <= 0.0

    @property
    def requisitos(self) -> dict[str, bool]:
        campos = set(self.campos)
        return {
            "OTIF de salida sobre pedidos reales": self.mide_lo_realizado,
            "fill rate de linea sobre pedidos reales": self.mide_lo_realizado
            and bool(_alguno_contiene(campos, _CAMPOS_SERVICIO_REALIZADO)),
            "dias con quiebre por SKU": bool(_alguno_contiene(campos, _CAMPOS_DIAS_QUIEBRE)),
            "venta perdida estimada": bool(_alguno_contiene(campos, _CAMPOS_VENTA_PERDIDA)),
        }

    @property
    def faltantes(self) -> list[str]:
        return [nombre for nombre, ok in self.requisitos.items() if not ok]

    def como_detalle(self) -> dict[str, object]:
        return {
            "entregable": self.entregable,
            "campos": sorted(self.campos),
            "faltantes": self.faltantes,
            "fill_rate_sin_pedidos": self.fill_rate_sin_pedidos,
            "mide_lo_realizado": self.mide_lo_realizado,
            "prueba_origen": self.prueba_origen,
        }


def _veredicto_servicio(cand: _CandidatoServicio) -> ResultadoTarea:
    """Puntua T10. Eliminatorio: "Reportar 'nivel de servicio objetivo' del modelo
    en lugar del realizado NO cuenta"."""
    detalle: dict[str, object] = cand.como_detalle()
    if not cand.mide_lo_realizado:
        return ResultadoTarea(
            tarea_id="T10",
            estado=Estado.NO_CUMPLE,
            evidencia=(
                f"el servicio que Kern reporta es el del modelo, no el realizado: "
                f"{cand.prueba_origen}. El criterio lo descalifica por nombre. Tambien falta: "
                f"{', '.join(cand.faltantes)}"
            ),
            detalle=detalle,
        )

    return _cerrar_veredicto(
        "T10",
        faltantes=cand.faltantes,
        evidencia_cumple=(
            "OTIF y fill rate calculados sobre pedidos reales, con dias de quiebre y venta "
            "perdida por SKU"
        ),
        evidencia_falta=(
            f"el servicio sale de pedidos reales, pero el entregable no trae: "
            f"{', '.join(cand.faltantes)}"
        ),
        detalle=detalle,
    )


@comprobacion("T10")
def t10_servicio_realizado_al_cliente(_: Contexto) -> ResultadoTarea:
    """T10 — medir el servicio REALIZADO: OTIF de salida, fill rate, quiebres, venta perdida.

    El sondeo es una demostracion al reves: se le pide un fill rate al motor sin
    entregarle una sola linea de pedido de cliente. Si sale un numero, ese numero
    no puede ser lo realizado -- es la beta que implica la politica. Y eso es
    exactamente lo que el criterio descalifica.

    Vale registrar el corte de cableado, que es el mismo patron que sigma_L en
    T08: ``jobs.inventory_optimization.run`` acepta ``observed_fill_rates`` para
    realimentar la politica con el servicio medido, pero nadie en ``jobs/`` lo
    calcula. El unico OTIF que Kern computa es de ENTRADA (proveedor -> nosotros,
    ``SupplierScore.otif``, ver T09), no de salida al cliente.
    """
    from jobs.inventory_optimization import SkuRecommendation
    from src.fill_rate import fill_rate_from_safety_stock

    beta = fill_rate_from_safety_stock(_SS_SONDA, _DEMANDA_CICLO_SONDA, _SIGMA_CICLO_SONDA)
    return _veredicto_servicio(_CandidatoServicio(
        entregable="jobs.inventory_optimization.SkuRecommendation + src.fill_rate",
        campos=tuple(sorted(_campos_del_registro(SkuRecommendation))),
        fill_rate_sin_pedidos=beta,
        prueba_origen=(
            f"sin una sola linea de pedido, con SS={_SS_SONDA:g} y sigma={_SIGMA_CICLO_SONDA:g} "
            f"sobre una demanda de ciclo de {_DEMANDA_CICLO_SONDA:g}, "
            f"fill_rate_from_safety_stock ya devuelve {beta:.1%}"
        ),
    ))


#: Nombres con los que el descuento y el margen podrian llegar al entregable (T12).
_CAMPOS_DESCUENTO = ("discount", "descuento", "markdown_pct", "pct_off")
_CAMPOS_MARGEN = ("margin", "margen", "gross", "contribution")


@dataclass(frozen=True)
class _CandidatoMarkdown:
    """El plan de liquidacion/markdown medido contra el criterio de T12."""

    entregable: str
    campos: tuple[str, ...]
    metodos: dict[str, str]
    precios_propuestos: dict[str, float | None]
    prueba_degradacion: str

    @property
    def declara_el_supuesto(self) -> bool:
        """Gate: "Debe declarar el supuesto de respuesta de demanda usado"."""
        return bool(self.metodos) and all(bool(m) for m in self.metodos.values())

    @property
    def requisitos(self) -> dict[str, bool]:
        campos = set(self.campos)
        return {
            "precio propuesto por SKU": bool(self.precios_propuestos) and all(
                p is not None for p in self.precios_propuestos.values()
            ),
            "% de descuento": bool(_alguno_contiene(campos, _CAMPOS_DESCUENTO)),
            "unidades proyectadas": bool(_alguno_contiene(campos, _CAMPOS_CANTIDAD)),
            "margen bruto resultante en dinero": bool(_alguno_contiene(campos, _CAMPOS_MARGEN)),
            "cobertura que se libera": bool(_alguno_contiene(campos, _CAMPOS_COBERTURA)),
        }

    @property
    def faltantes(self) -> list[str]:
        return [nombre for nombre, ok in self.requisitos.items() if not ok]

    def como_detalle(self) -> dict[str, object]:
        return {
            "entregable": self.entregable,
            "campos": sorted(self.campos),
            "faltantes": self.faltantes,
            "metodos_declarados": self.metodos,
            "precios_propuestos": self.precios_propuestos,
            "declara_el_supuesto": self.declara_el_supuesto,
            "prueba_degradacion": self.prueba_degradacion,
        }


def _veredicto_markdown(cand: _CandidatoMarkdown) -> ResultadoTarea:
    """Puntua T12. Eliminatorio: sin el supuesto de respuesta de demanda declarado,
    el precio propuesto no se puede discutir con comercial -- es un numero sin
    origen, que es la version elegante de "sugerir bajar el precio"."""
    detalle: dict[str, object] = cand.como_detalle()
    if not cand.declara_el_supuesto:
        return ResultadoTarea(
            tarea_id="T12",
            estado=Estado.NO_CUMPLE,
            evidencia=(
                f"el entregable no declara con que supuesto de respuesta de demanda armo el "
                f"precio ({cand.metodos}), y el criterio lo exige"
            ),
            detalle=detalle,
        )

    return _cerrar_veredicto(
        "T12",
        faltantes=cand.faltantes,
        evidencia_cumple=(
            f"por SKU: precio propuesto, % de descuento, unidades, margen bruto y cobertura "
            f"liberada, con el supuesto declarado ({sorted(set(cand.metodos.values()))})"
        ),
        evidencia_falta=(
            f"el supuesto si viene declarado por linea ({sorted(set(cand.metodos.values()))}), "
            f"pero el entregable sale sin: {', '.join(cand.faltantes)}. "
            f"{cand.prueba_degradacion}"
        ),
        detalle=detalle,
    )


@comprobacion("T12")
def t12_markdown_con_unidades_y_margen(_: Contexto) -> ResultadoTarea:
    """T12 — decidir markdown sobre el inventario existente y proyectar unidades y margen.

    Se corre el plan de liquidacion dos veces sobre el MISMO stock: con historial
    de precios y sin el. Con historial el metodo es ``elasticity`` y sale un
    precio; sin historial todo cae a ``salvage_heuristic`` y el precio propuesto
    es ``None`` -- el entregable pasa de "vendelo a este precio" a "rematalo al
    30%", y lo dice, que es lo que salva el filo del criterio.

    Lo que no sale de ningun lado es el % de descuento (el entregable no lleva el
    precio actual contra el que compararlo), el margen bruto en dinero
    (``recovered_value`` es recaudacion y ``at_risk_value`` es costo: la resta no
    esta escrita) ni la cobertura que se libera.
    """
    import tempfile
    from pathlib import Path

    import pandas as pd

    from jobs import markdown_liquidation_job as job
    from src.liquidation import LiquidationLine

    stock = pd.DataFrame({
        "product_id": ["SKU-EXC", "SKU-MUERTO"],
        "on_hand": [900.0, 500.0],
        "daily_demand": [1.0, 0.0],
        "unit_cost": [8.0, 12.5],
        "days_since_last_sale": [10, 400],
    })
    historial = "\n".join(
        ["date,product_id,price,quantity"]
        + [f"2026-01-{i + 1:02d},SKU-EXC,{20.0 - i * 0.5},{5 + i * 2}" for i in range(12)]
    )
    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "historial_precios.csv"
        ruta.write_text(historial, encoding="utf-8")
        con_historial = job.run(job.prepare_records(
            stock, {"price_history_path": str(ruta), "price_period": "D"}
        ))
    sin_historial = job.run(job.prepare_records(stock))

    return _veredicto_markdown(_CandidatoMarkdown(
        entregable="src.liquidation.LiquidationLine",
        campos=tuple(sorted(_campos_del_registro(LiquidationLine))),
        metodos={ln.product_id: ln.method for ln in con_historial.lines},
        precios_propuestos={ln.product_id: ln.clearance_price for ln in con_historial.lines},
        prueba_degradacion=(
            "sin historial de precios el plan degrada a "
            f"{sorted({ln.method for ln in sin_historial.lines})} y el precio propuesto queda "
            f"en {[ln.clearance_price for ln in sin_historial.lines]}: no propone precio, "
            "propone remate"
        ),
    ))


@comprobacion("T03")
def t03_segmentacion_con_servicio_diferenciado(ctx: Contexto) -> ResultadoTarea:
    """T03 — ABC-XYZ con nivel de servicio objetivo **distinto** por segmento.

    El criterio tiene un filo propio: "si todos los SKU tienen el mismo objetivo,
    no hubo segmentacion". Y exige ABC por facturacion o margen — un ranking por
    unidades disfrazado de valor no es lo pedido.
    """
    ruta = ctx.dataset(_CSV_VENTAS)
    if ruta is None:
        return ResultadoTarea(
            tarea_id="T03",
            estado=Estado.SIN_DATO,
            evidencia=f"falta {_CSV_VENTAS}: la tarea exige 12 meses de ventas por SKU con costo",
            detalle={"dataset_esperado": _CSV_VENTAS},
        )

    from jobs import abc_xyz_job as job

    reporte = job.run(job.prepare(str(ruta)))
    if problemas := job.verify(reporte):
        return ResultadoTarea(
            tarea_id="T03",
            estado=Estado.NO_CUMPLE,
            evidencia=f"el QA del propio job rechaza el reporte: {'; '.join(problemas)}",
            detalle={"qa_issues": problemas, "dataset": str(ruta)},
        )

    clases = reporte.classifications
    niveles = {c.service_level for c in clases}
    celdas = {c.cell for c in clases}
    # El entregable puede o no declarar su base de valor. Se distinguen los tres
    # casos con evidencia propia -- nunca con un default silencioso, que fue
    # justo el bug que este check tuvo antes (getattr(..., 'is_flat', False)
    # devolvia el default y daba CUMPLE donde correspondia PARCIAL).
    base = getattr(reporte, "value_basis", None)
    declara_base = base is not None
    # ``ValueBasis.discriminates`` es False cuando el costo unitario no varia: ahi
    # el ABC ordena por unidades y se rotula valor anual. Acceso directo al campo:
    # si se renombra, tiene que romper fuerte.
    ranking_por_unidades = declara_base and not base.discriminates

    detalle = {
        "dataset": str(ruta),
        "n_skus": reporte.n_skus,
        "celdas": sorted(celdas),
        "niveles_de_servicio": sorted(niveles),
        "declara_base_de_valor": declara_base,
        "ranking_por_unidades": ranking_por_unidades,
    }

    if len(niveles) <= 1:
        return ResultadoTarea(
            tarea_id="T03",
            estado=Estado.NO_CUMPLE,
            evidencia=(
                f"los {reporte.n_skus} SKU comparten un unico nivel de servicio "
                f"({niveles}): sin objetivo diferenciado no hubo segmentacion"
            ),
            detalle=detalle,
        )

    if ranking_por_unidades:
        return ResultadoTarea(
            tarea_id="T03",
            estado=Estado.PARCIAL,
            evidencia=(
                f"hay {len(celdas)} celdas con {len(niveles)} niveles de servicio, pero el "
                "costo unitario es plano en todo el catalogo: el ABC ordena por UNIDADES "
                "y se rotula valor anual. La tarea pide ABC por facturacion o margen"
            ),
            detalle=detalle,
        )

    if not declara_base:
        return ResultadoTarea(
            tarea_id="T03",
            estado=Estado.PARCIAL,
            evidencia=(
                f"hay {len(celdas)} celdas con {len(niveles)} niveles de servicio, pero el "
                "entregable no declara su base de valor: no hay forma de saber si el ABC "
                "ordeno por facturacion o por unidades con un costo placeholder. La tarea "
                "pide ABC por facturacion o margen, y sin la declaracion no se acredita"
            ),
            detalle=detalle,
        )

    return ResultadoTarea(
        tarea_id="T03",
        estado=Estado.CUMPLE,
        evidencia=(
            f"{reporte.n_skus} SKU en {len(celdas)} celdas ABC-XYZ con {len(niveles)} "
            f"niveles de servicio distintos (min {min(niveles):.0%}, max {max(niveles):.0%}) "
            f"y el ABC ordena por valor, no por unidades"
        ),
        detalle=detalle,
    )


@comprobacion("T11")
def t11_excedente_y_obsoleto(ctx: Contexto) -> ResultadoTarea:
    """T11 — identificar excedente, baja rotacion y obsoleto.

    Criterio: capital inmovilizado **en dinero por categoria**, **meses de
    cobertura por SKU**, y **una accion asignada a cada SKU** de la lista. La
    fuente es explicita en que un ranking de "los que menos venden" sin monto ni
    accion NO cuenta.
    """
    ruta = ctx.dataset(_CSV_STOCK)
    if ruta is None:
        return ResultadoTarea(
            tarea_id="T11",
            estado=Estado.SIN_DATO,
            evidencia=f"falta {_CSV_STOCK}: la tarea exige stock on-hand real por SKU",
            detalle={"dataset_esperado": _CSV_STOCK},
        )

    from jobs import excess_obsolete_job as job

    reporte = job.run(job.prepare(str(ruta)))
    if problemas := job.verify(reporte):
        return ResultadoTarea(
            tarea_id="T11",
            estado=Estado.NO_CUMPLE,
            evidencia=f"el QA del propio job rechaza el reporte: {'; '.join(problemas)}",
            detalle={"qa_issues": problemas, "dataset": str(ruta)},
        )

    lineas = reporte.lines
    en_riesgo = [ln for ln in lineas if ln.classification != "healthy"]

    # Los tres exigidos por el criterio, verificados uno por uno.
    tiene_dinero_por_categoria = reporte.dead_value > 0 or reporte.excess_value > 0
    tiene_cobertura_por_sku = all(ln.days_of_cover is not None for ln in en_riesgo)
    tiene_accion_por_sku = all(
        isinstance(ln.recommended_action, str) and ln.recommended_action.strip()
        for ln in en_riesgo
    )

    faltantes = [
        nombre
        for nombre, ok in (
            ("capital inmovilizado por categoria", tiene_dinero_por_categoria),
            ("cobertura por SKU", tiene_cobertura_por_sku),
            ("accion por SKU", tiene_accion_por_sku),
        )
        if not ok
    ]
    detalle = {
        "dataset": str(ruta),
        "n_skus": reporte.n_skus,
        "n_en_riesgo": len(en_riesgo),
        "dead_value": reporte.dead_value,
        "excess_value": reporte.excess_value,
        "faltantes": faltantes,
    }

    if not en_riesgo:
        # Sin SKU en riesgo no hay lista que juzgar: no se puede acreditar la tarea.
        return ResultadoTarea(
            tarea_id="T11",
            estado=Estado.SIN_DATO,
            evidencia=(
                f"el dataset no produce ningun SKU excedente ni obsoleto sobre "
                f"{reporte.n_skus} SKU: no hay lista contra la que juzgar el criterio"
            ),
            detalle=detalle,
        )

    if faltantes:
        return ResultadoTarea(
            tarea_id="T11",
            estado=Estado.PARCIAL if len(faltantes) < 3 else Estado.NO_CUMPLE,
            evidencia=f"la lista sale sin: {', '.join(faltantes)}",
            detalle=detalle,
        )

    return ResultadoTarea(
        tarea_id="T11",
        estado=Estado.CUMPLE,
        evidencia=(
            f"{len(en_riesgo)} SKU en riesgo sobre {reporte.n_skus}, cada uno con "
            f"cobertura y accion; capital inmovilizado partido en dead "
            f"{reporte.dead_value:,.0f} / excess {reporte.excess_value:,.0f}"
        ),
        detalle=detalle,
    )

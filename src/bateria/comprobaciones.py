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

#: Nombres con los que la cobertura agregada podria llegar al entregable.
_CAMPOS_COBERTURA = ("cover", "cobertura", "meses", "months", "dio_days", "days_of")

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

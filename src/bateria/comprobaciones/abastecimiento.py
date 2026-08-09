"""T05-T07 -- propuesta de OC, ajuste al proveedor y calendario de caja.

Movido desde ``comprobaciones.py`` sin cambio de logica."""

from __future__ import annotations

from dataclasses import dataclass

from src.bateria.comprobaciones._comunes import (
    _CAMPOS_CANTIDAD,
    _CAMPOS_COBERTURA,
    _CAMPOS_COMPARACION,
    _CAMPOS_EN_TRANSITO,
    _CAMPOS_EXCESO,
    _CAMPOS_FECHA,
    _CAMPOS_FECHA_PAGO,
    _CAMPOS_PROVEEDOR,
    _CAMPOS_QUIEBRE,
    _CAMPOS_RAZON,
    _CAMPOS_RECOMENDACION,
    _CAMPOS_SKU,
    _CAMPOS_TOPE,
    _CORTE_SONDA_COSTO,
    _CORTE_SONDA_QTY,
    _MOQ_SONDA,
    _MULTIPLO_SONDA,
    _ON_ORDER_SONDA,
    _TOPE_SONDA,
    _alguno_contiene,
    _campos_del_registro,
    _campos_planos,
    _cerrar_veredicto,
)
from src.bateria.scorer import Contexto, Estado, ResultadoTarea, comprobacion


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
    rotulados = _alguno_contiene(campos_linea, _CAMPOS_EXCESO)
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
            f"el redondeo compra {exceso_u:g} u de mas ({exceso_usd:,.0f} usd) y el entregable lo "
            f"rotula en {', '.join(rotulados)}"
            if rotulados else
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

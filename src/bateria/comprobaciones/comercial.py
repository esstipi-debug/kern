"""T12-T14, T20 -- markdown, exposicion, devoluciones y acta de S&OP.

Movido desde ``comprobaciones.py`` sin cambio de logica."""

from __future__ import annotations

from dataclasses import dataclass

from src.bateria.comprobaciones._comunes import (
    _CAMPOS_ACCION,
    _CAMPOS_ANCLA,
    _CAMPOS_BRECHAS,
    _CAMPOS_CANTIDAD,
    _CAMPOS_COBERTURA,
    _CAMPOS_DEMANDA_ACORDADA,
    _CAMPOS_DESCUENTO,
    _CAMPOS_FECHA_ACCION,
    _CAMPOS_MARGEN,
    _CAMPOS_RECLAMO,
    _CAMPOS_RESPONSABLE,
    _CAMPOS_SUMINISTRO,
    _MOTIVO_DESPACHO,
    _MOTIVO_FALLA,
    _alguno_contiene,
    _campos_del_registro,
    _campos_planos,
    _cerrar_veredicto,
)
from src.bateria.scorer import Contexto, Estado, ResultadoTarea, comprobacion


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


@dataclass(frozen=True)
class _CandidatoExposicion:
    """El scan de disrupcion + el registro de riesgo, medidos contra T13."""

    entregable: str
    campos: tuple[str, ...]
    ranking_por_puntaje: bool
    prueba_ranking: str

    @property
    def requisitos(self) -> dict[str, bool]:
        campos = set(self.campos)
        return {
            "ranking por exposicion con criterio de puntaje explicito": self.ranking_por_puntaje,
            "anclado a un proveedor o SKU concreto": bool(_alguno_contiene(campos, _CAMPOS_ANCLA)),
            "accion concreta para el top N": bool(_alguno_contiene(campos, _CAMPOS_ACCION)),
            "accion fechada": bool(_alguno_contiene(campos, _CAMPOS_FECHA_ACCION)),
        }

    @property
    def anclado_y_accionable(self) -> bool:
        """Gate: "Una lista de riesgos genericos sin SKU ni accion NO cuenta"."""
        req = self.requisitos
        return req["anclado a un proveedor o SKU concreto"] and req["accion concreta para el top N"]

    @property
    def faltantes(self) -> list[str]:
        return [nombre for nombre, ok in self.requisitos.items() if not ok]

    def como_detalle(self) -> dict[str, object]:
        return {
            "entregable": self.entregable,
            "campos": sorted(self.campos),
            "faltantes": self.faltantes,
            "anclado_y_accionable": self.anclado_y_accionable,
            "prueba_ranking": self.prueba_ranking,
        }


def _veredicto_exposicion(cand: _CandidatoExposicion) -> ResultadoTarea:
    """Puntua T13."""
    detalle: dict[str, object] = cand.como_detalle()
    if not cand.anclado_y_accionable:
        return ResultadoTarea(
            tarea_id="T13",
            estado=Estado.NO_CUMPLE,
            evidencia=(
                "lo que sale es una lista de riesgos sin proveedor/SKU concreto o sin accion "
                f"asociada, y el criterio dice que asi no cuenta. Falta: "
                f"{', '.join(cand.faltantes)}"
            ),
            detalle=detalle,
        )

    return _cerrar_veredicto(
        "T13",
        faltantes=cand.faltantes,
        evidencia_cumple=(
            f"ranking por exposicion con puntaje explicito ({cand.prueba_ranking}), anclado a "
            "proveedor y con accion fechada para el top N"
        ),
        evidencia_falta=(
            f"el ranking sale por exposicion con puntaje explicito ({cand.prueba_ranking}) y "
            f"anclado a proveedor, con mitigaciones rankeadas por beneficio neto, pero sale sin: "
            f"{', '.join(cand.faltantes)} -- ninguna mitigacion lleva cuando"
        ),
        detalle=detalle,
    )


@comprobacion("T13")
def t13_exposicion_a_disrupcion(_: Contexto) -> ResultadoTarea:
    """T13 — exposicion a disrupcion de abastecimiento y mitigacion.

    Se corre el registro de riesgo sobre dos hazards con impacto x probabilidad
    distintos para verificar que el ranking sale de un puntaje explicito y no de
    un orden de entrada. El ancla y la accion se miden sobre los campos del
    entregable: ``SupplierDisruption`` rankea PROVEEDORES (no riesgos genericos) y
    ``RiskAssessment`` trae mitigaciones rankeadas por beneficio neto.

    Lo que no existe en ninguna capa es el "cuando": ni ``MitigationOption``, ni
    ``RankedMitigation``, ni ``ExecutionOption`` tienen fecha. La accion queda
    recomendada pero sin comprometer, que es la mitad de lo que pide el criterio.
    """
    from nosilo_core.guided import ExecutionOption

    from src.disruption import SupplierDisruption
    from src.risk import MitigationOption, RankedMitigation, RiskAssessment, RiskFactor
    from src.risk import assess_portfolio as _assess

    chico = RiskFactor(
        name="corte de ruta", category="logistics", likelihood=0.10, impact_value=50_000.0,
        mitigations=(MitigationOption("segundo transportista", "redundancy", 5_000.0, 0.0, 0.4),),
    )
    grande = RiskFactor(
        name="quiebre del proveedor unico", category="supplier", likelihood=0.30,
        impact_value=400_000.0,
        mitigations=(MitigationOption("calificar segundo proveedor", "redundancy", 20_000.0, 0.5),),
    )
    reporte = _assess([chico, grande])
    orden = [a.name for a in reporte.assessments]
    emvs = [round(a.emv, 1) for a in reporte.assessments]

    campos = (
        _campos_del_registro(SupplierDisruption)
        | _campos_del_registro(RiskAssessment)
        | _campos_del_registro(RankedMitigation)
        | _campos_del_registro(ExecutionOption)
    )
    return _veredicto_exposicion(_CandidatoExposicion(
        entregable="src.disruption.SupplierDisruption + src.risk.RiskAssessment",
        campos=tuple(sorted(campos)),
        ranking_por_puntaje=emvs == sorted(emvs, reverse=True) and len(set(emvs)) > 1,
        prueba_ranking=f"EMV desc {dict(zip(orden, emvs, strict=True))}, con occurrence/severity/rpn por riesgo",
    ))


@dataclass(frozen=True)
class _CandidatoDevoluciones:
    """El plan de devoluciones medido contra el criterio de T20."""

    entregable: str
    campos: tuple[str, ...]
    disposicion_por_motivo: dict[str, str]
    tasa_sobre_ventas: bool
    prueba_motivos: str

    @property
    def distingue_motivos(self) -> bool:
        """Gate: falla de producto y error de despacho no pueden terminar igual."""
        return len(set(self.disposicion_por_motivo.values())) > 1

    @property
    def requisitos(self) -> dict[str, bool]:
        campos = set(self.campos)
        return {
            "tasa de devolucion (%) por motivo": self.tasa_sobre_ventas,
            "destino de cada lote devuelto": bool(_alguno_contiene(campos, ("action", "dispos"))),
            "monto reclamable al proveedor": bool(_alguno_contiene(campos, _CAMPOS_RECLAMO)),
            "distingue falla de producto de error de despacho": self.distingue_motivos,
        }

    @property
    def faltantes(self) -> list[str]:
        return [nombre for nombre, ok in self.requisitos.items() if not ok]

    def como_detalle(self) -> dict[str, object]:
        return {
            "entregable": self.entregable,
            "campos": sorted(self.campos),
            "faltantes": self.faltantes,
            "disposicion_por_motivo": self.disposicion_por_motivo,
            "distingue_motivos": self.distingue_motivos,
            "prueba_motivos": self.prueba_motivos,
        }


def _veredicto_devoluciones(cand: _CandidatoDevoluciones) -> ResultadoTarea:
    """Puntua T20. Eliminatorio: "Debe distinguir devolucion por falla de producto
    de devolucion por error de despacho: la accion correctiva es distinta"."""
    detalle: dict[str, object] = cand.como_detalle()
    if not cand.distingue_motivos:
        return ResultadoTarea(
            tarea_id="T20",
            estado=Estado.NO_CUMPLE,
            evidencia=(
                f"el motivo no toca la decision: {cand.prueba_motivos}. El criterio lo marca como "
                f"eliminatorio porque la accion correctiva es distinta. Tambien falta: "
                f"{', '.join(n for n in cand.faltantes if 'distingue' not in n)}"
            ),
            detalle=detalle,
        )

    return _cerrar_veredicto(
        "T20",
        faltantes=cand.faltantes,
        evidencia_cumple=(
            "tasa de devolucion por motivo, destino de cada lote y monto reclamable al proveedor, "
            "con la falla de producto separada del error de despacho"
        ),
        evidencia_falta=(
            f"el motivo si cambia la disposicion ({cand.prueba_motivos}), pero el entregable sale "
            f"sin: {', '.join(cand.faltantes)}"
        ),
        detalle=detalle,
    )


@comprobacion("T20")
def t20_flujo_de_devoluciones(_: Contexto) -> ResultadoTarea:
    """T20 — clasificar devoluciones por motivo, decidir disposicion y reclamar al proveedor.

    El sondeo pone dos lotes IDENTICOS salvo el motivo: uno volvio por falla de
    producto y el otro por error de despacho. Si la disposicion recomendada es la
    misma, el motivo es decorativo -- y ahi el criterio corta, porque la accion
    correctiva de una falla (reclamar al proveedor, bloquear el lote) no se parece
    a la de un error de despacho (corregir el picking y reingresar).

    Lo otro que no esta: la tasa de devolucion. El job recibe SOLO devoluciones,
    sin ventas del periodo, asi que ``reason_pareto`` es participacion sobre las
    devoluciones, no una tasa. Y ninguna linea tiene proveedor: no hay a quien
    reclamarle.
    """
    import pandas as pd

    from jobs import returns_job as job
    from src.reverse_logistics import Disposition, ReturnLine

    devoluciones = pd.DataFrame({
        "product_id": ["SKU-A", "SKU-B"],
        "returned_units": [10.0, 10.0],
        "reason": [_MOTIVO_FALLA, _MOTIVO_DESPACHO],
        "unit_cost": [20.0, 20.0],
        "resale_value": [30.0, 30.0],
    })
    # Las ventas del periodo son el denominador que la tasa necesita. Se pasan a
    # proposito distintas de las devoluciones (500 vendidas contra 20 devueltas): si el
    # entregable devolviera la participacion sobre las devoluciones en vez de la tasa
    # sobre ventas, daria 50/50 y no 2%.
    ventas = {"SKU-A": 500.0, "SKU-B": 500.0}
    reporte = job.run(job.prepare_records(devoluciones), units_sold=ventas)
    tasa = reporte.return_rate_by_reason or {}
    por_motivo = {d.line.reason: d.best.action for d in reporte.dispositions}
    recuperos = {d.line.reason: round(d.best.net_recovery_per_unit, 2) for d in reporte.dispositions}

    return _veredicto_devoluciones(_CandidatoDevoluciones(
        entregable="jobs.returns_job.ReturnsReport",
        campos=tuple(sorted(
            _campos_del_registro(ReturnLine)
            | _campos_del_registro(Disposition)
            | _campos_del_registro(job.ReturnsReport)
        )),
        disposicion_por_motivo=por_motivo,
        # No alcanza con que el campo exista: tiene que ser una tasa SOBRE VENTAS. Con
        # 10 u devueltas por motivo contra 1000 vendidas, la tasa es 1% por motivo; la
        # participacion sobre las devoluciones daria 50%. Se exige el 1%.
        tasa_sobre_ventas=bool(tasa) and all(
            abs(v - 1.0) < 1e-9 for v in tasa.values()
        ),
        prueba_motivos=(
            f"dos lotes identicos, uno por '{_MOTIVO_FALLA}' y otro por '{_MOTIVO_DESPACHO}', "
            f"reciben la misma disposicion {por_motivo} con el mismo recupero {recuperos} -- "
            "la unidad fallada vuelve a stock a precio pleno"
            if len(set(por_motivo.values())) == 1
            else f"la disposicion cambia segun el motivo: {por_motivo}"
        ),
    ))


@dataclass(frozen=True)
class _CandidatoActa:
    """Un entregable de S&OP medido contra el criterio de T14."""

    tool: str
    entregable: str
    campos: tuple[str, ...]
    registra_desacuerdo: bool
    prueba_desacuerdo: str

    @property
    def requisitos(self) -> dict[str, bool]:
        campos = set(self.campos)
        return {
            "numero de demanda acordado": bool(
                _alguno_contiene(campos, _CAMPOS_DEMANDA_ACORDADA)
            ),
            "plan de suministro que lo soporta": bool(_alguno_contiene(campos, _CAMPOS_SUMINISTRO)),
            "brechas sin resolver declaradas": bool(_alguno_contiene(campos, _CAMPOS_BRECHAS)),
            "una decision con nombre y fecha": bool(
                _alguno_contiene(campos, _CAMPOS_RESPONSABLE)
            ) and bool(_alguno_contiene(campos, _CAMPOS_FECHA_ACCION)),
        }

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
            "campos": sorted(self.campos),
            "faltantes": self.faltantes,
            "registra_desacuerdo": self.registra_desacuerdo,
            "prueba_desacuerdo": self.prueba_desacuerdo,
        }


def _veredicto_acta(candidatos: tuple[_CandidatoActa, ...]) -> ResultadoTarea:
    """Puntua T14. Eliminatorio: "Debe registrar el desacuerdo ... en vez de
    esconderlo" -- un acta que solo muestra el numero final no es un acta."""
    mejor = max(candidatos, key=lambda c: (c.registra_desacuerdo, c.cumplidos))
    detalle: dict[str, object] = {
        "mejor": mejor.tool,
        "candidatos": [c.como_detalle() for c in candidatos],
    }
    if not mejor.registra_desacuerdo:
        return ResultadoTarea(
            tarea_id="T14",
            estado=Estado.NO_CUMPLE,
            evidencia=(
                "ningun entregable registra el desacuerdo entre areas; sale el numero final y "
                "nada de lo que se discutio para llegar ahi. "
                + "; ".join(f"{c.tool}: {c.prueba_desacuerdo}" for c in candidatos)
            ),
            detalle=detalle,
        )

    return _cerrar_veredicto(
        "T14",
        faltantes=mejor.faltantes,
        evidencia_cumple=(
            f"{mejor.tool} lleva demanda acordada, suministro que la soporta, brechas declaradas "
            f"y una decision con nombre y fecha, con el desacuerdo registrado "
            f"({mejor.prueba_desacuerdo})"
        ),
        evidencia_falta=(
            f"{mejor.tool} registra el desacuerdo ({mejor.prueba_desacuerdo}) pero el acta sale "
            f"sin: {', '.join(mejor.faltantes)}"
        ),
        detalle=detalle,
    )


@comprobacion("T14")
def t14_acta_de_sop(_: Contexto) -> ResultadoTarea:
    """T14 — la reunion mensual de S&OP: un solo plan y el desacuerdo registrado.

    La tarea esta partida en dos entregables que no se hablan. El plan integrado
    trae demanda, suministro y las brechas (los coherence checks), pero es un
    documento sin conflicto: nadie pidio nada que no se aprobara. El plan
    reconciliado si trae el conflicto -- el acta de concesiones dice cuanto cede
    cada sombrero respecto de su ideal -- pero no lleva el numero de demanda
    acordado ni las brechas.

    Y lo que falta en los dos es el cierre: ninguna capa tiene responsable ni
    fecha, asi que no hay "una decision con nombre y fecha" en ningun lado.
    """
    from jobs.hats_job import SettlementReport, run_settlement
    from src.eoq import PriceBreak
    from src.hat_council import ActaEntry, Settlement
    from src.hats import HatConfig, build_inputs
    from src.sop_engine.demand_plan import DemandPlanLine
    from src.sop_engine.engine import IntegratedPlanReport
    from src.sop_engine.purchase_plan import PurchasePlanLine

    entrada = build_inputs(
        sku="A", annual_demand=5200.0, mean_weekly=100.0, std_weekly=20.0,
        lead_time_weeks=2.0, unit_cost=10.0, config=HatConfig(),
        price_breaks=(PriceBreak(0.0, 10.0),),
    )
    from src.hats import DEFAULT_WEIGHTS

    acuerdo = run_settlement(
        {"inputs": (entrada,), "weights": dict(DEFAULT_WEIGHTS)}
    ).settlements[0]
    concesiones = {e.hat_key: round(e.concesion, 3) for e in acuerdo.acta}

    plan = _CandidatoActa(
        tool="integrated_plan",
        entregable="src.sop_engine.engine.IntegratedPlanReport",
        campos=tuple(sorted(
            _campos_del_registro(IntegratedPlanReport)
            | _campos_del_registro(DemandPlanLine)
            | _campos_del_registro(PurchasePlanLine)
        )),
        registra_desacuerdo=False,
        prueba_desacuerdo=(
            "el plan integrado no tiene donde anotar que pidio cada area: sus checks son "
            "incoherencias tecnicas del plan, no posiciones de ventas, compras o finanzas"
        ),
    )
    settlement = _CandidatoActa(
        tool="hat_settlement",
        entregable="src.hat_council.Settlement",
        campos=tuple(sorted(
            _campos_planos(acuerdo)
            | _campos_del_registro(ActaEntry)
            | _campos_del_registro(Settlement)
            | _campos_del_registro(SettlementReport)
        )),
        registra_desacuerdo=any(c > 0 for c in concesiones.values()),
        prueba_desacuerdo=(
            f"el acta anota cuanto cede cada sombrero respecto de su ideal: {concesiones}"
        ),
    )
    return _veredicto_acta((plan, settlement))

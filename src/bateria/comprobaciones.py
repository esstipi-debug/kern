"""Las comprobaciones: una por tarea de software de la bateria.

Cada una corre la capa ``jobs/`` sobre un dataset real y verifica el
``criterio_de_aprobado`` **campo por campo**, no "a ojo". La evidencia que
devuelve tiene que alcanzar para defender el veredicto sin volver a correr nada.

Importar este modulo registra las comprobaciones en el scorer.
"""

from __future__ import annotations

from src.bateria.scorer import Contexto, Estado, ResultadoTarea, comprobacion

#: Dataset de stock con el que se puntua T11. Sin el, la tarea es SIN_DATO.
_CSV_STOCK = "stock_on_hand.csv"


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

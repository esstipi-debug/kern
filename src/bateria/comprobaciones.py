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
    # ``ValueBasis.discriminates`` es False cuando el costo unitario no varia: ahi
    # el ABC ordena por unidades y se rotula valor anual. Se accede directo, sin
    # getattr con default: si el campo se renombra queremos que rompa fuerte, no
    # que el check devuelva un veredicto silenciosamente equivocado.
    ranking_por_unidades = not reporte.value_basis.discriminates

    detalle = {
        "dataset": str(ruta),
        "n_skus": reporte.n_skus,
        "celdas": sorted(celdas),
        "niveles_de_servicio": sorted(niveles),
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

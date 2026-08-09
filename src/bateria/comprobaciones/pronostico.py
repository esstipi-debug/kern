"""T01-T02 -- pronostico: banda de incertidumbre y sesgo firmado.

Movido desde ``comprobaciones.py`` sin cambio de logica."""

from __future__ import annotations

from src.bateria.comprobaciones._comunes import (
    _CAMPOS_DISPERSION,
    _CAMPOS_SESGO,
    _alguno_contiene,
    _campos_del_registro,
)
from src.bateria.scorer import Contexto, Estado, ResultadoTarea, comprobacion


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
            f"el entregable sale sin: {', '.join(faltantes)}"
            + (
                ". El sesgo firmado ya viaja por SKU; lo que falta es el ciclo anterior "
                "congelado: sin el no hay contra que comparar"
                if tiene_mape and sesgo else
                ". src.forecasting.ForecastResult calcula bias y el job lo descarta"
            )
        ),
        detalle=detalle,
    )

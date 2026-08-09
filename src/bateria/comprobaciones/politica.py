"""T04, T03, T11 -- politica de inventario, segmentacion y excedente.

Movido desde ``comprobaciones.py`` sin cambio de logica."""

from __future__ import annotations

from src.bateria.comprobaciones._comunes import (
    _CSV_STOCK,
    _CSV_VENTAS,
    _SIGMA_LT_SONDA,
    _campos_del_registro,
)
from src.bateria.scorer import Contexto, Estado, ResultadoTarea, comprobacion


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

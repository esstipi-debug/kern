"""Constantes de vocabulario y helpers compartidos por las comprobaciones.

Extraido de ``comprobaciones.py`` al partirlo en partes (movimiento puro de
codigo, sin cambio de logica). Las constantes viven todas aca porque varias
las comparten mas de una comprobacion, y repartirlas por archivo dejaria
referencias sueltas.
"""

from __future__ import annotations

from src.bateria.scorer import Estado, ResultadoTarea

#: Dataset de stock con el que se puntua T11. Sin el, la tarea es SIN_DATO.
_CSV_STOCK = "stock_on_hand.csv"

#: Dataset de historial de ventas por SKU (T03).
_CSV_VENTAS = "sales_history.csv"

#: Nombres con los que una banda de incertidumbre podria llegar al entregable.
_CAMPOS_DISPERSION = ("sigma", "std", "desvio", "p10", "p90", "percentil", "intervalo", "banda")

#: Nombres con los que el sesgo firmado podria llegar al entregable.
_CAMPOS_SESGO = ("bias", "sesgo")

#: sigma_L con el que se sondea la reaccion del safety stock en T04.
_SIGMA_LT_SONDA = 4.0

_CAMPOS_PROVEEDOR = ("supplier", "vendor", "proveedor")

_CAMPOS_SKU = ("sku", "part_id", "product_id", "item", "codigo")

_CAMPOS_CANTIDAD = ("qty", "quantity", "cantidad", "restock", "units")

_CAMPOS_FECHA = ("date", "fecha", "eta", "arriv", "llegada")

_CAMPOS_RAZON = ("reason", "razon", "motivo", "zone", "trigger", "priority", "classification")

#: Nombres con los que las ordenes ya emitidas podrian entrar al entregable.
_CAMPOS_EN_TRANSITO = ("transit", "transito", "on_order", "onorder", "incoming", "open_po")

#: Ordenes ya emitidas con las que se sondea el neteo en T05.
_ON_ORDER_SONDA = 80.0

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

#: Nombres con los que una fecha de pago o una semana de egreso podria llegar al entregable.
_CAMPOS_FECHA_PAGO = ("payment", "pago", "due", "vencimiento", "week", "semana", "calendar",
                      "calendario", "date", "fecha")

#: Nombres con los que el contraste contra el tope de caja podria llegar al entregable.
_CAMPOS_TOPE = ("budget", "presupuesto", "tope", "shortfall", "deficit", "investment")

#: Nombres con los que el impacto en quiebre del recorte podria llegar al entregable.
_CAMPOS_QUIEBRE = ("stockout", "quiebre", "at_risk", "en_riesgo", "lost_sale", "fill_rate")

#: Tope de caja con el que se sondea el recorte en T07 (por debajo de lo pedido).
_TOPE_SONDA = 1000.0

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

#: Nombres con los que el desvio de precio (OC vs facturado) podria llegar al entregable.
_CAMPOS_DESVIO_PRECIO = ("price_variance", "desvio_precio", "price_gap", "price_dev", "billed")

#: Nombres con los que los peores del panel podrian quedar marcados para accion.
_CAMPOS_PEORES = ("peor", "worst", "accion", "action", "flag", "watchlist")

#: Identificador de OC que se inyecta para ver si el entregable lo conserva (T09).
_PREFIJO_OC_SONDA = "PO-000"

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

_CAMPOS_DESCUENTO = ("discount", "descuento", "markdown_pct", "pct_off")

_CAMPOS_MARGEN = ("margin", "margen", "gross", "contribution")

#: Nombres con los que el ancla (proveedor/SKU) y la accion podrian llegar (T13).
_CAMPOS_ANCLA = ("supplier", "proveedor", "sku", "product_id", "vendor")

_CAMPOS_ACCION = ("mitigation", "mitigacion", "accion", "action", "recommended", "plan")

_CAMPOS_FECHA_ACCION = ("due", "deadline", "fecha", "date", "sla", "by_when", "vence")

#: Motivos con los que se sondea si la disposicion cambia segun por que volvio (T20).
_MOTIVO_FALLA = "falla de producto"

_MOTIVO_DESPACHO = "error de despacho"

#: Nombres con los que el reclamo al proveedor podria llegar al entregable.
_CAMPOS_RECLAMO = ("claim", "reclam", "chargeback", "nota_credito", "credit_note", "supplier")

#: Nombres con los que cada pieza del acta de S&OP podria llegar al entregable.
_CAMPOS_DEMANDA_ACORDADA = ("shaped_demand", "demanda", "demand_plan", "forecast")

_CAMPOS_SUMINISTRO = ("purchase_plan", "recommended_order", "supply", "order_quantity", "chosen")

_CAMPOS_BRECHAS = ("check", "brecha", "gap", "unresolved", "unmeasured", "residual")

_CAMPOS_RESPONSABLE = ("owner", "responsable", "asignado", "assignee")

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

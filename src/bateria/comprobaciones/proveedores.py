"""T08-T10 -- lead time real, scorecard y servicio realizado.

Movido desde ``comprobaciones.py`` sin cambio de logica."""

from __future__ import annotations

from dataclasses import dataclass

from src.bateria.comprobaciones._comunes import (
    _CAMPOS_DESVIO_LT,
    _CAMPOS_DESVIO_PRECIO,
    _CAMPOS_DIAS_QUIEBRE,
    _CAMPOS_LT_SISTEMA,
    _CAMPOS_PEORES,
    _CAMPOS_RECALCULO,
    _CAMPOS_SERVICIO_REALIZADO,
    _CAMPOS_VENTA_PERDIDA,
    _DEMANDA_CICLO_SONDA,
    _LT_DECLARADO_SONDA,
    _OC_SONDA_FILAS,
    _OC_SONDA_HEADER,
    _PREFIJO_OC_SONDA,
    _SIGMA_CICLO_SONDA,
    _SS_SONDA,
    _alguno_contiene,
    _campos_del_registro,
    _cerrar_veredicto,
)
from src.bateria.scorer import Contexto, Estado, ResultadoTarea, comprobacion


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

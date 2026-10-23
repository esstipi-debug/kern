"""El comparador de transportistas, enchufado al contrato Tool del core.

Igual que source_to_pay: un `register()` lo pone en el mismo ciclo
prepare -> run -> qa -> deliver, con el mismo veto "QA falla => no hay entregable".
No escribe en ningun sistema: entrega opciones para que una persona decida.
"""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from nosilo_core.guided import ExecutionOption, GuidedOutcome, HandoffPacket, Residual, as_handoff, as_options
from nosilo_core.llm import LLMProvider
from nosilo_core.registry import Prepared, Produced, Tool, ToolRegistry
from nosilo_core.types import STATUS_NEEDS_CLARIFICATION, STATUS_NEEDS_DATA, STATUS_OK, JobRequest

from .engine import (
    MIN_DIAS_ANUALIZAR,
    MIN_ENVIOS_DEFAULT,
    MOTIVO_POCOS_ENVIOS,
    MOTIVO_SIN_FECHAS,
    RUTA_ACTUAL_ES_EL_MEJOR,
    RUTA_ACTUAL_NO_ELEGIBLE,
    RUTA_MENOS_DE_DOS,
    RUTA_SIN_ALTERNATIVA,
    Celda,
    Comparacion,
    Pesos,
    Recomendacion,
    comparar,
)
from .loader import OPTIONAL_COLUMNS, REQUIRED_COLUMNS, LoadError, load_envios

TOOL_KEY = "carrier_lane_compare"

MOTIVOS_TEXTO = {
    MOTIVO_POCOS_ENVIOS: "menos envios fechados que el minimo",
    MOTIVO_SIN_FECHAS: "sin fecha prometida y de entrega",
    RUTA_SIN_ALTERNATIVA: "sin alternativa: un solo transportista",
    RUTA_ACTUAL_NO_ELEGIBLE: "el transportista actual no tiene muestra suficiente",
    RUTA_MENOS_DE_DOS: "menos de dos transportistas con muestra suficiente",
    RUTA_ACTUAL_ES_EL_MEJOR: "el transportista actual ya tiene el mejor puntaje (o empata con el mejor)",
}


class _ParamError(ValueError):
    pass


def _pesos(params: dict) -> Pesos:
    try:
        costo = Decimal(str(params.get("peso_costo", Pesos().costo)))
        servicio = Decimal(str(params.get("peso_servicio", Pesos().servicio)))
    except InvalidOperation:
        raise _ParamError("peso_costo y peso_servicio deben ser numeros (por ejemplo 0.6 y 0.4)") from None
    if not (costo.is_finite() and servicio.is_finite()):
        raise _ParamError("peso_costo y peso_servicio deben ser numeros finitos")
    try:
        return Pesos(costo, servicio)
    except ValueError as exc:
        raise _ParamError(f"{exc}: indica peso_costo y peso_servicio juntos") from None


def _min_envios(params: dict) -> int:
    raw = params.get("min_envios", MIN_ENVIOS_DEFAULT)
    try:
        value = int(str(raw))
    except ValueError:
        raise _ParamError(f"min_envios debe ser un entero (es {raw!r})") from None
    if value < 1:
        raise _ParamError(f"min_envios debe ser >= 1 (es {value})")
    return value


def prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(
            status=STATUS_NEEDS_DATA,
            messages=["carrier_lane_compare necesita un CSV de envios con columnas: "
                      + ", ".join(REQUIRED_COLUMNS) + " (opcionales: " + ", ".join(OPTIONAL_COLUMNS) + ")"],
        )
    try:
        envios = load_envios(request.data_path)
    except LoadError as exc:
        return Prepared(status=STATUS_NEEDS_DATA, messages=[str(exc)])
    params = request.params or {}
    try:
        pesos = _pesos(params)
        min_envios = _min_envios(params)
    except _ParamError as exc:
        return Prepared(status=STATUS_NEEDS_CLARIFICATION, messages=[str(exc)])
    return Prepared(status=STATUS_OK, payload={"envios": envios, "pesos": pesos, "min_envios": min_envios})


def run(prepared: object, params: dict) -> Produced:
    payload: dict = prepared  # type: ignore[assignment]
    comp = comparar(payload["envios"], payload["pesos"], payload["min_envios"])
    return Produced(report=comp, summary=_summary(comp))


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('1')):,}"


def _ahorro_anual_texto(comp: Comparacion) -> str:
    """El total anualizado, o por que no existe: un total que no se midio no se muestra como 0."""
    n = len(comp.recomendaciones)
    if n == 0:
        return "sin recomendaciones"
    total = comp.ahorro_anual_total
    if total is None:
        return f"sin anualizar ({n} recomendacion(es) con menos de {MIN_DIAS_ANUALIZAR} dias de historia)"
    texto = f"{_money(total)} {comp.moneda}"
    anualizadas = comp.recomendaciones_anualizadas
    if anualizadas < n:
        texto += (f" ({anualizadas} de {n} recomendaciones; las otras tienen menos de "
                  f"{MIN_DIAS_ANUALIZAR} dias de historia)")
    return texto


def _summary(comp: Comparacion) -> str:
    return (
        f"{len(comp.rutas)} ruta(s), {comp.rutas_comparables} comparable(s), "
        f"{len(comp.recomendaciones)} recomendacion(es); ahorro anualizado: {_ahorro_anual_texto(comp)}"
    )


def qa(report: object) -> list[str]:
    comp: Comparacion = report  # type: ignore[assignment]
    issues: list[str] = []
    if comp.rutas_comparables == 0:
        issues.append(
            f"ninguna ruta tiene dos transportistas con al menos {comp.min_envios} envios fechados: "
            "es un problema de dato (faltan fechas o historia), no una ausencia de oportunidades"
        )
    for ruta in comp.rutas:
        for c in ruta.celdas:
            if c.puntaje is not None and not Decimal(0) <= c.puntaje <= Decimal(1):
                issues.append(f"{ruta.ruta} / {c.transportista}: puntaje {c.puntaje} fuera de [0, 1]")
            if c.elegible != (c.puntaje is not None):
                issues.append(f"{ruta.ruta} / {c.transportista}: elegibilidad y puntaje no coinciden")
    kg_celdas = sum((c.kg_total for r in comp.rutas for c in r.celdas), Decimal(0))
    if kg_celdas != comp.kg_total:
        issues.append(f"los kg por ruta ({kg_celdas}) no suman los kg del archivo ({comp.kg_total})")
    return issues


def _fmt(value: Decimal | None, places: int) -> str:
    if value is None:
        return ""
    return str(value.quantize(Decimal(1).scaleb(-places)))


MATRIZ_COLUMNAS = (
    "ruta", "transportista", "es_actual", "envios", "kg_total", "costo_total", "costo_kg",
    "envios_costo_cero", "primer_despacho", "ultimo_despacho",
    "envios_fechados", "otd", "elegible", "motivo", "indice_costo", "indice_servicio",
    "peso_costo", "peso_servicio", "puntaje",
)


def _fila(ruta_actual: str, c: Celda, comp: Comparacion) -> list[str]:
    return [
        c.ruta, c.transportista, "si" if c.transportista == ruta_actual else "no", str(c.envios),
        _fmt(c.kg_total, 2), _fmt(c.costo_total, 2), _fmt(c.costo_kg, 4),
        str(c.envios_costo_cero), c.primer_despacho.isoformat(), c.ultimo_despacho.isoformat(),
        str(c.envios_fechados), _fmt(c.otd, 4), "si" if c.elegible else "no", c.motivo or "",
        _fmt(c.indice_costo, 4), _fmt(c.indice_servicio, 4),
        str(comp.pesos.costo), str(comp.pesos.servicio), _fmt(c.puntaje, 4),
    ]


def deliver(report: object, out_dir: Path, client: str) -> dict[str, Path]:
    comp: Comparacion = report  # type: ignore[assignment]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    matriz = out_dir / "fletes_matriz.csv"
    with matriz.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(MATRIZ_COLUMNAS)
        for ruta in comp.rutas:
            for c in ruta.celdas:
                writer.writerow(_fila(ruta.actual, c, comp))

    informe = out_dir / "fletes_informe.md"
    informe.write_text(_render(comp, client), encoding="utf-8")
    return {"matriz": matriz, "informe": informe}


def _orden_recomendaciones(comp: Comparacion):
    return sorted(
        comp.recomendaciones,
        key=lambda r: (r.ahorro_anual is None, -(r.ahorro_anual or Decimal(0)), -r.ahorro_periodo),
    )


def _aviso_costo_cero(ruta: str, c: Celda) -> str:
    """Un costo 0 suele ser un costo todavia sin cargar: baja el costo/kg del transportista en silencio."""
    base = f"- **{ruta} / {c.transportista}**: "
    revisar = "Revisa que el costo este cargado."
    if c.envios_costo_cero == c.envios:
        if c.elegible:
            return base + f"costo 0 en todos sus envios; se le asigna indice de costo 1. {revisar}"
        return base + f"costo 0 en todos sus envios. {revisar}"
    return base + (f"{c.envios_costo_cero} de {c.envios} envios tienen costo 0; si es un costo aun no cargado, "
                   f"su costo/kg sale subestimado. {revisar}")


def _aviso_volumen_movido(rec: Recomendacion) -> str | None:
    """Si el mejor empieza despues de que el actual dejo de despachar, el cambio ya ocurrio."""
    if rec.mejor.primer_despacho <= rec.actual.ultimo_despacho:
        return None
    return (
        f"- **{rec.ruta}**: el volumen ya parece haberse movido: {rec.mejor.transportista} empieza el "
        f"{rec.mejor.primer_despacho.isoformat()} y {rec.actual.transportista} no envia despues del "
        f"{rec.actual.ultimo_despacho.isoformat()}. El ahorro de esta recomendacion puede estar ya capturado."
    )


def _avisos(comp: Comparacion) -> tuple[list[str], set[str]]:
    """Las lineas de la seccion Avisos y las rutas cuya recomendacion lleva un aviso de volumen movido."""
    lineas: list[str] = []
    movidas: set[str] = set()
    for ruta in comp.rutas:
        lineas += [_aviso_costo_cero(ruta.ruta, c) for c in ruta.celdas if c.envios_costo_cero]
        if ruta.recomendacion is not None:
            aviso = _aviso_volumen_movido(ruta.recomendacion)
            if aviso is not None:
                lineas.append(aviso)
                movidas.add(ruta.ruta)
    return lineas, movidas


def _render(comp: Comparacion, client: str) -> str:
    avisos, movidas = _avisos(comp)
    lines = [
        f"# Comparador de transportistas - {client}",
        "",
        f"- Envios analizados: **{comp.envios}** en **{len(comp.rutas)}** ruta(s); moneda **{comp.moneda}**",
        f"- Pesos del puntaje: costo **{comp.pesos.costo}**, servicio (OTD) **{comp.pesos.servicio}**",
        f"- Muestra minima para recomendar: **{comp.min_envios}** envios con fecha prometida y de entrega",
        f"- Ahorro anualizado de las recomendaciones: **{_ahorro_anual_texto(comp)}**",
        "",
        "Servicio = OTD (entregado en o antes de la fecha prometida), no OTIF: un archivo de fletes "
        "no dice si el pedido llego completo.",
        "",
        "## Recomendaciones",
        "",
    ]
    recs = _orden_recomendaciones(comp)
    if not recs:
        lines.append("Ninguna.")
    for r in recs:
        anual = (f"{_money(r.ahorro_anual)} {comp.moneda} al año" if r.ahorro_anual is not None
                 else f"no se anualiza (periodo de {r.dias_periodo} dias)")
        lines.append(
            f"- **{r.ruta}**: pasar de {r.actual.transportista} a {r.mejor.transportista}. "
            f"Ahorro del periodo {_money(r.ahorro_periodo)} {comp.moneda} ({anual}); "
            f"OTD {r.delta_otd_pp.quantize(Decimal('0.1')):+} pp; "
            f"puntaje {_fmt(r.mejor.puntaje, 3)} contra {_fmt(r.actual.puntaje, 3)}."
            + (" (ver Avisos)" if r.ruta in movidas else "")
        )
    lines += ["", "## Rutas sin recomendacion", ""]
    sin = [r for r in comp.rutas if r.recomendacion is None]
    if not sin:
        lines.append("Ninguna.")
    for r in sin:
        lines.append(f"- **{r.ruta}**: {MOTIVOS_TEXTO.get(r.motivo or '', r.motivo or '')}")
    if avisos:
        lines += ["", "## Avisos", "", *avisos]
    lines += [
        "",
        "Los nombres de transportista, origen y destino se agrupan sin distinguir mayusculas, acentos ni "
        "espacios de mas; alias distintos (por ejemplo una razon social y una marca) cuentan como dos transportistas.",
        "",
    ]
    return "\n".join(lines)


def options(report: object) -> GuidedOutcome:
    comp: Comparacion = report  # type: ignore[assignment]
    recs = _orden_recomendaciones(comp)
    if recs:
        return as_options(
            summary=_summary(comp),
            options=[
                ExecutionOption(
                    label=f"Mover {r.ruta} de {r.actual.transportista} a {r.mejor.transportista}",
                    summary=(f"Ahorro del periodo {_money(r.ahorro_periodo)} {comp.moneda}; "
                             f"OTD {r.delta_otd_pp.quantize(Decimal('0.1')):+} pp"),
                    score=float(r.ahorro_anual if r.ahorro_anual is not None else r.ahorro_periodo),
                    tradeoffs=(f"puntaje {_fmt(r.mejor.puntaje, 3)} contra {_fmt(r.actual.puntaje, 3)} "
                               f"con pesos costo {comp.pesos.costo} / servicio {comp.pesos.servicio}"),
                )
                for r in recs
            ],
            residuals=[
                Residual(
                    description="Negociar con los transportistas y reasignar el volumen",
                    risk_if_skipped="Se sigue pagando de mas en las rutas recomendadas",
                )
            ],
        )
    return as_handoff(
        summary=_summary(comp) + " - sin cambios recomendados",
        packets=[
            HandoffPacket(
                title="Revisar la matriz de transportistas",
                steps=[
                    "Abrir fletes_matriz.csv y revisar las rutas sin recomendacion y su motivo.",
                    "Si falta muestra, sumar historia o fechas de entrega y volver a correr.",
                    "Si el actual ya es el mejor, usar la matriz como referencia en la proxima negociacion.",
                ],
            )
        ],
        residuals=[
            Residual(
                description="Completar fechas de entrega donde faltan",
                risk_if_skipped="Transportistas sin fechas quedan fuera de la comparacion",
            )
        ],
    )


CARRIER_LANE_COMPARE = Tool(
    key=TOOL_KEY,
    title="Comparador de transportistas por ruta (fletes)",
    description=(
        "Compara los transportistas del propio cliente ruta por ruta: costo por kg, "
        "puntualidad (OTD) y un puntaje desglosado, con recomendacion de reasignacion y su ahorro."
    ),
    intent_keywords=(
        "comparar transportistas", "comparador de transportistas",
        "comparacion de transportistas", "comparación de transportistas",
        "transportistas por ruta", "costo de flete", "flete por ruta",
        "compare carriers", "carrier comparison", "freight cost per lane",
        "carrier scorecard", "lane carrier",
    ),
    requires_data=True,
    prepare=prepare,
    run=run,
    qa=qa,
    deliver=deliver,
    options=options,
    required_columns=REQUIRED_COLUMNS,
    optional_columns=OPTIONAL_COLUMNS,
)


def register(registry: ToolRegistry) -> ToolRegistry:
    """Toda la superficie de integracion del pack: una llamada, sin tocar el orquestador."""
    registry.register(CARRIER_LANE_COMPARE)
    return registry


def build_fletes_tools() -> ToolRegistry:
    """Un registry con solo este pack."""
    return register(ToolRegistry())

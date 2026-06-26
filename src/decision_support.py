"""Decision-support guardrails — the human-facing side of the Guided Execution Layer.

Each function answers ONE decision a remote Inventory/SCM operator faces but the agent
cannot take on its own (negotiate, switch supplier, approve spend, staff a shift). Rather
than leave the human with a blank, it returns a ``DecisionCard``: a semaforo verdict, the
*guardrails* (healthy minimums / ranges / floors), >=2 ranked executable options (reusing
``src.guided``), the reasoning, and an L3 citation.

Pure / deterministic: composes the existing engines (queuing, capacity_planning, pricing,
safety_stock, eoq, working_capital, mcdm) and never does I/O. The webapp layer
(``webapp/decisions.py``) is a thin translator over these functions.

References (L3 corpus): Vandeput *Inventory Optimization* (2020); Jacobs & Chase *OSCM*;
Chopra & Meindl *Supply Chain Management*; Rezaei (2015) Best-Worst Method / TOPSIS.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from src.eoq import compute_eoq
from src.guided import ExecutionOption, as_options
from src.mcdm import Criterion, topsis_rank
from src.queuing import mmc
from src.safety_stock import safety_stock
from src.working_capital import working_capital

# Semaforo levels.
VERDE = "verde"
AMARILLO = "amarillo"
ROJO = "rojo"

# Utilization bands for the operational-capacity verdict.
_UTIL_SANA = 0.85       # below this: comfortable
_UTIL_LIMITE = 0.95     # 0.85-0.95: tight; above: overloaded
_DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class Guardrail:
    """One decision parameter with its healthy value/range and a plain-language note."""

    etiqueta: str
    valor: str
    explicacion: str


@dataclass(frozen=True)
class Veredicto:
    """The semaforo (verde/amarillo/rojo) plus a one-line phrase."""

    nivel: str
    frase: str


@dataclass(frozen=True)
class DecisionCard:
    """The never-dead-end contract, made friendly: verdict + guardrails + ranked options."""

    titulo: str
    veredicto: Veredicto
    guardrails: tuple[Guardrail, ...]
    opciones: tuple[ExecutionOption, ...]
    por_que: str
    cita: str | None = None
    supuestos: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "titulo": self.titulo,
            "veredicto": asdict(self.veredicto),
            "guardrails": [asdict(g) for g in self.guardrails],
            "opciones": [asdict(o) for o in self.opciones],
            "por_que": self.por_que,
            "cita": self.cita,
            "supuestos": self.supuestos,
        }


def _rank(options: list[ExecutionOption]) -> tuple[ExecutionOption, ...]:
    """Run options through the guided contract so exactly one is flagged recommended."""
    return tuple(as_options("opciones de decision", options).options)


def _money(x: float) -> str:
    return f"${x:,.0f}"


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


# --------------------------------------------------------------------------- #
# 1. Viabilidad operativa / capacidad de operarios
# --------------------------------------------------------------------------- #
def viabilidad_operativa(
    *,
    entrada_unidades_dia: float,
    salida_unidades_dia: float,
    num_operarios: int,
    unidades_hora_operario: float,
    horas_turno: float,
) -> DecisionCard:
    """Can the workforce cover inbound+outbound volume? In how long? Does it create backlog?"""
    if min(num_operarios, unidades_hora_operario, horas_turno) <= 0:
        raise ValueError("operarios, unidades/hora y horas de turno deben ser > 0")
    if entrada_unidades_dia < 0 or salida_unidades_dia < 0:
        raise ValueError("las unidades por dia no pueden ser negativas")
    carga_dia = entrada_unidades_dia + salida_unidades_dia
    if carga_dia <= 0:
        raise ValueError("ingresa al menos una unidad de entrada o de salida por dia")

    cap_hora_total = num_operarios * unidades_hora_operario
    cap_dia = cap_hora_total * horas_turno
    utilizacion = carga_dia / cap_dia if cap_dia > 0 else float("inf")
    horas_necesarias = carga_dia / cap_hora_total if cap_hora_total > 0 else float("inf")
    # Minimum operarios to land back inside the healthy band (<= 85% utilization).
    min_operarios_sano = math.ceil(carga_dia / (_UTIL_SANA * unidades_hora_operario * horas_turno))

    supuestos = {
        "entrada_unidades_dia": entrada_unidades_dia,
        "salida_unidades_dia": salida_unidades_dia,
        "num_operarios": num_operarios,
        "unidades_hora_operario": unidades_hora_operario,
        "horas_turno": horas_turno,
        "carga_unidades_dia": carga_dia,
    }
    cita = "Jacobs & Chase, OSCM cap. 22 (lineas de espera) + planificacion de capacidad."

    if utilizacion >= 1.0:
        backlog = carga_dia - cap_dia
        faltan = min_operarios_sano - num_operarios
        guardrails = (
            Guardrail("Dan abasto?", "No", "La carga supera la capacidad del turno."),
            Guardrail("Utilizacion", _pct(utilizacion), "Por encima del 100%: se acumula trabajo."),
            Guardrail("Backlog diario", f"{backlog:,.0f} u/dia",
                      "Unidades que quedan sin procesar cada dia (atrasa otras operaciones)."),
            Guardrail("Operarios minimos (zona sana)", f"{min_operarios_sano}",
                      f"Para bajar la utilizacion al {_pct(_UTIL_SANA)}; faltan {faltan}."),
        )
        opciones = _rank([
            ExecutionOption(label=f"Sumar {faltan} operario(s)",
                            summary=f"Llegar a {min_operarios_sano} para operar en zona sana.",
                            score=3.0, action=f"staff:add:{faltan}"),
            ExecutionOption(label="Extender el turno",
                            summary=f"Subir horas hasta cubrir {horas_necesarias:.1f} h de carga.",
                            score=2.0, action="shift:extend"),
            ExecutionOption(label="Tercerizar el excedente",
                            summary=f"Derivar ~{backlog:,.0f} u/dia a un 3PL mientras se ajusta dotacion.",
                            score=1.0, action="outsource:overflow"),
        ])
        return DecisionCard(
            titulo="Viabilidad operativa",
            veredicto=Veredicto(ROJO, "No dan abasto: la carga supera la capacidad."),
            guardrails=guardrails, opciones=opciones,
            por_que=("La carga diaria (entrada+salida) excede la capacidad del turno "
                     "(operarios x unidades/hora x horas), asi que la utilizacion supera el 100% "
                     "y el trabajo no procesado se acumula como backlog."),
            cita=cita, supuestos=supuestos,
        )

    # Feasible (rho < 1): use M/M/c for the waiting picture.
    metrics = mmc(carga_dia / horas_turno, unidades_hora_operario, num_operarios)
    if utilizacion < _UTIL_SANA:
        nivel, frase = VERDE, "Dan abasto, con holgura."
    elif utilizacion < _UTIL_LIMITE:
        nivel, frase = AMARILLO, "Dan abasto, pero al limite."
    else:
        nivel, frase = AMARILLO, "Al borde de la saturacion."

    guardrails = (
        Guardrail("Dan abasto?", "Si" if nivel == VERDE else "Si, ajustado",
                  "La capacidad cubre la carga del dia."),
        Guardrail("Utilizacion", _pct(utilizacion),
                  f"Sano < {_pct(_UTIL_SANA)}; tension {_pct(_UTIL_SANA)}-{_pct(_UTIL_LIMITE)}."),
        Guardrail("Horas para la carga", f"{horas_necesarias:.1f} h",
                  f"Tiempo de proceso vs turno de {horas_turno:g} h."),
        Guardrail("Operarios minimos (zona sana)", f"{min_operarios_sano}",
                  "Dotacion para mantener la utilizacion en zona sana."),
        Guardrail("Espera media en cola", f"{metrics.wq:.2f} h",
                  "Tiempo medio que una unidad espera antes de ser procesada."),
    )
    if nivel == VERDE:
        opciones = _rank([
            ExecutionOption(label="Mantener la dotacion actual",
                            summary="Hay holgura; la operacion es viable sin cambios.",
                            score=3.0, action="staff:keep"),
            ExecutionOption(label="Reasignar 1 operario",
                            summary="Liberar capacidad a otra tarea si el pico no se repite.",
                            score=2.0, action="staff:reassign:1"),
        ])
    else:
        opciones = _rank([
            ExecutionOption(label="Sumar 1 operario o extender turno",
                            summary="Devolver holgura antes de que un pico genere cola.",
                            score=3.0, action="staff:add:1"),
            ExecutionOption(label="Mantener y monitorear",
                            summary="Viable hoy, pero sin margen ante picos de demanda.",
                            score=2.0, action="staff:keep"),
        ])
    return DecisionCard(
        titulo="Viabilidad operativa",
        veredicto=Veredicto(nivel, frase), guardrails=guardrails, opciones=opciones,
        por_que=("Modelo de capacidad (operarios x unidades/hora x horas) y de cola M/M/c: "
                 "la utilizacion mide cuanto del turno se consume y la espera media indica "
                 "si se forma fila."),
        cita=cita, supuestos=supuestos,
    )


# --------------------------------------------------------------------------- #
# 2. Negociacion con proveedor
# --------------------------------------------------------------------------- #
def negociacion_proveedor(
    *,
    costo_unitario: float,
    precio_venta: float,
    margen_minimo_pct: float,
    lead_time_actual_dias: float,
    demanda_std_dia: float = 0.0,
    nivel_servicio: float = 0.95,
) -> DecisionCard:
    """Guardrails to walk into a supplier negotiation: cost ceiling, max lead time, payment."""
    if precio_venta <= 0 or costo_unitario <= 0:
        raise ValueError("precio de venta y costo unitario deben ser > 0")
    if not 0 < margen_minimo_pct < 1:
        raise ValueError("el margen minimo debe estar entre 0 y 1 (exclusivo)")
    if lead_time_actual_dias <= 0:
        raise ValueError("el lead time debe ser > 0")

    costo_max = precio_venta * (1 - margen_minimo_pct)   # walk-away cost ceiling
    margen_actual = (precio_venta - costo_unitario) / precio_venta
    # Extra safety stock per additional day of lead time (cost of a worse plazo).
    ss_actual = safety_stock(demanda_std_dia, nivel_servicio, risk_periods=lead_time_actual_dias)
    ss_mas_un_dia = safety_stock(demanda_std_dia, nivel_servicio, risk_periods=lead_time_actual_dias + 1)
    delta_ss_dia = ss_mas_un_dia.safety_stock - ss_actual.safety_stock

    supuestos = {
        "costo_unitario": costo_unitario, "precio_venta": precio_venta,
        "margen_minimo_pct": margen_minimo_pct, "lead_time_actual_dias": lead_time_actual_dias,
        "demanda_std_dia": demanda_std_dia, "nivel_servicio": nivel_servicio,
        "margen_actual": margen_actual,
    }
    cita = "Chopra & Meindl, margen y palancas de capital de trabajo; Vandeput cap. 4 (safety stock)."

    if margen_actual < margen_minimo_pct:
        nivel = ROJO
        frase = "Por debajo del margen minimo: no cerrar a este costo."
    elif margen_actual < margen_minimo_pct + 0.05:
        nivel = AMARILLO
        frase = "Margen ajustado: cerrar solo con concesiones."
    else:
        nivel = VERDE
        frase = "Hay margen para cerrar."

    guardrails = (
        Guardrail("Costo maximo aceptable (walk-away)", _money(costo_max),
                  f"Por encima de esto el margen cae bajo {_pct(margen_minimo_pct)}. Es tu limite duro."),
        Guardrail("Margen actual", _pct(margen_actual),
                  f"Con costo {_money(costo_unitario)} y precio {_money(precio_venta)}."),
        Guardrail("Plazo de entrega objetivo", f"<= {lead_time_actual_dias:g} dias",
                  f"Cada dia extra agrega ~{delta_ss_dia:.1f} u de stock de seguridad."),
        Guardrail("Termino de pago objetivo", f">= {lead_time_actual_dias:g} dias",
                  "Que el plazo de pago cubra el inventario en transito (no financiarlo tu)."),
    )
    if nivel == ROJO:
        opciones = _rank([
            ExecutionOption(label=f"Renegociar costo a <= {_money(costo_max)}",
                            summary="Unica forma de cerrar respetando el margen minimo.",
                            score=3.0, action="negotiate:cost_down"),
            ExecutionOption(label="Subir precio de venta",
                            summary=f"Alternativa: precio que recupere {_pct(margen_minimo_pct)} de margen.",
                            score=2.0, action="pricing:raise"),
        ])
    elif nivel == AMARILLO:
        opciones = _rank([
            ExecutionOption(label=f"Cerrar solo bajo {_money(costo_max)}",
                            summary="Aceptable con colchon; pedir mejor plazo de pago.",
                            score=3.0, action="negotiate:conditional"),
            ExecutionOption(label="Buscar proveedor alternativo",
                            summary="Comparar antes de comprometer un margen tan ajustado.",
                            score=2.0, action="sourcing:compare"),
        ])
    else:
        opciones = _rank([
            ExecutionOption(label="Cerrar el trato",
                            summary="Hay margen; asegurar plazo de entrega y pago objetivos.",
                            score=3.0, action="negotiate:close"),
            ExecutionOption(label="Pedir mejor plazo de pago",
                            summary="Usar el margen para empujar DPO y liberar capital.",
                            score=2.0, action="negotiate:terms"),
        ])
    return DecisionCard(
        titulo="Negociacion con proveedor",
        veredicto=Veredicto(nivel, frase), guardrails=guardrails, opciones=opciones,
        por_que=("El costo maximo sale del precio de venta y el margen minimo sano; el plazo de "
                 "entrega se castiga por el stock de seguridad extra que obliga a mantener."),
        cita=cita, supuestos=supuestos,
    )


# --------------------------------------------------------------------------- #
# 3. Cambiar de proveedor (si/no)
# --------------------------------------------------------------------------- #
def _topsis_dos(actual: dict, alternativo: dict) -> tuple[dict, str]:
    criteria = [
        Criterion("precio", benefit=False),
        Criterion("otif", benefit=True),
        Criterion("lead", benefit=False),
        Criterion("defectos", benefit=False),
    ]
    weights = {"precio": 0.4, "otif": 0.3, "lead": 0.2, "defectos": 0.1}
    res = topsis_rank({"actual": actual, "alternativo": alternativo}, criteria, weights)
    return res.scores, res.ranking[0]


def cambiar_proveedor(
    *,
    actual_precio: float,
    actual_otif: float,
    actual_lead: float,
    actual_defectos: float,
    alt_precio: float,
    alt_otif: float,
    alt_lead: float,
    alt_defectos: float,
) -> DecisionCard:
    """Compare current vs alternative supplier (TOPSIS) and give the price breakeven."""
    for name, v in (("precio", actual_precio), ("precio alt.", alt_precio),
                    ("lead", actual_lead), ("lead alt.", alt_lead)):
        if v <= 0:
            raise ValueError(f"{name} debe ser > 0")
    for name, v in (("OTIF", actual_otif), ("OTIF alt.", alt_otif),
                    ("defectos", actual_defectos), ("defectos alt.", alt_defectos)):
        if not 0 <= v <= 1:
            raise ValueError(f"{name} debe estar entre 0 y 1")

    actual = {"precio": actual_precio, "otif": actual_otif, "lead": actual_lead, "defectos": actual_defectos}
    alt = {"precio": alt_precio, "otif": alt_otif, "lead": alt_lead, "defectos": alt_defectos}
    scores, ganador = _topsis_dos(actual, alt)

    # Price breakeven: vary the alternative's price until the ranking flips (bisection).
    breakeven = _breakeven_precio(actual, alt)

    supuestos = {"actual": actual, "alternativo": alt, "scores": scores}
    cita = "Rezaei (2015) BWM / TOPSIS; ASCM scorecards de proveedor (M8)."

    if ganador == "alternativo":
        margen = scores["alternativo"] - scores["actual"]
        nivel = VERDE if margen >= 0.05 else AMARILLO
        frase = "Conviene cambiar." if nivel == VERDE else "El alternativo gana, pero por poco."
        be_txt = (f"hasta {_money(breakeven)}" if breakeven is not None
                  else "en todo el rango evaluado")
        opciones = _rank([
            ExecutionOption(label="Cambiar al alternativo",
                            summary=f"Mejor score multicriterio (precio/OTIF/lead/defectos). Conviene {be_txt}.",
                            score=3.0, action="sourcing:switch"),
            ExecutionOption(label="Renegociar con el actual",
                            summary="Usar la oferta del alternativo como palanca antes de migrar.",
                            score=2.0, action="negotiate:incumbent"),
        ])
    else:
        margen = scores["actual"] - scores["alternativo"]
        nivel = VERDE if margen >= 0.05 else AMARILLO
        frase = "Conviene quedarse." if nivel == VERDE else "El actual gana, pero por poco."
        be_txt = (f"si baja a {_money(breakeven)}" if breakeven is not None
                  else "no en el rango evaluado")
        opciones = _rank([
            ExecutionOption(label="Quedarse con el actual",
                            summary=f"Mejor score multicriterio. El alternativo solo conviene {be_txt}.",
                            score=3.0, action="sourcing:keep"),
            ExecutionOption(label="Pedir mejora al alternativo",
                            summary="Mantener la opcion viva pidiendo precio/lead mejores.",
                            score=2.0, action="sourcing:challenge"),
        ])

    guardrails = (
        Guardrail("Score actual", f"{scores['actual']:.3f}", "Cercania al ideal (TOPSIS, 0-1)."),
        Guardrail("Score alternativo", f"{scores['alternativo']:.3f}", "Cercania al ideal (TOPSIS, 0-1)."),
        Guardrail("Recomendado", "Alternativo" if ganador == "alternativo" else "Actual",
                  "Ganador del analisis multicriterio ponderado."),
        Guardrail("Breakeven de precio (alternativo)",
                  _money(breakeven) if breakeven is not None else "n/d",
                  "Precio del alternativo en el que se empata la decision."),
    )
    return DecisionCard(
        titulo="Cambiar de proveedor",
        veredicto=Veredicto(nivel, frase), guardrails=guardrails, opciones=opciones,
        por_que=("TOPSIS pondera precio (40%), OTIF (30%), lead time (20%) y defectos (10%) y "
                 "mide la cercania de cada proveedor al ideal. El breakeven indica desde que "
                 "precio cambia la conclusion."),
        cita=cita, supuestos=supuestos,
    )


def _breakeven_precio(actual: dict, alt: dict) -> float | None:
    """Bisection: the alternative's price at which the TOPSIS ranking flips (or None)."""
    base = actual["precio"]
    lo, hi = base * 0.1, base * 5.0

    def gana_alt(precio: float) -> bool:
        probe = {**alt, "precio": precio}
        scores, ganador = _topsis_dos(actual, probe)
        return ganador == "alternativo"

    if gana_alt(lo) == gana_alt(hi):
        return None  # no flip in the evaluated range
    for _ in range(40):
        mid = (lo + hi) / 2
        if gana_alt(mid) == gana_alt(lo):
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# --------------------------------------------------------------------------- #
# 4. Niveles de inventario sanos
# --------------------------------------------------------------------------- #
def niveles_inventario(
    *,
    demanda_media_periodo: float,
    demanda_std_periodo: float,
    lead_time_periodos: float,
    nivel_servicio: float,
    costo_unitario: float,
    costo_ordenar: float,
    holding_rate_anual: float,
    periodos_por_anio: float = 52.0,
    stock_actual: float | None = None,
) -> DecisionCard:
    """Healthy min/max, safety stock, reorder point and EOQ; semaforo on current stock."""
    if demanda_media_periodo <= 0:
        raise ValueError("la demanda media debe ser > 0")
    if not 0 < nivel_servicio < 1:
        raise ValueError("el nivel de servicio debe estar entre 0 y 1 (exclusivo)")
    if min(costo_unitario, costo_ordenar, holding_rate_anual, lead_time_periodos) <= 0:
        raise ValueError("costos, holding rate y lead time deben ser > 0")

    ss = safety_stock(demanda_std_periodo, nivel_servicio, risk_periods=lead_time_periodos).safety_stock
    rop = demanda_media_periodo * lead_time_periodos + ss
    annual_demand = demanda_media_periodo * periodos_por_anio
    holding_unit = max(holding_rate_anual * costo_unitario, 1e-6)
    eoq = compute_eoq(annual_demand, holding_unit, costo_ordenar).order_quantity
    min_sano = ss
    max_sano = rop + eoq

    supuestos = {
        "demanda_media_periodo": demanda_media_periodo, "demanda_std_periodo": demanda_std_periodo,
        "lead_time_periodos": lead_time_periodos, "nivel_servicio": nivel_servicio,
        "costo_unitario": costo_unitario, "costo_ordenar": costo_ordenar,
        "holding_rate_anual": holding_rate_anual, "periodos_por_anio": periodos_por_anio,
        "stock_actual": stock_actual,
    }
    cita = "Vandeput, Inventory Optimization (2020): EOQ cap. 2, safety stock cap. 4."

    guardrails = [
        Guardrail("Stock de seguridad", f"{ss:,.0f} u",
                  f"Colchon para nivel de servicio {_pct(nivel_servicio)} en {lead_time_periodos:g} periodos."),
        Guardrail("Punto de reorden (ROP)", f"{rop:,.0f} u",
                  "Cuando el stock toca este nivel, disparar el pedido."),
        Guardrail("Cantidad optima (EOQ)", f"{eoq:,.0f} u", "Tamano de pedido que minimiza costo total."),
        Guardrail("Rango sano", f"{min_sano:,.0f} - {max_sano:,.0f} u",
                  "Por debajo del minimo hay riesgo de quiebre; por encima del maximo, exceso."),
    ]

    if stock_actual is None:
        nivel, frase = VERDE, "Politica calculada y lista para adoptar."
        opciones = _rank([
            ExecutionOption(label="Adoptar la politica",
                            summary=f"ROP {rop:,.0f} u, pedir {eoq:,.0f} u. Stock sano {min_sano:,.0f}-{max_sano:,.0f}.",
                            score=3.0, action="policy:adopt"),
            ExecutionOption(label="Revisar el nivel de servicio",
                            summary="Subirlo reduce quiebres pero inmoviliza mas capital.",
                            score=2.0, action="policy:tune_service"),
        ])
    elif stock_actual < min_sano:
        nivel, frase = ROJO, "Por debajo del stock de seguridad: riesgo de quiebre."
        faltante = rop - stock_actual
        guardrails.append(Guardrail("Stock actual", f"{stock_actual:,.0f} u", "Bajo el minimo sano."))
        opciones = _rank([
            ExecutionOption(label=f"Reponer hasta el ROP (pedir {max(faltante, eoq):,.0f} u)",
                            summary="Volver a zona sana de inmediato.",
                            score=3.0, action="replenish:to_rop"),
            ExecutionOption(label="Acelerar el pedido pendiente",
                            summary="Si hay PO abierta, adelantar la entrega antes que abrir otra.",
                            score=2.0, action="replenish:expedite"),
        ])
    elif stock_actual <= max_sano:
        nivel, frase = VERDE, "Stock en rango sano."
        guardrails.append(Guardrail("Stock actual", f"{stock_actual:,.0f} u", "Dentro del rango sano."))
        opciones = _rank([
            ExecutionOption(label="Mantener la politica",
                            summary="Stock sano; reordenar al tocar el ROP.",
                            score=3.0, action="policy:hold"),
            ExecutionOption(label="Afinar nivel de servicio",
                            summary="Ajuste fino segun criticidad del SKU.",
                            score=2.0, action="policy:tune_service"),
        ])
    else:
        nivel, frase = AMARILLO, "Exceso: capital inmovilizado."
        exceso = stock_actual - max_sano
        guardrails.append(Guardrail("Exceso sobre el maximo", f"{exceso:,.0f} u",
                                    f"~{_money(exceso * costo_unitario)} de capital inmovilizado."))
        opciones = _rank([
            ExecutionOption(label="Frenar compras y promover salida",
                            summary="Dejar caer el stock hacia el rango sano antes de reordenar.",
                            score=3.0, action="inventory:drawdown"),
            ExecutionOption(label="Mantener si es estacional",
                            summary="Justificado solo si hay un pico de demanda previsto.",
                            score=2.0, action="inventory:hold_seasonal"),
        ])
    return DecisionCard(
        titulo="Niveles de inventario sanos",
        veredicto=Veredicto(nivel, frase), guardrails=tuple(guardrails), opciones=opciones,
        por_que=("Safety stock = z * sigma * raiz(lead); ROP = demanda*lead + safety stock; "
                 "EOQ minimiza costo de ordenar + mantener. El rango sano va del safety stock "
                 "(piso) al ROP+EOQ (techo)."),
        cita=cita, supuestos=supuestos,
    )


# --------------------------------------------------------------------------- #
# 5. Aprobar compra / liquidar stock
# --------------------------------------------------------------------------- #
def aprobar_compra(
    *,
    monto_compra: float,
    inventario_actual_valor: float,
    cogs_anual: float,
    ventas_anual: float,
    dio_objetivo_max: float = 90.0,
    dso_dias: float = 45.0,
    dpo_dias: float = 30.0,
) -> DecisionCard:
    """How much is healthy to spend on stock without blowing the cash cycle (DIO ceiling)."""
    if min(monto_compra, inventario_actual_valor) < 0:
        raise ValueError("los montos no pueden ser negativos")
    if cogs_anual <= 0 or ventas_anual <= 0 or dio_objetivo_max <= 0:
        raise ValueError("COGS, ventas y DIO objetivo deben ser > 0")

    dio_actual = inventario_actual_valor / cogs_anual * _DAYS_PER_YEAR
    inv_nuevo = inventario_actual_valor + monto_compra
    dio_nuevo = inv_nuevo / cogs_anual * _DAYS_PER_YEAR
    # Healthy spend cap: extra inventory that keeps DIO at/under the target.
    inv_max_sano = cogs_anual / _DAYS_PER_YEAR * dio_objetivo_max
    cap_compra = max(inv_max_sano - inventario_actual_valor, 0.0)
    wc = working_capital(revenue=ventas_anual, cogs=cogs_anual,
                         dio=dio_nuevo, dso=dso_dias, dpo=dpo_dias)

    supuestos = {
        "monto_compra": monto_compra, "inventario_actual_valor": inventario_actual_valor,
        "cogs_anual": cogs_anual, "ventas_anual": ventas_anual,
        "dio_objetivo_max": dio_objetivo_max, "dso_dias": dso_dias, "dpo_dias": dpo_dias,
        "dio_actual": dio_actual, "dio_tras_compra": dio_nuevo,
    }
    cita = "SCOR cash-to-cash (AM.1.1); Chopra & Meindl, palancas de capital de trabajo."

    if dio_nuevo <= dio_objetivo_max:
        nivel, frase = VERDE, "Compra sana: el ciclo de caja aguanta."
    elif dio_nuevo <= dio_objetivo_max * 1.15:
        nivel, frase = AMARILLO, "Compra ajustada: roza el limite de DIO."
    else:
        nivel, frase = ROJO, "Compra excede el DIO sano: inmoviliza demasiado capital."

    guardrails = (
        Guardrail("Cuanto es sano comprar", _money(cap_compra),
                  f"Maximo que mantiene el DIO en {dio_objetivo_max:g} dias."),
        Guardrail("DIO tras la compra", f"{dio_nuevo:.0f} dias",
                  f"Objetivo <= {dio_objetivo_max:g}; actual {dio_actual:.0f}."),
        Guardrail("Capital que inmoviliza", _money(monto_compra),
                  "Efectivo atado en inventario hasta venderlo."),
        Guardrail("Ciclo de caja resultante", f"{wc.cash_conversion_cycle:.0f} dias",
                  "DIO + DSO - DPO: dias entre pagar y cobrar."),
    )
    if nivel == VERDE:
        opciones = _rank([
            ExecutionOption(label="Aprobar la compra",
                            summary="Dentro del DIO sano; no estresa la caja.",
                            score=3.0, action="purchase:approve"),
            ExecutionOption(label="Negociar mejor plazo de pago",
                            summary="Extender DPO acorta el ciclo de caja aun mas.",
                            score=2.0, action="negotiate:dpo"),
        ])
    else:
        opciones = _rank([
            ExecutionOption(label=f"Comprar hasta {_money(cap_compra)}",
                            summary="Reducir el monto para no pasar el DIO sano.",
                            score=3.0, action="purchase:cap"),
            ExecutionOption(label="Escalonar la compra",
                            summary="Dividir en entregas para suavizar el impacto en caja.",
                            score=2.0, action="purchase:stagger"),
        ])
    return DecisionCard(
        titulo="Aprobar compra",
        veredicto=Veredicto(nivel, frase), guardrails=guardrails, opciones=opciones,
        por_que=("La compra sube el inventario y con el el DIO (dias de inventario). El tope sano "
                 "es el monto que mantiene el DIO bajo el objetivo, para no inmovilizar caja."),
        cita=cita, supuestos=supuestos,
    )


def liquidar_stock(
    *,
    unidades_excedente: float,
    costo_unitario: float,
    precio_actual: float,
    valor_recuperacion_pct: float = 0.4,
) -> DecisionCard:
    """Recommended clearance price and freed cash for excess / dead stock."""
    if unidades_excedente <= 0 or costo_unitario <= 0 or precio_actual <= 0:
        raise ValueError("unidades, costo y precio deben ser > 0")
    if not 0 < valor_recuperacion_pct <= 1:
        raise ValueError("el valor de recuperacion debe estar entre 0 y 1")

    piso = costo_unitario * valor_recuperacion_pct           # salvage floor
    precio_liq = max(piso, precio_actual * 0.7)              # start markdown, never below salvage
    precio_liq = min(precio_liq, precio_actual)
    cash_liberado = unidades_excedente * precio_liq
    perdida_vs_costo = max(costo_unitario - precio_liq, 0.0) * unidades_excedente

    supuestos = {
        "unidades_excedente": unidades_excedente, "costo_unitario": costo_unitario,
        "precio_actual": precio_actual, "valor_recuperacion_pct": valor_recuperacion_pct,
        "precio_liquidacion": precio_liq,
    }
    cita = "Retail-math markdown; Vandeput, gestion de slow/dead stock."

    if precio_liq >= costo_unitario:
        nivel, frase = VERDE, "Liquidas recuperando el costo."
    elif precio_liq > piso:
        nivel, frase = AMARILLO, "Perdida parcial, pero liberas capital."
    else:
        nivel, frase = ROJO, "Solo valor de recuperacion: evaluar donar/scrap."

    guardrails = (
        Guardrail("Precio de liquidacion recomendado", _money(precio_liq),
                  f"Descuento desde {_money(precio_actual)}, sin bajar del piso de recuperacion."),
        Guardrail("Piso (valor de recuperacion)", _money(piso),
                  f"{_pct(valor_recuperacion_pct)} del costo; no liquidar por debajo."),
        Guardrail("Cash liberado", _money(cash_liberado),
                  f"Por {unidades_excedente:,.0f} u; capital que vuelve a la operacion."),
        Guardrail("Perdida vs costo", _money(perdida_vs_costo),
                  "Diferencia contra el costo (si el precio queda por debajo)."),
    )
    opciones = _rank([
        ExecutionOption(label=f"Liquidar a {_money(precio_liq)}",
                        summary=f"Libera {_money(cash_liberado)} y descongestiona el almacen.",
                        score=3.0, action="liquidate:markdown"),
        ExecutionOption(label="Donar o dar de baja (scrap)",
                        summary="Si el precio cae al piso, el beneficio fiscal/espacio puede superar la venta.",
                        score=1.0, action="liquidate:scrap"),
        ExecutionOption(label="Retener si es estacional",
                        summary="Solo si un pico cercano justifica el costo de mantener.",
                        score=2.0, action="liquidate:hold"),
    ])
    return DecisionCard(
        titulo="Liquidar stock",
        veredicto=Veredicto(nivel, frase), guardrails=guardrails, opciones=opciones,
        por_que=("El precio de liquidacion baja desde el actual pero nunca por debajo del valor de "
                 "recuperacion; el cash liberado es el capital que vuelve a la operacion."),
        cita=cita, supuestos=supuestos,
    )

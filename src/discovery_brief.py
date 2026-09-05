"""Auditoría de parámetros de cliente -- si el CONTEXTO alcanza, y qué preguntar.

El espejo de ``src.intake_scan`` sobre la otra mitad de la entrada: aquel audita
si el DATO alcanza, este audita si los PARAMETROS de negocio alcanzan. Puro y sin
dependencias: nada toca disco, nada llama a un LLM, y dos corridas sobre la misma
entrada devuelven exactamente el mismo resultado en el mismo orden.

El catálogo está indexado POR PARAMETRO y no por herramienta: el parámetro es del
dominio, y duplicarlo por herramienta es la repetición que produjo cinco
instancias del mismo defecto en intake_scan.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from nosilo_core.guided import ExecutionOption, GuidedOutcome, Residual, as_executed, as_options

# Las cuatro areas de src.hats.HAT_KEYS, copiadas a proposito en vez de
# importadas: src.hats arrastra numpy y scipy por su cadena de imports, y este
# modulo es puro y liviano por contrato. La copia NO puede derivar --
# tests/test_discovery_brief.py asierta igualdad contra src.hats.HAT_KEYS, donde
# importar scipy no cuesta nada.
_HAT_KEYS = ("comprador", "planner", "cfo", "comercial")


@dataclass(frozen=True)
class ParamSpec:
    """Un parámetro del dominio: qué preguntar, qué es sano, y qué se rompe sin él."""

    name: str
    question: str
    unit: str
    low: float
    high: float
    moves: str
    # (tool_key, valor, sitio en el código). TUPLA a propósito: el mismo parámetro
    # tiene distinto default según quién lo lea (order_cost vale 100.0 en
    # simulation_job y 75 según el docstring de client_profile). Un default único
    # mentiría en `moves`.
    defaults: tuple[tuple[str, float, str], ...] = ()
    # Sub-claves de un parámetro granular (hoy solo objection_thresholds). Vacío
    # => el parámetro es escalar y "presente" sí es un booleano.
    subkeys: tuple[str, ...] = ()


# Parámetros que al menos una herramienta declara en `required_client_params`.
# Estos producen veredictos que BLOQUEAN.
CATALOG: tuple[ParamSpec, ...] = (
    ParamSpec(
        name="holding_rate",
        question="¿Qué porcentaje anual del valor del inventario les cuesta mantenerlo "
                 "(capital, depósito, seguro, obsolescencia)?",
        unit="fracción anual",
        low=0.05, high=0.60,
        moves="El costo de mantener y, con él, el ahorro anual de la portada.",
        defaults=(
            ("inventory_optimization", 0.25, "jobs/inventory_optimization.py:229"),
            ("inventory_optimization", 0.25, "src/client_profile.py (docstring)"),
            ("webapp", 0.22, "webapp/app.py:569"),
        ),
    ),
    ParamSpec(
        name="service_level",
        question="¿Qué probabilidad de NO quebrar stock durante el lead time quieren "
                 "sostener por ciclo?",
        unit="fracción",
        low=0.50, high=0.999,
        moves="El z del stock de seguridad: mueve el inventario objetivo de toda la red.",
        defaults=(
            ("flow_map", 0.95, "jobs/flow_map_job.py:159"),
            ("inventory_optimization", 0.95, "jobs/inventory_optimization.py:228"),
            ("inventory_optimization", 0.95, "src/client_profile.py (docstring)"),
            ("multi_echelon", 0.95, "jobs/multi_echelon_job.py:31"),
        ),
    ),
    ParamSpec(
        name="order_cost",
        question="¿Cuánto les cuesta emitir y recibir UNA orden de compra, todo incluido?",
        unit="usd/pedido",
        low=1.0, high=5000.0,
        moves="El lote económico y la frecuencia de reposición de toda la red.",
        defaults=(
            ("batch", 1000.0, "src/batch.py:28"),
            ("hats", 75.0, "src/hats.py:90"),
            ("inventory_optimization", 75.0, "jobs/inventory_optimization.py:230"),
            ("powerbi_export", 1000.0, "src/powerbi_export.py:56"),
            ("powerbi_export", 1000.0, "src/powerbi_export.py:249"),
            ("simulation", 100.0, "jobs/simulation_job.py:31"),
            ("webapp", 80.0, "webapp/app.py:568"),
        ),
    ),
    ParamSpec(
        name="goodwill",
        question="Además del margen perdido, ¿cuánto les cuesta en reputación cada "
                 "unidad que no pudieron entregar?",
        unit="usd/unidad faltante",
        low=0.0, high=10000.0,
        moves="El ratio crítico del newsvendor: un cero asumido baja la cantidad "
              "óptima de TODOS los SKUs a la vez.",
        defaults=(("newsvendor", 0.0, "jobs/newsvendor_job.py:93"),),
    ),
    ParamSpec(
        name="objection_thresholds",
        question="Por cada área (comprador, planner, CFO, comercial): ¿cuánto puede "
                 "empeorar su indicador propio antes de que la decisión se caiga?",
        unit="unidades del KPI de cada área",
        low=0.0, high=1_000_000.0,
        moves="Qué áreas pueden evaluarse en el cruce de impacto; sin umbral, el "
              "área sale NO_EVALUABLE.",
        defaults=(),
        subkeys=_HAT_KEYS,
    ),
)

# Parámetros que el motor usa con default silencioso y NINGUNA herramienta declara.
# No bloquean nada: son el backlog de la tanda 2, y existen para que el reporte
# tenga un denominador honesto en vez de una lista de deseos.
UNDECLARED_USE: tuple[ParamSpec, ...] = (
    ParamSpec(
        name="review_period",
        question="¿Cada cuántos días revisan el stock para decidir una reposición?",
        unit="días",
        low=1.0, high=90.0,
        moves="El horizonte de riesgo del (R,S): un período asumido de 1 día "
              "subestima el stock de seguridad.",
        defaults=(
            ("multi_echelon", 1.0, "jobs/multi_echelon_job.py:32"),
            ("simulation", 1, "jobs/simulation_job.py:33"),
        ),
    ),
    ParamSpec(
        name="backorder_cost",
        question="¿Cuánto les cuesta dejar una unidad pendiente hasta poder entregarla?",
        unit="usd/unidad/período",
        low=0.0, high=10000.0,
        moves="El balance faltante-vs-exceso: impacto de costo de escasez vs exceso.",
        defaults=(
            ("batch", 50.0, "src/batch.py:29"),
            ("powerbi_export", 50.0, "src/powerbi_export.py:57"),
            ("powerbi_export", 50.0, "src/powerbi_export.py:250"),
            ("simulation", 5.0, "jobs/simulation_job.py:32"),
        ),
    ),
    ParamSpec(
        name="lead_time_days",
        question="Cuando el CSV no lo trae por SKU, ¿cuántos días tarda el "
                 "reabastecimiento típico?",
        unit="días",
        low=1.0, high=365.0,
        moves="El plazo sobre el que se dimensiona el stock de seguridad.",
        defaults=(),
    ),
)

_BY_NAME: dict[str, ParamSpec] = {s.name: s for s in CATALOG + UNDECLARED_USE}


def spec_for(name: str) -> ParamSpec | None:
    """El ParamSpec de un parámetro, o None si el catálogo no lo conoce."""
    return _BY_NAME.get(name)


GREEN = "verde"
YELLOW = "amarillo"
RED = "rojo"
UNDECLARED = "no_declarado"
NO_PROFILE = "sin_perfil"


@dataclass(frozen=True)
class ToolVerdict:
    """Si una herramienta correría hoy con números del cliente, y qué le falta.

    Los cuatro modos de falla viajan SEPARADOS porque cada uno tiene un arreglo
    distinto, y decirle al operador el equivocado le hace perder la reunión:

    - `missing`: el parámetro nunca se declaró. Hay que PEDIRLO.
    - `out_of_range`: está cargado pero es implausible (el clásico holding_rate
      de 25.0 en vez de 0.25). Pedirlo de nuevo no sirve: hay que CORREGIRLO.
    - `malformed`: el parámetro está presente pero es del tipo equivocado (un
      bool, un string, un dict donde va un escalar o viceversa). No sirve
      pedirlo de nuevo ni corregir un número: hay que REEMPLAZAR el valor
      basura por uno del tipo correcto.
    - `incomplete`: parámetro granular presente pero parcial. La corrida es
      válida y cubre menos -- por eso amarillo, nunca rojo.
    """

    tool_key: str
    state: str
    missing: tuple[str, ...] = ()
    out_of_range: tuple[tuple[str, float], ...] = ()
    incomplete: tuple[tuple[str, tuple[str, ...]], ...] = ()
    malformed: tuple[tuple[str, str], ...] = ()


_MALFORMED_REPR_MAX = 40


def _is_malformed(spec: ParamSpec, value: object) -> bool:
    """El valor está presente pero es del tipo equivocado para este parámetro.

    Un escalar (`not spec.subkeys`) es malformado si es un bool -- un bool es
    un int en Python y nunca es un valor legítimo para estos parámetros -- o
    si no es un int/float en absoluto (string, None, lista...). Un granular
    (`spec.subkeys`) es malformado si no es un dict: `_missing_subkeys` solo
    puede juzgar completitud sobre un dict real, y un no-dict no es "una
    corrida parcial legítima" sino datos rotos.
    """
    if spec.subkeys:
        return not isinstance(value, dict)
    return isinstance(value, bool) or not isinstance(value, (int, float))


def _short_repr(value: object) -> str:
    """Repr corto y acotado para que un valor gigante no reviente una celda CSV."""
    text = repr(value)
    if len(text) > _MALFORMED_REPR_MAX:
        return text[:_MALFORMED_REPR_MAX]
    return text


def _out_of_range(spec: ParamSpec, value: object) -> bool:
    """Un escalar numérico fuera del rango plausible del catálogo.

    Los granulares no se evalúan por rango aquí: su completitud se juzga en
    `_missing_subkeys`. Los valores mal tipados (bool, string, etc.) ya fueron
    descartados por `_is_malformed` antes de llegar acá.
    """
    if spec.subkeys:
        return False
    return not (spec.low <= float(value) <= spec.high)


def _missing_subkeys(spec: ParamSpec, value: object) -> tuple[str, ...]:
    """Las sub-claves que un parámetro granular no trae. Ordenadas alfabéticamente."""
    if not spec.subkeys:
        return ()
    present = set(value) if isinstance(value, dict) else set()
    return tuple(sorted(k for k in spec.subkeys if k not in present))


def verdicts_for(tools: list, params: dict | None) -> tuple[ToolVerdict, ...]:
    """Un veredicto por herramienta, ordenado por key para que dos corridas coincidan.

    ``params`` es lo que devuelve ``ClientProfile.as_params()``, o ``None``
    cuando el cliente no tiene perfil -- un estado propio, nunca colapsado en
    rojo: la acción es crear el perfil, no pedirle un número al cliente.
    """
    out: list[ToolVerdict] = []
    for tool in sorted(tools, key=lambda t: t.key):
        required = tuple(tool.required_client_params)
        if not required:
            out.append(ToolVerdict(tool.key, UNDECLARED))
            continue
        if params is None:
            out.append(ToolVerdict(tool.key, NO_PROFILE, missing=tuple(sorted(required))))
            continue

        missing: list[str] = []
        bad_range: list[tuple[str, float]] = []
        incomplete: list[tuple[str, tuple[str, ...]]] = []
        bad_type: list[tuple[str, str]] = []
        for name in sorted(required):
            spec = spec_for(name)
            if name not in params:
                missing.append(name)
                continue
            if spec is None:
                # Declaración huérfana: el catálogo no conoce este parámetro. El
                # motor no adivina -- lo trata como presente y el QA gate de la
                # capa de job lo reporta como fallo duro (ver jobs/discovery_brief_job.verify).
                continue
            value = params[name]
            if _is_malformed(spec, value):
                bad_type.append((name, _short_repr(value)))
                continue
            if _out_of_range(spec, value):
                bad_range.append((name, float(value)))
                continue
            faltan = _missing_subkeys(spec, value)
            if faltan:
                incomplete.append((name, faltan))

        if missing or bad_range or bad_type:
            state = RED
        elif incomplete:
            state = YELLOW
        else:
            state = GREEN
        out.append(ToolVerdict(
            tool.key, state,
            missing=tuple(missing),
            out_of_range=tuple(bad_range),
            incomplete=tuple(incomplete),
            malformed=tuple(bad_type),
        ))
    return tuple(out)


@dataclass(frozen=True)
class ParamGap:
    """Un parámetro genuinamente ausente, y cuánto compra preguntarlo."""

    param: str
    unlocks: int
    tools: tuple[str, ...]


def rank_gaps(verdicts: tuple[ToolVerdict, ...]) -> tuple[ParamGap, ...]:
    """Parámetros ausentes, ordenados por cuántas herramientas desbloquea cada uno.

    REGLA DE CAUSA UNICA: una herramienta solo acredita a un parámetro si ese
    parámetro es su UNICO obstáculo. Si le faltan dos, o si además tiene uno
    fuera de rango o uno malformado, pedir uno solo la deja roja igual --
    acreditarla prometería un desbloqueo que no va a ocurrir.

    ``sin_perfil`` no entra: ahí la acción es crear el perfil, no preguntar.
    Un solo ranker a propósito, para no repetir la regla en varios lugares (ver
    la deuda #1 declarada de intake_scan, donde cuatro rankers la
    reimplementaron y quedaron simétricos a mano).

    Empates por nombre de parámetro, para que dos corridas coincidan.
    """
    by_param: dict[str, list[str]] = {}
    for v in verdicts:
        if v.state != RED or v.out_of_range or v.malformed or len(v.missing) != 1:
            continue
        (param,) = v.missing
        by_param.setdefault(param, []).append(v.tool_key)
    gaps = [
        ParamGap(param, len(keys), tuple(sorted(keys)))
        for param, keys in by_param.items()
    ]
    return tuple(sorted(gaps, key=lambda g: (-g.unlocks, g.param)))


_TOP_GAPS = 3

_TRUTH_RESIDUAL = Residual(
    description=(
        "Esta auditoría dice si el parámetro ESTA cargado, nunca si es VERDAD. Un "
        "holding_rate de 0.25 en rango y cargado puede haber sido inventado por el "
        "analista; discovery_brief no lo puede detectar y no lo pretende."
    ),
    risk_if_skipped=(
        "Leer un verde como 'los números del cliente son correctos' es exactamente el "
        "falso verde que esta auditoría existe para evitar."
    ),
)


@dataclass(frozen=True)
class BriefReport:
    """La auditoría completa: qué corre hoy, qué falta y qué preguntar primero."""

    verdicts: tuple[ToolVerdict, ...]
    gaps: tuple[ParamGap, ...]
    undeclared_use: tuple[ParamSpec, ...]
    n_tools_evaluated: int
    n_tools_total: int
    has_profile: bool
    outcome: GuidedOutcome
    summary: str


def _gap_option(gap: ParamGap) -> ExecutionOption:
    spec = spec_for(gap.param)
    pregunta = spec.question if spec else gap.param
    mueve = spec.moves if spec else "efecto no catalogado"
    unidad = f" ({spec.unit}, entre {spec.low:g} y {spec.high:g})" if spec else ""
    return ExecutionOption(
        label=f"Preguntar '{gap.param}' al cliente{unidad}",
        summary=(
            f"{pregunta} Desbloquea {gap.unlocks} herramienta(s): "
            f"{', '.join(gap.tools)}. Si no lo dan: {mueve}"
        ),
        score=float(gap.unlocks),
        action=f"ask_param:{gap.param}",
        tradeoffs=(
            "Una pregunta, un número. Cargarlo sigue siendo un paso deliberado del "
            f"operador: client_profile.upsert_profile(..., {gap.param}=<valor>)"
        ),
    )


def _outcome(
    gaps: tuple[ParamGap, ...],
    verdicts: tuple[ToolVerdict, ...],
    has_profile: bool,
    client_id: str,
) -> GuidedOutcome:
    if not has_profile:
        return as_options(
            "El cliente no tiene perfil cargado: no hay número que auditar todavía.",
            [ExecutionOption(
                label="Crear el perfil del cliente",
                summary=(
                    "Ninguna herramienta puede tomar parámetros reales hasta que exista "
                    "el perfil. Crearlo primero; recién después tiene sentido rankear "
                    "qué preguntar."
                ),
                score=1.0,
                recommended=True,
                action=f"create_profile:{client_id}",
                tradeoffs="Un solo paso del operador, con los números que ya tenga a mano.",
            )],
            residuals=[_TRUTH_RESIDUAL],
        )

    n_evaluated = sum(1 for v in verdicts if v.state != UNDECLARED)
    if not gaps:
        if n_evaluated == 0:
            return as_executed(
                "Ninguna herramienta declara parámetros de cliente: no se evaluó nada, "
                "así que no hay cobertura que reclamar.",
                residuals=[_TRUTH_RESIDUAL],
            )
        yellow = [v for v in verdicts if v.state == YELLOW]
        if yellow:
            incomplete_params = sorted({name for v in yellow for name, _ in v.incomplete})
            return as_executed(
                f"Los parámetros declarados están todos presentes, pero {len(yellow)} "
                f"herramienta(s) corren con un parámetro granular incompleto: "
                f"{', '.join(incomplete_params)}. Cobertura parcial, no total: falta "
                "completar esas sub-claves.",
                residuals=[_TRUTH_RESIDUAL],
            )
        return as_executed(
            f"El perfil cubre todo lo declarado ({n_evaluated} herramienta(s) evaluada(s)); "
            "nada que preguntar.",
            residuals=[_TRUTH_RESIDUAL],
        )

    ranked = [_gap_option(g) for g in gaps[:_TOP_GAPS]]
    options = [replace(o, recommended=(i == 0)) for i, o in enumerate(ranked)]
    if len(gaps) > _TOP_GAPS:
        outcome_summary = (
            "Parámetros ausentes, ordenados por cuántas herramientas desbloquea cada uno; "
            f"mostrando los {_TOP_GAPS} de {len(gaps)}."
        )
    else:
        outcome_summary = (
            "Parámetros ausentes, ordenados por cuántas herramientas desbloquea cada uno."
        )
    return as_options(
        outcome_summary,
        options,
        residuals=[_TRUTH_RESIDUAL],
    )


def build_report(*, tools: list, params: dict | None, client_id: str = "") -> BriefReport:
    """Arma la auditoría. El evaluadas-sobre-total viaja en el summary, siempre."""
    verdicts = verdicts_for(tools, params)
    gaps = rank_gaps(verdicts)
    evaluated = sum(1 for v in verdicts if v.state != UNDECLARED)
    total = len(verdicts)
    counts = {
        state: sum(1 for v in verdicts if v.state == state)
        for state in (GREEN, YELLOW, RED, NO_PROFILE)
    }
    has_profile = params is not None
    if not has_profile:
        top = "el cliente no tiene perfil: crearlo es el paso previo a preguntar nada"
    elif gaps:
        top = f"lo más valioso: preguntar '{gaps[0].param}' desbloquea {gaps[0].unlocks}"
    else:
        top = "nada que preguntar"
    summary = (
        f"Evaluadas {evaluated} de {total} herramienta(s) registrada(s): "
        f"{counts[GREEN]} verde, {counts[YELLOW]} amarillo, {counts[RED]} rojo, "
        f"{counts[NO_PROFILE]} sin perfil; {top}."
    )
    return BriefReport(
        verdicts=verdicts,
        gaps=gaps,
        undeclared_use=UNDECLARED_USE,
        n_tools_evaluated=evaluated,
        n_tools_total=total,
        has_profile=has_profile,
        outcome=_outcome(gaps, verdicts, has_profile, client_id),
        summary=summary,
    )

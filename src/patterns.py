"""Motor de deteccion de patrones cronicos (tool #47, pattern_scan).

Funciones puras: entra una lista de :class:`Occurrence`, salen episodios,
perfiles de recurrencia y hallazgos de denominador comun. Este modulo NO sabe
si el dato vino de sqlite, de un CSV o de un ERP -- esa traduccion es de
``jobs/pattern_scan_job.py``. Es lo que permite testear la parte con casos
borde (huecos, duty cycle, correccion por comparaciones multiples) sin montar
una base ni el orquestador.

Spec: docs/superpowers/specs/2026-07-25-pattern-scan-design.md
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from scipy.stats import fisher_exact

# --------------------------------------------------------------------------
# Occurrence: el unico contrato que ve el motor
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Occurrence:
    """Una fila normalizada de historia.

    ``ts`` es SIEMPRE tz-aware UTC -- los adaptadores lo garantizan. ``signal``
    es el tipo crudo, sin normalizar: la agrupacion en familias es de este
    modulo (:func:`family_of`), no del adaptador, para que el CSV de un cliente
    con vocabulario propio corra en modo declarado en vez de ser adivinado.
    """

    entity_id: str
    signal: str
    ts: datetime
    severity: str | None = None
    source: str | None = None


# --------------------------------------------------------------------------
# Familias de senal
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalFamily:
    """Un grupo de senales que describen la MISMA condicion subyacente.

    ``escalation_signal`` es la senal que convierte al episodio en un
    episodio-FALLA. Cuando es ``None``, todo episodio de la familia cuenta como
    falla (no hay una version "benigna" de la condicion).
    """

    name: str
    signals: frozenset[str]
    escalation_signal: str | None


# rop_breach y stockout_projected son excluyentes por SKU en un mismo instante
# (ver scm_agent/monitors.py): sin agruparlos, el peor articulo del catalogo se
# parte en dos perfiles one_off y desaparece del reporte.
SIGNAL_FAMILIES: dict[str, SignalFamily] = {
    "availability": SignalFamily(
        name="availability",
        signals=frozenset({"rop_breach", "stockout_projected"}),
        escalation_signal="stockout_projected",
    ),
    "excess": SignalFamily(
        name="excess",
        signals=frozenset({"excess_growing"}),
        escalation_signal=None,
    ),
    "price": SignalFamily(
        name="price",
        signals=frozenset(
            {"price_move", "competitor_price_move", "competitor_oos", "promo_detected"}
        ),
        escalation_signal=None,
    ),
}

_FAMILY_BY_SIGNAL: dict[str, SignalFamily] = {
    signal: family for family in SIGNAL_FAMILIES.values() for signal in family.signals
}


def family_of(signal: str) -> str:
    """La familia de ``signal``, o la senal misma si no esta mapeada.

    El fallback es deliberado y se declara en el reporte: una senal desconocida
    (tipico del CSV de un cliente) corre como su propia familia -- modo
    ``signals-as-given``. Cero fuzzy matching: normalizar en silencio genera
    falsos amigos, que es exactamente lo que costo tres PRs de correccion en la
    trilogia de citas (#160/#162/#164).
    """
    family = _FAMILY_BY_SIGNAL.get(signal)
    return family.name if family is not None else signal


def _escalation_signal_for(family_name: str) -> str | None:
    family = SIGNAL_FAMILIES.get(family_name)
    return family.escalation_signal if family is not None else None


# --------------------------------------------------------------------------
# Episodios
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Episode:
    """Una corrida continua de ocurrencias de la misma familia sobre una entidad."""

    entity_id: str
    family: str
    start: datetime
    end: datetime
    span_hours: float
    n_occurrences: int
    peak_signal: str
    is_failure: bool
    boundary_certified: bool
    prior_gap_hours: float | None


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_episodes(
    occurrences: list[Occurrence],
    *,
    gap_hours: float,
    cadence_hours: float,
    certified_gap: Callable[[datetime, datetime], bool] | None = None,
) -> list[Episode]:
    """Agrupa las ocurrencias de UNA entidad y UNA familia en episodios.

    Un hueco parte episodio solo si es ``>= gap_hours`` **Y** esta certificado.
    ``certified_gap(desde, hasta)`` responde "hay evidencia independiente de que
    el sistema estaba vivo durante este hueco". Si devuelve ``False``, los dos
    lados quedan como UN episodio marcado ``boundary_certified=False``: un
    outage de la aplicacion convertiria un ``persistent`` (no estas reponiendo)
    en un ``chronic`` (buffer mal perfilado), que son diagnosticos OPUESTOS con
    arreglos opuestos. Fusionar es el error conservador; inventar recurrencia no.
    ``None`` = todo hueco se asume certificado (es el caso del CSV, donde no hay
    instrumentacion que consultar, y el reporte lo declara).

    ``cadence_hours`` es cuanto "dura" una sola ocurrencia: la ventana de dedup
    (1h) para el ledger, el intervalo mediano observado para el CSV. Sin esto un
    episodio de una unica ocurrencia valdria 0 horas y no contaria nunca para el
    duty cycle, escondiendo justo a la entidad que rompio una vez y quedo rota.
    """
    if not occurrences:
        return []

    entities = {o.entity_id for o in occurrences}
    if len(entities) > 1:
        raise ValueError(f"to_episodes expects one entity, got {sorted(entities)}")
    families = {family_of(o.signal) for o in occurrences}
    if len(families) > 1:
        raise ValueError(f"to_episodes expects one family, got {sorted(families)}")

    family = next(iter(families))
    escalation = _escalation_signal_for(family)
    ordered = sorted(occurrences, key=lambda o: _ensure_utc(o.ts))

    groups: list[list[Occurrence]] = [[ordered[0]]]
    boundaries: list[tuple[bool, float | None]] = [(True, None)]
    for previous, current in zip(ordered, ordered[1:]):
        gap = (_ensure_utc(current.ts) - _ensure_utc(previous.ts)).total_seconds() / 3600.0
        splits = gap >= gap_hours
        certified = True
        if splits and certified_gap is not None:
            certified = bool(certified_gap(_ensure_utc(previous.ts), _ensure_utc(current.ts)))
            splits = certified
        if splits:
            groups.append([current])
            boundaries.append((certified, gap))
        else:
            groups[-1].append(current)
            if gap >= gap_hours and not certified:
                boundaries[-1] = (False, boundaries[-1][1])

    episodes: list[Episode] = []
    for group, (certified, prior_gap) in zip(groups, boundaries):
        start = _ensure_utc(group[0].ts)
        end = _ensure_utc(group[-1].ts)
        span = (end - start).total_seconds() / 3600.0 + cadence_hours
        signals = [o.signal for o in group]
        is_failure = True if escalation is None else escalation in signals
        peak = escalation if (escalation is not None and escalation in signals) else signals[0]
        episodes.append(
            Episode(
                entity_id=group[0].entity_id,
                family=family,
                start=start,
                end=end,
                span_hours=span,
                n_occurrences=len(group),
                peak_signal=peak,
                is_failure=is_failure,
                boundary_certified=certified,
                prior_gap_hours=prior_gap,
            )
        )
    return episodes


# --------------------------------------------------------------------------
# Perfiles de recurrencia
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Thresholds:
    """Los umbrales de clasificacion. Todos se imprimen en el reporte."""

    chronic_min_episodes: int = 3
    persistent_duty_pct: float = 50.0
    episode_gap_hours: float = 48.0
    cadence_hours: float = 1.0

    def __post_init__(self) -> None:
        # Con chronic_min_episodes=1 todo one_off seria cronico y la banda
        # acute (2 <= n < chronic_min) quedaria indefinida en vez de vacia.
        object.__setattr__(self, "chronic_min_episodes", max(2, int(self.chronic_min_episodes)))


@dataclass(frozen=True)
class RecurrenceProfile:
    """Todo lo que sabemos de una (entidad, familia) dentro de la ventana.

    ``recurrence_label`` y ``is_persistent`` son ejes ORTOGONALES: una entidad
    puede ser ``one_off`` y persistente a la vez (un unico episodio que nunca
    cerro). Colapsarlos en una sola etiqueta fue el error del borrador -- manda
    la receta equivocada, porque re-perfilar un buffer no arregla que no estes
    reponiendo.
    """

    entity_id: str
    family: str
    episodes: tuple[Episode, ...]
    n_occurrences: int
    n_episodes: int
    n_failure_episodes: int
    escalation_rate: float
    duty_cycle: float
    days_since_last_episode: float
    n_boundaries_unknown: int
    recurrence_label: str
    is_persistent: bool
    is_oscillating: bool


def build_profiles(
    occurrences: list[Occurrence],
    *,
    window_start: datetime,
    window_end: datetime,
    thresholds: Thresholds,
    certified_gap: Callable[[datetime, datetime], bool] | None = None,
) -> list[RecurrenceProfile]:
    """Un perfil por (entidad, familia de senal).

    Las ocurrencias se filtran a ``window_start <= ts <= window_end`` (los dos
    bordes inclusive) ANTES de agrupar por entidad y familia. Sin este filtro
    ``duty_cycle`` no tiene techo: una entidad con historia que se extiende
    mucho mas alla de la ventana (p.ej. 150 dias de ocurrencias contra una
    ventana de 90) da un porcentaje sin sentido (166%) y arrastra
    ``is_persistent`` a ``True`` con el, sin que el llamador se haya
    equivocado en nada mas que en no recortar su propio input. Filtrar aca
    deja el duty cycle acotado por construccion en vez de depender de eso.

    Dos consecuencias del filtro, ambas intencionales:

    - Una entidad/familia cuyas ocurrencias caen TODAS fuera de la ventana no
      genera perfil -- ni vacio ni con datos. Nunca entra al ``dict`` de
      agrupacion, asi que simplemente no aparece en el resultado.
    - ``is_oscillating`` se resuelve al final, cuando ya estan todos los
      perfiles (ya filtrados): necesita saber si la MISMA entidad tuvo
      ``excess_growing`` en la ventana, y eso no se puede ver desde adentro
      del perfil de disponibilidad.
    """
    window_start = _ensure_utc(window_start)
    window_end = _ensure_utc(window_end)
    window_hours = (window_end - window_start).total_seconds() / 3600.0

    in_window = [o for o in occurrences if window_start <= _ensure_utc(o.ts) <= window_end]

    grouped: dict[tuple[str, str], list[Occurrence]] = {}
    for occurrence in in_window:
        grouped.setdefault((occurrence.entity_id, family_of(occurrence.signal)), []).append(
            occurrence
        )

    drafts: list[RecurrenceProfile] = []
    for (entity_id, family), group in sorted(grouped.items()):
        episodes = to_episodes(
            group,
            gap_hours=thresholds.episode_gap_hours,
            cadence_hours=thresholds.cadence_hours,
            certified_gap=certified_gap,
        )
        n_failures = sum(1 for e in episodes if e.is_failure)
        covered = sum(e.span_hours for e in episodes)
        duty = min(covered / window_hours * 100.0, 100.0) if window_hours > 0 else 0.0
        last_end = max(e.end for e in episodes)
        if n_failures >= thresholds.chronic_min_episodes:
            label = "chronic"
        elif n_failures >= 2:
            label = "acute"
        else:
            label = "one_off"
        drafts.append(
            RecurrenceProfile(
                entity_id=entity_id,
                family=family,
                episodes=tuple(episodes),
                n_occurrences=len(group),
                n_episodes=len(episodes),
                n_failure_episodes=n_failures,
                escalation_rate=(n_failures / len(episodes)) if episodes else 0.0,
                duty_cycle=duty,
                days_since_last_episode=(window_end - last_end).total_seconds() / 86400.0,
                n_boundaries_unknown=sum(1 for e in episodes if not e.boundary_certified),
                recurrence_label=label,
                is_persistent=duty >= thresholds.persistent_duty_pct,
                is_oscillating=False,
            )
        )

    entities_with_excess = {p.entity_id for p in drafts if p.family == "excess"}
    resolved: list[RecurrenceProfile] = []
    for profile in drafts:
        oscillating = (
            profile.family == "availability"
            and profile.entity_id in entities_with_excess
            and (profile.recurrence_label == "chronic" or profile.is_persistent)
        )
        resolved.append(replace(profile, is_oscillating=True) if oscillating else profile)
    return resolved


def is_chronic_or_worse(profile: RecurrenceProfile) -> bool:
    """Los perfiles que fundan un hallazgo de denominador comun y bloquean EXECUTED."""
    return (
        profile.recurrence_label == "chronic"
        or profile.is_persistent
        or profile.is_oscillating
    )


# --------------------------------------------------------------------------
# Denominador comun
# --------------------------------------------------------------------------

SIN_DATO = "<sin dato>"


@dataclass(frozen=True)
class DenominatorThresholds:
    """Los cuatro pisos del gate, mas los limites de forma de la dimension.

    Son cuatro cosas DISTINTAS y las cuatro tienen que cumplirse: exposicion
    (``min_base``), tamano de efecto (``min_chronics``), magnitud (``min_lift``)
    y evidencia (``fdr_q``). El borrador solo tenia las dos del medio, y por eso
    con 4 dimensiones de 10 valores y cero senal real encontraba un patron con
    probabilidad 1.000.
    """

    min_lift: float = 1.5
    min_chronics: int = 3
    min_base: int = 10
    fdr_q: float = 0.10
    max_values: int = 50
    overlap_pct: float = 80.0


@dataclass(frozen=True)
class GroupFinding:
    """Una concentracion de cronicos en un valor de una dimension."""

    dimension: str
    value: str
    n_v: int
    m_v: int
    chronic_total: int
    base_total: int
    lift: float
    excess: float
    p_value: float
    q_value: float
    entities: tuple[str, ...]
    aliases: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DimensionRejection:
    column: str
    reason: str


@dataclass(frozen=True)
class DenominatorResult:
    findings: tuple[GroupFinding, ...]
    absolute_counts: dict[str, dict[str, int]]
    dimensions_evaluated: tuple[str, ...]
    dimensions_rejected: tuple[DimensionRejection, ...]
    n_comparisons: int


def benjamini_hochberg(p_values: list[float]) -> list[float]:
    """q-valores de Benjamini-Hochberg, en el mismo orden que la entrada.

    Controla la tasa de falsos descubrimientos: con K comparaciones, exigir
    p<=0.05 en cada una garantiza falsos positivos cuando K crece. Se aplica la
    correccion monotona estandar (minimo acumulado desde el p mas grande).
    """
    k = len(p_values)
    if k == 0:
        return []
    order = sorted(range(k), key=lambda i: p_values[i])
    q_sorted = [0.0] * k
    running_min = 1.0
    for rank in range(k, 0, -1):
        index = order[rank - 1]
        candidate = min(1.0, p_values[index] * k / rank)
        running_min = min(running_min, candidate)
        q_sorted[index] = running_min
    return q_sorted


def _dimension_columns(attributes: dict[str, dict]) -> list[str]:
    columns: list[str] = []
    for row in attributes.values():
        for column in row:
            if column not in columns:
                columns.append(column)
    return columns


def find_denominators(
    profiles: list[RecurrenceProfile],
    attributes: dict[str, dict],
    *,
    base_entities: set[str],
    thresholds: DenominatorThresholds,
) -> DenominatorResult:
    """Que tienen en comun los cronicos.

    ``attributes`` mapea entidad -> {columna: valor}; toda columna es una
    dimension candidata y ningun nombre esta hardcodeado. Las entidades de
    ``base_entities`` sin dato para una dimension caen en el bucket
    :data:`SIN_DATO`, para que las shares sumen 1.0 -- ese bucket se reporta
    como calidad de dato pero NUNCA puede ser un hallazgo.
    """
    chronic_entities = {p.entity_id for p in profiles if is_chronic_or_worse(p)}
    chronic_in_base = chronic_entities & base_entities

    evaluated: list[str] = []
    rejected: list[DimensionRejection] = []
    absolute_counts: dict[str, dict[str, int]] = {}
    cells: list[tuple[str, str, int, int, float]] = []

    for column in _dimension_columns(attributes):
        value_of = {
            entity: str(attributes.get(entity, {}).get(column, SIN_DATO))
            if attributes.get(entity, {}).get(column, None) is not None
            else SIN_DATO
            for entity in base_entities
        }

        # Las tablas de conteo absoluto se publican SIEMPRE, pase o no el
        # gate de forma (uniforme/granular) que sigue mas abajo: son
        # base-independientes y garantizan que el valor que concentra mas
        # dolor no quede fuera del reporte por un artefacto de la eleccion de
        # base -- incluye el bucket <sin dato> como senal de calidad de dato.
        counts: dict[str, int] = {}
        for entity in chronic_in_base:
            counts[value_of[entity]] = counts.get(value_of[entity], 0) + 1
        absolute_counts[column] = dict(sorted(counts.items(), key=lambda kv: -kv[1]))

        distinct = {v for v in value_of.values() if v != SIN_DATO}
        if len(distinct) < 2:
            rejected.append(DimensionRejection(column, "demasiado uniforme"))
            continue
        if len(distinct) > thresholds.max_values:
            rejected.append(
                DimensionRejection(column, f"demasiado granular (k={len(distinct)} valores)")
            )
            continue

        evaluated.append(column)
        base_total = len(base_entities)
        chronic_total = len(chronic_in_base)
        for value in sorted(distinct):
            m_v = sum(1 for v in value_of.values() if v == value)
            n_v = counts.get(value, 0)
            if m_v < thresholds.min_base or n_v < thresholds.min_chronics:
                continue
            lift = (n_v / chronic_total) / (m_v / base_total) if chronic_total and m_v else 0.0
            if lift < thresholds.min_lift:
                continue
            table = [
                [n_v, m_v - n_v],
                [chronic_total - n_v, base_total - m_v - (chronic_total - n_v)],
            ]
            if table[1][1] < 0:
                continue
            _, p_value = fisher_exact(table, alternative="greater")
            cells.append((column, value, n_v, m_v, float(p_value)))

    n_comparisons = sum(
        1
        for column in evaluated
        for value in {
            str(attributes.get(e, {}).get(column))
            for e in base_entities
            if attributes.get(e, {}).get(column) is not None
        }
    )
    q_values = benjamini_hochberg([c[4] for c in cells])

    base_total = len(base_entities)
    chronic_total = len(chronic_in_base)
    survivors: list[GroupFinding] = []
    for (column, value, n_v, m_v, p_value), q_value in zip(cells, q_values):
        if q_value > thresholds.fdr_q:
            continue
        expected = chronic_total * m_v / base_total if base_total else 0.0
        entities = tuple(
            sorted(
                e
                for e in chronic_in_base
                if str(attributes.get(e, {}).get(column, SIN_DATO)) == value
            )
        )
        survivors.append(
            GroupFinding(
                dimension=column,
                value=value,
                n_v=n_v,
                m_v=m_v,
                chronic_total=chronic_total,
                base_total=base_total,
                lift=(n_v / chronic_total) / (m_v / base_total),
                excess=n_v - expected,
                p_value=p_value,
                q_value=q_value,
                entities=entities,
            )
        )

    # Ranking por exceso atribuible ("cuantos cronicos se evitarian"), no por
    # n*lift: esa expresion equivale a N*n^2/(C*m_v), o sea premia al
    # denominador chico al cuadrado y entierra al hallazgo que cubre mas gente.
    survivors.sort(key=lambda f: (-f.excess, f.q_value))
    collapsed = _collapse_overlapping(survivors, thresholds.overlap_pct)

    return DenominatorResult(
        findings=tuple(collapsed),
        absolute_counts=absolute_counts,
        dimensions_evaluated=tuple(evaluated),
        dimensions_rejected=tuple(rejected),
        n_comparisons=n_comparisons,
    )


def _collapse_overlapping(findings: list[GroupFinding], overlap_pct: float) -> list[GroupFinding]:
    """Colapsa hallazgos que describen al MISMO grupo de cronicos por otra columna.

    "proveedor=Alfa" y "marca=Alfa-brand" sobre los mismos 12 SKUs son un
    hallazgo, no dos. Se conserva el de mayor exposicion base (el mas
    accionable) y el resto viaja como alias, para no inflar el reporte ni
    sugerir que hay varias causas independientes.
    """
    kept: list[GroupFinding] = []
    for finding in findings:
        target = None
        for index, existing in enumerate(kept):
            shared = set(existing.entities) & set(finding.entities)
            smaller = min(len(existing.entities), len(finding.entities))
            if smaller and len(shared) / smaller * 100.0 >= overlap_pct:
                target = index
                break
        if target is None:
            kept.append(finding)
            continue
        existing = kept[target]
        if finding.m_v > existing.m_v:
            kept[target] = replace(
                finding, aliases=existing.aliases + ((existing.dimension, existing.value),)
            )
        else:
            kept[target] = replace(
                existing, aliases=existing.aliases + ((finding.dimension, finding.value),)
            )
    return kept

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
from dataclasses import dataclass
from datetime import datetime, timezone

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

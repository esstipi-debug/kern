# pattern_scan (tool #47) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Leer la historia del `EventLedger` (o un CSV del cliente) y separar el sintoma repetido — que pide un arreglo de politica — del incidente aislado, que pide un pedido puntual.

**Architecture:** Un motor puro en `src/patterns.py` que solo conoce el tipo `Occurrence` (entidad, senal, timestamp), y dos adaptadores en `jobs/pattern_scan_job.py` que lo alimentan desde sqlite o desde un CSV. El motor arma episodios a partir de huecos temporales certificados, los clasifica en dos ejes ortogonales (recurrencia y persistencia), y busca el denominador comun de los cronicos con un gate estadistico de cuatro pisos. La salida es un `GuidedOutcome` con opciones rankeadas; el tool no muta nada.

**Tech Stack:** Python 3.11+, pandas (solo en los adaptadores), scipy (`fisher_exact`, ya es dependencia core), sqlite3 (stdlib), pytest.

**Spec:** `docs/superpowers/specs/2026-07-25-pattern-scan-design.md`. Ante cualquier duda de contrato, el spec manda.

## Global Constraints

- **Python 3.11+.** Tests con `PYTHONPATH=.`, `pytest tests/ -q`. Lint: `ruff check src tests examples`.
- **ASCII-only en prints de consola** (cp1252 en Windows rompe con em dashes). Los `.md` en utf-8 estan bien.
- **Nunca un import de nivel de modulo de un extra opcional** en la cadena de arranque (`webapp.app` -> `scm_agent` -> `tools` -> `jobs` -> `src`). `scipy` y `pandas` SI son core y pueden importarse a nivel de modulo. Cualquier otra cosa va lazy, adentro de la funcion que la usa.
- **Regla 14, ningun cap silencioso.** Toda fila descartada, dimension rechazada o ventana acortada se cuenta y se publica en el report. Un descarte silencioso es un bug, no una optimizacion.
- **Nunca afirmar una causa que no se midio.** El copy de las opciones no puede decir "el buffer esta mal" cuando lo unico observado es recurrencia.
- **`escalation_rate` se llama "tasa de escalacion a cover critico"**, jamas *cycle service level*: el ledger no registra quiebre consumado ni recepciones.
- **Toda fecha es tz-aware UTC.** Ninguna ruta hace `.replace(tzinfo=None)`.
- **Workflow:** rama -> draft PR -> CI verde -> squash-merge. Nunca push directo a `main`.
- **Tool #47.** El pin de conteo global vive en `tests/test_price_watch_tool.py:165`.

---

### Task 1: PR-0b — backfill de how_it_works (41 -> 46)

Prerrequisito independiente. Hoy la pagina publica `/how-it-works` dice "41 capabilities" con 46 tools registrados. Sin este backfill, la Task 9 tendria que subir el contador de 41 a 42, que seria mentir dos veces.

Va en su propia rama y su propio PR: reasignar `domain_area` y `scor_bucket` de 5 tools es criterio, no mecanica.

**Files:**
- Modify: `webapp/how_it_works_data.py`
- Test: `tests/test_how_it_works_data.py`

**Interfaces:**
- Consumes: nada.
- Produces: `TOOLS` en `how_it_works_data.py` pasa a tener 46 entradas. La Task 9 le agrega la #47.

- [ ] **Step 1: Ver el shape exacto de una entrada y cuales faltan**

```bash
cd .wt-pattern-scan-spec
python - <<'EOF'
from webapp.how_it_works_data import TOOLS
from scm_agent.tools import build_default_registry
have = {t.key for t in TOOLS}
reg = {t.key for t in build_default_registry().list()}
print("en how_it_works:", len(have), "| registrados:", len(reg))
print("faltan:", sorted(reg - have))
print("sobran:", sorted(have - reg))
print("\nejemplo de entrada:")
print(TOOLS[0])
print("\ndomain_area en uso:", sorted({t.domain_area for t in TOOLS}))
print("scor_bucket en uso:", sorted({t.scor_bucket for t in TOOLS}))
EOF
```

Expected: `faltan: ['disruption_scan', 'hat_settlement', 'hat_tension', 'network_design', 'supplier_management']`, `sobran: []`.

Si `sobran` no esta vacio, PARA: hay drift en la otra direccion y este plan no lo cubre. Reporta y espera.

- [ ] **Step 2: Actualizar los tres asserts del test primero (TDD)**

En `tests/test_how_it_works_data.py`, subir el `len` esperado de 41 a 46 y ajustar los dos tallies (`test_domain_area_tally`, `test_scor_bucket_tally`) para incluir los 5 nuevos segun el criterio del Step 3.

- [ ] **Step 3: Correr el test para verlo fallar**

Run: `pytest tests/test_how_it_works_data.py -q`
Expected: FAIL — 3 tests, `assert 41 == 46` y los dos tallies.

- [ ] **Step 4: Agregar las 5 entradas**

Copiar el shape exacto de `TOOLS[0]` que imprimio el Step 1. Asignacion de `domain_area` / `scor_bucket` (criterio, siguiendo la asignacion de tools hermanos ya presentes en el archivo):

| key | domain_area | scor_bucket | razon |
|---|---|---|---|
| `disruption_scan` | el mismo que `risk` | el mismo que `risk` | alimenta el mismo motor `src/risk.py` |
| `supplier_management` | el mismo que `sourcing` | el mismo que `sourcing` | segmentacion Kraljic, es sourcing |
| `network_design` | el mismo que `facility_location` | el mismo que `facility_location` | p-median multi-facility, misma familia |
| `hat_tension` | el mismo que `sop` | el mismo que `sop` | mapa de tension entre decisiones, capa S&OP |
| `hat_settlement` | el mismo que `sop` | el mismo que `sop` | plan reconciliado, misma capa |

Leer el valor real de cada tool hermano en el archivo antes de escribir; no adivinar el literal.

- [ ] **Step 5: Correr los tests hasta verde**

Run: `pytest tests/test_how_it_works_data.py -q`
Expected: PASS, 3 tests.

- [ ] **Step 6: Verificar que la pagina no se rompio**

Run: `pytest tests/ -q -k how_it_works`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add webapp/how_it_works_data.py tests/test_how_it_works_data.py
git commit -m "fix(how-it-works): backfill 41 -> 46 capabilities (drift vs el registry)"
```

---

### Task 2: Lectura acotada del ledger (`list_between`) + port de digest_job

El unico lector del ledger hoy es `list_all()`, que trae la tabla entera y hace `json.loads` por fila sin indice por `ts`. `jobs/digest_job.py:48` ya usa ese antipatron en produccion. `pattern_scan` necesita leer 90 dias sin cargar todo.

**Files:**
- Modify: `scm_agent/events.py` (agregar `list_between`, `count_between`, `epoch`, indice `idx_events_ts`)
- Modify: `jobs/digest_job.py:46-48` (portar `_recent_events`)
- Test: `tests/test_events_range.py` (crear)

**Interfaces:**
- Consumes: `scm_agent.events.EventLedger`, `Event`, `_ensure_utc` (ya existen).
- Produces:
  - `EventLedger.list_between(start: datetime, end: datetime, *, types: tuple[str, ...] | None = None, limit: int | None = None) -> list[Event]` — oldest-first por `ts`.
  - `EventLedger.count_between(start: datetime, end: datetime, *, types: tuple[str, ...] | None = None) -> int`
  - `EventLedger.epoch() -> datetime | None` — el `ts` mas viejo del ledger, o `None` si esta vacio.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_events_range.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from scm_agent.events import Event, EventLedger


def _ev(key: str, ts: datetime, *, type_: str = "rop_breach") -> Event:
    return Event(type=type_, severity="medium", source="test", dedup_key=key, sku=key, ts=ts)


@pytest.fixture()
def ledger():
    led = EventLedger(":memory:", dedup_window_seconds=0.0)
    yield led
    led.close()


def test_list_between_excludes_rows_outside_the_window(ledger):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    for offset_days in (0, 5, 40):
        ledger.emit(_ev(f"sku-{offset_days}", base + timedelta(days=offset_days)))

    got = ledger.list_between(base + timedelta(days=1), base + timedelta(days=10))

    assert [e.sku for e in got] == ["sku-5"]


def test_list_between_orders_by_ts_not_insertion_order(ledger):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    ledger.emit(_ev("late", base + timedelta(days=3)))
    ledger.emit(_ev("early", base + timedelta(days=1)))

    got = ledger.list_between(base, base + timedelta(days=10))

    assert [e.sku for e in got] == ["early", "late"]


def test_list_between_end_is_exclusive_and_start_inclusive(ledger):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    ledger.emit(_ev("at-start", base))
    ledger.emit(_ev("at-end", base + timedelta(days=2)))

    got = ledger.list_between(base, base + timedelta(days=2))

    assert [e.sku for e in got] == ["at-start"]


def test_list_between_filters_by_type(ledger):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    ledger.emit(_ev("a", base, type_="rop_breach"))
    ledger.emit(_ev("b", base + timedelta(hours=1), type_="excess_growing"))

    got = ledger.list_between(base, base + timedelta(days=1), types=("excess_growing",))

    assert [e.sku for e in got] == ["b"]


def test_list_between_accepts_naive_datetimes_as_utc(ledger):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    ledger.emit(_ev("a", base + timedelta(days=1)))

    got = ledger.list_between(datetime(2026, 7, 1), datetime(2026, 7, 5))

    assert [e.sku for e in got] == ["a"]


def test_count_between_matches_list_between(ledger):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    for offset_days in (0, 1, 2, 40):
        ledger.emit(_ev(f"sku-{offset_days}", base + timedelta(days=offset_days)))

    window = (base, base + timedelta(days=10))

    assert ledger.count_between(*window) == len(ledger.list_between(*window))


def test_epoch_returns_the_oldest_ts(ledger):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    ledger.emit(_ev("late", base + timedelta(days=9)))
    ledger.emit(_ev("early", base))

    assert ledger.epoch() == base


def test_epoch_is_none_on_an_empty_ledger(ledger):
    assert ledger.epoch() is None


def test_list_between_does_not_parse_payload_json(ledger):
    """Una fila con payload corrupto se lee igual: list_between no hace json.loads."""
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    ledger.emit(_ev("ok", base))
    ledger._conn.execute("UPDATE events SET payload_json = ? WHERE sku = ?", ("{not json", "ok"))
    ledger._conn.commit()

    got = ledger.list_between(base, base + timedelta(days=1))

    assert [e.sku for e in got] == ["ok"]
    assert got[0].payload == {}
```

- [ ] **Step 2: Correr los tests para verlos fallar**

Run: `pytest tests/test_events_range.py -q`
Expected: FAIL — `AttributeError: 'EventLedger' object has no attribute 'list_between'`.

- [ ] **Step 3: Implementar en `scm_agent/events.py`**

En `EventLedger.__init__`, despues del `CREATE INDEX ... idx_events_type`, agregar:

```python
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
```

Y agregar estos metodos a la clase (despues de `list_recent`):

```python
    def list_between(
        self,
        start: datetime,
        end: datetime,
        *,
        types: tuple[str, ...] | None = None,
        limit: int | None = None,
    ) -> list[Event]:
        """Eventos con ``start <= ts < end``, mas viejo primero POR ``ts``.

        Deliberadamente distinto de ``list_all()`` (tabla entera, orden por ``seq``):
        una ventana de 90 dias no puede costar una lectura completa, y ``seq`` es el
        orden de INSERCION, que no coincide con el cronologico -- ``price_move`` se
        emite con ``ts=offer.observed_at`` mientras ``competitor_price_move``, emitido
        despues en el mismo ciclo, usa ``datetime.now()``.

        Los limites se comparan como texto ISO-8601. Es seguro porque TODA escritura
        pasa por ``emit()``, que normaliza con ``_ensure_utc(...).isoformat()`` y por lo
        tanto produce siempre el mismo offset fijo ``+00:00``: para ese formato el
        orden lexicografico y el cronologico coinciden. Un ``start``/``end`` naive se
        trata como UTC, igual que en ``emit()``.
        """
        query = (
            "SELECT id, type, severity, sku, source, payload_json, dedup_key, ts"
            " FROM events WHERE ts >= ? AND ts < ?"
        )
        params: list = [_ensure_utc(start).isoformat(), _ensure_utc(end).isoformat()]
        if types:
            query += " AND type IN (" + ",".join("?" * len(types)) + ")"
            params.extend(types)
        query += " ORDER BY ts ASC"
        if limit is not None:
            if limit < 1:
                raise ValueError(f"limit must be >= 1, got {limit}")
            query += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_event(r) for r in rows]

    def count_between(
        self, start: datetime, end: datetime, *, types: tuple[str, ...] | None = None
    ) -> int:
        """Cuantas filas devolveria ``list_between`` con los mismos limites (sin ``limit``).

        Existe para poder decidir ``needs_clarification`` por tamano ANTES de
        materializar la ventana entera en memoria.
        """
        query = "SELECT COUNT(*) FROM events WHERE ts >= ? AND ts < ?"
        params: list = [_ensure_utc(start).isoformat(), _ensure_utc(end).isoformat()]
        if types:
            query += " AND type IN (" + ",".join("?" * len(types)) + ")"
            params.extend(types)
        return int(self._conn.execute(query, params).fetchone()[0])

    def epoch(self) -> datetime | None:
        """El ``ts`` mas viejo registrado, o ``None`` si el ledger esta vacio.

        Es lo que permite decir "la historia arranca el X" en vez de dejar creer que
        una ventana de 90 dias vio 90 dias.
        """
        row = self._conn.execute("SELECT MIN(ts) FROM events").fetchone()
        if row is None or row[0] is None:
            return None
        return _ensure_utc(datetime.fromisoformat(row[0]))
```

Nota: `_row_to_event` SI hace `json.loads`. El test `test_list_between_does_not_parse_payload_json` exige que una fila corrupta no explote — hay que hacer `_row_to_event` tolerante. Cambiar su cuerpo:

```python
def _row_to_event(row: tuple) -> Event:
    id_, type_, severity, sku, source, payload_json, dedup_key, ts = row
    try:
        payload = json.loads(payload_json)
    except (ValueError, TypeError):
        # Una fila con payload ilegible sigue siendo una ocurrencia valida: el
        # analisis de patrones solo necesita (entidad, tipo, ts). Perder la fila
        # entera por un payload corrupto seria un cap silencioso.
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return Event(
        type=type_, severity=severity, source=source, dedup_key=dedup_key,
        sku=sku, payload=payload, id=id_, ts=datetime.fromisoformat(ts),
    )
```

- [ ] **Step 4: Correr los tests hasta verde**

Run: `pytest tests/test_events_range.py -q`
Expected: PASS, 9 tests.

- [ ] **Step 5: Portar `jobs/digest_job.py`**

Reemplazar `_recent_events` (lineas 46-48):

```python
def _recent_events(ledger: EventLedger, *, window_hours: float, now: datetime) -> list[Event]:
    # list_between (no list_all) para no cargar la tabla entera en cada digest:
    # con un ledger de meses, filtrar en Python 24h de eventos costaba leer todo.
    cutoff = now - timedelta(hours=window_hours)
    return ledger.list_between(cutoff, now + timedelta(seconds=1))
```

- [ ] **Step 6: Verificar que el digest sigue verde**

Run: `pytest tests/ -q -k "digest or events"`
Expected: PASS, sin regresiones.

- [ ] **Step 7: Suite completa + lint**

Run: `pytest tests/ -q`
Expected: PASS (3658+ tests).
Run: `ruff check src tests examples`
Expected: sin errores.

- [ ] **Step 8: Commit**

```bash
git add scm_agent/events.py jobs/digest_job.py tests/test_events_range.py
git commit -m "perf(events): list_between/count_between/epoch con indice por ts; portar digest_job"
```

---

### Task 3: Motor — `Occurrence`, familias de senal y episodios

Primera mitad del motor puro. Sin I/O: entra una lista de `Occurrence`, sale una lista de `Episode`.

**Files:**
- Create: `src/patterns.py`
- Test: `tests/test_patterns.py` (crear)

**Interfaces:**
- Consumes: nada del proyecto (stdlib + dataclasses).
- Produces:
  - `Occurrence(entity_id: str, signal: str, ts: datetime, severity: str | None = None, source: str | None = None)` — frozen dataclass.
  - `SIGNAL_FAMILIES: dict[str, SignalFamily]` y `family_of(signal: str) -> str`.
  - `SignalFamily(name: str, signals: frozenset[str], escalation_signal: str | None)`.
  - `Episode(entity_id, family, start, end, span_hours, n_occurrences, peak_signal, is_failure, boundary_certified, prior_gap_hours)` — frozen.
  - `to_episodes(occurrences: list[Occurrence], *, gap_hours: float, cadence_hours: float, certified_gap: Callable[[datetime, datetime], bool] | None = None) -> list[Episode]`.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_patterns.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from src.patterns import (
    Occurrence,
    family_of,
    to_episodes,
)

BASE = datetime(2026, 7, 1, tzinfo=timezone.utc)


def occ(hours: float, *, signal: str = "rop_breach", entity: str = "SKU-1") -> Occurrence:
    return Occurrence(entity_id=entity, signal=signal, ts=BASE + timedelta(hours=hours))


def test_family_of_groups_the_two_availability_signals():
    assert family_of("rop_breach") == "availability"
    assert family_of("stockout_projected") == "availability"


def test_family_of_falls_back_to_the_signal_itself_when_unknown():
    """Una senal del CSV del cliente que no conocemos es su propia familia."""
    assert family_of("quiebre_de_stock") == "quiebre_de_stock"


def test_a_continuous_run_is_one_episode():
    occs = [occ(h) for h in range(0, 10)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)

    assert len(episodes) == 1
    assert episodes[0].n_occurrences == 10


def test_a_certified_gap_splits_into_two_episodes():
    occs = [occ(0), occ(1), occ(100), occ(101)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0,
                           certified_gap=lambda a, b: True)

    assert len(episodes) == 2
    assert [e.n_occurrences for e in episodes] == [2, 2]


def test_an_uncertified_gap_does_not_split():
    """El sistema pudo haber estado caido: fusionar es la respuesta conservadora."""
    occs = [occ(0), occ(1), occ(100), occ(101)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0,
                           certified_gap=lambda a, b: False)

    assert len(episodes) == 1
    assert episodes[0].n_occurrences == 4
    assert episodes[0].boundary_certified is False


def test_gaps_below_the_threshold_never_split_regardless_of_certification():
    occs = [occ(0), occ(20)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0,
                           certified_gap=lambda a, b: True)

    assert len(episodes) == 1


def test_a_single_occurrence_episode_spans_one_cadence_unit_not_zero():
    episodes = to_episodes([occ(0)], gap_hours=48.0, cadence_hours=1.0)

    assert episodes[0].span_hours == pytest.approx(1.0)


def test_span_is_last_minus_first_plus_one_cadence_unit():
    occs = [occ(0), occ(1), occ(2)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)

    assert episodes[0].span_hours == pytest.approx(3.0)


def test_occurrences_are_sorted_by_ts_before_grouping():
    """Las filas pueden llegar desordenadas; el motor no confia en el orden de entrada."""
    occs = [occ(5), occ(0), occ(2)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)

    assert len(episodes) == 1
    assert episodes[0].start == BASE


def test_an_episode_containing_the_escalation_signal_is_a_failure_episode():
    occs = [occ(0), occ(1, signal="stockout_projected")]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)

    assert episodes[0].is_failure is True


def test_a_rop_only_episode_is_not_a_failure_episode():
    """Cruzar el ROP es el comportamiento disenado de revision continua."""
    occs = [occ(0), occ(1)]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)

    assert episodes[0].is_failure is False


def test_a_family_without_escalation_signal_counts_every_episode_as_failure():
    occs = [occ(0, signal="excess_growing")]

    episodes = to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)

    assert episodes[0].is_failure is True


def test_mixed_entities_raise_because_the_caller_must_group_first():
    occs = [occ(0, entity="A"), occ(1, entity="B")]

    with pytest.raises(ValueError, match="one entity"):
        to_episodes(occs, gap_hours=48.0, cadence_hours=1.0)


def test_empty_input_returns_no_episodes():
    assert to_episodes([], gap_hours=48.0, cadence_hours=1.0) == []
```

- [ ] **Step 2: Correr para verlos fallar**

Run: `pytest tests/test_patterns.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.patterns'`.

- [ ] **Step 3: Crear `src/patterns.py`**

```python
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
```

- [ ] **Step 4: Correr hasta verde**

Run: `pytest tests/test_patterns.py -q`
Expected: PASS, 13 tests.

- [ ] **Step 5: Lint**

Run: `ruff check src/patterns.py tests/test_patterns.py`
Expected: sin errores.

- [ ] **Step 6: Commit**

```bash
git add src/patterns.py tests/test_patterns.py
git commit -m "feat(patterns): Occurrence, familias de senal y episodios con huecos certificados"
```

---

### Task 4: Motor — perfiles de recurrencia y clasificacion

**Files:**
- Modify: `src/patterns.py` (agregar `RecurrenceProfile`, `Thresholds`, `build_profiles`)
- Modify: `tests/test_patterns.py` (agregar la clase de tests)

**Interfaces:**
- Consumes: `Occurrence`, `Episode`, `to_episodes`, `family_of` de la Task 3.
- Produces:
  - `Thresholds(chronic_min_episodes: int = 3, persistent_duty_pct: float = 50.0, episode_gap_hours: float = 48.0, cadence_hours: float = 1.0)` — frozen, con `__post_init__` que aplica `chronic_min_episodes = max(2, valor)`.
  - `RecurrenceProfile(entity_id, family, episodes, n_occurrences, n_episodes, n_failure_episodes, escalation_rate, duty_cycle, days_since_last_episode, n_boundaries_unknown, recurrence_label, is_persistent, is_oscillating)` — frozen. `recurrence_label` en `{"one_off", "acute", "chronic"}`.
  - `build_profiles(occurrences, *, window_start, window_end, thresholds, certified_gap=None) -> list[RecurrenceProfile]`.
  - `is_chronic_or_worse(profile) -> bool`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_patterns.py`:

```python
from src.patterns import Thresholds, build_profiles, is_chronic_or_worse

WINDOW_START = BASE
WINDOW_END = BASE + timedelta(days=90)
TH = Thresholds()


def _profiles(occs, **kwargs):
    return build_profiles(
        occs, window_start=WINDOW_START, window_end=WINDOW_END,
        thresholds=kwargs.pop("thresholds", TH), **kwargs
    )


def _failure_run(day: float, *, entity: str = "SKU-1") -> list[Occurrence]:
    """Un episodio-falla corto: cruza el ROP y escala a riesgo de quiebre."""
    return [
        occ(day * 24, entity=entity),
        occ(day * 24 + 1, signal="stockout_projected", entity=entity),
    ]


def test_three_failure_episodes_is_chronic():
    occs = _failure_run(0) + _failure_run(30) + _failure_run(60)

    profile = _profiles(occs, certified_gap=lambda a, b: True)[0]

    assert profile.n_failure_episodes == 3
    assert profile.recurrence_label == "chronic"


def test_two_failure_episodes_is_acute():
    occs = _failure_run(0) + _failure_run(30)

    profile = _profiles(occs, certified_gap=lambda a, b: True)[0]

    assert profile.recurrence_label == "acute"


def test_one_failure_episode_is_one_off():
    profile = _profiles(_failure_run(0), certified_gap=lambda a, b: True)[0]

    assert profile.recurrence_label == "one_off"


def test_rop_only_episodes_never_reach_chronic():
    """Seis cruces de ROP sin escalacion son ciclos de reposicion, no fallas."""
    occs = [o for day in (0, 15, 30, 45, 60, 75) for o in [occ(day * 24)]]

    profile = _profiles(occs, certified_gap=lambda a, b: True)[0]

    assert profile.n_episodes == 6
    assert profile.n_failure_episodes == 0
    assert profile.escalation_rate == 0.0
    assert profile.recurrence_label == "one_off"


def test_persistent_is_an_independent_axis_from_the_episode_count():
    """Un unico episodio que cubre media ventana es persistent, no one_off-y-listo."""
    occs = [occ(h) for h in range(0, 24 * 50, 6)]

    profile = _profiles(occs)[0]

    assert profile.n_episodes == 1
    assert profile.recurrence_label == "one_off"
    assert profile.is_persistent is True


def test_duty_cycle_is_covered_hours_over_window_hours():
    occs = [occ(h) for h in range(0, 24 * 45, 6)]

    profile = _profiles(occs)[0]

    assert profile.duty_cycle == pytest.approx(50.0, abs=1.0)


def test_oscillating_needs_availability_and_excess_on_the_same_entity():
    occs = (
        _failure_run(0) + _failure_run(30) + _failure_run(60)
        + [occ(70 * 24, signal="excess_growing")]
    )

    profiles = _profiles(occs, certified_gap=lambda a, b: True)
    availability = [p for p in profiles if p.family == "availability"][0]

    assert availability.is_oscillating is True


def test_excess_on_a_different_entity_does_not_make_it_oscillating():
    occs = (
        _failure_run(0) + _failure_run(30) + _failure_run(60)
        + [occ(70 * 24, signal="excess_growing", entity="OTRO-SKU")]
    )

    profiles = _profiles(occs, certified_gap=lambda a, b: True)
    availability = [p for p in profiles if p.entity_id == "SKU-1"][0]

    assert availability.is_oscillating is False


def test_uncertified_gaps_are_counted_and_reported():
    occs = _failure_run(0) + _failure_run(30)

    profile = _profiles(occs, certified_gap=lambda a, b: False)[0]

    assert profile.n_episodes == 1
    assert profile.n_boundaries_unknown == 1


def test_days_since_last_episode_is_measured_from_the_window_end():
    profile = _profiles(_failure_run(80), certified_gap=lambda a, b: True)[0]

    assert profile.days_since_last_episode == pytest.approx(10.0, abs=0.2)


def test_chronic_min_episodes_is_clamped_to_at_least_two():
    """Con 1, todo one_off seria cronico y la clase acute quedaria indefinida."""
    assert Thresholds(chronic_min_episodes=1).chronic_min_episodes == 2


def test_profiles_are_built_per_entity_and_family():
    occs = _failure_run(0, entity="A") + [occ(0, signal="excess_growing", entity="A")]

    profiles = _profiles(occs)

    assert {(p.entity_id, p.family) for p in profiles} == {("A", "availability"), ("A", "excess")}


def test_is_chronic_or_worse_covers_chronic_persistent_and_oscillating():
    chronic = _profiles(_failure_run(0) + _failure_run(30) + _failure_run(60),
                        certified_gap=lambda a, b: True)[0]
    one_off = _profiles(_failure_run(0), certified_gap=lambda a, b: True)[0]

    assert is_chronic_or_worse(chronic) is True
    assert is_chronic_or_worse(one_off) is False
```

- [ ] **Step 2: Correr para verlos fallar**

Run: `pytest tests/test_patterns.py -q`
Expected: FAIL — `ImportError: cannot import name 'Thresholds'`.

- [ ] **Step 3: Implementar en `src/patterns.py`**

Agregar al final del modulo:

```python
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

    ``is_oscillating`` se resuelve al final, cuando ya estan todos los perfiles:
    necesita saber si la MISMA entidad tuvo ``excess_growing`` en la ventana, y
    eso no se puede ver desde adentro del perfil de disponibilidad.
    """
    window_start = _ensure_utc(window_start)
    window_end = _ensure_utc(window_end)
    window_hours = (window_end - window_start).total_seconds() / 3600.0

    grouped: dict[tuple[str, str], list[Occurrence]] = {}
    for occurrence in occurrences:
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
        duty = (covered / window_hours * 100.0) if window_hours > 0 else 0.0
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
```

- [ ] **Step 4: Correr hasta verde**

Run: `pytest tests/test_patterns.py -q`
Expected: PASS, 26 tests.

- [ ] **Step 5: Lint y commit**

Run: `ruff check src/patterns.py tests/test_patterns.py`
Expected: sin errores.

```bash
git add src/patterns.py tests/test_patterns.py
git commit -m "feat(patterns): perfiles de recurrencia, duty cycle y clasificacion en dos ejes"
```

---

### Task 5: Motor — denominador comun con gate de cuatro pisos

La pieza que la revision adversarial reescribio entera. Sin el piso de evidencia, con 4 dimensiones de 10 valores y CERO senal real la probabilidad de un hallazgo espurio es 1.000: el tool ofreceria "atacar el denominador" en todos los clientes, siempre.

**Files:**
- Modify: `src/patterns.py`
- Modify: `tests/test_patterns.py`

**Interfaces:**
- Consumes: `RecurrenceProfile`, `is_chronic_or_worse` de la Task 4.
- Produces:
  - `DenominatorThresholds(min_lift=1.5, min_chronics=3, min_base=10, fdr_q=0.10, max_values=50, overlap_pct=80.0)` — frozen.
  - `GroupFinding(dimension, value, n_v, m_v, chronic_total, base_total, lift, excess, p_value, q_value, entities, aliases)` — frozen.
  - `DimensionRejection(column, reason)` — frozen.
  - `benjamini_hochberg(p_values: list[float]) -> list[float]`
  - `find_denominators(profiles, attributes, *, base_entities, thresholds) -> DenominatorResult`
  - `DenominatorResult(findings, absolute_counts, dimensions_evaluated, dimensions_rejected, n_comparisons)`

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_patterns.py`:

```python
from src.patterns import (
    DenominatorThresholds,
    benjamini_hochberg,
    find_denominators,
)

DTH = DenominatorThresholds()


def _chronic_profile(entity: str) -> RecurrenceProfile:
    occs = _failure_run(0, entity=entity) + _failure_run(30, entity=entity) + _failure_run(60, entity=entity)
    return _profiles(occs, certified_gap=lambda a, b: True)[0]


def test_benjamini_hochberg_is_monotone_and_never_below_the_raw_p():
    qs = benjamini_hochberg([0.001, 0.02, 0.5])

    assert qs == sorted(qs)
    assert all(q >= p for q, p in zip(qs, [0.001, 0.02, 0.5]))


def test_benjamini_hochberg_on_an_empty_list_returns_empty():
    assert benjamini_hochberg([]) == []


def test_a_real_concentration_is_found():
    """20 de 30 SKUs del proveedor Alfa son cronicos, contra 2 de 70 del resto."""
    chronic = [f"A{i}" for i in range(20)] + [f"B{i}" for i in range(2)]
    base = {f"A{i}" for i in range(30)} | {f"B{i}" for i in range(70)}
    attributes = {e: {"proveedor": "Alfa" if e.startswith("A") else "Beta"} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert [f.value for f in result.findings] == ["Alfa"]
    assert result.findings[0].n_v == 20
    assert result.findings[0].m_v == 30
    assert result.findings[0].lift > 1.5
    assert result.findings[0].q_value <= 0.10


def test_pure_noise_produces_no_finding():
    """Asignacion aleatoria fija: sin el gate de evidencia esto SI daba hallazgo."""
    base = {f"S{i}" for i in range(200)}
    chronic = [f"S{i}" for i in range(0, 200, 10)]
    attributes = {e: {"dim": f"v{int(e[1:]) % 10}"} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert result.findings == ()
    assert result.n_comparisons == 10


def test_a_value_below_the_exposure_floor_cannot_be_a_finding():
    """3 de 3 SKUs de un proveedor chiquito: lift altisimo, evidencia nula."""
    base = {f"A{i}" for i in range(3)} | {f"B{i}" for i in range(97)}
    chronic = [f"A{i}" for i in range(3)]
    attributes = {e: {"proveedor": "Chico" if e.startswith("A") else "Grande"} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert result.findings == ()


def test_absolute_counts_are_published_even_when_nothing_passes_the_gate():
    base = {f"A{i}" for i in range(3)} | {f"B{i}" for i in range(97)}
    chronic = [f"A{i}" for i in range(3)]
    attributes = {e: {"proveedor": "Chico" if e.startswith("A") else "Grande"} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert result.absolute_counts["proveedor"]["Chico"] == 3


def test_entities_without_master_data_land_in_the_sin_dato_bucket():
    base = {f"A{i}" for i in range(50)}
    chronic = [f"A{i}" for i in range(10)]
    attributes = {e: {"proveedor": "Alfa"} for e in list(base)[:25]}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert "<sin dato>" in result.absolute_counts["proveedor"]
    assert all(f.value != "<sin dato>" for f in result.findings)


def test_a_column_with_one_value_is_rejected_as_too_uniform():
    base = {f"A{i}" for i in range(50)}
    chronic = [f"A{i}" for i in range(10)]
    attributes = {e: {"pais": "CL"} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert ("pais", "demasiado uniforme") in [
        (r.column, r.reason) for r in result.dimensions_rejected
    ]


def test_a_column_with_too_many_values_is_rejected_as_too_granular():
    base = {f"A{i}" for i in range(60)}
    chronic = [f"A{i}" for i in range(10)]
    attributes = {e: {"codigo_interno": e} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    reasons = {r.column: r.reason for r in result.dimensions_rejected}
    assert "codigo_interno" in reasons
    assert "granular" in reasons["codigo_interno"]


def test_numeric_values_are_treated_as_discrete_labels():
    """lead_time_days {7, 45} tiene cardinalidad 2 y es la dimension mas predictiva."""
    base = {f"A{i}" for i in range(30)} | {f"B{i}" for i in range(70)}
    chronic = [f"A{i}" for i in range(20)] + [f"B{i}" for i in range(2)]
    attributes = {e: {"lead_time_days": 45 if e.startswith("A") else 7} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert [str(f.value) for f in result.findings] == ["45"]


def test_correlated_dimensions_collapse_into_one_finding_with_aliases():
    base = {f"A{i}" for i in range(30)} | {f"B{i}" for i in range(70)}
    chronic = [f"A{i}" for i in range(20)] + [f"B{i}" for i in range(2)]
    attributes = {
        e: {
            "proveedor": "Alfa" if e.startswith("A") else "Beta",
            "marca": "Alfa-brand" if e.startswith("A") else "Beta-brand",
        }
        for e in base
    }
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert len(result.findings) == 1
    assert len(result.findings[0].aliases) == 1


def test_ranking_is_by_attributable_excess_not_by_lift():
    """BIG gana por exceso (7.2 vs 5.0) aunque SML gane por lift (6.25 vs 2.5).

    Los numeros estan elegidos para que los dos criterios se CONTRADIGAN: si el
    ranking fuera por lift (o por n*lift), este test daria SML. Con C=24 sobre
    una base de 300: BIG 12-de-60 -> esperado 4.8, exceso 7.2; SML 6-de-12 ->
    esperado 0.96, exceso 5.04.
    """
    base = (
        {f"BIG{i}" for i in range(60)}
        | {f"SML{i}" for i in range(12)}
        | {f"OTH{i}" for i in range(228)}
    )
    chronic = (
        [f"BIG{i}" for i in range(12)] + [f"SML{i}" for i in range(6)]
        + [f"OTH{i}" for i in range(6)]
    )

    def group(entity: str) -> str:
        return entity[:3]

    attributes = {e: {"proveedor": group(e)} for e in base}
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, attributes, base_entities=base, thresholds=DTH)

    assert [f.value for f in result.findings][0] == "BIG"


def test_no_chronics_produces_no_findings_and_no_crash():
    base = {f"A{i}" for i in range(50)}
    attributes = {e: {"proveedor": "Alfa"} for e in base}

    result = find_denominators([], attributes, base_entities=base, thresholds=DTH)

    assert result.findings == ()


def test_no_master_data_at_all_evaluates_no_dimension():
    base = {f"A{i}" for i in range(50)}
    chronic = [f"A{i}" for i in range(10)]
    profiles = [_chronic_profile(e) for e in chronic]

    result = find_denominators(profiles, {}, base_entities=base, thresholds=DTH)

    assert result.dimensions_evaluated == ()
    assert result.findings == ()
```

- [ ] **Step 2: Correr para verlos fallar**

Run: `pytest tests/test_patterns.py -q`
Expected: FAIL — `ImportError: cannot import name 'DenominatorThresholds'`.

- [ ] **Step 3: Implementar en `src/patterns.py`**

Agregar el import de scipy arriba del todo (es dependencia core, no un extra opcional — es seguro a nivel de modulo):

```python
from scipy.stats import fisher_exact
```

Y al final del modulo:

```python
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
        counts: dict[str, int] = {}
        for entity in chronic_in_base:
            counts[value_of[entity]] = counts.get(value_of[entity], 0) + 1
        absolute_counts[column] = dict(sorted(counts.items(), key=lambda kv: -kv[1]))

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
```

- [ ] **Step 4: Correr hasta verde**

Run: `pytest tests/test_patterns.py -q`
Expected: PASS, 40 tests.

Si `test_pure_noise_produces_no_finding` falla, NO subas los umbrales para taparlo: significa que el gate de evidencia no esta cortando y hay que revisar `benjamini_hochberg`.

- [ ] **Step 5: Lint y commit**

Run: `ruff check src/patterns.py tests/test_patterns.py`

```bash
git add src/patterns.py tests/test_patterns.py
git commit -m "feat(patterns): denominador comun con Fisher + Benjamini-Hochberg y ranking por exceso"
```

---

### Task 6: Adaptadores — ledger, CSV y roster

**Files:**
- Create: `jobs/pattern_scan_job.py`
- Create: `tests/test_pattern_scan_job.py`
- Create: `tests/fixtures/patterns/history.csv`
- Create: `tests/fixtures/patterns/history_ambiguous_dates.csv`
- Create: `tests/fixtures/patterns/master.csv`

**Interfaces:**
- Consumes: `Occurrence` (Task 3); `EventLedger.list_between/epoch` (Task 2).
- Produces:
  - `HISTORY_SIGNALS: tuple[str, ...]` — allowlist de tipos consumidos.
  - `LoadResult(occurrences, n_rows_read, n_rows_dropped_bad_ts, n_rows_dropped_no_entity, n_rows_future_ts, ts_min, ts_max, date_format_applied, signal_vocabulary, source_kind)` — frozen. `source_kind` es `"ledger"` o `"csv"`: la Task 9 lo necesita para saber si puede certificar huecos, y adivinarlo por `date_format_applied` seria acoplar dos cosas que no tienen nada que ver.
  - `from_event_ledger(ledger, *, start, end) -> LoadResult`
  - `from_history_csv(path, *, now, date_format="dmy") -> LoadResult` — levanta `AmbiguousDateFormat` si el archivo es contradictorio.
  - `AmbiguousDateFormat(Exception)`
  - `load_master(path) -> dict[str, dict]`
  - `load_roster() -> set[str]` — vacio si no hay snapshot legible.
  - `certified_gap_from_ledger(ledger) -> Callable[[datetime, datetime], bool]`

- [ ] **Step 1: Crear los fixtures**

`tests/fixtures/patterns/history.csv`:

```csv
sku,tipo,fecha
SKU-1,rop_breach,01/07/2026
SKU-1,stockout_projected,01/07/2026
SKU-1,rop_breach,15/07/2026
SKU-1,stockout_projected,15/07/2026
SKU-2,excess_growing,03/07/2026
SKU-3,rop_breach,no-es-fecha
,rop_breach,04/07/2026
```

`tests/fixtures/patterns/history_ambiguous_dates.csv`:

```csv
sku,tipo,fecha
SKU-1,rop_breach,13/07/2026
SKU-2,rop_breach,07/25/2026
```

`tests/fixtures/patterns/master.csv`:

```csv
sku,proveedor,lead_time_days
SKU-1,Alfa,45
SKU-2,Beta,7
SKU-3,Alfa,45
```

- [ ] **Step 2: Escribir los tests que fallan**

Crear `tests/test_pattern_scan_job.py`:

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobs.pattern_scan_job import (
    AmbiguousDateFormat,
    certified_gap_from_ledger,
    from_event_ledger,
    from_history_csv,
    load_master,
)
from scm_agent.events import Event, EventLedger

FIXTURES = Path("tests/fixtures/patterns")
BASE = datetime(2026, 7, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


@pytest.fixture()
def ledger():
    led = EventLedger(":memory:", dedup_window_seconds=0.0)
    yield led
    led.close()


def test_csv_adapter_reads_the_valid_rows():
    result = from_history_csv(FIXTURES / "history.csv", now=NOW)

    assert len(result.occurrences) == 5
    assert {o.entity_id for o in result.occurrences} == {"SKU-1", "SKU-2"}


def test_csv_adapter_counts_the_bad_timestamp_instead_of_dropping_it_silently():
    result = from_history_csv(FIXTURES / "history.csv", now=NOW)

    assert result.n_rows_dropped_bad_ts == 1
    assert result.n_rows_read == 7


def test_csv_adapter_counts_the_missing_entity():
    result = from_history_csv(FIXTURES / "history.csv", now=NOW)

    assert result.n_rows_dropped_no_entity == 1


def test_csv_adapter_publishes_the_parsed_range_and_the_vocabulary():
    result = from_history_csv(FIXTURES / "history.csv", now=NOW)

    assert result.ts_min == datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert result.ts_max == datetime(2026, 7, 15, tzinfo=timezone.utc)
    assert set(result.signal_vocabulary) == {"rop_breach", "stockout_projected", "excess_growing"}


def test_a_contradictory_date_file_raises_instead_of_guessing():
    with pytest.raises(AmbiguousDateFormat):
        from_history_csv(FIXTURES / "history_ambiguous_dates.csv", now=NOW)


def test_future_timestamps_are_counted_and_excluded():
    result = from_history_csv(FIXTURES / "history.csv", now=BASE)

    assert result.n_rows_future_ts > 0
    assert result.ts_max <= BASE + timedelta(hours=48)


def test_ledger_adapter_maps_sku_to_entity_id(ledger):
    ledger.emit(Event(type="rop_breach", severity="medium", source="t",
                      dedup_key="k", sku="SKU-9", ts=BASE))

    result = from_event_ledger(ledger, start=BASE - timedelta(days=1), end=NOW)

    assert [o.entity_id for o in result.occurrences] == ["SKU-9"]


def test_ledger_adapter_falls_back_to_competitor_ref_then_domain(ledger):
    ledger.emit(Event(type="price_move", severity="low", source="t", dedup_key="a",
                      sku=None, payload={"competitor_sku_ref": "COMP-1"}, ts=BASE))
    ledger.emit(Event(type="price_move", severity="low", source="t", dedup_key="b",
                      sku=None, payload={"domain": "rival.cl"}, ts=BASE + timedelta(hours=1)))

    result = from_event_ledger(ledger, start=BASE - timedelta(days=1), end=NOW)

    assert [o.entity_id for o in result.occurrences] == ["COMP-1", "rival.cl"]


def test_ledger_adapter_drops_and_counts_rows_with_no_resolvable_entity(ledger):
    ledger.emit(Event(type="price_move", severity="low", source="t", dedup_key="c",
                      sku=None, payload={}, ts=BASE))

    result = from_event_ledger(ledger, start=BASE - timedelta(days=1), end=NOW)

    assert result.occurrences == ()
    assert result.n_rows_dropped_no_entity == 1


def test_ledger_adapter_ignores_health_events(ledger):
    ledger.emit(Event(type="site_degraded", severity="high", source="t",
                      dedup_key="d", sku="SKU-1", ts=BASE))

    result = from_event_ledger(ledger, start=BASE - timedelta(days=1), end=NOW)

    assert result.occurrences == ()


def test_certified_gap_is_true_when_other_rows_exist_in_the_window(ledger):
    ledger.emit(Event(type="rop_breach", severity="medium", source="t",
                      dedup_key="x", sku="OTRO", ts=BASE + timedelta(days=2)))
    certified = certified_gap_from_ledger(ledger)

    assert certified(BASE, BASE + timedelta(days=5)) is True


def test_certified_gap_is_false_when_the_ledger_was_silent(ledger):
    certified = certified_gap_from_ledger(ledger)

    assert certified(BASE, BASE + timedelta(days=5)) is False


def test_load_master_keys_by_entity_and_keeps_every_other_column():
    master = load_master(FIXTURES / "master.csv")

    assert master["SKU-1"] == {"proveedor": "Alfa", "lead_time_days": 45}
```

- [ ] **Step 3: Correr para verlos fallar**

Run: `pytest tests/test_pattern_scan_job.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobs.pattern_scan_job'`.

- [ ] **Step 4: Implementar los adaptadores**

Crear `jobs/pattern_scan_job.py` (esta task solo escribe la mitad de carga; la Task 7 agrega `run`/`verify`/`options`):

```python
"""pattern_scan (tool #47): adaptadores + orquestacion del job.

Traduce dos fuentes -- el EventLedger de Kern y un CSV de historia del cliente
-- al unico tipo que entiende ``src.patterns`` (``Occurrence``), corre el motor
y arma el deliverable. La logica de deteccion NO vive aca: aca vive todo lo que
sabe de formatos, rutas y sqlite.

Spec: docs/superpowers/specs/2026-07-25-pattern-scan-design.md
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from scm_agent.events import EventLedger
from src.patterns import Occurrence

# Tipos que el analisis de patrones consume. Los eventos de SALUD del ledger
# (site_degraded / stale_feed / extraction_failed) comparten tabla y quedan
# FUERA del analisis, pero SI cuentan como actividad para certificar huecos:
# prueban que el sistema estaba vivo aunque ese SKU no alertara.
HISTORY_SIGNALS: tuple[str, ...] = (
    "rop_breach",
    "stockout_projected",
    "excess_growing",
    "price_move",
    "competitor_price_move",
    "competitor_oos",
    "promo_detected",
)

_ENTITY_COLS = ("entity_id", "sku", "product_id", "item", "codigo", "material")
_SIGNAL_COLS = ("signal", "event_type", "tipo", "evento", "alerta", "motivo")
_TS_COLS = ("timestamp", "date", "fecha", "ts", "datetime", "fecha_hora")

# Un ts posterior a now + este margen es un error de dato (=TODAY(), seriales
# 1900 mal renderizados), no el futuro: inflaria el span y haria pasar el piso
# de historia minima con datos de tres dias.
FUTURE_TOLERANCE_HOURS = 48.0


DEFAULT_MAX_ROWS = 500_000


class AmbiguousDateFormat(Exception):
    """El archivo prueba dd/mm en una fila y mm/dd en otra: no hay lectura correcta."""


class WindowTooLarge(Exception):
    """La ventana pedida excede ``max_rows``; el llamador la achica."""


@dataclass(frozen=True)
class LoadResult:
    """Lo que una fuente entrego, con TODO lo que se descarto declarado."""

    occurrences: tuple[Occurrence, ...]
    n_rows_read: int
    n_rows_dropped_bad_ts: int
    n_rows_dropped_no_entity: int
    n_rows_future_ts: int
    ts_min: datetime | None
    ts_max: datetime | None
    date_format_applied: str
    signal_vocabulary: tuple[str, ...]
    source_kind: str = "csv"  # "ledger" | "csv" -- solo el ledger puede certificar huecos


def _pick_column(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    lowered = {str(c).strip().lower(): c for c in df.columns}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return None


def _detect_date_format(values: list[str], declared: str) -> str:
    """Decide la convencion UNA vez por archivo, no fila por fila.

    Solo se bloquea el archivo internamente CONTRADICTORIO. Bloquear uno
    perfectamente usable porque en teoria es ambiguo es peor que declarar el
    supuesto -- y el supuesto se imprime en el deliverable.
    """
    proves_dmy = proves_mdy = False
    for raw in values:
        parts = str(raw).replace("-", "/").split("/")
        if len(parts) != 3:
            continue
        first, second = parts[0].strip(), parts[1].strip()
        if first.isdigit() and int(first) > 12:
            proves_dmy = True
        if second.isdigit() and int(second) > 12:
            proves_mdy = True
    if proves_dmy and proves_mdy:
        raise AmbiguousDateFormat(
            "el archivo tiene fechas que prueban dd/mm Y otras que prueban mm/dd; "
            "pasa --date-format=dmy o --date-format=mdy"
        )
    if proves_mdy:
        return "mdy"
    if proves_dmy:
        return "dmy"
    return declared


def from_history_csv(
    path: str | Path, *, now: datetime, date_format: str = "dmy"
) -> LoadResult:
    """Adaptador del CSV de historia del cliente."""
    df = pd.read_csv(path, dtype=str).fillna("")
    entity_col = _pick_column(df, _ENTITY_COLS)
    signal_col = _pick_column(df, _SIGNAL_COLS)
    ts_col = _pick_column(df, _TS_COLS)
    missing = [
        name
        for name, col in (("entity_id", entity_col), ("signal", signal_col), ("timestamp", ts_col))
        if col is None
    ]
    if missing:
        raise ValueError(f"faltan columnas obligatorias en el CSV de historia: {missing}")

    applied = _detect_date_format(list(df[ts_col]), date_format)
    parsed = pd.to_datetime(
        df[ts_col], errors="coerce", dayfirst=(applied == "dmy"), format="mixed"
    )

    horizon = now + timedelta(hours=FUTURE_TOLERANCE_HOURS)
    occurrences: list[Occurrence] = []
    bad_ts = no_entity = future = 0
    vocabulary: list[str] = []
    for (_, row), ts in zip(df.iterrows(), parsed):
        entity = str(row[entity_col]).strip()
        if pd.isna(ts):
            bad_ts += 1
            continue
        if not entity:
            no_entity += 1
            continue
        stamp = ts.to_pydatetime()
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp > horizon:
            future += 1
            continue
        signal = str(row[signal_col]).strip()
        if signal not in vocabulary:
            vocabulary.append(signal)
        occurrences.append(Occurrence(entity_id=entity, signal=signal, ts=stamp))

    stamps = [o.ts for o in occurrences]
    return LoadResult(
        occurrences=tuple(occurrences),
        n_rows_read=len(df),
        n_rows_dropped_bad_ts=bad_ts,
        n_rows_dropped_no_entity=no_entity,
        n_rows_future_ts=future,
        ts_min=min(stamps) if stamps else None,
        ts_max=max(stamps) if stamps else None,
        date_format_applied=applied,
        signal_vocabulary=tuple(vocabulary),
        source_kind="csv",
    )


def from_event_ledger(ledger: EventLedger, *, start: datetime, end: datetime) -> LoadResult:
    """Adaptador del EventLedger de Kern."""
    events = ledger.list_between(start, end, types=HISTORY_SIGNALS)
    occurrences: list[Occurrence] = []
    no_entity = 0
    vocabulary: list[str] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        entity = (
            event.sku
            or payload.get("competitor_sku_ref")
            or payload.get("domain")
            or ""
        )
        entity = str(entity).strip()
        # Nunca se coerciona a "" ni a "None": una clase entera de eventos tiene
        # sku=None legitimo y todos colapsarian en UNA entidad vacia que gana el
        # ranking de cronicos con episodios que no son de nadie.
        if not entity:
            no_entity += 1
            continue
        if event.type not in vocabulary:
            vocabulary.append(event.type)
        occurrences.append(
            Occurrence(
                entity_id=entity,
                signal=event.type,
                ts=event.ts,
                severity=event.severity,
                source=event.source,
            )
        )

    stamps = [o.ts for o in occurrences]
    return LoadResult(
        occurrences=tuple(occurrences),
        n_rows_read=len(events),
        n_rows_dropped_bad_ts=0,
        n_rows_dropped_no_entity=no_entity,
        n_rows_future_ts=0,
        ts_min=min(stamps) if stamps else None,
        ts_max=max(stamps) if stamps else None,
        date_format_applied="iso",
        signal_vocabulary=tuple(vocabulary),
        source_kind="ledger",
    )


def certified_gap_from_ledger(ledger: EventLedger) -> Callable[[datetime, datetime], bool]:
    """Un hueco esta certificado si el ledger escribio CUALQUIER fila durante el.

    Es la prueba independiente de que el sistema estaba vivo. Sin ella no se
    puede distinguir "la condicion se resolvio" de "la app estuvo caida", y esa
    confusion invierte el diagnostico: un persistent se leeria como chronic.
    Incluye a proposito los eventos de salud, que son justamente los que siguen
    latiendo cuando nada mas alerta.
    """

    def certified(start: datetime, end: datetime) -> bool:
        return ledger.count_between(start, end) > 0

    return certified


def load_master(path: str | Path) -> dict[str, dict]:
    """CSV maestro -> {entidad: {columna: valor}}. Toda columna extra es dimension."""
    df = pd.read_csv(path)
    entity_col = _pick_column(df, _ENTITY_COLS)
    if entity_col is None:
        raise ValueError("el CSV maestro no tiene una columna de entidad reconocible")
    master: dict[str, dict] = {}
    for _, row in df.iterrows():
        entity = str(row[entity_col]).strip()
        if not entity:
            continue
        master[entity] = {
            str(col): row[col]
            for col in df.columns
            if col != entity_col and not pd.isna(row[col])
        }
    return master


def load_roster() -> set[str]:
    """Las entidades del snapshot ``stock`` mas reciente de ``src.state``.

    Vacio si no hay snapshot legible -- el llamador degrada a
    ``base_mode="observed"`` y lo declara. Import lazy: ``src.state`` arrastra
    su propio sqlite y este modulo esta en la cadena de arranque de la app.
    """
    try:
        from src.state.system_state import latest

        snapshot = latest("stock")
    except Exception:
        return set()
    if snapshot is None:
        return set()
    return {str(v).strip() for v in snapshot.payload["product_id"] if str(v).strip()}
```

- [ ] **Step 5: Correr hasta verde**

Run: `pytest tests/test_pattern_scan_job.py -q`
Expected: PASS, 13 tests.

- [ ] **Step 6: Verificar que no rompio el arranque de la app**

Run: `python -c "import webapp.app; print('boot ok')"`
Expected: `boot ok` sin traceback. Si falla con `ModuleNotFoundError`, algun import de nivel de modulo tiene que pasar a lazy.

- [ ] **Step 7: Lint y commit**

```bash
git add jobs/pattern_scan_job.py tests/test_pattern_scan_job.py tests/fixtures/patterns/
git commit -m "feat(pattern_scan): adaptadores de ledger, CSV de historia, maestro y roster"
```

---

### Task 7: Report, `run()`, `options()` y `verify()`

**Files:**
- Modify: `jobs/pattern_scan_job.py`
- Modify: `tests/test_pattern_scan_job.py`

**Interfaces:**
- Consumes: todo lo de las Tasks 3-6.
- Produces:
  - `PatternReport` — frozen, con: `profiles`, `denominator`, `outcome`, `window_days_requested`, `window_days_effective`, `window_note`, `data_quality_note`, `base_mode`, `n_entities_valid`, `master_provided`, `ledger_epoch`, `sensitivity_changes`, `thresholds_applied`, mas los contadores del `LoadResult`.
  - `run(load: LoadResult, *, master, roster, now, params) -> PatternReport`
  - `verify(report: PatternReport) -> list[str]`
  - `build_options(report) -> GuidedOutcome`

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_pattern_scan_job.py`:

```python
from dataclasses import replace

from jobs.pattern_scan_job import LoadResult, PatternReport, run, verify
from src.guided import EXECUTED, OPTIONS
from src.patterns import Occurrence


def _load(occurrences) -> LoadResult:
    stamps = [o.ts for o in occurrences]
    return LoadResult(
        occurrences=tuple(occurrences), n_rows_read=len(occurrences),
        n_rows_dropped_bad_ts=0, n_rows_dropped_no_entity=0, n_rows_future_ts=0,
        ts_min=min(stamps) if stamps else None, ts_max=max(stamps) if stamps else None,
        date_format_applied="iso", signal_vocabulary=("rop_breach",),
    )


def _runs(entity: str, days: list[int]):
    out = []
    for day in days:
        stamp = BASE + timedelta(days=day)
        out.append(Occurrence(entity_id=entity, signal="rop_breach", ts=stamp))
        out.append(Occurrence(entity_id=entity, signal="stockout_projected",
                              ts=stamp + timedelta(hours=1)))
    return out


def test_a_clean_scan_is_executed_and_never_says_ruido():
    report = run(_load(_runs("SKU-1", [0])), master={}, roster=set(),
                 now=BASE + timedelta(days=90), params={})

    assert report.outcome.status == EXECUTED
    assert "ruido" not in report.outcome.summary.lower()


def test_chronic_profiles_produce_options():
    report = run(_load(_runs("SKU-1", [0, 30, 60])), master={}, roster=set(),
                 now=BASE + timedelta(days=90), params={})

    assert report.outcome.status == OPTIONS
    assert len(report.outcome.options) >= 2


def test_a_persistent_profile_blocks_executed():
    occs = [Occurrence(entity_id="SKU-1", signal="rop_breach", ts=BASE + timedelta(hours=h))
            for h in range(0, 24 * 50, 6)]

    report = run(_load(occs), master={}, roster=set(), now=BASE + timedelta(days=90), params={})

    assert report.outcome.status == OPTIONS


def test_verify_is_empty_on_a_healthy_report():
    report = run(_load(_runs("SKU-1", [0, 30, 60])), master={}, roster=set(),
                 now=BASE + timedelta(days=90), params={})

    assert verify(report) == []


def test_verify_blocks_when_dropped_rows_are_not_disclosed():
    report = run(_load(_runs("SKU-1", [0, 30, 60])), master={}, roster=set(),
                 now=BASE + timedelta(days=90), params={})
    broken = replace(report, n_rows_dropped_bad_ts=3, data_quality_note="")

    assert any("descartada" in issue for issue in verify(broken))


def test_no_option_that_adds_inventory_cites_an_oscillating_entity():
    occs = _runs("SKU-1", [0, 30, 60]) + [
        Occurrence(entity_id="SKU-1", signal="excess_growing", ts=BASE + timedelta(days=70))
    ]

    report = run(_load(occs), master={}, roster=set(), now=BASE + timedelta(days=90), params={})

    buffer_options = [o for o in report.outcome.options if "buffer" in o.label.lower()]
    assert all("SKU-1" not in o.summary for o in buffer_options)
    assert verify(report) == []


def test_base_mode_falls_back_to_observed_without_a_roster():
    report = run(_load(_runs("SKU-1", [0, 30, 60])), master={}, roster=set(),
                 now=BASE + timedelta(days=90), params={})

    assert report.base_mode == "observed"


def test_base_mode_is_evaluated_when_a_roster_is_available():
    report = run(_load(_runs("SKU-1", [0, 30, 60])), master={}, roster={"SKU-1", "SKU-2"},
                 now=BASE + timedelta(days=90), params={})

    assert report.base_mode == "evaluated"


def test_the_sensitivity_run_reports_entities_whose_label_changes():
    """Tres episodios separados por 30h: con gap=48h se fusionan, con 24h no."""
    occs = _runs("SKU-1", [0]) + [
        Occurrence(entity_id="SKU-1", signal="stockout_projected",
                   ts=BASE + timedelta(hours=30)),
        Occurrence(entity_id="SKU-1", signal="stockout_projected",
                   ts=BASE + timedelta(hours=60)),
    ]

    report = run(_load(occs), master={}, roster=set(), now=BASE + timedelta(days=90),
                 params={}, certified_gap=lambda a, b: True)

    assert report.sensitivity_changes
```

- [ ] **Step 2: Correr para verlos fallar**

Run: `pytest tests/test_pattern_scan_job.py -q`
Expected: FAIL — `ImportError: cannot import name 'run'`.

- [ ] **Step 3: Implementar**

Agregar a `jobs/pattern_scan_job.py` los imports:

```python
from src.guided import ExecutionOption, GuidedOutcome, Residual, as_executed, as_options, verify_guided
from src.patterns import (
    DenominatorResult,
    DenominatorThresholds,
    RecurrenceProfile,
    Thresholds,
    build_profiles,
    find_denominators,
    is_chronic_or_worse,
)
```

Y al final:

```python
DEFAULT_WINDOW_DAYS = 90
DEFAULT_MIN_HISTORY_DAYS = 30
DEFAULT_GAP_ALT_HOURS = 24.0


@dataclass(frozen=True)
class PatternReport:
    """Todo lo que el scan encontro, con sus supuestos declarados."""

    profiles: tuple[RecurrenceProfile, ...]
    denominator: DenominatorResult
    outcome: GuidedOutcome
    window_days_requested: int
    window_days_effective: float
    window_note: str
    data_quality_note: str
    base_mode: str
    n_entities_valid: dict[str, int]
    master_provided: bool
    ledger_epoch: datetime | None
    sensitivity_changes: tuple[str, ...]
    thresholds_applied: dict
    n_rows_read: int
    n_rows_dropped_bad_ts: int
    n_rows_dropped_no_entity: int
    n_rows_future_ts: int
    n_boundaries_unknown: int
    n_profiles_censored: int


def _entities_label(entities: tuple[str, ...], limit: int = 5) -> str:
    shown = ", ".join(entities[:limit])
    return shown if len(entities) <= limit else f"{shown} (+{len(entities) - limit} mas)"


def build_options(report: PatternReport) -> GuidedOutcome:
    """Las opciones rankeadas. Ninguna afirma una causa que no se midio."""
    chronic = [p for p in report.profiles if p.recurrence_label == "chronic"]
    persistent = [p for p in report.profiles if p.is_persistent]
    acute = [p for p in report.profiles if p.recurrence_label == "acute"]
    oscillating = [p for p in report.profiles if p.is_oscillating]
    oscillating_ids = {p.entity_id for p in oscillating}

    if not (chronic or persistent or acute or oscillating):
        return as_executed(
            "sin patron recurrente en esta ventana: son incidentes aislados, tratarlos por "
            "incidente. Limitacion declarada: una ventana de "
            f"{report.window_days_requested} dias no puede observar recurrencia estacional "
            "ni ciclos de reposicion largos.",
            confidence=0.9,
            residuals=[
                Residual(
                    description="revisar los incidentes sueltos por severidad",
                    risk_if_skipped="un incidente aislado grave se pierde entre el ruido",
                )
            ],
        )

    options: list[ExecutionOption] = []

    if report.denominator.findings:
        top = report.denominator.findings[0]
        options.append(
            ExecutionOption(
                label=f"Atacar el denominador: {top.dimension}={top.value}",
                summary=(
                    f"{top.n_v} de {top.m_v} entidades con {top.dimension}={top.value} son "
                    f"cronicas (lift {top.lift:.1f}, q={top.q_value:.3f}); exceso atribuible "
                    f"{top.excess:.1f} entidades."
                ),
                score=0.9,
                action=f"revisar {top.dimension}={top.value} como causa estructural",
                tradeoffs="el arreglo mas estructural, pero es una negociacion, no un parametro",
            )
        )

    # Las entidades que oscilan NO pueden aparecer en una opcion que sume stock:
    # subirles el buffer amplifica el bullwhip y congela capital.
    safe_chronic = [p for p in chronic if p.entity_id not in oscillating_ids]
    if safe_chronic:
        ids = tuple(sorted({p.entity_id for p in safe_chronic}))
        options.append(
            ExecutionOption(
                label=f"Re-perfilar los buffers de {len(ids)} entidad(es) cronica(s)",
                summary=(
                    f"{_entities_label(ids)}. Verifica primero las fechas de los episodios: si "
                    "coinciden con promociones o temporada, el arreglo es un pre-build "
                    "estacional, NO subir el buffer base."
                ),
                score=0.7,
                action="revisar y re-dimensionar los buffers de esas entidades",
                tradeoffs="inmoviliza capital si la causa real es estacional",
            )
        )
        options.append(
            ExecutionOption(
                label=f"Correr inventory_optimization sobre {len(ids)} SKU(s)",
                summary=f"{_entities_label(ids)} con service_level=0.98.",
                score=0.5,
                action=f"correr inventory_optimization sobre {len(ids)} SKUs con service_level=0.98",
                tradeoffs=(
                    "el costo de este salto lo calcula ese tool; pattern_scan solo identifica "
                    "los SKUs por severidad y recurrencia"
                ),
            )
        )

    if persistent:
        ids = tuple(sorted({p.entity_id for p in persistent}))
        options.append(
            ExecutionOption(
                label=f"{len(ids)} entidad(es) no se estan reponiendo",
                summary=(
                    f"{_entities_label(ids)} con duty cycle sobre el umbral: revisar cadencia y "
                    "cantidad de pedido, no el buffer."
                ),
                score=0.75,
                action="revisar cadencia y cantidad de pedido de esas entidades",
                tradeoffs="requiere mirar el proceso de reposicion, no un parametro",
            )
        )

    if oscillating:
        ids = tuple(sorted(oscillating_ids))
        options.append(
            ExecutionOption(
                label=f"{len(ids)} entidad(es) oscilan entre quiebre y exceso",
                summary=(
                    f"{_entities_label(ids)}: el problema es cantidad y cadencia de pedido, no "
                    "el nivel. Subirles el buffer amplifica la oscilacion."
                ),
                score=0.95,
                action="revisar tamano de lote y cadencia de esas entidades",
                tradeoffs="ninguna: subir el nivel aca empeora el problema",
            )
        )

    if acute and not (chronic or persistent or oscillating):
        ids = tuple(sorted({p.entity_id for p in acute}))
        options.append(
            ExecutionOption(
                label=f"Watchlist de {len(ids)} entidad(es)",
                summary=f"{_entities_label(ids)} con 2 episodios; re-escanear en 30 dias.",
                score=0.6,
                action="agendar un nuevo pattern_scan en 30 dias",
                tradeoffs="no actua todavia; el patron puede consolidarse mientras tanto",
            )
        )

    return as_options(
        f"{len(report.profiles)} perfil(es) analizados; "
        f"{len(chronic)} cronico(s), {len(persistent)} persistente(s), "
        f"{len(oscillating)} oscilante(s).",
        options,
        confidence=0.8,
        residuals=[
            Residual(
                description="validar la causa raiz con el equipo de compras antes de mover parametros",
                risk_if_skipped="se corrige un sintoma cuya causa esta en el proveedor o en el calendario",
            )
        ],
    )


def run(
    load: LoadResult,
    *,
    master: dict[str, dict],
    roster: set[str],
    now: datetime,
    params: dict,
    certified_gap: Callable[[datetime, datetime], bool] | None = None,
    ledger_epoch: datetime | None = None,
) -> PatternReport:
    """Motor + politica de reporte. No toca disco ni red."""
    window_days = int(params.get("window_days", DEFAULT_WINDOW_DAYS))
    thresholds = Thresholds(
        chronic_min_episodes=int(params.get("chronic_min_episodes", 3)),
        persistent_duty_pct=float(params.get("persistent_duty_pct", 50.0)),
        episode_gap_hours=float(params.get("episode_gap_hours", 48.0)),
        cadence_hours=float(params.get("cadence_hours", 1.0)),
    )
    denominator_thresholds = DenominatorThresholds(
        min_lift=float(params.get("denominator_min_lift", 1.5)),
        min_chronics=int(params.get("denominator_min_chronics", 3)),
        min_base=int(params.get("denominator_min_base", 10)),
        fdr_q=float(params.get("denominator_fdr_q", 0.10)),
        max_values=int(params.get("dimension_max_values", 50)),
        overlap_pct=float(params.get("finding_overlap_pct", 80.0)),
    )

    window_end = now
    window_start = now - timedelta(days=window_days)
    effective_start = max(window_start, load.ts_min) if load.ts_min else window_start
    effective_days = (window_end - effective_start).total_seconds() / 86400.0
    window_note = ""
    if effective_days < window_days - 0.5:
        window_note = (
            f"la ventana pedida de {window_days} dias cubre solo {effective_days:.0f} dias "
            "reales de historia"
        )

    profiles = build_profiles(
        list(load.occurrences),
        window_start=effective_start,
        window_end=window_end,
        thresholds=thresholds,
        certified_gap=certified_gap,
    )

    alt = Thresholds(
        chronic_min_episodes=thresholds.chronic_min_episodes,
        persistent_duty_pct=thresholds.persistent_duty_pct,
        episode_gap_hours=float(params.get("episode_gap_hours_alt", DEFAULT_GAP_ALT_HOURS)),
        cadence_hours=thresholds.cadence_hours,
    )
    alt_labels = {
        (p.entity_id, p.family): p.recurrence_label
        for p in build_profiles(
            list(load.occurrences), window_start=effective_start, window_end=window_end,
            thresholds=alt, certified_gap=certified_gap,
        )
    }
    sensitivity = tuple(
        f"{p.entity_id}/{p.family}: {p.recurrence_label} -> "
        f"{alt_labels[(p.entity_id, p.family)]} con gap={alt.episode_gap_hours:g}h"
        for p in profiles
        if alt_labels.get((p.entity_id, p.family), p.recurrence_label) != p.recurrence_label
    )

    observed = {p.entity_id for p in profiles}
    base_mode = "evaluated" if roster else "observed"
    base_entities = (roster | observed) if roster else observed
    censored = observed - roster if roster else set()

    denominator = find_denominators(
        profiles, master, base_entities=base_entities, thresholds=denominator_thresholds
    )

    quality_bits = []
    if load.n_rows_dropped_bad_ts:
        quality_bits.append(f"{load.n_rows_dropped_bad_ts} fila(s) descartada(s) por fecha invalida")
    if load.n_rows_dropped_no_entity:
        quality_bits.append(f"{load.n_rows_dropped_no_entity} fila(s) descartada(s) sin entidad")
    if load.n_rows_future_ts:
        quality_bits.append(f"{load.n_rows_future_ts} fila(s) con fecha futura excluida(s)")

    # build_options necesita el report ya armado (mira profiles, denominator y la
    # ventana), y el report lleva el outcome adentro. Se construye en dos pasos a
    # proposito, con un outcome provisional que NUNCA sale de esta funcion: el
    # return de abajo lo reemplaza por el definitivo. La alternativa (pasarle a
    # build_options siete argumentos sueltos) duplica la firma del report.
    draft_outcome = as_executed("outcome provisional, reemplazado antes del return")
    report = PatternReport(
        profiles=tuple(profiles),
        denominator=denominator,
        outcome=draft_outcome,
        window_days_requested=window_days,
        window_days_effective=effective_days,
        window_note=window_note,
        data_quality_note="; ".join(quality_bits),
        base_mode=base_mode,
        n_entities_valid={d: len(base_entities) for d in denominator.dimensions_evaluated},
        master_provided=bool(master),
        ledger_epoch=ledger_epoch,
        sensitivity_changes=sensitivity,
        thresholds_applied={
            "window_days": window_days,
            "episode_gap_hours": thresholds.episode_gap_hours,
            "episode_gap_hours_alt": alt.episode_gap_hours,
            "chronic_min_episodes": thresholds.chronic_min_episodes,
            "persistent_duty_pct": thresholds.persistent_duty_pct,
            "denominator_min_lift": denominator_thresholds.min_lift,
            "denominator_min_chronics": denominator_thresholds.min_chronics,
            "denominator_min_base": denominator_thresholds.min_base,
            "denominator_fdr_q": denominator_thresholds.fdr_q,
        },
        n_rows_read=load.n_rows_read,
        n_rows_dropped_bad_ts=load.n_rows_dropped_bad_ts,
        n_rows_dropped_no_entity=load.n_rows_dropped_no_entity,
        n_rows_future_ts=load.n_rows_future_ts,
        n_boundaries_unknown=sum(p.n_boundaries_unknown for p in profiles),
        n_profiles_censored=len(censored),
    )
    return replace(report, outcome=build_options(report))


def verify(report: PatternReport) -> list[str]:
    """Gate de QA. Primera linea: el contrato never-unprotected compartido."""
    issues = list(verify_guided(report.outcome))

    for profile in report.profiles:
        if not profile.entity_id or profile.entity_id in ("None", "nan"):
            issues.append(f"entidad vacia en un perfil de familia {profile.family}")
        for previous, current in zip(profile.episodes, profile.episodes[1:]):
            if current.start < previous.end:
                issues.append(f"episodios desordenados en {profile.entity_id}")

    chronic_ids = {p.entity_id for p in report.profiles if is_chronic_or_worse(p)}
    for finding in report.denominator.findings:
        if not set(finding.entities) <= chronic_ids:
            issues.append(f"hallazgo {finding.dimension}={finding.value} cita entidades no cronicas")
        if finding.base_total <= 0 or finding.lift != finding.lift:
            issues.append(f"hallazgo {finding.dimension}={finding.value} con base o lift invalido")
        if finding.dimension not in report.denominator.absolute_counts:
            issues.append(f"hallazgo en {finding.dimension} sin tabla de conteos absolutos")

    if (report.n_rows_dropped_bad_ts or report.n_rows_dropped_no_entity
            or report.n_rows_future_ts) and not report.data_quality_note.strip():
        issues.append("hay filas descartada(s) sin declarar en el reporte")
    if report.window_days_effective < report.window_days_requested - 0.5 and not report.window_note.strip():
        issues.append("ventana efectiva menor a la pedida sin declararlo")
    if report.master_provided and not (
        report.denominator.dimensions_evaluated or report.denominator.dimensions_rejected
    ):
        issues.append("maestro provisto pero ninguna dimension evaluada ni rechazada")

    oscillating = {p.entity_id for p in report.profiles if p.is_oscillating}
    for option in report.outcome.options:
        if "buffer" in option.label.lower() or "service_level" in option.action:
            if any(entity in option.summary for entity in oscillating):
                issues.append("una opcion que suma inventario cita una entidad oscilante")

    if report.outcome.status == "executed" and any(
        is_chronic_or_worse(p) or p.recurrence_label == "acute" for p in report.profiles
    ):
        issues.append("EXECUTED con perfiles cronicos, persistentes, agudos u oscilantes")

    for profile in report.profiles:
        if (profile.family == "availability" and profile.recurrence_label == "chronic"
                and profile.escalation_rate == 0.0):
            issues.append(
                f"{profile.entity_id}: cronico de disponibilidad con tasa de escalacion 0 "
                "no puede reportarse como falla de servicio"
            )

    return issues
```

- [ ] **Step 4: Correr hasta verde**

Run: `pytest tests/test_pattern_scan_job.py -q`
Expected: PASS, 22 tests.

- [ ] **Step 5: Lint y commit**

```bash
git add jobs/pattern_scan_job.py tests/test_pattern_scan_job.py
git commit -m "feat(pattern_scan): PatternReport, opciones guiadas y gate de QA"
```

---

### Task 8: Deliverables (`prepare`, `write_operational`, `build_deck`)

**Files:**
- Modify: `jobs/pattern_scan_job.py`
- Modify: `tests/test_pattern_scan_job.py`

**Interfaces:**
- Consumes: `PatternReport` (Task 7), `LoadResult` (Task 6).
- Produces:
  - `prepare(data_path: str | None, params: dict) -> LoadResult` — abre la fuente que corresponda.
  - `write_operational(report, out_dir, client="Client") -> dict[str, Path]`
  - `build_deck(report, *, client="Client", prepared="", citations=(), confidence=0.0, options=()) -> dict[str, Path]`

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_pattern_scan_job.py`:

```python
from jobs.pattern_scan_job import build_deck, write_operational


def test_write_operational_emits_one_row_per_profile(tmp_path):
    report = run(_load(_runs("SKU-1", [0, 30, 60])), master={}, roster=set(),
                 now=BASE + timedelta(days=90), params={})

    paths = write_operational(report, tmp_path)

    body = list(paths.values())[0].read_text(encoding="utf-8")
    assert "SKU-1" in body
    assert "chronic" in body


def test_the_deck_prints_the_thresholds_and_the_data_quality_note(tmp_path):
    load = LoadResult(
        occurrences=tuple(_runs("SKU-1", [0, 30, 60])), n_rows_read=12,
        n_rows_dropped_bad_ts=2, n_rows_dropped_no_entity=0, n_rows_future_ts=0,
        ts_min=BASE, ts_max=BASE + timedelta(days=60), date_format_applied="dmy",
        signal_vocabulary=("rop_breach",),
    )
    report = run(load, master={}, roster=set(), now=BASE + timedelta(days=90), params={})

    paths = build_deck(report, client="Acme")

    body = list(paths.values())[0].read_text(encoding="utf-8")
    assert "chronic_min_episodes" in body
    assert "fecha invalida" in body


def test_the_deck_prints_episode_dates_so_a_human_can_spot_a_promo_pattern(tmp_path):
    report = run(_load(_runs("SKU-1", [0, 30, 60])), master={}, roster=set(),
                 now=BASE + timedelta(days=90), params={})

    paths = build_deck(report)

    body = list(paths.values())[0].read_text(encoding="utf-8")
    assert "2026-07-01" in body


def test_prepare_refuses_a_window_that_exceeds_max_rows(tmp_path):
    """Se detecta con count_between, ANTES de materializar la ventana en memoria."""
    from jobs.pattern_scan_job import WindowTooLarge, prepare

    path = tmp_path / "events.sqlite3"
    led = EventLedger(path, dedup_window_seconds=0.0)
    for i in range(5):
        led.emit(Event(type="rop_breach", severity="medium", source="t",
                       dedup_key=f"k{i}", sku=f"SKU-{i}", ts=BASE + timedelta(hours=i)))
    led.close()

    with pytest.raises(WindowTooLarge, match="window_days="):
        prepare(None, {"events_path": str(path), "max_rows": 2,
                       "now": BASE + timedelta(days=1), "window_days": 90})


def test_the_deck_declares_the_base_mode(tmp_path):
    report = run(_load(_runs("SKU-1", [0, 30, 60])), master={}, roster=set(),
                 now=BASE + timedelta(days=90), params={})

    body = list(build_deck(report).values())[0].read_text(encoding="utf-8")
    assert "observed" in body
```

- [ ] **Step 2: Correr para verlos fallar**

Run: `pytest tests/test_pattern_scan_job.py -q`
Expected: FAIL — `ImportError: cannot import name 'write_operational'`.

- [ ] **Step 3: Implementar**

Agregar a `jobs/pattern_scan_job.py`:

```python
def prepare(data_path: str | None, params: dict | None = None) -> LoadResult:
    """Abre la fuente: CSV si vino ``data_path``, si no el EventLedger.

    El chequeo de existencia es ANTES de construir el ledger a proposito:
    ``EventLedger.__init__`` hace mkdir + CREATE TABLE IF NOT EXISTS, o sea que
    con una ruta inexistente fabricaria una base vacia y el tool declararia
    "scan limpio" sobre absolutamente nada.
    """
    params = params or {}
    now = params.get("now") or datetime.now(timezone.utc)
    if data_path:
        return from_history_csv(
            data_path, now=now, date_format=str(params.get("date_format", "dmy"))
        )

    from scm_agent.events import DEFAULT_PATH as EVENTS_DEFAULT_PATH

    events_path = str(params.get("events_path") or EVENTS_DEFAULT_PATH)
    if not Path(events_path).exists():
        raise FileNotFoundError(events_path)
    ledger = EventLedger(events_path)
    try:
        window_days = int(params.get("window_days", DEFAULT_WINDOW_DAYS))
        start = now - timedelta(days=window_days)
        max_rows = int(params.get("max_rows", DEFAULT_MAX_ROWS))
        # count_between ANTES de materializar: si la ventana no entra en memoria,
        # el llamador tiene que achicarla, no descubrirlo con un MemoryError.
        n_rows = ledger.count_between(start, now, types=HISTORY_SIGNALS)
        if n_rows > max_rows:
            suggested = max(1, int(window_days * max_rows / n_rows))
            raise WindowTooLarge(
                f"la ventana de {window_days} dias tiene {n_rows} filas (tope {max_rows}); "
                f"proba con window_days={suggested}"
            )
        return from_event_ledger(ledger, start=start, end=now)
    finally:
        ledger.close()


def _label_of(profile: RecurrenceProfile) -> str:
    if profile.is_oscillating:
        return "oscillating"
    if profile.is_persistent:
        return "persistent"
    return profile.recurrence_label


def write_operational(
    report: PatternReport, out_dir: str | Path, client: str = "Client"
) -> dict[str, Path]:
    """Una fila por perfil: la version que se abre en Excel."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "entity_id": p.entity_id,
            "family": p.family,
            "label": _label_of(p),
            "n_episodes": p.n_episodes,
            "n_failure_episodes": p.n_failure_episodes,
            "escalation_rate": round(p.escalation_rate, 3),
            "duty_cycle_pct": round(p.duty_cycle, 1),
            "days_since_last_episode": round(p.days_since_last_episode, 1),
            "boundaries_unknown": p.n_boundaries_unknown,
            "first_episode": p.episodes[0].start.date().isoformat() if p.episodes else "",
            "last_episode": p.episodes[-1].end.date().isoformat() if p.episodes else "",
        }
        for p in sorted(
            report.profiles,
            key=lambda p: (-p.n_failure_episodes, -p.duty_cycle, p.entity_id),
        )
    ]
    path = directory / "pattern_scan_perfiles.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return {"perfiles": path}


def build_deck(
    report: PatternReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple = (),
    confidence: float = 0.0,
    options: tuple = (),
) -> dict[str, Path]:
    """El deck narrativo. Todo supuesto se imprime; nada se descarta callado."""
    lines: list[str] = [
        f"# Escaneo de patrones cronicos - {client}",
        "",
        f"Ventana pedida: {report.window_days_requested} dias | "
        f"efectiva: {report.window_days_effective:.0f} dias | "
        f"base: {report.base_mode}",
        "",
    ]
    if report.window_note:
        lines += [f"> {report.window_note}", ""]
    if report.data_quality_note:
        lines += [f"> Calidad de dato: {report.data_quality_note}", ""]
    if report.ledger_epoch:
        lines += [f"> Historia disponible desde {report.ledger_epoch.date().isoformat()}.", ""]

    lines += ["## Perfiles", ""]
    for profile in sorted(report.profiles, key=lambda p: (-p.n_failure_episodes, p.entity_id)):
        dates = ", ".join(e.start.date().isoformat() for e in profile.episodes)
        lines.append(
            f"- **{profile.entity_id}** ({profile.family}): {_label_of(profile)} | "
            f"{profile.n_failure_episodes} de {profile.n_episodes} episodio(s) con escalacion | "
            f"duty cycle {profile.duty_cycle:.1f}% | episodios: {dates}"
        )
        if profile.family == "availability" and profile.escalation_rate == 0.0:
            lines.append(
                "  - Tasa de escalacion a cover critico 0%: cadencia de reposicion normal, "
                "la politica esta funcionando."
            )
    lines.append("")

    if report.denominator.dimensions_evaluated:
        lines += ["## Denominador comun", ""]
        if report.denominator.findings:
            for finding in report.denominator.findings:
                lines.append(
                    f"- **{finding.dimension}={finding.value}**: {finding.n_v} de {finding.m_v} "
                    f"(lift {finding.lift:.2f}, q={finding.q_value:.3f}, "
                    f"exceso {finding.excess:.1f})"
                )
                if finding.aliases:
                    alias = ", ".join(f"{d}={v}" for d, v in finding.aliases)
                    lines.append(
                        f"  - Correlacionado con {alias}: son los mismos cronicos. "
                        "El reporte no separa cual es la causa."
                    )
        else:
            lines.append(
                "- No hay denominador comun con evidencia en esta ventana "
                f"({report.denominator.n_comparisons} comparacion(es) evaluadas)."
            )
        lines += ["", "### Conteos absolutos", ""]
        for dimension, counts in report.denominator.absolute_counts.items():
            top = ", ".join(f"{value}={count}" for value, count in list(counts.items())[:8])
            lines.append(f"- {dimension}: {top}")
        lines.append("")
    if report.denominator.dimensions_rejected:
        lines += ["### Dimensiones descartadas", ""]
        for rejection in report.denominator.dimensions_rejected:
            lines.append(f"- {rejection.column}: {rejection.reason}")
        lines.append("")

    if report.sensitivity_changes:
        lines += ["## Sensibilidad al hueco", ""]
        lines += [f"- {change}" for change in report.sensitivity_changes]
        lines.append("")

    lines += ["## Umbrales aplicados", ""]
    for key, value in report.thresholds_applied.items():
        lines.append(f"- `{key}` = {value}")
    lines.append("")

    directory = Path("deliverables")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "pattern_scan_deck.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return {"deck": path}
```

- [ ] **Step 4: Correr hasta verde**

Run: `pytest tests/test_pattern_scan_job.py -q`
Expected: PASS, 26 tests.

- [ ] **Step 5: Commit**

```bash
git add jobs/pattern_scan_job.py tests/test_pattern_scan_job.py
git commit -m "feat(pattern_scan): deliverable operativo y deck con supuestos declarados"
```

---

### Task 9: Registro del tool, los 9 pinned places y el CLI

**Files:**
- Modify: `scm_agent/tools.py`
- Modify: `scm_agent/citation_gate.py`
- Modify: `src/i18n.py`
- Modify: `webapp/how_it_works_data.py`
- Modify: `examples/run_capability_smoke.py`
- Create: `examples/run_pattern_scan.py`
- Create: `tests/test_pattern_scan_tool.py`
- Modify: `tests/test_scm_agent.py`
- Modify: `tests/test_price_watch_tool.py:165`
- Modify: `tests/test_how_it_works_data.py`

**Interfaces:**
- Consumes: `jobs.pattern_scan_job.prepare/run/verify/write_operational/build_deck` (Tasks 6-8).
- Produces: `pattern_scan` registrado, `len(reg.list()) == 47`.

- [ ] **Step 1: Escribir el test del tool**

Crear `tests/test_pattern_scan_tool.py`:

```python
from scm_agent.tools import build_default_registry


def test_pattern_scan_is_registered():
    keys = {t.key for t in build_default_registry().list()}

    assert "pattern_scan" in keys


def test_the_registry_has_47_tools():
    assert len(build_default_registry().list()) == 47


def test_pattern_scan_matches_its_brief():
    registry = build_default_registry()

    ranked = registry.match("buscar patrones cronicos recurrentes en mis alertas")

    assert ranked and ranked[0][0].key == "pattern_scan"


def test_pattern_scan_has_a_distinct_spanish_title():
    from src.i18n import TOOL_TITLES

    titles = TOOL_TITLES["pattern_scan"]
    assert titles["es"] != titles["en"]


def test_pattern_scan_has_citation_concepts():
    from scm_agent.citation_gate import TOOL_CONCEPTS

    assert TOOL_CONCEPTS["pattern_scan"]
```

- [ ] **Step 2: Correr para verlo fallar**

Run: `pytest tests/test_pattern_scan_tool.py -q`
Expected: FAIL — `pattern_scan` no esta en el registry.

- [ ] **Step 3: Registrar el tool en `scm_agent/tools.py`**

Agregar `pattern_scan_job` al bloque `from jobs import (...)` de la linea 8, y antes de `build_default_registry`:

```python
# ---- pattern_scan (patrones cronicos sobre la historia de eventos) -----------

def _pattern_scan_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    try:
        load = pattern_scan_job.prepare(request.data_path, request.params)
    except FileNotFoundError:
        return Prepared(
            status="needs_data",
            messages=[
                "no hay historia de eventos todavia: pasa un CSV de historia "
                "(entity_id, signal, timestamp) o espera a que el ledger acumule datos."
            ],
        )
    except (pattern_scan_job.AmbiguousDateFormat, pattern_scan_job.WindowTooLarge) as exc:
        return Prepared(status="needs_clarification", messages=[str(exc)])
    span_days = (
        (load.ts_max - load.ts_min).total_seconds() / 86400.0
        if load.ts_min and load.ts_max
        else 0.0
    )
    min_days = float(request.params.get("min_history_days", pattern_scan_job.DEFAULT_MIN_HISTORY_DAYS))
    if not load.occurrences or span_days < min_days:
        return Prepared(
            status="needs_data",
            messages=[
                f"hacen falta al menos {min_days:.0f} dias de historia y hay {span_days:.0f}. "
                "Un patron sobre menos que eso seria inventado."
            ],
        )
    return Prepared(status="ok", payload=load)


def _pattern_scan_run(payload: object, params: dict) -> Produced:
    master = (
        pattern_scan_job.load_master(params["master_path"]) if params.get("master_path") else {}
    )
    # El ledger se REABRE aca a proposito: prepare() cierra el suyo en su finally,
    # y la certificacion de huecos se consulta DURANTE el armado de episodios, no
    # antes. Sin esto certified_gap seria None por el camino del tool y un outage
    # de la app volveria a leerse como recurrencia -- justo el bug que el diseno
    # existe para evitar. Con fuente CSV no hay instrumentacion que consultar, asi
    # que los huecos se asumen certificados y el deck lo declara.
    certified_gap = None
    ledger_epoch = None
    ledger = None
    if getattr(payload, "source_kind", "csv") == "ledger":
        from scm_agent.events import DEFAULT_PATH as EVENTS_DEFAULT_PATH

        events_path = str(params.get("events_path") or EVENTS_DEFAULT_PATH)
        if Path(events_path).exists():
            ledger = pattern_scan_job.EventLedger(events_path)
            certified_gap = pattern_scan_job.certified_gap_from_ledger(ledger)
            ledger_epoch = ledger.epoch()
    try:
        report = pattern_scan_job.run(
            payload,
            master=master,
            roster=pattern_scan_job.load_roster(),
            now=params.get("now") or datetime.now(timezone.utc),
            params=params,
            certified_gap=certified_gap,
            ledger_epoch=ledger_epoch,
        )
    finally:
        if ledger is not None:
            ledger.close()
    n_chronic = sum(1 for p in report.profiles if p.recurrence_label == "chronic")
    return Produced(
        report=report,
        summary=f"{len(report.profiles)} perfil(es), {n_chronic} cronico(s).",
    )


def pattern_scan_tool() -> Tool:
    return Tool(
        key="pattern_scan",
        title="Escaneo de Patrones Cronicos",
        description=(
            "Lee la historia de eventos (ledger de Kern o un CSV del cliente) y separa el "
            "sintoma repetido, que pide un arreglo de politica, del incidente aislado. "
            "Busca ademas el denominador comun de los cronicos con un gate estadistico."
        ),
        intent_keywords=(
            "patrones cronicos", "chronic pattern", "pattern scan", "escaneo de patrones",
            "alertas recurrentes", "recurring alerts", "se repite siempre",
            "patrones recurrentes", "root cause recurrente", "denominador comun",
        ),
        requires_data=False,
        prepare=_pattern_scan_prepare,
        run=_pattern_scan_run,
        qa=lambda report: pattern_scan_job.verify(report),
        deliver=lambda report, out_dir, client: pattern_scan_job.write_operational(
            report, out_dir, client
        ),
        deck=lambda report, out_dir, client, citations, confidence, options: (
            pattern_scan_job.build_deck(
                report, client=client, citations=tuple(citations), confidence=confidence
            )
        ),
        options=lambda report: report.outcome,
    )
```

Y en `build_default_registry`, junto a `reg.register(disruption_scan_tool())`:

```python
    reg.register(pattern_scan_tool())
```

Verificar que `datetime`, `timezone` y `Path` esten importados a nivel de modulo en `tools.py`; si no, agregarlos (los tres son stdlib, no hay riesgo de boot).

- [ ] **Step 4: Los otros pinned places**

`scm_agent/citation_gate.py`, en `TOOL_CONCEPTS` (orden alfabetico):

```python
    # pattern_scan (tool #47) se ancla en causa raiz y variabilidad, no en el hub
    # de inventario: lo que detecta es recurrencia estructural, no un nivel.
    "pattern_scan": ("root_cause_analysis", "demand_variability", "bullwhip_effect"),
```

Verificar que los tres nodos existan antes de commitear:

```bash
python examples/query_knowledge.py --search "root cause analysis" | head -20
```

Si alguno no existe en el grafo, reemplazarlo por el nodo mas cercano que SI exista — no inventar ids.

`src/i18n.py`, en `TOOL_TITLES`:

```python
    "pattern_scan": {"es": "Escaneo de Patrones Cronicos",
                     "en": "Chronic Pattern Scan"},
```

`webapp/how_it_works_data.py`: agregar la entrada #47 copiando el shape de sus vecinos, con el mismo `domain_area`/`scor_bucket` que `risk` (es diagnostico de riesgo estructural).

`tests/test_how_it_works_data.py`: subir 46 -> 47 y ajustar los dos tallies.

`tests/test_price_watch_tool.py:165`: `assert len(reg.list()) == 46` -> `== 47`.

`tests/test_scm_agent.py:123`: agregar `"pattern_scan"` al key set esperado.

`examples/run_capability_smoke.py`: agregar `_BRIEF["pattern_scan"] = "buscar patrones cronicos en mis alertas"` y apuntar su fixture a `tests/fixtures/patterns/history.csv`. Sin fixture el tool sale `GATED` en vez de `PASS`.

- [ ] **Step 5: El CLI**

Crear `examples/run_pattern_scan.py`:

```python
"""CLI de pattern_scan: --data <historia.csv> | --ledger <ruta.sqlite3> | --demo."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from jobs import pattern_scan_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Escaneo de patrones cronicos")
    parser.add_argument("--data", help="CSV de historia (entity_id, signal, timestamp)")
    parser.add_argument("--ledger", help="ruta al events.sqlite3")
    parser.add_argument("--master", help="CSV maestro opcional para el denominador comun")
    parser.add_argument("--window-days", type=int, default=90)
    parser.add_argument("--demo", action="store_true", help="corre sobre el fixture de ejemplo")
    args = parser.parse_args()

    data_path = args.data
    if args.demo:
        data_path = "tests/fixtures/patterns/history.csv"
    now = datetime(2026, 8, 1, tzinfo=timezone.utc) if args.demo else datetime.now(timezone.utc)

    params = {"window_days": args.window_days, "now": now}
    if args.ledger:
        params["events_path"] = args.ledger

    load = pattern_scan_job.prepare(data_path, params)
    master = pattern_scan_job.load_master(args.master) if args.master else {}
    report = pattern_scan_job.run(
        load, master=master, roster=pattern_scan_job.load_roster(), now=now, params=params
    )

    issues = pattern_scan_job.verify(report)
    print(f"perfiles: {len(report.profiles)} | base: {report.base_mode}")
    for profile in report.profiles:
        print(
            f"  {profile.entity_id} ({profile.family}): {profile.recurrence_label} "
            f"| duty {profile.duty_cycle:.1f}% | episodios {profile.n_episodes}"
        )
    print(f"outcome: {report.outcome.status} - {report.outcome.summary}")
    if issues:
        print("QA FAIL:")
        for issue in issues:
            print(f"  - {issue}")
        raise SystemExit(1)
    paths = pattern_scan_job.build_deck(report)
    print(f"deck: {list(paths.values())[0]}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Correr el CLI en modo demo**

Run: `python examples/run_pattern_scan.py --demo`
Expected: imprime los perfiles y un `outcome:` sin `QA FAIL`. Salida ASCII-only.

- [ ] **Step 7: Suite completa + lint + boot**

Run: `pytest tests/ -q`
Expected: PASS. Si `tests/test_scm_agent.py` falla por drift de claves, el key set quedo desactualizado — corregilo ahi, no en el registry.

Run: `ruff check src tests examples`
Expected: sin errores.

Run: `python -c "import webapp.app; print('boot ok')"`
Expected: `boot ok`.

- [ ] **Step 8: Commit**

```bash
git add scm_agent/tools.py scm_agent/citation_gate.py src/i18n.py webapp/how_it_works_data.py examples/run_pattern_scan.py examples/run_capability_smoke.py tests/
git commit -m "feat(tool): pattern_scan - deteccion de patrones cronicos (tool #47)"
```

---

## Verificacion final

- [ ] `pytest tests/ -q` — verde
- [ ] `ruff check src tests examples` — limpio
- [ ] `python -c "import webapp.app"` — sin traceback
- [ ] `python examples/run_pattern_scan.py --demo` — corre y no falla QA
- [ ] El venv de prod-boot (`.[web,mcp,pricing-intel,dataquality]`) importa `webapp.app` sin traceback
- [ ] Draft PR abierto contra `main`, CI verde en py3.11/3.12/3.13

## Follow-ups (issue el mismo dia, NO en este PR)

- Tabla `monitor_runs` + latido en `scm_agent/monitors.py::run_all_monitors` (nunca en `jobs/monitors_job.py::run_monitors_cycle`: ese wrapper no es el camino de prod, `webapp/app.py:1058` llama `run_all_monitors` directo).
- Politica de retencion del ledger. Disparador: `ledger_growth` al 70% del volumen de 1 GB.

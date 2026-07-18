# Loop de auto-mejora de Linchpin — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que Linchpin aprenda de sus propias fallas reales (QA failures, escalaciones, errores) en vez de perderlas, y convierta los hallazgos mecánicos en draft PRs verificados adversarialmente — sin depender de ningún servicio de terceros.

**Architecture:** Un `SignalStore` (SQLite) captura cada resultado no-`ok`/escalado del `Orchestrator`. Un chequeo diario barato (`scripts/evolve_check_threshold.py`) decide si hay suficiente señal acumulada para disparar el Workflow `evolve` (`.claude/workflows/evolve.js`), que mina → agrupa → clasifica (con un filtro determinístico de zonas excluidas) → propone fix con TDD → verifica adversarialmente → abre draft PR o reporta en `documentation/EVOLUTION_LOG.md`.

**Tech Stack:** Python 3.11+ stdlib (`sqlite3`, `json`, `subprocess`, `argparse` — sin dependencias nuevas), la herramienta Workflow de Claude Code para el pipeline de agentes, `gh`/`fly` CLI (ya instalados) para CI y el pull best-effort de la DB de producción.

**Referencia de diseño:** [`docs/superpowers/specs/2026-07-07-loop-auto-mejora-design.md`](../specs/2026-07-07-loop-auto-mejora-design.md) — leer antes de tocar código si algo en este plan no queda claro.

## Global Constraints

- Python 3.11+, sin dependencias nuevas (todo con stdlib). Tests: `PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/ -q`.
- **ASCII-only en prints de consola** (Windows cp1252) — todos los `print()` de este plan emiten JSON puro vía `json.dumps(...)` (ASCII-safe por default, `ensure_ascii=True`), así que ya cumplen sin cambios extra.
- **Nunca auto-mergear, nunca push directo a `main`** — cada tarea de código termina en su propia rama + `gh pr create --draft`. Nunca commitear directo a `main` (ver el error que este mismo agente cometió y corrigió en la sesión que generó este plan).
- **Zonas excluidas del auto-PR** (constante `EXCLUDED_PREFIXES` en `src/evolve/excluded_paths.py`): `src/writeback.py`, `src/writeback_store.py`, `src/connectors/`, `src/mcp_keys.py`, `src/pricing.py`, `webapp/mcp_auth.py`.
- **Scrub de PII en la captura**: `src/signals_store.py`'s `_ALLOWED_CONTEXT_KEYS` es una whitelist, no una blacklist — cualquier campo no listado se descarta, nunca se versiona.
- **`.claude/*` está gitignorado por defecto** (`.gitignore` línea `.claude/*` con excepciones `!.claude/settings.json`/`!.claude/hooks/`) — el Workflow script necesita su propia excepción `!.claude/workflows/` o nunca se commitea.
- Lint (matches CI): `ruff check src tests examples` — `scripts/` NO está en el scope de lint de CI (ver `CLAUDE.md`), así que los scripts CLI nuevos no rompen CI por ese lado, pero deben seguir el mismo estilo igual.
- Convención de nombres de test: un archivo por área de comportamiento (`tests/test_orchestrator_kb_warnings.py`, `tests/test_orchestrator_persona.py`, etc.), no un `test_orchestrator.py` monolítico.

---

## File Structure

```
src/
  signals_store.py          # NEW — SignalStore (SQLite), mirror de src/writeback_store.py
  evolve/
    __init__.py              # NEW — subpackage marker (mismo patrón que src/connectors/)
    excluded_paths.py        # NEW — is_excluded(path) -> bool, gate determinístico
    state.py                 # NEW — EvolveState, load_state/save_state, should_run()
    mining.py                # NEW — normalize_reason(), cluster_events(), merge_events()
scm_agent/
  orchestrator.py            # MODIFY — nuevo param signals_store + hook _record_signal()
webapp/
  app.py                     # MODIFY — _get_orchestrator() pasa signals_store=SignalStore()
scripts/
  evolve_mine.py              # NEW — CLI: junta señal local+prod+CI, imprime clusters JSON
  evolve_persist.py           # NEW — CLI: marca eventos consumidos + guarda evolve_state.json
  evolve_check_excluded.py    # NEW — CLI: wrapper de is_excluded() para el Workflow
  evolve_check_threshold.py   # NEW — CLI: decide si el cron debe disparar el Workflow
.claude/
  workflows/
    evolve.js                 # NEW — el pipeline Workflow (mine->classify->fix->verify->land)
documentation/
  EVOLUTION_LOG.md            # NEW — reporte append-only para hallazgos no auto-PR
.gitignore                    # MODIFY — !.claude/workflows/, data/evolve_state.json
CLAUDE.md, HANDOFF.md         # MODIFY — puntero al nuevo mecanismo (Tarea 6)
tests/
  test_signals_store.py               # NEW
  test_orchestrator_signals.py        # NEW
  test_evolve_excluded_paths.py       # NEW
  test_evolve_state.py                # NEW
  test_evolve_mining.py               # NEW
```

---

### Task 1: SignalStore — persistencia de señales reales

**Files:**
- Create: `src/signals_store.py`
- Test: `tests/test_signals_store.py`

**Interfaces:**
- Produces: `SignalStore(path=DEFAULT_PATH)` con métodos `record_event(kind, tool, reason, context=None, *, now=None) -> int`, `unconsumed_events(limit=1000) -> list[SignalEvent]`, `count_unconsumed() -> int`, `mark_consumed(event_ids: list[int], run_id: str) -> None`, `close() -> None`. Dataclass `SignalEvent(id, timestamp, kind, tool, reason, context, consumed_by_run_id)`. Constantes `KIND_QA_FAILED`, `KIND_ERROR`, `KIND_NEEDS_CLARIFICATION`, `KIND_ESCALATED`, `KIND_HANDOFF` (strings, ver Task 2 — coinciden literalmente con `STATUS_QA_FAILED`/etc. de `scm_agent/types.py`).

- [ ] **Step 1: Escribir los tests (fallando)**

```python
# tests/test_signals_store.py
from __future__ import annotations

from src.signals_store import KIND_ESCALATED, KIND_ERROR, KIND_QA_FAILED, SignalStore


def test_record_event_returns_id_and_is_unconsumed():
    store = SignalStore(":memory:")
    event_id = store.record_event(KIND_QA_FAILED, "eoq", "min_order_qty missing")
    assert event_id == 1
    unconsumed = store.unconsumed_events()
    assert len(unconsumed) == 1
    assert unconsumed[0].kind == KIND_QA_FAILED
    assert unconsumed[0].reason == "min_order_qty missing"
    assert unconsumed[0].consumed_by_run_id is None


def test_count_unconsumed_tracks_only_unmarked_events():
    store = SignalStore(":memory:")
    store.record_event(KIND_QA_FAILED, "eoq", "reason a")
    store.record_event(KIND_ERROR, "safety_stock", "reason b")
    assert store.count_unconsumed() == 2


def test_mark_consumed_excludes_events_from_future_reads():
    store = SignalStore(":memory:")
    first_id = store.record_event(KIND_QA_FAILED, "eoq", "reason a")
    store.record_event(KIND_ERROR, "safety_stock", "reason b")
    store.mark_consumed([first_id], run_id="run-1")
    remaining = store.unconsumed_events()
    assert len(remaining) == 1
    assert remaining[0].kind == KIND_ERROR
    assert store.count_unconsumed() == 1


def test_context_is_scrubbed_to_the_allowed_key_whitelist():
    store = SignalStore(":memory:")
    store.record_event(
        KIND_ESCALATED, "sourcing", "supplier dispute",
        context={"job_type": "sourcing", "confidence": 0.4, "client_name": "Acme Corp", "raw_brief": "..."},
    )
    event = store.unconsumed_events()[0]
    assert event.context == {"job_type": "sourcing", "confidence": 0.4}
    assert "client_name" not in event.context
    assert "raw_brief" not in event.context


def test_persists_across_reconnection(tmp_path):
    db_path = tmp_path / "signals.sqlite3"
    store = SignalStore(db_path)
    store.record_event(KIND_QA_FAILED, "eoq", "reason a")
    store.close()

    reopened = SignalStore(db_path)
    assert reopened.count_unconsumed() == 1
```

- [ ] **Step 2: Correr los tests y confirmar que fallan**

Run: `PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_signals_store.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'src.signals_store'`

- [ ] **Step 3: Implementar `src/signals_store.py`**

```python
"""Persistent log of real usage signals (QA failures, escalations, errors).

Nothing before this module durably records why a real job run went wrong -
JobResult and GuidedOutcome are computed and returned to the caller, then
lost. SignalStore closes that gap: scm_agent/orchestrator.py appends one row
per non-`ok`/escalated/handoff outcome, and scripts/evolve_mine.py later
mines those rows to find recurring product defects worth fixing.

Mirrors src/writeback_store.py's SqliteAuditLedger pattern (stdlib sqlite3,
one file per store, gitignored under data/*.sqlite3).
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = "data/signals.sqlite3"

KIND_QA_FAILED = "qa_failed"
KIND_ERROR = "error"
KIND_NEEDS_CLARIFICATION = "needs_clarification"
KIND_ESCALATED = "escalated"
KIND_HANDOFF = "handoff"

# Whitelist, not blacklist: only these keys ever reach context_json, and only
# with a scalar value. Anything else (a client name, a CSV row, a free-text
# brief) is dropped rather than risk a client identifier leaking into a log
# that documentation/EVOLUTION_LOG.md and draft PR descriptions later quote
# verbatim. See CLAUDE.md: "Never read or surface PII."
_ALLOWED_CONTEXT_KEYS = frozenset({"job_type", "confidence", "qa_issue_count", "clarification_count"})


def _scrub_context(context: dict | None) -> dict:
    if not context:
        return {}
    scrubbed: dict = {}
    for key in _ALLOWED_CONTEXT_KEYS:
        if key in context and isinstance(context[key], (str, int, float, bool)):
            scrubbed[key] = context[key]
    return scrubbed


@dataclass(frozen=True)
class SignalEvent:
    """One recorded real-usage signal."""

    id: int
    timestamp: float
    kind: str
    tool: str | None
    reason: str
    context: dict
    consumed_by_run_id: str | None


class SignalStore:
    """Append-only log of real usage signals, read back by scripts/evolve_mine.py.

    Pass ``:memory:`` for a store that behaves the same but never touches
    disk (tests). Pass a real file path (the default) for a store that
    survives a process restart - the same code runs in local dev and in the
    Fly deployment (same `/data` mount as writeback's own ledger).
    """

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, timeout=30.0)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " timestamp REAL NOT NULL,"
            " kind TEXT NOT NULL,"
            " tool TEXT,"
            " reason TEXT NOT NULL,"
            " context_json TEXT NOT NULL,"
            " consumed_by_run_id TEXT"
            ")"
        )
        self._conn.commit()

    def record_event(
        self,
        kind: str,
        tool: str | None,
        reason: str,
        context: dict | None = None,
        *,
        now: float | None = None,
    ) -> int:
        """Append one event. Returns the new row's id."""
        if now is None:
            now = time.time()
        cur = self._conn.execute(
            "INSERT INTO events (timestamp, kind, tool, reason, context_json, consumed_by_run_id)"
            " VALUES (?, ?, ?, ?, ?, NULL)",
            (now, kind, tool, reason, json.dumps(_scrub_context(context))),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def unconsumed_events(self, limit: int = 1000) -> list[SignalEvent]:
        rows = self._conn.execute(
            "SELECT id, timestamp, kind, tool, reason, context_json, consumed_by_run_id"
            " FROM events WHERE consumed_by_run_id IS NULL ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            SignalEvent(id=r[0], timestamp=r[1], kind=r[2], tool=r[3], reason=r[4],
                        context=json.loads(r[5]), consumed_by_run_id=r[6])
            for r in rows
        ]

    def count_unconsumed(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM events WHERE consumed_by_run_id IS NULL").fetchone()
        return int(row[0])

    def mark_consumed(self, event_ids: list[int], run_id: str) -> None:
        if not event_ids:
            return
        placeholders = ",".join("?" for _ in event_ids)
        self._conn.execute(
            f"UPDATE events SET consumed_by_run_id = ? WHERE id IN ({placeholders})",
            (run_id, *event_ids),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Correr los tests y confirmar que pasan**

Run: `PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_signals_store.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git checkout -b evolve/signal-store
git add src/signals_store.py tests/test_signals_store.py
git commit -m "feat: add SignalStore, a persistent log of real usage signals"
```

---

### Task 2: Hook del orchestrator + wiring en producción

**Files:**
- Modify: `scm_agent/orchestrator.py`
- Modify: `webapp/app.py:69-79` (`_get_orchestrator`)
- Test: `tests/test_orchestrator_signals.py`

**Interfaces:**
- Consumes: `SignalStore`, `KIND_QA_FAILED`, `KIND_ERROR`, `KIND_NEEDS_CLARIFICATION`, `KIND_ESCALATED`, `KIND_HANDOFF` de `src/signals_store.py` (Task 1). `ESCALATED`, `HANDOFF` de `src/guided.py` (ya existen).
- Produces: `Orchestrator(..., signals_store: SignalStore | None = None)` — `None` (default) desactiva la captura, así ningún test/script existente empieza a escribir en disco sin pedirlo. `Orchestrator._record_signal(result: JobResult) -> None` (privado, fire-and-forget).

- [ ] **Step 1: Escribir los tests (fallando)**

```python
# tests/test_orchestrator_signals.py
"""The orchestrator captures real-usage signals for qa_failed/error/needs_clarification
statuses and for ESCALATED/HANDOFF GuidedOutcomes - see src/signals_store.py.
"""

from __future__ import annotations

from scm_agent import llm, tools
from scm_agent.orchestrator import Orchestrator
from scm_agent.types import JobResult
from src.guided import ESCALATED, HANDOFF, EscalationPacket, GuidedOutcome, HandoffPacket
from src.signals_store import (
    KIND_ERROR,
    KIND_ESCALATED,
    KIND_HANDOFF,
    KIND_NEEDS_CLARIFICATION,
    KIND_QA_FAILED,
    SignalStore,
)


def _orch(signals_store=None):
    return Orchestrator(
        registry=tools.build_default_registry(), provider=llm.RulesFallback(),
        clients_root=None, signals_store=signals_store,
    )


def test_signal_capture_is_disabled_by_default():
    assert _orch().signals_store is None


def test_ok_result_records_no_signal(tmp_path):
    store = SignalStore(":memory:")
    result = _orch(store).run("evaluate our SC leadership", out_dir=tmp_path,
                               overrides={"scores": "3 2 3 1 1", "name": "T"})
    assert result.status == "ok"
    assert store.count_unconsumed() == 0


def test_ambiguous_brief_records_needs_clarification_signal_end_to_end(tmp_path):
    store = SignalStore(":memory:")
    result = _orch(store).run("zzz qqq flibbertigibbet", out_dir=tmp_path)
    assert result.status == "needs_clarification"
    events = store.unconsumed_events()
    assert len(events) == 1
    assert events[0].kind == KIND_NEEDS_CLARIFICATION


def test_qa_failed_result_records_signal_with_reason():
    store = SignalStore(":memory:")
    orch = _orch(store)
    result = JobResult(status="qa_failed", tool="eoq", confidence=0.8, deliverables={},
                        summary="eoq: QA failed", qa_issues=["min_order_qty missing"])
    orch._record_signal(result)
    events = store.unconsumed_events()
    assert len(events) == 1
    assert events[0].kind == KIND_QA_FAILED
    assert events[0].tool == "eoq"
    assert "min_order_qty missing" in events[0].reason


def test_error_result_records_signal():
    store = SignalStore(":memory:")
    orch = _orch(store)
    result = JobResult(status="error", tool=None, confidence=0.0, deliverables={},
                        summary="An internal error occurred.")
    orch._record_signal(result)
    assert store.unconsumed_events()[0].kind == KIND_ERROR


def test_escalated_guided_outcome_records_signal():
    store = SignalStore(":memory:")
    orch = _orch(store)
    escalation = EscalationPacket(reason="supplier dispute over $50k invoice", route_to="finance")
    guided = GuidedOutcome(status=ESCALATED, summary="Escalated to finance.", escalation=escalation)
    result = JobResult(status="ok", tool="sourcing", confidence=0.9, deliverables={},
                        summary="ok", guided=guided)
    orch._record_signal(result)
    assert store.unconsumed_events()[0].kind == KIND_ESCALATED


def test_handoff_guided_outcome_records_signal():
    store = SignalStore(":memory:")
    orch = _orch(store)
    handoff = HandoffPacket(title="Negotiate new lead time", steps=["Call supplier"])
    guided = GuidedOutcome(status=HANDOFF, summary="Prepared a handoff.", handoffs=[handoff])
    result = JobResult(status="ok", tool="supplier_scorecard", confidence=0.9, deliverables={},
                        summary="ok", guided=guided)
    orch._record_signal(result)
    assert store.unconsumed_events()[0].kind == KIND_HANDOFF
```

- [ ] **Step 2: Correr los tests y confirmar que fallan**

Run: `PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_orchestrator_signals.py -v`
Expected: FAIL con `TypeError: Orchestrator.__init__() got an unexpected keyword argument 'signals_store'`

- [ ] **Step 3: Modificar `scm_agent/orchestrator.py`**

Agregar el import (junto a los demás imports del módulo, línea 9-16):

```python
from src import client_profile
from src.guided import ESCALATED, HANDOFF
from src.signals_store import SignalStore
```

Modificar `__init__` (líneas 30-52) — agregar el parámetro y guardarlo:

```python
    def __init__(
        self,
        registry: ToolRegistry | None = None,
        provider: LLMProvider | None = None,
        knowledge: KnowledgeBase | None = None,
        persona: str = "",
        clients_root: Path | str | None = client_profile.DEFAULT_CLIENTS_ROOT,
        signals_store: SignalStore | None = None,
    ) -> None:
        self.registry = registry if registry is not None else build_default_registry()
        self.provider = provider if provider is not None else get_provider()
        self.knowledge = knowledge if knowledge is not None else KnowledgeBase()
        self.persona = persona
        self.clients_root = None if clients_root is None else Path(clients_root)
        # Real-usage signal capture (see src/signals_store.py). None (the default)
        # means disabled - existing tests and one-off scripts must opt in
        # explicitly, so a bare Orchestrator() never writes to disk unexpectedly.
        self.signals_store = signals_store
```

Modificar `run()` (líneas 54-77) — capturar la señal justo en el boundary único antes de retornar:

```python
    def run(
        self,
        brief: str,
        *,
        data_path: str | None = None,
        overrides: dict | None = None,
        job_type: str | None = None,
        client: str = "Client",
        strict_params: bool = False,
        out_dir: str | Path = "deliverables/agent",
    ) -> JobResult:
        overrides = overrides or {}
        request = JobRequest(brief=brief, data_path=data_path, job_type=job_type,
                             params=dict(overrides), client=client, strict_params=strict_params)
        try:
            result = self._run(request, Path(out_dir))
        except Exception:  # never crash the caller — surface as error status
            logger.error("orchestrator.run failed", exc_info=True)
            result = JobResult(status=STATUS_ERROR, tool=None, confidence=0.0,
                               deliverables={}, summary="An internal error occurred.")
        # Single boundary: every result leaves with a protected, executable path. A tool may
        # supply its own ranked-options outcome on success (set in _run); otherwise derive the
        # protected fallback. Either way, no result is a dead end.
        result = replace(result, guided=result.guided or to_guided_outcome(result))
        self._record_signal(result)
        return result

    def _record_signal(self, result: JobResult) -> None:
        """Fire-and-forget capture of a real-usage signal (see src/signals_store.py).

        Never allowed to affect the result it is observing: a logging failure is
        swallowed here, not surfaced to the caller.
        """
        if self.signals_store is None:
            return
        if result.status in (STATUS_QA_FAILED, STATUS_ERROR, STATUS_NEEDS_CLARIFICATION):
            kind = result.status
            reason = "; ".join(result.qa_issues or result.clarifications or [result.summary])
        elif result.guided is not None and result.guided.status in (ESCALATED, HANDOFF):
            kind = result.guided.status
            reason = result.guided.summary
        else:
            return
        try:
            self.signals_store.record_event(
                kind, result.tool, reason,
                context={"job_type": result.tool, "confidence": result.confidence},
            )
        except Exception:
            logger.warning("signal capture failed", exc_info=True)
```

- [ ] **Step 4: Correr los tests del orchestrator y confirmar que pasan**

Run: `PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_orchestrator_signals.py -v`
Expected: PASS (7 tests)

Run también el resto de la suite del orchestrator para confirmar que no rompiste nada existente:

Run: `PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/ -k orchestrator -q`
Expected: PASS (todos los tests de orchestrator, incluidos los preexistentes)

- [ ] **Step 5: Wiring en `webapp/app.py` (captura de tráfico real de producción)**

`webapp/mcp_server.py`'s `build_mcp_server(_get_orchestrator())` ya comparte esta misma instancia (confirmado leyendo `webapp/app.py:162`), así que este único cambio cubre TANTO el dashboard/API como el tráfico MCP real — no hace falta tocar `webapp/mcp_server.py`.

Agregar el import (junto a los demás imports post-sys.path, línea ~41):

```python
from src.signals_store import SignalStore  # noqa: E402
```

Modificar `_get_orchestrator()` (líneas 69-79):

```python
def _get_orchestrator() -> Orchestrator:
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        # Client profiles are DISABLED on this surface by default: /api/jobs and the
        # MCP mount are multi-tenant, and `client` here is a caller-typed display
        # label, not an authenticated identity — honoring it for profile lookup would
        # let any caller pull another client's real cost parameters by naming them.
        # A local single-operator deployment can opt in via LINCHPIN_CLIENTS_ROOT.
        clients_root = os.environ.get("LINCHPIN_CLIENTS_ROOT", "").strip() or None
        _ORCHESTRATOR = Orchestrator(clients_root=clients_root, signals_store=SignalStore())
    return _ORCHESTRATOR
```

- [ ] **Step 6: Confirmar que el webapp sigue arrancando y su suite pasa**

Run: `PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/ -k "webapp or app" -q`
Expected: PASS (sin regresiones)

- [ ] **Step 7: Commit**

```bash
git add scm_agent/orchestrator.py webapp/app.py tests/test_orchestrator_signals.py
git commit -m "feat: capture real-usage signals in the orchestrator and wire it into the webapp"
```

---

### Task 3: `src/evolve/` — núcleo puro y testeable del loop

**Files:**
- Create: `src/evolve/__init__.py` (vacío)
- Create: `src/evolve/excluded_paths.py`
- Create: `src/evolve/state.py`
- Create: `src/evolve/mining.py`
- Test: `tests/test_evolve_excluded_paths.py`
- Test: `tests/test_evolve_state.py`
- Test: `tests/test_evolve_mining.py`

**Interfaces:**
- Consumes: `SignalEvent` de `src/signals_store.py` (Task 1).
- Produces: `is_excluded(path: str) -> bool`. `EvolveState(last_run_at: float|None, last_consumed_event_id: int)`, `load_state(path=DEFAULT_STATE_PATH) -> EvolveState`, `save_state(state, path=DEFAULT_STATE_PATH) -> None`, `should_run(unconsumed_count, last_run_at, *, threshold=5, max_dormancy_days=30, now) -> bool`. `Cluster(kind, tool, example_reason, event_ids, count)`, `normalize_reason(reason: str) -> str`, `cluster_events(events, ci_failures=None) -> list[Cluster]`, `merge_events(local, prod) -> list[SignalEvent]`.

#### 3a — `excluded_paths.py`

- [ ] **Step 1: Escribir los tests (fallando)**

```python
# tests/test_evolve_excluded_paths.py
from src.evolve.excluded_paths import is_excluded


def test_excludes_exact_file_match():
    assert is_excluded("src/mcp_keys.py") is True


def test_excludes_files_under_a_directory_prefix():
    assert is_excluded("src/connectors/odoo.py") is True
    assert is_excluded("src/connectors/excel.py") is True


def test_does_not_exclude_unrelated_files():
    assert is_excluded("src/eoq.py") is False
    assert is_excluded("jobs/eoq_job.py") is False


def test_normalizes_windows_path_separators():
    assert is_excluded("src\\connectors\\odoo.py") is True


def test_does_not_false_positive_on_similar_prefix():
    # a file that merely starts with similar characters but isn't actually
    # under the excluded directory must not match
    assert is_excluded("src/connectors_helpers.py") is False
```

- [ ] **Step 2: Confirmar que fallan** (`ModuleNotFoundError`)

- [ ] **Step 3: Implementar**

```python
# src/evolve/__init__.py
```
(vacío — subpackage marker, mismo patrón que `src/connectors/__init__.py`)

```python
# src/evolve/excluded_paths.py
"""Deterministic gate for the evolve loop's auto-PR: which files are excluded.

Checked by scripts/evolve_check_excluded.py, called from .claude/workflows/
evolve.js BEFORE any agent decides whether a cluster is "mechanical" - so a
cluster whose root cause traces into one of these paths always becomes a
documentation/EVOLUTION_LOG.md report entry, never a draft PR, regardless of
how confident an agent is.
"""

from __future__ import annotations

EXCLUDED_PREFIXES = (
    "src/writeback.py",
    "src/writeback_store.py",
    "src/connectors/",
    "src/mcp_keys.py",
    "src/pricing.py",
    "webapp/mcp_auth.py",
)


def is_excluded(path: str) -> bool:
    """True if `path` (repo-relative) falls under an excluded zone."""
    normalized = path.replace("\\", "/").lstrip("/")
    return any(normalized == prefix or normalized.startswith(prefix) for prefix in EXCLUDED_PREFIXES)
```

- [ ] **Step 4: Confirmar que pasan** (`pytest tests/test_evolve_excluded_paths.py -v`)

- [ ] **Step 5: Commit**

```bash
git add src/evolve/__init__.py src/evolve/excluded_paths.py tests/test_evolve_excluded_paths.py
git commit -m "feat: add the evolve loop's deterministic excluded-path gate"
```

#### 3b — `state.py`

- [ ] **Step 1: Escribir los tests (fallando)**

```python
# tests/test_evolve_state.py
from __future__ import annotations

from src.evolve.state import EvolveState, load_state, save_state, should_run

DAY = 86400.0


def test_should_run_false_with_zero_unconsumed_events():
    assert should_run(0, None, now=1000.0) is False
    assert should_run(0, 1000.0 - 60 * DAY, now=1000.0) is False


def test_should_run_true_once_threshold_reached():
    assert should_run(5, None, threshold=5, now=1000.0) is True
    assert should_run(4, None, threshold=5, now=1000.0) is False


def test_should_run_true_on_first_ever_check_with_any_signal():
    # last_run_at=None (never run before) - don't wait forever for a first pass.
    assert should_run(1, None, threshold=5, now=1000.0) is True


def test_should_run_false_before_dormancy_cap_with_low_signal():
    now = 1_000_000.0
    last_run_at = now - (10 * DAY)  # 10 days ago, under the 30-day cap
    assert should_run(2, last_run_at, threshold=5, max_dormancy_days=30, now=now) is False


def test_should_run_true_after_dormancy_cap_even_with_low_signal():
    now = 1_000_000.0
    last_run_at = now - (31 * DAY)
    assert should_run(2, last_run_at, threshold=5, max_dormancy_days=30, now=now) is True


def test_save_and_load_state_roundtrip(tmp_path):
    path = tmp_path / "evolve_state.json"
    save_state(EvolveState(last_run_at=123.0, last_consumed_event_id=42), path)
    loaded = load_state(path)
    assert loaded == EvolveState(last_run_at=123.0, last_consumed_event_id=42)


def test_load_state_defaults_when_file_is_missing(tmp_path):
    loaded = load_state(tmp_path / "does_not_exist.json")
    assert loaded == EvolveState(last_run_at=None, last_consumed_event_id=0)


def test_load_state_defaults_when_file_is_corrupt(tmp_path):
    path = tmp_path / "evolve_state.json"
    path.write_text("{not valid json", encoding="utf-8")
    loaded = load_state(path)
    assert loaded == EvolveState(last_run_at=None, last_consumed_event_id=0)
```

- [ ] **Step 2: Confirmar que fallan**

- [ ] **Step 3: Implementar**

```python
# src/evolve/state.py
"""Pure state/threshold logic for the evolve loop's cron trigger.

scripts/evolve_check_threshold.py is the thin CLI wrapper around this module -
kept separate so the threshold math is unit-testable without a real clock, a
real SignalStore file, or a real cron.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_STATE_PATH = "data/evolve_state.json"
DEFAULT_THRESHOLD = 5
DEFAULT_MAX_DORMANCY_DAYS = 30.0
SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class EvolveState:
    last_run_at: float | None
    last_consumed_event_id: int


def load_state(path: str | Path = DEFAULT_STATE_PATH) -> EvolveState:
    """Return the persisted state, or a fresh "never run" state if absent/corrupt.

    A corrupt or missing state file must never block the loop - falling back to
    "never run" just means the next check mines every unconsumed event (already
    consumed events are excluded at the SignalStore level, not here), which is
    safe, just possibly redundant with a prior partial run.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return EvolveState(
            last_run_at=raw.get("last_run_at"),
            last_consumed_event_id=int(raw.get("last_consumed_event_id", 0)),
        )
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError):
        return EvolveState(last_run_at=None, last_consumed_event_id=0)


def save_state(state: EvolveState, path: str | Path = DEFAULT_STATE_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps({"last_run_at": state.last_run_at, "last_consumed_event_id": state.last_consumed_event_id}),
        encoding="utf-8",
    )


def should_run(
    unconsumed_count: int,
    last_run_at: float | None,
    *,
    threshold: int = DEFAULT_THRESHOLD,
    max_dormancy_days: float = DEFAULT_MAX_DORMANCY_DAYS,
    now: float,
) -> bool:
    """True when the evolve Workflow should fire this check: enough new signal
    has accumulated, or it has been overdue too long since the last run.

    `now` has no default - the caller (scripts/evolve_check_threshold.py)
    always passes the real wall clock; tests pass a fixed value.
    """
    if unconsumed_count <= 0:
        return False
    if unconsumed_count >= threshold:
        return True
    days_since = float("inf") if last_run_at is None else (now - last_run_at) / SECONDS_PER_DAY
    return days_since >= max_dormancy_days
```

- [ ] **Step 4: Confirmar que pasan** (`pytest tests/test_evolve_state.py -v`)

- [ ] **Step 5: Commit**

```bash
git add src/evolve/state.py tests/test_evolve_state.py
git commit -m "feat: add the evolve loop's threshold/state logic"
```

#### 3c — `mining.py`

- [ ] **Step 1: Escribir los tests (fallando)**

```python
# tests/test_evolve_mining.py
from __future__ import annotations

from src.evolve.mining import cluster_events, merge_events, normalize_reason
from src.signals_store import SignalEvent


def _event(id_, kind="error", tool="eoq", reason="reason"):
    return SignalEvent(id=id_, timestamp=0.0, kind=kind, tool=tool, reason=reason,
                        context={}, consumed_by_run_id=None)


def test_normalize_reason_collapses_numbers_and_quoted_values():
    assert normalize_reason("SKU '12345' below reorder point 42") == \
        normalize_reason("SKU '99999' below reorder point 7")


def test_cluster_events_groups_by_kind_tool_and_normalized_reason():
    events = [
        _event(1, reason="SKU '111' missing lead_time"),
        _event(2, reason="SKU '222' missing lead_time"),
        _event(3, kind="qa_failed", reason="different problem"),
    ]
    clusters = cluster_events(events)
    assert len(clusters) == 2
    assert clusters[0].count == 2
    assert set(clusters[0].event_ids) == {1, 2}


def test_cluster_events_ranks_most_frequent_first():
    events = [_event(1, tool="a"), _event(2, tool="b"), _event(3, tool="b")]
    clusters = cluster_events(events)
    assert clusters[0].tool == "b"
    assert clusters[0].count == 2


def test_cluster_events_includes_ci_failures_as_their_own_cluster():
    clusters = cluster_events([], ci_failures=[{"workflowName": "tests", "conclusion": "failure", "url": "https://x"}])
    assert len(clusters) == 1
    assert clusters[0].kind == "ci_failure"
    assert clusters[0].tool == "tests"


def test_cluster_events_handles_no_signal_at_all():
    assert cluster_events([], []) == []


def test_merge_events_concatenates_disjoint_ids():
    local = [_event(1), _event(2)]
    prod = [_event(3), _event(4)]
    merged = merge_events(local, prod)
    assert [e.id for e in merged] == [1, 2, 3, 4]


def test_merge_events_deduplicates_colliding_ids_favoring_local():
    local = [_event(1, reason="local version")]
    prod = [_event(1, reason="prod version"), _event(2)]
    merged = merge_events(local, prod)
    assert [e.id for e in merged] == [1, 2]
    assert merged[0].reason == "local version"
```

- [ ] **Step 2: Confirmar que fallan**

- [ ] **Step 3: Implementar**

```python
# src/evolve/mining.py
"""Pure clustering logic for the evolve loop's Mine stage.

Groups raw signal events (and CI failures) into distinct root-cause clusters
by (kind, tool, normalized reason) - string grouping, no ML, which is enough
at this project's signal volume (see design spec section 8). Kept here, not
inline in .claude/workflows/evolve.js, so it is unit-testable with plain
pytest instead of only exercisable via a live Workflow run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.signals_store import SignalEvent

KIND_CI_FAILURE = "ci_failure"

_DIGITS_RE = re.compile(r"[0-9]+")
_QUOTED_RE = re.compile(r"['\"][^'\"]*['\"]")


def normalize_reason(reason: str) -> str:
    """Collapse numbers and quoted values so near-duplicate messages
    (different SKU ids, different quoted product names) cluster together."""
    text = (reason or "").lower()
    text = _DIGITS_RE.sub("#", text)
    text = _QUOTED_RE.sub("<value>", text)
    return text[:80]


@dataclass(frozen=True)
class Cluster:
    kind: str
    tool: str | None
    example_reason: str
    event_ids: tuple[int, ...]
    count: int


def merge_events(local: list[SignalEvent], prod: list[SignalEvent]) -> list[SignalEvent]:
    """Merge a local-dev event list with a best-effort production pull,
    de-duplicating by id. Local wins on a collision (kept first)."""
    seen_ids = {e.id for e in local}
    return local + [e for e in prod if e.id not in seen_ids]


def cluster_events(events: list[SignalEvent], ci_failures: list[dict] | None = None) -> list[Cluster]:
    """Group events (and optionally CI failures) into ranked clusters, most
    frequent first. `ci_failures` items are dicts with `workflowName`,
    `conclusion`, `url` (the shape `gh run list --json ...` returns)."""
    groups: dict[tuple[str, str | None, str], list[SignalEvent]] = {}
    order: list[tuple[str, str | None, str]] = []
    for e in events:
        key = (e.kind, e.tool, normalize_reason(e.reason))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(e)

    clusters = [
        Cluster(kind=key[0], tool=key[1], example_reason=groups[key][0].reason,
                event_ids=tuple(m.id for m in groups[key]), count=len(groups[key]))
        for key in order
    ]

    for ci in ci_failures or []:
        workflow_name = ci.get("workflowName", "unknown")
        clusters.append(Cluster(
            kind=KIND_CI_FAILURE, tool=workflow_name,
            example_reason=ci.get("url", ci.get("conclusion", "")),
            event_ids=(), count=1,
        ))

    return sorted(clusters, key=lambda c: c.count, reverse=True)
```

- [ ] **Step 4: Confirmar que pasan** (`pytest tests/test_evolve_mining.py -v`)

- [ ] **Step 5: Commit**

```bash
git add src/evolve/mining.py tests/test_evolve_mining.py
git commit -m "feat: add the evolve loop's clustering logic"
```

---

### Task 4: CLIs delgados (`scripts/evolve_*.py`)

**Files:**
- Create: `scripts/evolve_mine.py`
- Create: `scripts/evolve_persist.py`
- Create: `scripts/evolve_check_excluded.py`
- Create: `scripts/evolve_check_threshold.py`

**Interfaces:**
- Consumes: todo lo de Task 1 y Task 3 (`SignalStore`, `cluster_events`, `merge_events`, `is_excluded`, `EvolveState`, `load_state`, `save_state`, `should_run`).
- Produces: 4 scripts CLI ejecutables con `PYTHONPATH=.`, cada uno imprime JSON a stdout. Consumidos por los `agent()` calls de `.claude/workflows/evolve.js` (Task 5) y por el prompt del cron (Task 6).

No llevan tests de pytest propios más allá de lo ya cubierto en Task 1/3 — la parte nueva en cada uno (`subprocess` a `gh`/`fly`, `argparse`, `main()`) es I/O real, verificada manualmente en el Step de smoke-test de Task 6, no con mocks de `subprocess` (ver `CLAUDE.md`: preferir verificación real sobre mocks para este tipo de integración externa).

- [ ] **Step 1: `scripts/evolve_mine.py`**

```python
#!/usr/bin/env python
"""Gather raw signal for one evolve run: local + best-effort production
SignalStore events, plus recent CI failures, already clustered. Prints one
JSON array to stdout - the evolve Workflow's "Mine" stage runs this via an
agent and reads the result back with a schema.

Usage:
    python scripts/evolve_mine.py
    python scripts/evolve_mine.py --prod-db data/signals_prod_pull.sqlite3
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from src.evolve.mining import cluster_events, merge_events
from src.signals_store import SignalStore


def fetch_ci_failures(limit: int = 20) -> list[dict]:
    try:
        out = subprocess.run(
            ["gh", "run", "list", "--status", "failure", "--limit", str(limit),
             "--json", "workflowName,conclusion,url"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        return json.loads(out.stdout)
    except Exception:
        return []  # no gh auth / not in a repo with a remote - treat as no CI signal


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prod-db", default=None, help="path to a best-effort production DB pull, if any")
    args = parser.parse_args()

    local_store = SignalStore()
    events = local_store.unconsumed_events()
    if args.prod_db and Path(args.prod_db).exists():
        prod_store = SignalStore(args.prod_db)
        events = merge_events(events, prod_store.unconsumed_events())
        prod_store.close()

    clusters = cluster_events(events, fetch_ci_failures())
    print(json.dumps({
        "clusters": [
            {"kind": c.kind, "tool": c.tool, "exampleReason": c.example_reason,
             "eventIds": list(c.event_ids), "count": c.count}
            for c in clusters
        ],
    }))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: `scripts/evolve_persist.py`**

```python
#!/usr/bin/env python
"""Mark this evolve run's mined local events consumed and advance the state
file. Called once at the end of a (non-dry-run) evolve Workflow run.

Usage:
    python scripts/evolve_persist.py --run-id evolve-20260710 --event-ids 1,2,3
    python scripts/evolve_persist.py --run-id evolve-20260710 --event-ids ""
"""

from __future__ import annotations

import argparse
import time

from src.evolve.state import EvolveState, save_state
from src.signals_store import SignalStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--event-ids", required=True,
                         help="comma-separated local SignalStore event ids consumed this run, or empty")
    args = parser.parse_args()

    event_ids = [int(x) for x in args.event_ids.split(",") if x.strip()]
    store = SignalStore()
    if event_ids:
        store.mark_consumed(event_ids, args.run_id)
    save_state(EvolveState(last_run_at=time.time(), last_consumed_event_id=max(event_ids, default=0)))
    print(f"persisted run {args.run_id}: {len(event_ids)} event(s) marked consumed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: `scripts/evolve_check_excluded.py`**

```python
#!/usr/bin/env python
"""Print whether ANY of the given repo-relative paths falls under an
excluded zone (see src/evolve/excluded_paths.py) - the evolve Workflow's
deterministic gate before it will ever consider a draft PR.

Usage:
    python scripts/evolve_check_excluded.py src/eoq.py src/writeback.py
"""

from __future__ import annotations

import argparse
import json

from src.evolve.excluded_paths import is_excluded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    print(json.dumps({"anyExcluded": any(is_excluded(p) for p in args.paths)}))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: `scripts/evolve_check_threshold.py`**

```python
#!/usr/bin/env python
"""CLI entry point for the evolve loop's cron trigger.

Invoked by the scheduled agent configured via the `schedule` skill (see
docs/superpowers/plans/2026-07-07-loop-auto-mejora-plan.md Task 6). Prints a
single JSON line to stdout: the scheduled agent's prompt reads `should_run`
from it and only invokes Workflow(name="evolve") when true.

Usage:
    python scripts/evolve_check_threshold.py
    python scripts/evolve_check_threshold.py --threshold 5 --max-dormancy-days 30
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time

from src.evolve.state import DEFAULT_MAX_DORMANCY_DAYS, DEFAULT_THRESHOLD, load_state, should_run
from src.signals_store import SignalStore

PROD_DB_REMOTE_PATH = "/data/signals.sqlite3"
PROD_DB_LOCAL_PATH = "data/signals_prod_pull.sqlite3"


def pull_production_db(fly_app: str) -> str | None:
    """Best-effort pull of the production signal DB via `fly sftp get`.

    Never raises: a missing/expired `fly` auth or an unreachable app just
    means this check falls back to local-only signal (design spec section 8
    calls this an explicitly accepted best-effort, not a hard requirement).
    """
    try:
        subprocess.run(
            ["fly", "sftp", "get", PROD_DB_REMOTE_PATH, PROD_DB_LOCAL_PATH, "--app", fly_app],
            check=True, capture_output=True, timeout=60,
        )
        return PROD_DB_LOCAL_PATH
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    parser.add_argument("--max-dormancy-days", type=float, default=DEFAULT_MAX_DORMANCY_DAYS)
    parser.add_argument("--fly-app", default="linchpin", help="Fly app name for the best-effort production pull")
    parser.add_argument("--skip-prod-pull", action="store_true")
    args = parser.parse_args()

    local_store = SignalStore()
    unconsumed = local_store.count_unconsumed()

    prod_path = None if args.skip_prod_pull else pull_production_db(args.fly_app)
    if prod_path is not None:
        prod_store = SignalStore(prod_path)
        unconsumed += prod_store.count_unconsumed()
        prod_store.close()

    state = load_state()
    verdict = should_run(
        unconsumed, state.last_run_at,
        threshold=args.threshold, max_dormancy_days=args.max_dormancy_days, now=time.time(),
    )
    print(json.dumps({
        "should_run": verdict,
        "unconsumed_count": unconsumed,
        "last_run_at": state.last_run_at,
        "production_db_pulled": prod_path is not None,
    }))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Smoke-test manual de los 4 scripts**

Run: `PYTHONPATH=. .venv/Scripts/python.exe scripts/evolve_check_excluded.py src/eoq.py src/mcp_keys.py`
Expected: `{"anyExcluded": true}`

Run: `PYTHONPATH=. .venv/Scripts/python.exe scripts/evolve_mine.py --prod-db data/does-not-exist.sqlite3`
Expected: JSON con `{"clusters": []}` (sin señal capturada todavía) o los clusters que ya existan si corriste Task 2's tests contra el `data/signals.sqlite3` real — no debe tirar excepción aunque `gh` no tenga sesión o el prod-db no exista.

Run: `PYTHONPATH=. .venv/Scripts/python.exe scripts/evolve_check_threshold.py --skip-prod-pull`
Expected: JSON con `should_run`, `unconsumed_count`, `last_run_at`, `production_db_pulled: false` — sin excepción.

- [ ] **Step 6: Commit**

```bash
git add scripts/evolve_mine.py scripts/evolve_persist.py scripts/evolve_check_excluded.py scripts/evolve_check_threshold.py
git commit -m "feat: add the evolve loop's CLI entry points"
```

---

### Task 5: El Workflow `evolve` + `.gitignore` + `EVOLUTION_LOG.md`

**Files:**
- Modify: `.gitignore`
- Create: `documentation/EVOLUTION_LOG.md`
- Create: `.claude/workflows/evolve.js`

**Interfaces:**
- Consumes: `scripts/evolve_mine.py`, `scripts/evolve_check_excluded.py`, `scripts/evolve_persist.py` (Task 4), invocados vía `agent()` con Bash — el Workflow script en sí NO tiene acceso a filesystem/subprocess directo (limitación de la herramienta Workflow), por eso toda I/O real pasa por un `agent()`.
- Produces: un Workflow nombrado `evolve`, invocable como `Workflow({name: 'evolve', args: {runId, dryRun}})` desde el cron (Task 6).

- [ ] **Step 1: Arreglar `.gitignore`**

`.claude/*` está gitignorado con excepciones puntuales (`!.claude/settings.json`, `!.claude/hooks/`) — sin agregar una excepción para `workflows/`, `.claude/workflows/evolve.js` nunca se commitearía. Agregar, junto a las excepciones existentes:

```gitignore
# Misc — version the shared hook + settings, ignore personal/local overrides
.claude/*
!.claude/settings.json
!.claude/hooks/
!.claude/workflows/
.claude/settings.local.json
```

Y en la sección de estado local de SQLite, agregar el nuevo archivo de estado (no es `.sqlite3`, así que el patrón existente no lo cubre):

```gitignore
# local SQLite state (writeback audit ledger, MCP client keys, evolve signals) - never
# versioned; the latter would otherwise leak paying-client names into git history.
data/*.sqlite3

# evolve loop's own runtime state (see src/evolve/state.py) - regenerable, never versioned.
data/evolve_state.json
```

- [ ] **Step 2: Crear `documentation/EVOLUTION_LOG.md`**

```markdown
# Evolution Log

> Auto-mejora de Linchpin a partir de senales de uso real (QA failures,
> errores, escalaciones) - ver
> docs/superpowers/specs/2026-07-07-loop-auto-mejora-design.md para el diseno
> completo. Cada corrida del Workflow `evolve` (.claude/workflows/evolve.js)
> agrega entradas aca para: hallazgos en zonas excluidas del auto-PR,
> hallazgos que requieren juicio de diseno, y fixes intentados que no
> sobrevivieron la verificacion adversarial. Nunca se borra una entrada - si
> un hallazgo se resuelve manualmente, se agrega una nota de seguimiento, no
> se reescribe la historia.
>
> Formato de cada entrada (mas reciente arriba):
> `## YYYY-MM-DD - <kind>/<tool> - <N ocurrencias>` seguido de
> `Origen` (ejemplo de razon) y `Por que no es auto-PR`.
```

- [ ] **Step 3: Escribir `.claude/workflows/evolve.js`**

```javascript
export const meta = {
  name: 'evolve',
  description: 'Mine real usage signals into verified draft PRs or a human report',
  phases: [
    { title: 'Mine' },
    { title: 'Classify' },
    { title: 'Fix' },
    { title: 'Verify' },
    { title: 'Land' },
  ],
}

const MAX_PRS_PER_RUN = 3
const RUN_ID = `evolve-${(args && args.runId) || 'unscheduled'}`
const DRY_RUN = Boolean(args && args.dryRun)

const MINE_SCHEMA = {
  type: 'object',
  properties: {
    clusters: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          kind: { type: 'string' },
          tool: { type: ['string', 'null'] },
          exampleReason: { type: 'string' },
          eventIds: { type: 'array', items: { type: 'number' } },
          count: { type: 'number' },
        },
        required: ['kind', 'tool', 'exampleReason', 'eventIds', 'count'],
      },
    },
  },
  required: ['clusters'],
}

phase('Mine')
log('Running scripts/evolve_mine.py (local + best-effort production signal, plus recent CI failures).')
const mined = await agent(
  'Run with Bash from the repo root: `PYTHONPATH=. python scripts/evolve_mine.py --prod-db ' +
  'data/signals_prod_pull.sqlite3` (that prod-db path may not exist - the script already handles that ' +
  'gracefully, do not treat a missing file as an error). Return its JSON stdout output.',
  { schema: MINE_SCHEMA, phase: 'Mine' },
)
const clusters = (mined && mined.clusters) || []
log(`Mined ${clusters.length} distinct cluster(s).`)

if (clusters.length === 0) {
  log('Nothing to mine this run - exiting without opening any PR or report entry.')
} else {

const CLASSIFY_SCHEMA = {
  type: 'object',
  properties: {
    rootCauseFiles: { type: 'array', items: { type: 'string' } },
    mechanical: { type: 'boolean' },
    rationale: { type: 'string' },
  },
  required: ['rootCauseFiles', 'mechanical', 'rationale'],
}
const EXCLUDED_SCHEMA = {
  type: 'object',
  properties: { anyExcluded: { type: 'boolean' } },
  required: ['anyExcluded'],
}
const FIX_SCHEMA = {
  type: 'object',
  properties: {
    applied: { type: 'boolean' },
    branch: { type: 'string' },
    filesChanged: { type: 'array', items: { type: 'string' } },
    testAdded: { type: 'string' },
    summary: { type: 'string' },
  },
  required: ['applied', 'summary'],
}
const VERIFY_SCHEMA = {
  type: 'object',
  properties: { refuted: { type: 'boolean' }, rationale: { type: 'string' } },
  required: ['refuted', 'rationale'],
}

let prsOpened = 0
const reportEntries = []
const consumedEventIds = []

await pipeline(
  clusters,
  async (cluster) => {
    phase('Classify')
    consumedEventIds.push(...cluster.eventIds)
    const classified = await agent(
      `Investigate this recurring Linchpin signal cluster using Read/Grep/Bash (read-only - make no edits ` +
      `yet): kind=${cluster.kind}, tool=${cluster.tool}, seen ${cluster.count} time(s), example reason: ` +
      `"${cluster.exampleReason}". Find the real root cause. Return the exact repo-relative file path(s) ` +
      `most responsible, whether a fix is mechanical (clear repro, isolated to 1-2 files, no ambiguous ` +
      `design judgment) or not, and your rationale.`,
      { schema: CLASSIFY_SCHEMA, phase: 'Classify', label: `classify:${cluster.tool}` },
    )
    return { cluster, classified }
  },
  async ({ cluster, classified }) => {
    const rootCauseFiles = (classified && classified.rootCauseFiles) || []
    const excludedCheck = rootCauseFiles.length === 0
      ? { anyExcluded: false }
      : await agent(
          `Run with Bash: \`PYTHONPATH=. python scripts/evolve_check_excluded.py ${rootCauseFiles.join(' ')}\` ` +
          `and return its JSON output.`,
          { schema: EXCLUDED_SCHEMA, phase: 'Classify', label: `gate:${cluster.tool}` },
        )
    if (excludedCheck.anyExcluded) {
      return { cluster, route: 'report', reason: `root cause under an excluded path (${rootCauseFiles.join(', ')})` }
    }
    if (!classified || !classified.mechanical) {
      return { cluster, route: 'report', reason: (classified && classified.rationale) || 'judgment-heavy' }
    }
    return { cluster, classified, route: 'fix' }
  },
  async ({ cluster, classified, route, reason }) => {
    if (route !== 'fix') return { cluster, route, reason }
    phase('Fix')
    if (DRY_RUN) {
      log(`[dry-run] would attempt a fix for ${cluster.tool}/${cluster.kind}`)
      return { cluster, route: 'report', reason: 'dry run - fix not attempted' }
    }
    const branch = `evolve/${cluster.tool || 'unknown'}-${cluster.kind}`.replace(/[^a-zA-Z0-9/_-]/g, '-')
    const fix = await agent(
      `Fix this recurring Linchpin defect using TDD. Root cause file(s): ${classified.rootCauseFiles.join(', ')}. ` +
      `Signal: kind=${cluster.kind}, tool=${cluster.tool}, seen ${cluster.count} time(s), example: ` +
      `"${cluster.exampleReason}".\n` +
      `1. \`git status --short\` and \`gh pr list --json headRefName,files\` - if an open PR or dirty file ` +
      `already touches ${classified.rootCauseFiles.join(', ')}, return applied=false with the conflict in ` +
      `summary and stop before branching.\n` +
      `2. \`git checkout -b ${branch}\`.\n` +
      `3. Write a failing test reproducing the defect in tests/, confirm it fails.\n` +
      `4. Write the minimal fix. Confirm the new test passes.\n` +
      `5. Run \`PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/ -q\` and \`ruff check src tests examples\` ` +
      `- both clean before applied=true. Commit.\n` +
      `Return applied, branch, filesChanged, testAdded, and a one-paragraph summary.`,
      { schema: FIX_SCHEMA, phase: 'Fix', label: `fix:${cluster.tool}` },
    )
    if (!fix || !fix.applied) {
      return { cluster, route: 'report', reason: (fix && fix.summary) || 'fix attempt did not apply' }
    }
    return { cluster, route: 'verify', fix }
  },
  async ({ cluster, route, reason, fix }) => {
    if (route !== 'verify') return { cluster, route, reason, fix }
    phase('Verify')
    const votes = await parallel(
      Array.from({ length: 3 }, (_, i) => () =>
        agent(
          `Adversarially review branch "${fix.branch}" (git diff against main) for cluster kind=${cluster.kind}, ` +
          `tool=${cluster.tool}. Try hard to REFUTE that this fix resolves the root cause without regressions: ` +
          `re-run the full suite, confirm the diff stays scoped to ${fix.filesChanged.join(', ')}, and confirm ` +
          `the new test (${fix.testAdded}) actually reproduces the original defect (fails on main without the ` +
          `fix, per \`git stash\` + rerun). Default to refuted=true if not confident. Explain your rationale.`,
          { schema: VERIFY_SCHEMA, phase: 'Verify', label: `verify:${cluster.tool}:${i}` },
        ),
      ),
    )
    const refutedCount = votes.filter(Boolean).filter((v) => v.refuted).length
    const survives = refutedCount < 2
    return survives
      ? { cluster, route: 'land', fix }
      : { cluster, route: 'report', fix,
          reason: `verification refuted the fix (${refutedCount}/3): ` +
            votes.filter(Boolean).filter((v) => v.refuted).map((v) => v.rationale).join(' | ') }
  },
  async ({ cluster, route, reason, fix }) => {
    phase('Land')
    if (route === 'land') {
      if (prsOpened >= MAX_PRS_PER_RUN) {
        reportEntries.push({ cluster, reason: `PR cap (${MAX_PRS_PER_RUN}) reached this run - deferred to next run` })
        return { cluster, route: 'report-deferred' }
      }
      // Reserve the slot BEFORE the await below - pipeline stages run without a
      // barrier (concurrently), and this repo's own PR-conflict lessons
      // (HANDOFF.md section 5) are exactly why the cap must not race. JS is
      // single-threaded between awaits, so this check+increment is atomic.
      prsOpened += 1
      const opened = await agent(
        `On branch "${fix.branch}" (already committed, tests green): push it and open a draft PR with ` +
        `\`gh pr create --draft\`. Title: "fix: ${cluster.tool || 'unknown'} ${cluster.kind} (evolve loop)". ` +
        `Body must cite the originating signal (kind=${cluster.kind}, tool=${cluster.tool}, ${cluster.count} ` +
        `occurrence(s), example: "${cluster.exampleReason}") plus this summary: ${fix.summary}. Return the PR URL.`,
        { schema: { type: 'object', properties: { prUrl: { type: 'string' } }, required: ['prUrl'] },
          phase: 'Land', label: `pr:${cluster.tool}` },
      )
      log(`Opened draft PR for ${cluster.tool}/${cluster.kind}: ${opened && opened.prUrl}`)
      return { cluster, route: 'landed', prUrl: opened && opened.prUrl }
    }
    reportEntries.push({ cluster, reason })
    return { cluster, route: 'reported', reason }
  },
)

if (reportEntries.length > 0 && !DRY_RUN) {
  phase('Land')
  const entriesText = reportEntries
    .map(({ cluster, reason }) =>
      `## ${cluster.kind}/${cluster.tool || 'unknown'} - ${cluster.count} occurrence(s)\n` +
      `- Origen: ejemplo de razon: "${cluster.exampleReason}"\n` +
      `- Por que no es auto-PR: ${reason}\n`)
    .join('\n')
  await agent(
    'Prepend these entries (most recent first, right after documentation/EVOLUTION_LOG.md\'s header comment ' +
    `block) dated today:\n\n${entriesText}\n\nThen: \`git checkout -b evolve/report-${RUN_ID}\`, commit ` +
    `("docs: evolve run ${RUN_ID} report"), push, and \`gh pr create --draft\` titled "docs: evolve run ` +
    `${RUN_ID} report".`,
    { phase: 'Land', label: 'report-pr' },
  )
} else if (reportEntries.length > 0) {
  log(`[dry-run] would write ${reportEntries.length} report entrie(s) to EVOLUTION_LOG.md`)
}

if (!DRY_RUN) {
  await agent(
    `Run with Bash: \`PYTHONPATH=. python scripts/evolve_persist.py --run-id ${RUN_ID} --event-ids ` +
    `${consumedEventIds.join(',')}\`.`,
    { phase: 'Land', label: 'persist-state' },
  )
}

}

return { clustersMined: clusters.length, dryRun: DRY_RUN }
```

- [ ] **Step 4: Validar sintaxis del script antes de commitear**

El Workflow tool no acepta un dry-parse aislado — la validación real ocurre al invocarlo (Task 6, Step 2). Como chequeo previo barato, confirmar que no hay errores obvios de JS: abrir el archivo y revisar que cada `agent()`/`pipeline()`/`parallel()` tenga sus llaves balanceadas y que `meta` sea un literal puro (sin variables ni spreads) — cumple, `meta` no referencia nada fuera de sí mismo.

- [ ] **Step 5: Commit**

```bash
git checkout -b evolve/workflow-pipeline
git add .gitignore documentation/EVOLUTION_LOG.md .claude/workflows/evolve.js
git commit -m "feat: add the evolve Workflow pipeline (mine -> classify -> fix -> verify -> land)"
```

---

### Task 6: Disparo programado + verificación end-to-end + docs

**Files:**
- Modify: `CLAUDE.md` (sección "Project map" o una nueva breve)
- Modify: `HANDOFF.md` (puntero en la sección de estado actual)

**Interfaces:**
- Consumes: el Workflow `evolve` (Task 5), `scripts/evolve_check_threshold.py` (Task 4).
- Produces: un cron configurado vía la skill `schedule` (o `CronCreate` directamente) que corre diariamente.

- [ ] **Step 1: Smoke-test manual del Workflow en dry-run ANTES de programar el cron**

Sembrar una señal falsa para tener algo que minar:

```bash
PYTHONPATH=. .venv/Scripts/python.exe -c "from src.signals_store import SignalStore, KIND_QA_FAILED; SignalStore().record_event(KIND_QA_FAILED, 'eoq', 'smoke-test seeded signal - safe to ignore')"
```

Invocar el Workflow en modo dry-run (esto se hace desde la sesión de Claude Code activa, no desde una terminal):

```
Workflow({ name: 'evolve', args: { runId: 'smoketest', dryRun: true } })
```

Expected: el log muestra `Mined 1 distinct cluster(s)`, pasa por Classify, y termina en `[dry-run] would attempt a fix for eoq/qa_failed` sin abrir ningún PR ni tocar `data/evolve_state.json` (dry run no persiste). Verificar con `gh pr list` que no se creó ningún PR nuevo.

- [ ] **Step 2: Configurar el cron**

Usar la skill `schedule` (o cargar el schema real de `CronCreate` vía `ToolSearch` si se invoca la herramienta directamente) para crear una rutina diaria con este prompt exacto:

```
Run `PYTHONPATH=. python scripts/evolve_check_threshold.py` from the Linchpin
repo root (C:/Users/Gamer/Music/scm/supply-chain-optimization). Parse its
JSON stdout. If `should_run` is true, invoke the Workflow tool with
Workflow({name: 'evolve', args: {runId: '<today's date as YYYYMMDD>'}}). If
`should_run` is false, do nothing and end — do not invoke Workflow. Never
open a PR or make any commit outside of what the `evolve` Workflow itself
does.
```

Cadencia: diaria (una vez por día es más que suficiente dado que `should_run` ya filtra la mayoría de los días sin señal — no hace falta una cadencia más agresiva).

- [ ] **Step 3: Puntero en `CLAUDE.md`**

Agregar una fila a la tabla "Project map" (después de la fila de `documentation/operator/`):

```markdown
| `src/evolve/`, `scripts/evolve_*.py`, `.claude/workflows/evolve.js` | Self-improvement loop: mines real usage signals (QA failures, escalations) into verified draft PRs — see `docs/superpowers/specs/2026-07-07-loop-auto-mejora-design.md` |
```

- [ ] **Step 4: Puntero en `HANDOFF.md`**

Agregar un párrafo breve al principio de la sección "3. Immediate next steps" (o donde el estado actual del repo se describe), notando: el loop de auto-mejora está implementado y programado; señala al spec y a `documentation/EVOLUTION_LOG.md` como donde revisar sus hallazgos; aclara que sigue siendo secundario a conseguir el primer cliente pago (memoria `linchpin-priority-monetization`).

- [ ] **Step 5: Correr la suite completa una última vez**

Run: `PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/ -q`
Expected: todos los tests pasan (los preexistentes + los ~25 nuevos de las Tareas 1-3).

Run: `ruff check src tests examples`
Expected: limpio.

- [ ] **Step 6: Commit y PR final de esta tarea**

```bash
git checkout -b evolve/wire-cron-and-docs
git add CLAUDE.md HANDOFF.md
git commit -m "docs: wire the evolve loop's scheduled trigger and document it"
git push -u origin evolve/wire-cron-and-docs
gh pr create --draft --title "docs: wire evolve loop's cron trigger" --body "Wires the daily schedule for the evolve Workflow (Task 6 of docs/superpowers/plans/2026-07-07-loop-auto-mejora-plan.md) and points CLAUDE.md/HANDOFF.md at it."
```

Nota: cada una de las Tareas 1-5 también termina en su propia rama+PR (`evolve/signal-store`, etc.) — abrir y mergear esos PRs (CI verde -> `gh pr ready` si aplica -> squash-merge) ANTES de empezar la tarea siguiente, ya que cada una depende del código de la anterior estando en `main`.

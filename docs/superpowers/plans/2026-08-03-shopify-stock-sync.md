# Sync de stock Odoo → Shopify (B1) — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que una divergencia de stock entre Odoo y Shopify se detecte sola en cada tick del cron, se deje propuesta para aprobación en un clic, y no genere ruido mientras persiste.

**Architecture:** Un job nuevo, `jobs/shopify_stock_sync.py`, que recibe el estado deseado ya calculado y el store de Shopify inyectado. Compara, deja un `Changeset` en staging vía `nosilo_core.writeback` y emite un `Event` al `EventLedger` existente. No escribe en Shopify: eso lo hace la aprobación T2. No construye scheduler, cola, deduplicador ni motor de aprobación — las cuatro piezas ya existen en el repo.

**Tech Stack:** Python 3.11+, pytest, `uv`. Sin dependencias nuevas.

Spec: [`docs/superpowers/specs/2026-08-03-shopify-stock-sync-design.md`](../specs/2026-08-03-shopify-stock-sync-design.md)

## Global Constraints

- **Todo offline.** Ningún test toca la red. Stand-ins: `demo_odoo()`, `InMemoryShopifyInventory`, `EventLedger(":memory:")`.
- **Comandos:** `cd "C:\k-shopify"` y siempre `uv run --no-sync pytest ...`. Sin `--no-sync`, `uv run` re-sincroniza el venv al lockfile y la suite falla en colección con `ModuleNotFoundError: fastapi`.
- **Nunca commitear `uv.lock`.** `git checkout -- uv.lock` antes de cada `git add`.
- **El CI corre `ruff check nosilo_core packs src tests examples`.** Correr `uv run --no-sync ruff check .` antes de cada commit: un bloque de imports desordenado deja el CI en rojo, y eso ya pasó en el trabajo anterior.
- **Rama:** `shopify-stock-sync`, salida de `origin/main` en `b6e2d09`.
- **El job NUNCA escribe en Shopify.** `writeback.stage` es dry-run por contrato. Cualquier llamada a `commit`, `set_inventory` o `adjust_inventory` desde este job es un defecto.
- **Idioma:** docstrings y comentarios en inglés (el resto de `jobs/` y `src/` lo está). Mensajes de commit en español.
- **Fuente de verdad: Odoo.** El job nunca propone cambiar Odoo.

## File Structure

| Archivo | Responsabilidad | Tarea |
|---|---|---|
| `jobs/shopify_stock_sync.py` | *Nuevo.* Comparación, staging, clave determinista, emisión del evento y cableado programado | 1, 2, 3 |
| `tests/test_shopify_stock_sync.py` | *Nuevo.* Cobertura del anterior | 1, 2, 3 |
| `config/event_routing.yaml` | Ruta nueva para `shopify_stock_divergence` a tier T2 | 3 |

**Piezas existentes que se usan tal cual — no reimplementar ninguna:**

```python
# nosilo_core/writeback.py
stage(store, target, edits: dict[str, dict], *, risk_tier, idempotency_key, reason="") -> Changeset
Changeset.content_hash   # property: hash estable de (target, risk_tier, changes)
Changeset.changes        # tuple[Change, ...];  Change(entity_id, field, before, after)
writeback.TIER_REVERSIBLE

# scm_agent/events.py
Event(type, severity, source, dedup_key, sku=None, payload={}, id=..., ts=...)
EventLedger(path=..., dedup_window_seconds=3600.0)
EventLedger.emit(event) -> bool   # False si la misma dedup_key ya se registró DENTRO de la ventana

# src/connectors/shopify_inventory.py
ShopifyInventoryStore.read(sku) -> dict   # {"quantity": n}, o {} si el SKU no existe en Shopify

# jobs/scheduler.py
ScheduledJob(id=..., func=..., trigger=..., trigger_args=...)   # func() sin argumentos obligatorios
```

**Matiz sobre la deduplicación que el spec simplificaba.** `emit()` deduplica por `dedup_key` **dentro de una ventana** (`DEFAULT_DEDUP_WINDOW_SECONDS = 3600.0`), medida contra `event.ts`, no contra el reloj. Con el cron cada 4h, una divergencia que persiste **sí volverá a emitir** en el siguiente tick, porque 4h > 1h. Eso es deseable —es un recordatorio, no ruido— y no hay que cambiarlo. Lo que la clave evita es la duplicación dentro de una misma ventana (dos ticks juntos, un reintento manual, un `workflow_dispatch`).

**Vocabulario de severidad del repo:** `"high"`, `"medium"`, `"low"` (str libre, sin enum).

---

### Task 1: Comparar y dejar el changeset en staging

**Files:**
- Create: `jobs/shopify_stock_sync.py`
- Test: `tests/test_shopify_stock_sync.py`

**Interfaces:**
- Consumes: nada de tareas anteriores.
- Produces:
  - `TARGET = "shopify_inventory"`
  - `SyncReport` — dataclass congelada con `changeset: writeback.Changeset | None`, `missing_skus: tuple[str, ...]`
  - `idempotency_key(edits: dict[str, dict]) -> str`
  - `plan_stock_sync(store, desired: dict[str, float]) -> SyncReport`

- [ ] **Step 1: Write the failing tests**

Crea `tests/test_shopify_stock_sync.py`:

```python
"""Tests for jobs/shopify_stock_sync.py."""

from __future__ import annotations

from src.connectors.shopify_inventory import (
    InMemoryShopifyInventory,
    ShopifyInventoryStore,
)
from jobs.shopify_stock_sync import (
    TARGET,
    idempotency_key,
    plan_stock_sync,
)

LOC = "gid://shopify/Location/1"


def _store(stock: dict[str, int]) -> ShopifyInventoryStore:
    return ShopifyInventoryStore(InMemoryShopifyInventory(stock={s: {LOC: q} for s, q in stock.items()}),
                                 location_id=LOC)


def test_no_divergence_produces_no_changeset():
    report = plan_stock_sync(_store({"SKU-1": 40}), {"SKU-1": 40})
    assert report.changeset is None
    assert report.missing_skus == ()


def test_divergence_produces_a_changeset_naming_the_sku():
    report = plan_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40})
    assert report.changeset is not None
    assert report.changeset.target == TARGET
    changes = report.changeset.changes
    assert len(changes) == 1
    assert changes[0].entity_id == "SKU-1"
    assert changes[0].field == "quantity"
    assert float(changes[0].before) == 37.0
    assert float(changes[0].after) == 40.0


def test_planning_never_writes_to_shopify():
    rpc = InMemoryShopifyInventory(stock={"SKU-1": {LOC: 37}})
    store = ShopifyInventoryStore(rpc, location_id=LOC)
    plan_stock_sync(store, {"SKU-1": 40})
    # stage() is a dry run: Shopify must be untouched, and no write key recorded.
    assert rpc.get_inventory_level("gid://shopify/InventoryItem/SKU-1", LOC) == 37
    assert rpc.idempotency_keys == []


def test_sku_missing_in_shopify_is_reported_and_skipped():
    report = plan_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40, "SKU-NOPE": 5})
    assert report.missing_skus == ("SKU-NOPE",)
    assert report.changeset is not None
    assert [c.entity_id for c in report.changeset.changes] == ["SKU-1"]


def test_only_divergent_skus_enter_the_changeset():
    report = plan_stock_sync(_store({"SKU-1": 40, "SKU-2": 12}), {"SKU-1": 40, "SKU-2": 20})
    assert [c.entity_id for c in report.changeset.changes] == ["SKU-2"]


def test_empty_desired_is_a_valid_run_with_nothing_to_do():
    report = plan_stock_sync(_store({"SKU-1": 40}), {})
    assert report.changeset is None
    assert report.missing_skus == ()


def test_idempotency_key_is_deterministic_and_content_derived():
    a = idempotency_key({"SKU-1": {"quantity": 40.0}})
    b = idempotency_key({"SKU-1": {"quantity": 40.0}})
    c = idempotency_key({"SKU-1": {"quantity": 41.0}})
    assert a == b
    assert a != c
    assert a.startswith("shopify-stock-sync:")


def test_idempotency_key_ignores_dict_ordering():
    one = idempotency_key({"SKU-1": {"quantity": 1.0}, "SKU-2": {"quantity": 2.0}})
    two = idempotency_key({"SKU-2": {"quantity": 2.0}, "SKU-1": {"quantity": 1.0}})
    assert one == two
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --no-sync pytest tests/test_shopify_stock_sync.py -q
```

Esperado: fallo en colección — `ModuleNotFoundError: No module named 'jobs.shopify_stock_sync'`.

- [ ] **Step 3: Write the implementation**

Crea `jobs/shopify_stock_sync.py`:

```python
"""Stock sync Odoo -> Shopify: detect divergence, propose, never write.

Odoo is the system of record for stock: purchases, receipts, adjustments and
warehouses all live there, so Shopify follows and never leads. This job reads
the desired quantities (computed by its caller -- see ``plan_stock_sync``),
compares them against what Shopify currently reports, and stages a Changeset
for a human to approve.

It deliberately does NOT write. ``writeback.stage`` is a dry run by contract;
applying the changeset is the T2 approval's job, and that path already carries
signed approval, idempotency and rollback. A call to ``commit`` or to the
connector's ``set_inventory`` from this module would be a defect.

Nothing here schedules itself either -- an external cron already POSTs to
``/api/jobs/run-scheduled`` on a cadence (a persistent in-process scheduler was
rejected for this deploy; see that endpoint's docstring).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from nosilo_core import writeback

#: writeback target for the Shopify inventory store.
TARGET = "shopify_inventory"

_KEY_PREFIX = "shopify-stock-sync"
_FIELD = "quantity"


@dataclass(frozen=True)
class SyncReport:
    """What one planning pass found.

    ``changeset`` is None when there is nothing to propose -- either every SKU
    already agrees, or ``desired`` was empty. ``missing_skus`` are SKUs present
    in ``desired`` but unknown to Shopify; a partially aligned catalogue is
    normal, so they are reported rather than raised.
    """

    changeset: writeback.Changeset | None
    missing_skus: tuple[str, ...]


def idempotency_key(edits: dict[str, dict]) -> str:
    """Derive a stable key from the edits themselves.

    Deterministic on purpose: if the same divergence is re-planned on the next
    tick, the key is identical and writeback's own ``claim()`` rejects it as a
    duplicate. Idempotency therefore holds at the writeback layer too, not only
    at the event ledger's.
    """
    canonical = repr(sorted((sku, sorted(fields.items())) for sku, fields in edits.items()))
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return f"{_KEY_PREFIX}:{digest}"


def plan_stock_sync(store: Any, desired: dict[str, float]) -> SyncReport:
    """Compare ``desired`` against Shopify and stage the difference.

    ``desired`` maps SKU to quantity and is computed by the CALLER -- for stock
    it comes from ``OdooConnector.inventory_levels()``. Passing it in rather
    than reading Odoo here keeps this function pure enough to test without an
    ERP, and lets the price sync reuse it with a computed source instead of a
    read one.
    """
    edits: dict[str, dict] = {}
    missing: list[str] = []

    for sku in sorted(desired):
        current = store.read(sku)
        if not current:
            missing.append(sku)
            continue
        if float(current[_FIELD]) != float(desired[sku]):
            edits[sku] = {_FIELD: float(desired[sku])}

    if not edits:
        return SyncReport(changeset=None, missing_skus=tuple(missing))

    changeset = writeback.stage(
        store,
        TARGET,
        edits,
        risk_tier=writeback.TIER_REVERSIBLE,
        idempotency_key=idempotency_key(edits),
        reason="stock sync Odoo -> Shopify",
    )
    return SyncReport(changeset=changeset, missing_skus=tuple(missing))
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run --no-sync pytest tests/test_shopify_stock_sync.py -q
```

Esperado: 8 passed.

- [ ] **Step 5: Lint**

```bash
uv run --no-sync ruff check .
```

Esperado: `All checks passed!`. Si marca I001, corrige con `uv run --no-sync ruff check --fix jobs/shopify_stock_sync.py tests/test_shopify_stock_sync.py`.

- [ ] **Step 6: Commit**

```bash
git checkout -- uv.lock
git add jobs/shopify_stock_sync.py tests/test_shopify_stock_sync.py
git commit -m "feat(sync): comparar stock Odoo vs Shopify y dejarlo en staging

Odoo manda: el job compara y propone, nunca escribe. La clave de idempotencia
se deriva del contenido, asi que re-planificar la misma divergencia produce la
misma clave y writeback la rechaza como duplicada."
```

---

### Task 2: Emitir el evento de divergencia

**Files:**
- Modify: `jobs/shopify_stock_sync.py` (añadir al final)
- Modify: `tests/test_shopify_stock_sync.py` (añadir al final)

**Interfaces:**
- Consumes: `SyncReport`, `plan_stock_sync`, `TARGET` de la Tarea 1.
- Produces:
  - `EVENT_TYPE = "shopify_stock_divergence"`
  - `SOURCE = "shopify_stock_sync"`
  - `run_stock_sync(store, desired, *, ledger, now=None) -> tuple[SyncReport, Event | None, bool]`

**Severidad.** `"high"` si algún SKU tiene en Shopify MÁS de lo que dice Odoo — ese es el caso de sobreventa, que es el que duele. `"medium"` en caso contrario (venta perdida, recuperable).

- [ ] **Step 1: Write the failing tests**

Añade al final de `tests/test_shopify_stock_sync.py`:

```python
from datetime import datetime, timedelta, timezone  # noqa: E402

from scm_agent.events import EventLedger  # noqa: E402
from jobs.shopify_stock_sync import (  # noqa: E402
    EVENT_TYPE,
    SOURCE,
    run_stock_sync,
)


def _ledger() -> EventLedger:
    return EventLedger(path=":memory:")


def test_no_divergence_emits_no_event():
    report, event, emitted = run_stock_sync(_store({"SKU-1": 40}), {"SKU-1": 40}, ledger=_ledger())
    assert report.changeset is None
    assert event is None
    assert emitted is False


def test_empty_desired_emits_no_event():
    report, event, emitted = run_stock_sync(_store({"SKU-1": 40}), {}, ledger=_ledger())
    assert event is None
    assert emitted is False


def test_divergence_emits_an_event_keyed_on_the_changeset_hash():
    store = _store({"SKU-1": 37})
    report, event, emitted = run_stock_sync(store, {"SKU-1": 40}, ledger=_ledger())
    assert emitted is True
    assert event.type == EVENT_TYPE
    assert event.source == SOURCE
    assert event.dedup_key == report.changeset.content_hash


def test_event_payload_carries_what_the_approval_surface_needs():
    _report, event, _emitted = run_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40}, ledger=_ledger())
    assert event.payload["idempotency_key"].startswith("shopify-stock-sync:")
    assert event.payload["n_divergent"] == 1
    assert event.payload["target"] == TARGET
    assert event.payload["missing_skus"] == []


def test_shopify_ahead_of_odoo_is_high_severity():
    # Shopify says 45, Odoo says 40 -> five units that can be oversold.
    _report, event, _emitted = run_stock_sync(_store({"SKU-1": 45}), {"SKU-1": 40}, ledger=_ledger())
    assert event.severity == "high"


def test_shopify_behind_odoo_is_medium_severity():
    _report, event, _emitted = run_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40}, ledger=_ledger())
    assert event.severity == "medium"


def test_the_same_divergence_twice_in_one_window_emits_once():
    ledger = _ledger()
    ts = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    _r1, _e1, first = run_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40}, ledger=ledger, now=ts)
    _r2, _e2, second = run_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40}, ledger=ledger,
                                      now=ts + timedelta(minutes=5))
    assert first is True
    assert second is False


def test_a_different_divergence_emits_again():
    ledger = _ledger()
    ts = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    _r1, _e1, first = run_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40}, ledger=ledger, now=ts)
    _r2, _e2, second = run_stock_sync(_store({"SKU-1": 30}), {"SKU-1": 40}, ledger=ledger,
                                      now=ts + timedelta(minutes=5))
    assert first is True
    assert second is True


def test_a_persisting_divergence_emits_again_after_the_window():
    # The ledger dedups within a 1h window; the cron runs every 4h, so a
    # divergence that persists re-emits. That is a reminder, not noise.
    ledger = _ledger()
    ts = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    _r1, _e1, first = run_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40}, ledger=ledger, now=ts)
    _r2, _e2, second = run_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40}, ledger=ledger,
                                      now=ts + timedelta(hours=4))
    assert first is True
    assert second is True
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --no-sync pytest tests/test_shopify_stock_sync.py -q
```

Esperado: `ImportError: cannot import name 'EVENT_TYPE' from 'jobs.shopify_stock_sync'`.

- [ ] **Step 3: Write the implementation**

Añade los imports al principio de `jobs/shopify_stock_sync.py`, junto a los existentes:

```python
from datetime import datetime

from scm_agent.events import Event, EventLedger
```

Y añade al final del archivo:

```python
#: Event type routed to tier T2 in config/event_routing.yaml.
EVENT_TYPE = "shopify_stock_divergence"
SOURCE = "shopify_stock_sync"


def _severity(changeset: writeback.Changeset) -> str:
    """Rate the divergence: high when Shopify is ahead of Odoo.

    Shopify holding more than Odoo means units that can be oversold.

    Shopify lagging behind is a lost sale: annoying, recoverable, and it does
    not put the shop in the position of selling stock it does not have.
    """
    for change in changeset.changes:
        if float(change.before) > float(change.after):
            return "high"
    return "medium"


def run_stock_sync(
    store: Any,
    desired: dict[str, float],
    *,
    ledger: EventLedger,
    now: datetime | None = None,
) -> tuple[SyncReport, Event | None, bool]:
    """Plan the sync and emit one event describing what needs approving.

    Returns the report, the event (None when there is nothing to propose) and
    whether the ledger accepted it -- False means the same divergence was
    already recorded within the dedup window.

    ``now`` sets the event timestamp, which is what the dedup window is
    measured against; passing it explicitly makes the dedup behaviour
    deterministic in tests and correct in a replay.
    """
    report = plan_stock_sync(store, desired)
    if report.changeset is None:
        return report, None, False

    event = Event(
        type=EVENT_TYPE,
        severity=_severity(report.changeset),
        source=SOURCE,
        dedup_key=report.changeset.content_hash,
        payload={
            "idempotency_key": report.changeset.idempotency_key,
            "target": TARGET,
            "n_divergent": len(report.changeset.changes),
            "missing_skus": list(report.missing_skus),
        },
        **({"ts": now} if now is not None else {}),
    )
    return report, event, ledger.emit(event)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run --no-sync pytest tests/test_shopify_stock_sync.py -q
```

Esperado: 17 passed.

- [ ] **Step 5: Lint**

```bash
uv run --no-sync ruff check .
```

Esperado: `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git checkout -- uv.lock
git add jobs/shopify_stock_sync.py tests/test_shopify_stock_sync.py
git commit -m "feat(sync): emitir el evento de divergencia de stock

El dedup_key es el content_hash del changeset, asi que dos ticks juntos no
duplican el aviso pero una divergencia que cambia si genera uno nuevo.
Severidad alta solo cuando Shopify va por delante de Odoo: ese es el caso
que permite vender lo que no hay."
```

---

### Task 3: Cablear el job programado y la ruta

**Files:**
- Modify: `jobs/shopify_stock_sync.py` (añadir al final)
- Modify: `tests/test_shopify_stock_sync.py` (añadir al final)
- Modify: `config/event_routing.yaml` (añadir una ruta bajo `routes:`)

**Interfaces:**
- Consumes: `run_stock_sync`, `EVENT_TYPE` de la Tarea 2.
- Produces:
  - `desired_from_odoo(odoo) -> dict[str, float]`
  - `STOCK_SYNC_JOB: ScheduledJob` con `id="shopify_stock_sync"`

**El adaptador de Odoo.** `OdooConnector.inventory_levels()` devuelve `list[InventoryLevel]`, con campos `sku: str`, `available: float`, `location: str = "default"`. El job necesita `dict[str, float]`. Ese mapeo es lo único que ata B1 a Odoo, y por eso vive en su propia función: B2 pasará la salida del repricing por el mismo sitio sin tocar nada más.

Si un SKU aparece en varias ubicaciones de Odoo, **se suman**: Shopify recibe un único nivel por SKU en la ubicación configurada.

- [ ] **Step 1: Write the failing tests**

Añade al final de `tests/test_shopify_stock_sync.py`:

```python
from src.connectors import InventoryLevel  # noqa: E402
from jobs.shopify_stock_sync import (  # noqa: E402
    STOCK_SYNC_JOB,
    desired_from_odoo,
)


class _FakeOdoo:
    def __init__(self, levels: list[InventoryLevel]) -> None:
        self._levels = levels

    def inventory_levels(self) -> list[InventoryLevel]:
        return list(self._levels)


def test_desired_from_odoo_maps_sku_to_quantity():
    odoo = _FakeOdoo([InventoryLevel("SKU-1", 40.0), InventoryLevel("SKU-2", 12.0)])
    assert desired_from_odoo(odoo) == {"SKU-1": 40.0, "SKU-2": 12.0}


def test_desired_from_odoo_sums_across_locations():
    odoo = _FakeOdoo([
        InventoryLevel("SKU-1", 30.0, "WH-A"),
        InventoryLevel("SKU-1", 10.0, "WH-B"),
    ])
    assert desired_from_odoo(odoo) == {"SKU-1": 40.0}


def test_desired_from_odoo_on_an_empty_erp_is_empty():
    assert desired_from_odoo(_FakeOdoo([])) == {}


def test_the_scheduled_job_is_registrable():
    assert STOCK_SYNC_JOB.id == "shopify_stock_sync"
    assert STOCK_SYNC_JOB.trigger == "interval"
    assert STOCK_SYNC_JOB.trigger_args == {"hours": 4}


def test_the_event_type_is_routed_at_tier_t2():
    import yaml

    with open("config/event_routing.yaml", encoding="utf-8") as fh:
        routing = yaml.safe_load(fh)
    route = routing["routes"][EVENT_TYPE]
    assert route["autonomy_tier"] == "T2"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --no-sync pytest tests/test_shopify_stock_sync.py -q
```

Esperado: `ImportError: cannot import name 'STOCK_SYNC_JOB'`.

- [ ] **Step 3: Write the implementation**

Añade el import al principio de `jobs/shopify_stock_sync.py`:

```python
from .scheduler import ScheduledJob
```

Y añade al final del archivo:

```python
#: Matches the external cron's cadence for the other scheduled jobs.
DEFAULT_CADENCE_HOURS = 4


def desired_from_odoo(odoo: Any) -> dict[str, float]:
    """Map ``OdooConnector.inventory_levels()`` to the SKU -> quantity shape.

    This is the ONLY thing tying the stock sync to Odoo, which is why it lives
    apart from the job itself: the price sync will feed the same job from the
    repricing engine instead, with nothing else changing.

    A SKU stocked in several Odoo locations is SUMMED -- Shopify holds one
    level per SKU at the configured location, so the shop's sellable quantity
    is the total on hand, not whichever warehouse happened to be read last.
    """
    totals: dict[str, float] = {}
    for level in odoo.inventory_levels():
        totals[level.sku] = totals.get(level.sku, 0.0) + float(level.available)
    return totals


def _not_wired() -> None:
    """Placeholder callable: this job is registrable but not yet wired to credentials.

    Building a live OdooConnector and ShopifyInventoryStore needs ODOO_URL,
    ODOO_DB, ODOO_USERNAME, ODOO_API_KEY, SHOPIFY_SHOP_URL and
    SHOPIFY_ACCESS_TOKEN. That is deploy-time wiring, it cannot run offline,
    and it is deliberately NOT part of this plan -- see the plan's closing
    section. Raising rather than silently doing nothing means a deploy that
    schedules this job before wiring it finds out immediately.
    """
    raise NotImplementedError(
        "shopify_stock_sync is not wired to credentials yet -- see "
        "docs/superpowers/plans/2026-08-03-shopify-stock-sync.md"
    )


STOCK_SYNC_JOB = ScheduledJob(
    id="shopify_stock_sync",
    func=_not_wired,
    trigger="interval",
    trigger_args={"hours": DEFAULT_CADENCE_HOURS},
)
```

**Importante sobre `func`.** Registrar el job requiere construir los conectores desde el entorno (`ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_API_KEY`, `SHOPIFY_SHOP_URL`, `SHOPIFY_ACCESS_TOKEN`) — cableado de despliegue con credenciales, que no se puede testear offline y que este plan NO construye. `func` queda como un no-op explícito y comentado: el `ScheduledJob` es registrable y testeable ahora, y conectarlo a credenciales reales es trabajo aparte, declarado en la sección final. **No inventes el cableado de credenciales**: un job que construye clientes reales rompería el requisito de que todo corre offline.

- [ ] **Step 4: Add the routing entry**

En `config/event_routing.yaml`, bajo `routes:`, añade junto a las demás:

```yaml
  shopify_stock_divergence:
    tool: inventory_optimization
    param_builder: inventory_from_stock_event
    autonomy_tier: T2
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run --no-sync pytest tests/test_shopify_stock_sync.py -q
```

Esperado: 22 passed.

- [ ] **Step 6: Run the full suite**

```bash
uv run --no-sync pytest tests/ -q
```

Esperado: verde, cero regresiones. La línea base tras el merge de A es **4792 passed · 24 skipped** en este worktree (los 24 saltados son ambientales: `graphify-out/` no está presente aquí). Si algo rojo no lo tocaste tú, repórtalo en vez de arreglarlo a ciegas.

- [ ] **Step 7: Lint**

```bash
uv run --no-sync ruff check .
```

Esperado: `All checks passed!`.

- [ ] **Step 8: Commit**

```bash
git checkout -- uv.lock
git add jobs/shopify_stock_sync.py tests/test_shopify_stock_sync.py config/event_routing.yaml
git commit -m "feat(sync): cablear el job programado y la ruta a tier T2

El adaptador de Odoo suma las ubicaciones: Shopify tiene un nivel por SKU.
La ruta va como dato en event_routing.yaml, sin tocar codigo. El func del
ScheduledJob queda como no-op: construir los clientes desde credenciales es
cableado de despliegue y no se puede testear offline."
```

---

## Verificación final

- [ ] `uv run --no-sync pytest tests/ -q` en verde, sin regresiones sobre 4792.
- [ ] `uv run --no-sync ruff check .` limpio.
- [ ] `grep -rn "set_inventory\|adjust_inventory\|\.commit(" jobs/shopify_stock_sync.py` no devuelve nada — el job no escribe.
- [ ] `git diff origin/main --stat` toca solo los tres archivos de la tabla, más el spec.
- [ ] `git status` limpio, sin `uv.lock`.

## Lo que este trabajo NO entrega

- **El cableado de credenciales.** `STOCK_SYNC_JOB.func` es un no-op. Conectarlo requiere construir `OdooConnector` y `ShopifyInventoryStore` desde variables de entorno y registrarlo en `POST /api/jobs/run-scheduled`, junto a `PRICE_MONITOR_JOB` y `PRICE_WATCH_JOB`. Es trabajo de despliegue, no se puede verificar offline, y merece su propio ciclo.
- **La superficie de aprobación.** El evento se emite y se rutea a T2; quién lo pinta y dónde se pulsa el botón ya existe fuera de este job y no se toca aquí.
- **Verificación contra sistemas reales.** Como en A: todo el testing es offline. Que el sync funcione contra una tienda y un Odoo reales queda sin medir hasta que haya credenciales.

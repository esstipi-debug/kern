# Entorno de simulacion con marcador — plan de implementacion

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir un mundo de reposicion generado con semilla sobre el que corren un piso heuristico, Kern y un oraculo con informacion perfecta, viendo el mundo por una lente con cuatro niveles de suciedad, y que produce dos numeros por nivel: cuanto cayo el marcador y cuanto de esa caida Kern anuncio.

**Architecture:** Cuatro unidades puras en `src/simworld/` — `world.py` (la verdad, nadie la ve), `lens.py` (el CSV sucio que Kern lee), `runners.py` (los cuatro corredores tras un contrato unico) y `scorer.py` (el contrato de KPI portado de gym-invmgmt mas la degradacion anunciada). Un CLI en `examples/run_world_sim.py`. Kern entra por `Orchestrator.run` completo; la unica modificacion fuera de `simworld/` es exponer el reporte estructurado en `JobResult`.

**Tech Stack:** Python 3.11+, numpy, pandas, pytest. Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-07-27-simworld-marcador-design.md`

## Global Constraints

- Python 3.11+. Tests con `PYTHONPATH=.` y `pytest tests/ -q`.
- Lint que exige CI: `ruff check src tests examples`.
- **ASCII puro en todo `print()` de consola.** Windows usa cp1252 y rompe con guiones largos. Los `.md` escritos en utf-8 estan bien.
- **Ningun import de extra opcional a nivel de modulo** en la cadena de arranque (`webapp.app` -> `scm_agent` -> `tools` -> `jobs` -> `src`). `simworld` no esta en esa cadena, pero tampoco debe entrar a ella.
- `deliverables/` esta gitignored. Nada de output generado se commitea.
- Un archivo = una responsabilidad, 200-400 lineas tipico, 800 maximo.
- Sin mutacion: dataclasses `frozen=True`, funciones que devuelven copias nuevas.
- Rama: `feat/simworld-marcador`. Commits frecuentes, uno por tarea como minimo.
- **`simworld` NO se registra como herramienta del agente.** Es un arnes, no una capacidad de cliente. Nada de `register()` en `scm_agent/tools.py`.

## Decision tomada durante la planificacion (no estaba en el spec)

**Kern replanifica cada 4 semanas, no cada semana.** La politica se sostiene
entre replanificaciones y el mundo la aplica todas las semanas. Razones: (a) es
lo que hace un cliente real, que revisa el mes; (b) baja las llamadas al
orquestador de 52 a 13 por corrida, o sea 208 en toda la grilla en vez de 832.
El piso replanifica nunca (esa es su definicion). El oraculo decide cada semana
porque no tiene costo de replanificacion. La cadencia es un parametro
(`replan_every`), no una constante escondida.

## Estructura de archivos

| Archivo | Responsabilidad |
|---|---|
| `src/simworld/__init__.py` | Reexporta la superficie publica |
| `src/simworld/world.py` | La verdad: generacion con semilla y fisica semanal. Nadie salvo el marcador la lee |
| `src/simworld/lens.py` | El CSV que Kern lee. Nivel 0 canonico, inyectores de suciedad 1-3 |
| `src/simworld/runners.py` | Los cuatro corredores tras `(view) -> Policy` |
| `src/simworld/scorer.py` | Contrato de KPI, captura normalizada, degradacion anunciada |
| `examples/run_world_sim.py` | CLI. Escribe `deliverables/simworld/report.{md,json}` |
| `scm_agent/types.py` | **Modificar**: campo opcional `report` en `JobResult` |
| `scm_agent/orchestrator.py` | **Modificar**: poblarlo en el camino de exito |
| `tests/test_simworld_world.py` | Generacion, fisica, conservacion de masa, reproducibilidad |
| `tests/test_simworld_lens.py` | Un test por inyector, mas la propiedad de no-mutacion |
| `tests/test_simworld_runners.py` | Contrato de los corredores, traduccion del `GuidedOutcome` |
| `tests/test_simworld_scorer.py` | KPIs, captura normalizada, atribucion por periodo |
| `tests/test_simworld_e2e.py` | Corrida corta y golden de regresion |

---

### Task 1: El mundo — generacion con semilla

**Files:**
- Create: `src/simworld/__init__.py`
- Create: `src/simworld/world.py`
- Test: `tests/test_simworld_world.py`

**Interfaces:**
- Consumes: nada (primera tarea)
- Produces: `Sku`, `Supplier`, `World`, `WorldState`, `generate_world(seed:int, n_skus:int, n_suppliers:int, weeks:int, regime:str) -> World`. `World` expone `.skus: tuple[Sku, ...]`, `.suppliers: tuple[Supplier, ...]`, `.demand: np.ndarray` de forma `(n_skus, weeks)`, `.real_lead_weeks: np.ndarray` de forma `(n_skus, weeks)`, `.weeks: int`, `.seed: int`. `WorldState` expone `.week: int`, `.on_hand: np.ndarray`, `.in_transit: tuple[tuple[int,int,float], ...]` como `(sku_idx, semana_de_llegada, cantidad)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_simworld_world.py
import numpy as np
import pytest

from src.simworld.world import generate_world, initial_state


def test_same_seed_gives_identical_world():
    a = generate_world(seed=7, n_skus=10, n_suppliers=3, weeks=20, regime="seasonal_trend")
    b = generate_world(seed=7, n_skus=10, n_suppliers=3, weeks=20, regime="seasonal_trend")
    assert np.array_equal(a.demand, b.demand)
    assert np.array_equal(a.real_lead_weeks, b.real_lead_weeks)
    assert [s.sku for s in a.skus] == [s.sku for s in b.skus]


def test_different_seed_gives_different_world():
    a = generate_world(seed=7, n_skus=10, n_suppliers=3, weeks=20, regime="seasonal_trend")
    b = generate_world(seed=8, n_skus=10, n_suppliers=3, weeks=20, regime="seasonal_trend")
    assert not np.array_equal(a.demand, b.demand)


def test_demand_shape_and_non_negative():
    w = generate_world(seed=1, n_skus=6, n_suppliers=2, weeks=30, regime="seasonal_trend")
    assert w.demand.shape == (6, 30)
    assert (w.demand >= 0).all()


def test_promised_lead_time_differs_from_real_at_least_once():
    w = generate_world(seed=3, n_skus=12, n_suppliers=4, weeks=52, regime="seasonal_trend")
    promised = np.array([w.suppliers[s.supplier_idx].promised_lead_weeks for s in w.skus])
    assert (w.real_lead_weeks != promised[:, None]).any()


def test_unknown_regime_raises():
    with pytest.raises(ValueError, match="regime"):
        generate_world(seed=1, n_skus=2, n_suppliers=1, weeks=5, regime="nope")


def test_initial_state_is_week_zero_with_nothing_in_transit():
    w = generate_world(seed=1, n_skus=4, n_suppliers=2, weeks=10, regime="seasonal_trend")
    st = initial_state(w)
    assert st.week == 0
    assert st.in_transit == ()
    assert st.on_hand.shape == (4,)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_simworld_world.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'src.simworld'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/simworld/__init__.py
"""Arnes de simulacion: un mundo con verdad conocida para puntuar decisiones.

No es una capacidad del agente. Nada de aca se registra en scm_agent/tools.py.
"""

from src.simworld.world import Sku, Supplier, World, WorldState, generate_world, initial_state

__all__ = ["Sku", "Supplier", "World", "WorldState", "generate_world", "initial_state"]
```

```python
# src/simworld/world.py
"""La verdad del mundo simulado — generada una vez, con semilla, e inmutable.

Nadie fuera del marcador lee esto. Kern ve el mundo unicamente a traves de
``src/simworld/lens.py``. La demanda verdadera y los lead times reales se sortean
en la generacion, no durante la corrida: es lo que garantiza que los cuatro
corredores enfrenten la realizacion identica y que ninguno pueda tener suerte.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

REGIMES = ("seasonal_trend",)

WEEKS_PER_YEAR = 52.0


@dataclass(frozen=True)
class Sku:
    sku: str
    unit_cost: float
    price: float
    supplier_idx: int


@dataclass(frozen=True)
class Supplier:
    name: str
    promised_lead_weeks: int
    otif: float  # fraccion de ordenes que llegan en el lead time prometido


@dataclass(frozen=True)
class World:
    seed: int
    weeks: int
    regime: str
    skus: tuple[Sku, ...]
    suppliers: tuple[Supplier, ...]
    demand: np.ndarray          # (n_skus, weeks) — la demanda que realmente ocurre
    real_lead_weeks: np.ndarray  # (n_skus, weeks) — lead real de una orden puesta en la semana t


@dataclass(frozen=True)
class WorldState:
    week: int
    on_hand: np.ndarray
    in_transit: tuple[tuple[int, int, float], ...]  # (sku_idx, semana_de_llegada, cantidad)


def generate_world(
    *,
    seed: int,
    n_skus: int,
    n_suppliers: int,
    weeks: int,
    regime: str = "seasonal_trend",
) -> World:
    """Sortea un mundo completo. Misma semilla, mismo mundo, siempre."""
    if regime not in REGIMES:
        raise ValueError(f"regime desconocido: {regime!r}; conocidos: {REGIMES}")
    rng = np.random.default_rng(seed)

    suppliers = tuple(
        Supplier(
            name=f"Proveedor-{i + 1}",
            promised_lead_weeks=int(rng.choice([1, 2, 3, 4])),
            otif=float(rng.uniform(0.55, 0.95)),
        )
        for i in range(n_suppliers)
    )

    skus = []
    for i in range(n_skus):
        unit_cost = float(np.round(rng.uniform(2.0, 90.0), 2))
        skus.append(
            Sku(
                sku=f"SKU-{1000 + i}",
                unit_cost=unit_cost,
                price=float(np.round(unit_cost * rng.uniform(1.4, 2.8), 2)),
                supplier_idx=int(rng.integers(0, n_suppliers)),
            )
        )

    demand = _seasonal_trend_demand(rng, n_skus, weeks)
    real_lead = _real_lead_weeks(rng, skus, suppliers, weeks)
    return World(
        seed=seed, weeks=weeks, regime=regime,
        skus=tuple(skus), suppliers=suppliers,
        demand=demand, real_lead_weeks=real_lead,
    )


def _seasonal_trend_demand(rng: np.random.Generator, n_skus: int, weeks: int) -> np.ndarray:
    t = np.arange(weeks, dtype=float)
    base = rng.uniform(3.0, 40.0, size=(n_skus, 1))
    trend = rng.uniform(-0.25, 0.60, size=(n_skus, 1))
    amp = rng.uniform(0.05, 0.45, size=(n_skus, 1))
    phase = rng.uniform(0.0, 2 * np.pi, size=(n_skus, 1))
    seasonal = 1.0 + amp * np.sin(2 * np.pi * t / WEEKS_PER_YEAR + phase)
    level = base * (1.0 + trend * t / max(weeks, 1)) * seasonal
    noise = rng.normal(0.0, 0.18, size=(n_skus, weeks))
    return np.maximum(np.round(level * (1.0 + noise)), 0.0)


def _real_lead_weeks(
    rng: np.random.Generator,
    skus: list[Sku],
    suppliers: tuple[Supplier, ...],
    weeks: int,
) -> np.ndarray:
    out = np.zeros((len(skus), weeks), dtype=int)
    for i, sku in enumerate(skus):
        sup = suppliers[sku.supplier_idx]
        on_time = rng.random(weeks) < sup.otif
        slip = rng.integers(1, 4, size=weeks)
        out[i] = np.where(on_time, sup.promised_lead_weeks, sup.promised_lead_weeks + slip)
    return out


def initial_state(world: World) -> WorldState:
    """Arranca con cuatro semanas de la demanda media de cada SKU en mano."""
    start = np.round(world.demand[:, : min(4, world.weeks)].mean(axis=1) * 4.0)
    return WorldState(week=0, on_hand=start.astype(float), in_transit=())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_simworld_world.py -v`
Expected: 6 passed

- [ ] **Step 5: Lint and commit**

```bash
ruff check src tests
git add src/simworld/__init__.py src/simworld/world.py tests/test_simworld_world.py
git commit -m "feat(simworld): mundo generado con semilla, verdad reproducible"
```

---

### Task 2: El mundo — la fisica semanal

**Files:**
- Modify: `src/simworld/world.py` (agregar `advance` y `WeekOutcome`)
- Modify: `src/simworld/__init__.py` (reexportar)
- Test: `tests/test_simworld_world.py` (agregar casos)

**Interfaces:**
- Consumes: `World`, `WorldState`, `generate_world`, `initial_state` de la Task 1
- Produces: `WeekOutcome` con campos `week:int`, `demand:np.ndarray`, `sold:np.ndarray`, `lost:np.ndarray`, `received:np.ndarray`, `ordered:np.ndarray`, `on_hand_end:np.ndarray`, y `advance(world, state, orders: dict[str, float]) -> tuple[WorldState, WeekOutcome]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_simworld_world.py  (agregar al final)
from src.simworld.world import advance


def _tiny():
    return generate_world(seed=11, n_skus=3, n_suppliers=1, weeks=12, regime="seasonal_trend")


def test_advance_conserves_mass():
    w = _tiny()
    st = initial_state(w)
    before = st.on_hand.copy()
    st2, out = advance(w, st, {w.skus[0].sku: 25.0})
    # lo que habia + lo recibido = lo vendido + lo que queda
    left = before + out.received
    right = out.sold + out.on_hand_end
    assert np.allclose(left, right)


def test_lost_sales_are_unmet_demand_never_negative():
    w = _tiny()
    st = initial_state(w)
    _, out = advance(w, st, {})
    assert np.allclose(out.lost, np.maximum(out.demand - out.sold, 0.0))
    assert (out.lost >= 0).all()


def test_order_arrives_after_the_real_lead_time_not_the_promised_one():
    w = _tiny()
    st = initial_state(w)
    sku0 = w.skus[0].sku
    lead = int(w.real_lead_weeks[0, 0])
    st, _ = advance(w, st, {sku0: 100.0})
    for _ in range(lead - 1):
        st, out = advance(w, st, {})
        assert out.received[0] == 0.0
    st, out = advance(w, st, {})
    assert out.received[0] == 100.0


def test_unknown_sku_in_orders_raises():
    w = _tiny()
    st = initial_state(w)
    with pytest.raises(KeyError, match="SKU-9999"):
        advance(w, st, {"SKU-9999": 5.0})


def test_negative_order_raises():
    w = _tiny()
    st = initial_state(w)
    with pytest.raises(ValueError, match="negativa"):
        advance(w, st, {w.skus[0].sku: -3.0})


def test_advance_past_the_horizon_raises():
    w = generate_world(seed=2, n_skus=2, n_suppliers=1, weeks=2, regime="seasonal_trend")
    st = initial_state(w)
    st, _ = advance(w, st, {})
    st, _ = advance(w, st, {})
    with pytest.raises(IndexError, match="horizonte"):
        advance(w, st, {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_simworld_world.py -k advance -v`
Expected: FAIL con `ImportError: cannot import name 'advance'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/simworld/world.py  (agregar al final)


@dataclass(frozen=True)
class WeekOutcome:
    week: int
    demand: np.ndarray
    sold: np.ndarray
    lost: np.ndarray
    received: np.ndarray
    ordered: np.ndarray
    on_hand_end: np.ndarray


def advance(
    world: World,
    state: WorldState,
    orders: dict[str, float],
) -> tuple[WorldState, WeekOutcome]:
    """Una semana: llega lo que vencio, se vende lo que hay, se emite lo pedido.

    Venta perdida, no backorder: la demanda insatisfecha se pierde y no se arrastra.
    """
    t = state.week
    if t >= world.weeks:
        raise IndexError(f"semana {t} fuera del horizonte de {world.weeks}")

    index = {s.sku: i for i, s in enumerate(world.skus)}
    n = len(world.skus)

    ordered = np.zeros(n, dtype=float)
    for sku, qty in orders.items():
        if sku not in index:
            raise KeyError(f"SKU desconocida en las ordenes: {sku}")
        if qty < 0:
            raise ValueError(f"cantidad negativa para {sku}: {qty}")
        ordered[index[sku]] += float(qty)

    received = np.zeros(n, dtype=float)
    still_in_transit = []
    for sku_idx, arrival, qty in state.in_transit:
        if arrival <= t:
            received[sku_idx] += qty
        else:
            still_in_transit.append((sku_idx, arrival, qty))

    available = state.on_hand + received
    demand = world.demand[:, t].astype(float)
    sold = np.minimum(available, demand)
    lost = demand - sold
    on_hand_end = available - sold

    for i in range(n):
        if ordered[i] > 0:
            arrival = t + int(world.real_lead_weeks[i, t])
            still_in_transit.append((i, arrival, float(ordered[i])))

    next_state = WorldState(
        week=t + 1,
        on_hand=on_hand_end,
        in_transit=tuple(still_in_transit),
    )
    outcome = WeekOutcome(
        week=t, demand=demand, sold=sold, lost=lost,
        received=received, ordered=ordered, on_hand_end=on_hand_end,
    )
    return next_state, outcome
```

Actualizar el reexport:

```python
# src/simworld/__init__.py  — reemplazar el bloque de import y __all__
from src.simworld.world import (
    Sku,
    Supplier,
    WeekOutcome,
    World,
    WorldState,
    advance,
    generate_world,
    initial_state,
)

__all__ = [
    "Sku", "Supplier", "WeekOutcome", "World", "WorldState",
    "advance", "generate_world", "initial_state",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_simworld_world.py -v`
Expected: 12 passed

- [ ] **Step 5: Lint and commit**

```bash
ruff check src tests
git add src/simworld/world.py src/simworld/__init__.py tests/test_simworld_world.py
git commit -m "feat(simworld): fisica semanal con venta perdida y lead time real"
```

---

### Task 3: La lente — nivel 0, el export canonico

**Files:**
- Create: `src/simworld/lens.py`
- Modify: `src/simworld/__init__.py`
- Test: `tests/test_simworld_lens.py`

**Interfaces:**
- Consumes: `World`, `WeekOutcome` de las Tasks 1-2
- Produces: `export_history(world, outcomes: list[WeekOutcome], path: Path, level: int = 0) -> Path` y `history_frame(world, outcomes) -> pd.DataFrame`. El frame lleva las columnas canonicas que espera `jobs/intake.py`: `date`, `product_id`, `quantity`, `unit_cost`, `lead_time_days`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_simworld_lens.py
import numpy as np
import pandas as pd
import pytest

from src.simworld.lens import export_history, history_frame
from src.simworld.world import advance, generate_world, initial_state


def _run_weeks(n_weeks=8, seed=5, n_skus=4):
    w = generate_world(seed=seed, n_skus=n_skus, n_suppliers=2, weeks=n_weeks,
                       regime="seasonal_trend")
    st = initial_state(w)
    outs = []
    for _ in range(n_weeks):
        st, out = advance(w, st, {})
        outs.append(out)
    return w, outs


def test_level_zero_has_the_canonical_columns_intake_expects():
    w, outs = _run_weeks()
    df = history_frame(w, outs)
    for col in ("date", "product_id", "quantity", "unit_cost", "lead_time_days"):
        assert col in df.columns


def test_level_zero_row_count_is_skus_times_weeks():
    w, outs = _run_weeks(n_weeks=8, n_skus=4)
    assert len(history_frame(w, outs)) == 4 * 8


def test_level_zero_quantity_is_the_sold_quantity():
    w, outs = _run_weeks(n_weeks=3, n_skus=2)
    df = history_frame(w, outs)
    total = sum(float(o.sold.sum()) for o in outs)
    assert df["quantity"].sum() == pytest.approx(total)


def test_export_writes_a_readable_csv(tmp_path):
    w, outs = _run_weeks()
    p = export_history(w, outs, tmp_path / "h.csv", level=0)
    assert p.exists()
    back = pd.read_csv(p)
    assert len(back) == len(history_frame(w, outs))


def test_lens_never_mutates_the_world():
    w, outs = _run_weeks()
    before = w.demand.copy()
    history_frame(w, outs)
    assert np.array_equal(w.demand, before)


def test_unknown_level_raises(tmp_path):
    w, outs = _run_weeks()
    with pytest.raises(ValueError, match="nivel"):
        export_history(w, outs, tmp_path / "h.csv", level=9)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_simworld_lens.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'src.simworld.lens'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/simworld/lens.py
"""La lente: el archivo que Kern realmente lee, con suciedad graduable.

Nunca toca el mundo. El marcador se calcula siempre contra la verdad limpia,
aunque Kern haya visto basura: si Kern pidio mal porque el exporte tenia la SKU
duplicada, el quiebre se cuenta igual contra el.

Nivel 0 es el CSV canonico que ``jobs/intake.py`` espera. Los niveles 1-3
acumulan: 3 incluye 2 incluye 1.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.simworld.world import WeekOutcome, World

LEVELS = (0, 1, 2, 3)

# Lunes de una semana cualquiera; el mundo es relativo, la fecha absoluta no importa
# mientras sea consistente y parseable.
EPOCH = pd.Timestamp("2025-01-06")


def history_frame(world: World, outcomes: list[WeekOutcome]) -> pd.DataFrame:
    """El historico limpio: una fila por SKU y semana, con lo efectivamente vendido."""
    rows = []
    for out in outcomes:
        date = EPOCH + pd.Timedelta(weeks=out.week)
        for i, sku in enumerate(world.skus):
            sup = world.suppliers[sku.supplier_idx]
            rows.append(
                {
                    "date": date.strftime("%Y-%m-%d"),
                    "product_id": sku.sku,
                    "quantity": float(out.sold[i]),
                    "unit_cost": sku.unit_cost,
                    "lead_time_days": float(sup.promised_lead_weeks * 7),
                }
            )
    return pd.DataFrame(rows)


def export_history(
    world: World,
    outcomes: list[WeekOutcome],
    path: str | Path,
    *,
    level: int = 0,
    seed: int = 0,
) -> Path:
    """Escribe el historico con el nivel de suciedad pedido y devuelve la ruta."""
    if level not in LEVELS:
        raise ValueError(f"nivel de suciedad desconocido: {level}; conocidos: {LEVELS}")
    df = history_frame(world, outcomes)
    df = apply_dirt(df, level=level, seed=seed)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    return out


def apply_dirt(df: pd.DataFrame, *, level: int, seed: int = 0) -> pd.DataFrame:
    """Nivel 0 devuelve el frame tal cual. Los inyectores llegan en las Tasks 4-5."""
    if level not in LEVELS:
        raise ValueError(f"nivel de suciedad desconocido: {level}; conocidos: {LEVELS}")
    return df.copy()
```

Agregar al reexport:

```python
# src/simworld/__init__.py — agregar
from src.simworld.lens import apply_dirt, export_history, history_frame  # noqa: E402
```

y sumar `"apply_dirt", "export_history", "history_frame"` a `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_simworld_lens.py -v`
Expected: 6 passed

- [ ] **Step 5: Comprobar que intake lee el nivel 0 sin perder nada**

```python
# tests/test_simworld_lens.py  (agregar)
from jobs.intake import prepare_tracked


def test_intake_reads_level_zero_without_dropping_rows(tmp_path):
    w, outs = _run_weeks(n_weeks=10, n_skus=5)
    p = export_history(w, outs, tmp_path / "clean.csv", level=0)
    frame, quality = prepare_tracked(p)
    assert quality.n_dropped_bad_date == 0
    assert quality.n_dropped_bad_quantity == 0
    assert quality.synthesized == ()
    assert not frame.empty
```

Run: `PYTHONPATH=. pytest tests/test_simworld_lens.py -v`
Expected: 7 passed. **Si este test falla, el nivel 0 no es canonico y hay que
arreglar `history_frame`, no el test** — todo el marcador se apoya en que el
nivel 0 sea limpio de verdad.

- [ ] **Step 6: Lint and commit**

```bash
ruff check src tests
git add src/simworld/lens.py src/simworld/__init__.py tests/test_simworld_lens.py
git commit -m "feat(simworld): lente nivel 0, export canonico que intake lee entero"
```

---

### Task 4: La lente — suciedad nivel 1 y 2

**Files:**
- Modify: `src/simworld/lens.py`
- Test: `tests/test_simworld_lens.py`

**Interfaces:**
- Consumes: `apply_dirt(df, level, seed)` de la Task 3
- Produces: inyectores con firma `(_df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame`, registrados en `DIRT_BY_LEVEL: dict[int, tuple[Injector, ...]]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_simworld_lens.py  (agregar)
import numpy as np

from src.simworld.lens import (
    inject_mixed_date_formats,
    inject_mixed_units,
    inject_missing_lead_time,
    inject_returns_as_negatives,
    inject_split_sku,
    inject_thousand_separators,
    inject_whitespace_case,
)


def _clean_frame(n_weeks=12, n_skus=6):
    w, outs = _run_weeks(n_weeks=n_weeks, n_skus=n_skus)
    return history_frame(w, outs)


def test_whitespace_case_changes_only_product_id():
    df = _clean_frame()
    out = inject_whitespace_case(df, np.random.default_rng(0))
    assert (out["quantity"].to_numpy() == df["quantity"].to_numpy()).all()
    assert (out["product_id"] != df["product_id"]).any()


def test_mixed_date_formats_produces_two_distinct_patterns():
    df = _clean_frame()
    out = inject_mixed_date_formats(df, np.random.default_rng(0))
    slashed = out["date"].astype(str).str.contains("/").sum()
    assert 0 < slashed < len(out)


def test_thousand_separators_make_quantity_a_string_column():
    df = _clean_frame()
    out = inject_thousand_separators(df, np.random.default_rng(0))
    assert out["quantity"].dtype == object
    assert out["quantity"].astype(str).str.contains(",").any()


def test_split_sku_creates_a_second_spelling_and_preserves_total_quantity():
    df = _clean_frame()
    out = inject_split_sku(df, np.random.default_rng(0))
    assert out["product_id"].nunique() > df["product_id"].nunique()
    assert out["quantity"].sum() == pytest.approx(df["quantity"].sum())


def test_mixed_units_divides_some_rows_by_the_case_size():
    df = _clean_frame()
    out = inject_mixed_units(df, np.random.default_rng(0))
    assert out["quantity"].sum() < df["quantity"].sum()
    assert len(out) == len(df)


def test_returns_as_negatives_introduces_negative_quantities():
    df = _clean_frame()
    out = inject_returns_as_negatives(df, np.random.default_rng(0))
    assert (out["quantity"] < 0).any()


def test_missing_lead_time_blanks_about_a_third_of_the_suppliers():
    df = _clean_frame()
    out = inject_missing_lead_time(df, np.random.default_rng(0))
    assert out["lead_time_days"].isna().any()
    assert out["lead_time_days"].notna().any()


def _canon(series):
    return series.astype(str).str.strip().str.upper()


def test_levels_accumulate():
    df = _clean_frame()
    l1 = apply_dirt(df, level=1, seed=0)
    l2 = apply_dirt(df, level=2, seed=0)
    # el nivel 2 hereda el desprolijo del 1
    assert l2["product_id"].astype(str).str.contains(" ").any()
    # el nivel 1 solo ensucia la grafia: normalizando vuelve el catalogo original
    assert _canon(l1["product_id"]).nunique() == df["product_id"].nunique()
    # el nivel 2 parte una SKU de verdad: normalizar no la recupera
    assert _canon(l2["product_id"]).nunique() > df["product_id"].nunique()


def test_value_changing_dirt_runs_before_the_cosmetic_layer():
    """Si el formato se aplicara primero, los inyectores semanticos tendrian que
    parsear "1.234,50" y coercionarian a NaN en silencio."""
    df = _clean_frame()
    l2 = apply_dirt(df, level=2, seed=0)
    qty = pd.to_numeric(
        l2["quantity"].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
        errors="coerce",
    )
    assert qty.notna().mean() > 0.9


def test_level_two_degrades_intake_quality(tmp_path):
    w, outs = _run_weeks(n_weeks=16, n_skus=6)
    dirty = apply_dirt(history_frame(w, outs), level=2, seed=0)
    p = tmp_path / "dirty.csv"
    dirty.to_csv(p, index=False)
    _, quality = prepare_tracked(p)
    dropped = (
        quality.n_dropped_bad_date
        + quality.n_dropped_bad_quantity
        + quality.n_dropped_negative_quantity
    )
    assert dropped > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_simworld_lens.py -v`
Expected: FAIL con `ImportError: cannot import name 'inject_whitespace_case'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/simworld/lens.py  (agregar; y agregar `import numpy as np` arriba)

CASE_SIZE = 12.0


def inject_whitespace_case(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    out = df.copy()
    mask = rng.random(len(out)) < 0.35
    ids = out["product_id"].astype(str)
    out.loc[mask, "product_id"] = " " + ids[mask].str.lower()
    return out


def inject_mixed_date_formats(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    out = df.copy()
    mask = rng.random(len(out)) < 0.4
    parsed = pd.to_datetime(out.loc[mask, "date"])
    out.loc[mask, "date"] = parsed.dt.strftime("%d/%m/%Y")
    return out


def inject_thousand_separators(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    out = df.copy()
    mask = rng.random(len(out)) < 0.3
    vals = out.loc[mask, "quantity"].astype(float)
    out["quantity"] = out["quantity"].astype(object)
    out.loc[mask, "quantity"] = [f"{v:,.2f}".replace(".", "#").replace(",", ".").replace("#", ",")
                                 for v in vals]
    return out


def inject_split_sku(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """La misma SKU cargada con dos grafias: la demanda se parte en dos series."""
    out = df.copy()
    ids = sorted(out["product_id"].astype(str).str.strip().unique())
    if not ids:
        return out
    victim = ids[int(rng.integers(0, len(ids)))]
    is_victim = out["product_id"].astype(str).str.strip() == victim
    flip = is_victim & (rng.random(len(out)) < 0.5)
    out.loc[flip, "product_id"] = victim.replace("SKU-", "Sku ")
    return out


def inject_mixed_units(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Algunas filas vienen en cajas y otras en unidades, sin columna que lo diga."""
    out = df.copy()
    mask = rng.random(len(out)) < 0.25
    qty = pd.to_numeric(out["quantity"], errors="coerce")
    out.loc[mask, "quantity"] = (qty[mask] / CASE_SIZE).round(2)
    return out


def inject_returns_as_negatives(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    out = df.copy()
    mask = rng.random(len(out)) < 0.06
    qty = pd.to_numeric(out["quantity"], errors="coerce")
    out.loc[mask, "quantity"] = -(qty[mask] * 0.2).round(2)
    return out


def inject_missing_lead_time(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    out = df.copy()
    leads = sorted(pd.to_numeric(out["lead_time_days"], errors="coerce").dropna().unique())
    if not leads:
        return out
    blanked = {v for v in leads if rng.random() < 0.34}
    mask = pd.to_numeric(out["lead_time_days"], errors="coerce").isin(blanked)
    if not mask.any() and leads:
        mask = pd.to_numeric(out["lead_time_days"], errors="coerce") == leads[0]
    out.loc[mask, "lead_time_days"] = np.nan
    return out


def inject_calendar_gaps(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Semanas sin filas: cero demanda o no se cargo, el archivo no lo dice."""
    out = df.copy()
    dates = sorted(out["date"].astype(str).unique())
    if len(dates) < 4:
        return out
    drop = {d for d in dates if rng.random() < 0.12}
    return out[~out["date"].astype(str).isin(drop)].reset_index(drop=True)


DIRT_BY_LEVEL: dict[int, tuple] = {
    0: (),
    1: (inject_whitespace_case, inject_mixed_date_formats, inject_thousand_separators),
    2: (inject_split_sku, inject_mixed_units, inject_returns_as_negatives,
        inject_missing_lead_time, inject_calendar_gaps),
    3: (),  # Task 5
}
```

Reemplazar `apply_dirt`:

```python
COSMETIC_LEVEL = 1


def apply_dirt(df: pd.DataFrame, *, level: int, seed: int = 0) -> pd.DataFrame:
    """Aplica la suciedad hasta ``level`` inclusive. Los niveles acumulan.

    Orden deliberado: **primero los defectos que cambian el valor** (niveles 2 y
    3), **despues los cosmeticos** del nivel 1. Es el orden real — el dato es lo
    que es, y el formato del exporte se aplica al final — y ademas evita que un
    inyector semantico tenga que parsear ``"1.234,50"`` y lo coercione a NaN en
    silencio, corrompiendo el fixture en vez del archivo.
    """
    if level not in LEVELS:
        raise ValueError(f"nivel de suciedad desconocido: {level}; conocidos: {LEVELS}")
    rng = np.random.default_rng(seed)
    out = df.copy()
    for lvl in range(COSMETIC_LEVEL + 1, level + 1):
        for injector in DIRT_BY_LEVEL[lvl]:
            out = injector(out, rng)
    if level >= COSMETIC_LEVEL:
        for injector in DIRT_BY_LEVEL[COSMETIC_LEVEL]:
            out = injector(out, rng)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_simworld_lens.py -v`
Expected: 17 passed

- [ ] **Step 5: Lint and commit**

```bash
ruff check src tests
git add src/simworld/lens.py tests/test_simworld_lens.py
git commit -m "feat(simworld): inyectores de suciedad nivel 1 y 2"
```

---

### Task 5: La lente — suciedad nivel 3

**Files:**
- Modify: `src/simworld/lens.py`
- Test: `tests/test_simworld_lens.py`

**Interfaces:**
- Consumes: `DIRT_BY_LEVEL`, `apply_dirt` de la Task 4
- Produces: `inject_duplicate_rows`, `inject_zero_cost`, `inject_renamed_supplier`, `inject_unmarked_promo`, poblando `DIRT_BY_LEVEL[3]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_simworld_lens.py  (agregar)
from src.simworld.lens import (
    inject_duplicate_rows,
    inject_renamed_supplier,
    inject_unmarked_promo,
    inject_zero_cost,
)


def test_duplicate_rows_inflates_row_count_and_quantity():
    df = _clean_frame()
    out = inject_duplicate_rows(df, np.random.default_rng(0))
    assert len(out) > len(df)
    assert out["quantity"].sum() > df["quantity"].sum()


def test_zero_cost_zeroes_some_unit_costs_but_not_all():
    df = _clean_frame()
    out = inject_zero_cost(df, np.random.default_rng(0))
    assert (out["unit_cost"] == 0).any()
    assert (out["unit_cost"] > 0).any()


def test_renamed_supplier_splits_one_lead_time_into_two():
    df = _clean_frame()
    out = inject_renamed_supplier(df, np.random.default_rng(0))
    assert "supplier" in out.columns
    assert out["supplier"].nunique() > df.get(
        "supplier", pd.Series(["x"] * len(df))
    ).nunique()


def test_unmarked_promo_creates_a_spike_without_a_flag_column():
    df = _clean_frame()
    out = inject_unmarked_promo(df, np.random.default_rng(0))
    assert out["quantity"].max() > df["quantity"].max()
    assert "promo" not in out.columns


def test_level_three_includes_level_two_and_one():
    df = _clean_frame()
    l3 = apply_dirt(df, level=3, seed=0)
    assert l3["product_id"].astype(str).str.contains(" ").any()   # nivel 1
    assert (pd.to_numeric(l3["quantity"], errors="coerce") < 0).any()  # nivel 2
    assert (pd.to_numeric(l3["unit_cost"], errors="coerce") == 0).any()  # nivel 3


def test_dirt_is_reproducible_for_a_given_seed():
    df = _clean_frame()
    a = apply_dirt(df, level=3, seed=42)
    b = apply_dirt(df, level=3, seed=42)
    pd.testing.assert_frame_equal(a, b)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_simworld_lens.py -k "level_three or duplicate or zero_cost or renamed or promo" -v`
Expected: FAIL con `ImportError: cannot import name 'inject_duplicate_rows'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/simworld/lens.py  (agregar antes de DIRT_BY_LEVEL)


def inject_duplicate_rows(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Doble exportacion: filas identicas repetidas."""
    out = df.copy()
    mask = rng.random(len(out)) < 0.05
    dupes = out[mask]
    return pd.concat([out, dupes], ignore_index=True)


def inject_zero_cost(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    out = df.copy()
    mask = rng.random(len(out)) < 0.08
    out.loc[mask, "unit_cost"] = 0.0
    return out


def inject_renamed_supplier(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Un proveedor renombrado a mitad del historico: el OTIF se parte en dos."""
    out = df.copy()
    leads = pd.to_numeric(out["lead_time_days"], errors="coerce")
    out["supplier"] = leads.map(lambda v: f"Prov-{int(v)}d" if pd.notna(v) else "Prov-?")
    dates = sorted(out["date"].astype(str).unique())
    if len(dates) < 4:
        return out
    cut = dates[len(dates) // 2]
    victim = out["supplier"].mode()
    if victim.empty:
        return out
    target = victim.iloc[0]
    mask = (out["supplier"] == target) & (out["date"].astype(str) >= cut)
    out.loc[mask, "supplier"] = f"{target} SpA"
    return out


def inject_unmarked_promo(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Un pico de promocion que no es demanda base, y ninguna columna lo dice."""
    out = df.copy()
    dates = sorted(out["date"].astype(str).unique())
    if not dates:
        return out
    week = dates[int(rng.integers(0, len(dates)))]
    mask = out["date"].astype(str) == week
    qty = pd.to_numeric(out["quantity"], errors="coerce")
    out.loc[mask, "quantity"] = (qty[mask] * 4.0).round(2)
    return out
```

Reemplazar la entrada del nivel 3:

```python
    3: (inject_duplicate_rows, inject_zero_cost, inject_renamed_supplier,
        inject_unmarked_promo),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_simworld_lens.py -v`
Expected: 23 passed

- [ ] **Step 5: Lint and commit**

```bash
ruff check src tests
git add src/simworld/lens.py tests/test_simworld_lens.py
git commit -m "feat(simworld): inyectores de suciedad nivel 3"
```

---

### Task 6: Exponer el reporte estructurado en `JobResult`

**Files:**
- Modify: `scm_agent/types.py`
- Modify: `scm_agent/orchestrator.py:178-184` (el `JobResult` del camino de exito)
- Test: `tests/test_simworld_runners.py`

**Interfaces:**
- Consumes: nada de simworld
- Produces: `JobResult.report: object | None = None`, poblado con `produced.report` solo en `STATUS_OK`

**Por que:** `produced.report` hoy no sale del orquestador — el `JobResult` lleva
solo rutas de archivo. Recuperar las cantidades parseando el `deliverable.xlsx`
seria fragil y lento (208 idas y vueltas a Excel). El campo es opcional, con
default `None`, y **no afecta ninguna superficie serializada**: se verifico que
nadie hace `asdict(result)` — el webapp arma su payload campo por campo
(`webapp/app.py:661-673`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_simworld_runners.py
import pandas as pd
import pytest

from scm_agent.orchestrator import Orchestrator
from scm_agent.types import JobResult


def test_jobresult_report_defaults_to_none():
    r = JobResult(status="ok", tool=None, confidence=1.0, deliverables={}, summary="")
    assert r.report is None


def test_orchestrator_populates_report_on_success(tmp_path):
    csv = tmp_path / "demand.csv"
    rows = []
    for week in range(20):
        for sku in ("SKU-1", "SKU-2"):
            rows.append({
                "date": pd.Timestamp("2025-01-06") + pd.Timedelta(weeks=week),
                "product_id": sku,
                "quantity": 10 + week % 5,
                "unit_cost": 12.0,
                "lead_time_days": 14,
            })
    pd.DataFrame(rows).to_csv(csv, index=False)

    orch = Orchestrator(clients_root=None)
    result = orch.run(
        "set reorder points and safety stock for these SKUs",
        data_path=str(csv),
        job_type="inventory_optimization",
        out_dir=str(tmp_path / "out"),
    )
    assert result.status == "ok", result.summary
    assert result.report is not None
    assert hasattr(result.report, "recommendations")


def test_webapp_payload_does_not_leak_the_report():
    import inspect

    from webapp import app as webapp_app

    src = inspect.getsource(webapp_app)
    assert "asdict(result)" not in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_simworld_runners.py -v`
Expected: FAIL con `TypeError` o `AttributeError: 'JobResult' object has no attribute 'report'`

- [ ] **Step 3: Write minimal implementation**

En `scm_agent/types.py`, agregar como ultimo campo de `JobResult` (despues de `guided`):

```python
    # El reporte estructurado del tool, para un llamador en proceso que necesita los
    # numeros y no los archivos (el arnes src/simworld/). Es un objeto arbitrario:
    # NUNCA asumir que es serializable ni incluirlo en un payload HTTP/MCP.
    report: object | None = None
```

En `scm_agent/orchestrator.py`, en el `JobResult` del camino de exito (el que
lleva `status=STATUS_OK`), agregar el argumento:

```python
        return JobResult(
            status=STATUS_OK, tool=tool.key, confidence=intent.confidence,
            deliverables={name: str(path) for name, path in written.items()}, summary=summary,
            citations=citations, kb_warnings=self.knowledge.warnings(),
            guided=guided, report=produced.report,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_simworld_runners.py -v`
Expected: 3 passed

- [ ] **Step 5: Verificar que nada del suite existente se rompio**

Run: `PYTHONPATH=. pytest tests/ -q -x`
Expected: todo verde. Es un campo con default; si algo se rompe, es un test que
construye `JobResult` posicionalmente y hay que arreglarlo ahi.

- [ ] **Step 6: Lint and commit**

```bash
ruff check src tests scm_agent
git add scm_agent/types.py scm_agent/orchestrator.py tests/test_simworld_runners.py
git commit -m "feat(agent): exponer el reporte estructurado en JobResult para llamadores en proceso"
```

---

### Task 7: Los corredores — piso y oraculo

**Files:**
- Create: `src/simworld/runners.py`
- Modify: `src/simworld/__init__.py`
- Test: `tests/test_simworld_runners.py`

**Interfaces:**
- Consumes: `World`, `WorldState`, `WeekOutcome` de las Tasks 1-2
- Produces: `Policy` (frozen, con `reorder_point: dict[str,float]`, `order_up_to: dict[str,float]`, `announced: bool`, `note: str`), `naive_policy(world, outcomes) -> Policy`, `apply_policy(world, state, policy) -> dict[str,float]`, `oracle_orders(world, state) -> dict[str,float]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_simworld_runners.py  (agregar)
import numpy as np

from src.simworld.runners import Policy, apply_policy, naive_policy, oracle_orders
from src.simworld.world import advance, generate_world, initial_state


def _world_and_history(weeks=12, n_skus=4, seed=5):
    w = generate_world(seed=seed, n_skus=n_skus, n_suppliers=2, weeks=weeks,
                       regime="seasonal_trend")
    st = initial_state(w)
    outs = []
    for _ in range(4):
        st, out = advance(w, st, {})
        outs.append(out)
    return w, st, outs


def test_naive_policy_covers_five_weeks_of_the_last_four_weeks_average():
    w, _, outs = _world_and_history()
    pol = naive_policy(w, outs)
    sku0 = w.skus[0].sku
    avg = float(np.mean([o.sold[0] for o in outs[-4:]]))
    assert pol.reorder_point[sku0] == pytest.approx(5.0 * avg)


def test_naive_policy_never_announces():
    w, _, outs = _world_and_history()
    assert naive_policy(w, outs).announced is False


def test_apply_policy_orders_up_to_the_level_when_below_the_reorder_point():
    w, st, outs = _world_and_history()
    sku0 = w.skus[0].sku
    pol = Policy(
        reorder_point={s.sku: 1e9 for s in w.skus},
        order_up_to={s.sku: 1e9 for s in w.skus},
        announced=False, note="",
    )
    orders = apply_policy(w, st, pol)
    assert orders[sku0] > 0


def test_apply_policy_orders_nothing_when_well_stocked():
    w, st, outs = _world_and_history()
    pol = Policy(
        reorder_point={s.sku: 0.0 for s in w.skus},
        order_up_to={s.sku: 0.0 for s in w.skus},
        announced=False, note="",
    )
    assert apply_policy(w, st, pol) == {}


def test_apply_policy_counts_in_transit_so_it_does_not_double_order():
    w, st, outs = _world_and_history()
    pol = Policy(
        reorder_point={s.sku: 500.0 for s in w.skus},
        order_up_to={s.sku: 500.0 for s in w.skus},
        announced=False, note="",
    )
    first = apply_policy(w, st, pol)
    st2, _ = advance(w, st, first)
    second = apply_policy(w, st2, pol)
    assert sum(second.values()) < sum(first.values())


def test_oracle_orders_exactly_the_demand_of_its_real_lead_window():
    w = generate_world(seed=9, n_skus=3, n_suppliers=1, weeks=20, regime="seasonal_trend")
    st = initial_state(w)
    orders = oracle_orders(w, st)
    lead = int(w.real_lead_weeks[0, 0])
    window = w.demand[0, 0:lead].sum()
    expected = max(0.0, window - st.on_hand[0])
    assert orders.get(w.skus[0].sku, 0.0) == pytest.approx(expected)


def test_oracle_beats_naive_on_lost_sales_over_a_full_run():
    w = generate_world(seed=4, n_skus=8, n_suppliers=2, weeks=40, regime="seasonal_trend")

    def run(strategy):
        st = initial_state(w)
        outs, lost = [], 0.0
        for _ in range(w.weeks):
            orders = strategy(w, st, outs)
            st, out = advance(w, st, orders)
            outs.append(out)
            lost += float(out.lost.sum())
        return lost

    naive_lost = run(lambda wo, s, o: apply_policy(wo, s, naive_policy(wo, o)) if len(o) >= 4 else {})
    oracle_lost = run(lambda wo, s, o: oracle_orders(wo, s))
    assert oracle_lost < naive_lost
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_simworld_runners.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'src.simworld.runners'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/simworld/runners.py
"""Los corredores: piso, Kern y techo, todos tras el mismo contrato.

Un corredor produce una ``Policy``; el mundo la aplica todas las semanas. Es la
forma honesta: Kern entrega una politica, no una cantidad, y quien la ejecuta es
la operacion.

El oraculo es la excepcion: decide semana a semana porque ve el futuro y no
tiene costo de replanificar. Nunca ve la lente.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from src.simworld.world import WeekOutcome, World, WorldState

NAIVE_COVER_WEEKS = 5.0
NAIVE_LOOKBACK_WEEKS = 4


@dataclass(frozen=True)
class Policy:
    """Que hacer cada semana, hasta la proxima replanificacion."""

    reorder_point: dict[str, float]
    order_up_to: dict[str, float]
    announced: bool = False   # el corredor emitio senal protectora al producirla
    note: str = ""
    status: str = "ok"
    extras: dict[str, float] = field(default_factory=dict)


def naive_policy(world: World, outcomes: list[WeekOutcome]) -> Policy:
    """El piso: punto de reorden fijo, congelado, sin revisar nunca.

    ``R = 5 x promedio semanal de las ultimas 4 semanas observadas``. Es lo que
    hace el ``stale_reorder_point`` del demo actual, y lo que hace el cliente real.
    """
    window = outcomes[-NAIVE_LOOKBACK_WEEKS:] if outcomes else []
    reorder: dict[str, float] = {}
    for i, sku in enumerate(world.skus):
        avg = float(np.mean([o.sold[i] for o in window])) if window else 0.0
        reorder[sku.sku] = NAIVE_COVER_WEEKS * avg
    return Policy(reorder_point=dict(reorder), order_up_to=dict(reorder),
                  announced=False, note="punto de reorden fijo, sin revision")


def apply_policy(world: World, state: WorldState, policy: Policy) -> dict[str, float]:
    """Traduce una politica en las ordenes de esta semana.

    Posicion de inventario = en mano + en transito. Si cae al o por debajo del
    punto de reorden, se pide hasta ``order_up_to``. Solo se devuelven las
    cantidades estrictamente positivas.
    """
    pipeline = np.zeros(len(world.skus), dtype=float)
    for sku_idx, _arrival, qty in state.in_transit:
        pipeline[sku_idx] += qty

    orders: dict[str, float] = {}
    for i, sku in enumerate(world.skus):
        rop = float(policy.reorder_point.get(sku.sku, 0.0))
        target = float(policy.order_up_to.get(sku.sku, rop))
        position = float(state.on_hand[i]) + float(pipeline[i])
        if position <= rop:
            qty = target - position
            if qty > 0:
                orders[sku.sku] = qty
    return orders


def oracle_orders(world: World, state: WorldState) -> dict[str, float]:
    """El techo: pide exactamente lo que se consumira durante su lead time real.

    Sin stock de seguridad, porque no hay incertidumbre que cubrir. Miope por
    diseno y aun asi inalcanzable. **Nunca ve la lente.**
    """
    t = state.week
    if t >= world.weeks:
        return {}

    pipeline = np.zeros(len(world.skus), dtype=float)
    for sku_idx, _arrival, qty in state.in_transit:
        pipeline[sku_idx] += qty

    orders: dict[str, float] = {}
    for i, sku in enumerate(world.skus):
        lead = int(world.real_lead_weeks[i, t])
        end = min(t + lead, world.weeks)
        need = float(world.demand[i, t:end].sum())
        have = float(state.on_hand[i]) + float(pipeline[i])
        qty = need - have
        if qty > 0:
            orders[sku.sku] = qty
    return orders
```

Agregar al reexport de `src/simworld/__init__.py`:

```python
from src.simworld.runners import Policy, apply_policy, naive_policy, oracle_orders  # noqa: E402
```

y sumar `"Policy", "apply_policy", "naive_policy", "oracle_orders"` a `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_simworld_runners.py -v`
Expected: 10 passed

- [ ] **Step 5: Lint and commit**

```bash
ruff check src tests
git add src/simworld/runners.py src/simworld/__init__.py tests/test_simworld_runners.py
git commit -m "feat(simworld): corredores piso y oraculo, con el contrato de politica"
```

---

### Task 8: Los corredores — Kern

**Files:**
- Modify: `src/simworld/runners.py`
- Test: `tests/test_simworld_runners.py`

**Interfaces:**
- Consumes: `Policy` de la Task 7, `JobResult.report` de la Task 6, `export_history` de la Task 3
- Produces: `kern_policy(world, outcomes, *, level, seed, out_dir, forced: bool) -> Policy`. Cuando `forced=True` pasa `job_type="inventory_optimization"`; cuando `False` va por clasificacion de intencion con brief en lenguaje natural.

**Traduccion del `GuidedOutcome`** (contrato del spec): `EXECUTED` u `OPTIONS`
-> se usa la politica del reporte. `HANDOFF` o `ESCALATED` -> **semana sin
accion**: `Policy` con puntos de reorden en cero y `status` igual al del
`GuidedOutcome`. Modela a un humano dormido y le pone precio al contrato de
nunca desprotegido en vez de esconderlo.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_simworld_runners.py  (agregar)
from src.simworld.lens import export_history
from src.simworld.runners import kern_policy


def _history(weeks=24, n_skus=5, seed=5):
    w = generate_world(seed=seed, n_skus=n_skus, n_suppliers=2, weeks=weeks,
                       regime="seasonal_trend")
    st = initial_state(w)
    outs = []
    for _ in range(weeks):
        st, out = advance(w, st, {})
        outs.append(out)
    return w, outs


def test_kern_forced_returns_a_policy_covering_every_sku(tmp_path):
    w, outs = _history()
    pol = kern_policy(w, outs, level=0, seed=0, out_dir=tmp_path, forced=True)
    assert pol.status in {"ok", "handoff", "escalated", "needs_data",
                          "needs_clarification", "qa_failed", "error"}
    if pol.status == "ok":
        assert set(pol.reorder_point) == {s.sku for s in w.skus}


def test_kern_brief_lane_routes_without_a_forced_job_type(tmp_path):
    w, outs = _history()
    pol = kern_policy(w, outs, level=0, seed=0, out_dir=tmp_path, forced=False)
    assert isinstance(pol, Policy)


def test_a_blocked_policy_orders_nothing(tmp_path):
    w, outs = _history()
    blocked = Policy(reorder_point={}, order_up_to={}, announced=True,
                     note="handoff", status="handoff")
    st = initial_state(w)
    assert apply_policy(w, st, blocked) == {}


def test_dirty_level_marks_the_policy_as_announced_or_degrades_it(tmp_path):
    w, outs = _history(weeks=32, n_skus=6)
    clean = kern_policy(w, outs, level=0, seed=0, out_dir=tmp_path / "c", forced=True)
    dirty = kern_policy(w, outs, level=3, seed=0, out_dir=tmp_path / "d", forced=True)
    # o Kern aviso, o el resultado cambio: lo que no puede pasar es que sea
    # identico y silencioso
    changed = dirty.reorder_point != clean.reorder_point or dirty.status != clean.status
    assert dirty.announced or changed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_simworld_runners.py -k kern -v`
Expected: FAIL con `ImportError: cannot import name 'kern_policy'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/simworld/runners.py  (agregar; y los imports arriba)
from pathlib import Path

from scm_agent.orchestrator import Orchestrator
from src import guided as guided_mod
from src.simworld.lens import export_history

KERN_BRIEF = (
    "Revisa el historico de ventas adjunto y fija el punto de reorden y el stock "
    "de seguridad por SKU para la reposicion de este mes."
)
BLOCKING_GUIDED = (guided_mod.HANDOFF, guided_mod.ESCALATED)


def kern_policy(
    world: World,
    outcomes: list[WeekOutcome],
    *,
    level: int,
    seed: int,
    out_dir: str | Path,
    forced: bool,
) -> Policy:
    """Corre a Kern por el orquestador completo sobre la lente del nivel pedido."""
    out = Path(out_dir)
    view = export_history(world, outcomes, out / "view.csv", level=level, seed=seed)

    orch = Orchestrator(clients_root=None)
    result = orch.run(
        KERN_BRIEF,
        data_path=str(view),
        job_type="inventory_optimization" if forced else None,
        out_dir=str(out / "agent"),
    )

    announced = _announced(result)
    if result.status != "ok" or result.report is None:
        return Policy(reorder_point={}, order_up_to={}, announced=announced,
                      note=result.summary, status=result.status)

    guided = result.guided
    if guided is not None and guided.status in BLOCKING_GUIDED:
        return Policy(reorder_point={}, order_up_to={}, announced=announced,
                      note=guided.summary, status=guided.status)

    reorder: dict[str, float] = {}
    target: dict[str, float] = {}
    for rec in result.report.recommendations:
        rop = float(rec.reorder_point)
        reorder[rec.product_id] = rop
        if rec.order_up_to is not None:
            target[rec.product_id] = float(rec.order_up_to)
        else:
            target[rec.product_id] = rop + float(rec.order_quantity or 0.0)

    return Policy(reorder_point=reorder, order_up_to=target, announced=announced,
                  note=result.summary, status="ok")


def _announced(result: object) -> bool:
    """Kern emitio alguna senal protectora sobre la calidad de lo que leyo."""
    status = getattr(result, "status", "")
    if status in {"needs_data", "needs_clarification", "qa_failed", "error"}:
        return True
    if getattr(result, "qa_issues", None):
        return True
    guided = getattr(result, "guided", None)
    if guided is not None:
        if guided.status in BLOCKING_GUIDED:
            return True
        if getattr(guided, "residuals", None):
            return True
    report = getattr(result, "report", None)
    quality = getattr(report, "intake_quality", None) if report is not None else None
    if quality is not None:
        # El umbral es el que el propio tool declara (JobReport.intake_quality_threshold,
        # 0.20 por defecto) — no uno inventado por el arnes, o estariamos midiendo
        # nuestra idea de "aviso" en vez de la de Kern.
        threshold = float(getattr(report, "intake_quality_threshold", 0.20))
        n_raw = getattr(quality, "n_raw", 0) or 0
        kept = getattr(quality, "n_canonical_rows", 0) or 0
        dropped = n_raw - kept
        if n_raw > 0 and dropped / n_raw > threshold:
            return True
        if getattr(quality, "synthesized", ()):
            return True
    return False
```

Agregar `kern_policy` al reexport y a `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_simworld_runners.py -v`
Expected: 14 passed

**Si `test_kern_forced_...` devuelve `status="needs_clarification"` por
`required_client_params`:** el tool declara `required_client_params=("holding_rate",
"service_level")`, pero solo bloquean con `strict_params=True`, que no se usa
aca. Si igual bloquea, pasar `overrides={"holding_rate": 0.25, "service_level":
0.95}` en la llamada a `orch.run` y documentarlo como parametro del arnes.

- [ ] **Step 5: Lint and commit**

```bash
ruff check src tests
git add src/simworld/runners.py src/simworld/__init__.py tests/test_simworld_runners.py
git commit -m "feat(simworld): corredor Kern por el orquestador completo, con traduccion del guided"
```

---

### Task 9: El marcador — contrato de KPI y captura normalizada

**Files:**
- Create: `src/simworld/scorer.py`
- Modify: `src/simworld/__init__.py`
- Test: `tests/test_simworld_scorer.py`

**Interfaces:**
- Consumes: `World`, `WeekOutcome` de las Tasks 1-2
- Produces: `Kpis` (frozen), `score_run(world, outcomes, *, order_cost=75.0, holding_rate=0.25, shortage_penalty_rate=1.0) -> Kpis`, `weekly_cost(world, outcomes, **kw) -> np.ndarray`, `capture(floor_cost: float, kern_cost: float, oracle_cost: float) -> float`

**Contrato de KPI portado de gym-invmgmt:** nivel de servicio, fill rate,
demanda insatisfecha, inventario promedio, diagnostico de latigo, costo
descompuesto (compra, mantenimiento, tuberia, penalidad de quiebre, costo fijo
de ordenar) y profit por periodo.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_simworld_scorer.py
import numpy as np
import pytest

from src.simworld.runners import apply_policy, naive_policy, oracle_orders
from src.simworld.scorer import Kpis, capture, score_run, weekly_cost
from src.simworld.world import advance, generate_world, initial_state


def _run(strategy, seed=4, weeks=30, n_skus=6):
    w = generate_world(seed=seed, n_skus=n_skus, n_suppliers=2, weeks=weeks,
                       regime="seasonal_trend")
    st = initial_state(w)
    outs = []
    for _ in range(w.weeks):
        st, out = advance(w, st, strategy(w, st, outs))
        outs.append(out)
    return w, outs


def _naive(w, st, outs):
    return apply_policy(w, st, naive_policy(w, outs)) if len(outs) >= 4 else {}


def _oracle(w, st, outs):
    return oracle_orders(w, st)


def test_fill_rate_is_between_zero_and_one():
    w, outs = _run(_naive)
    k = score_run(w, outs)
    assert 0.0 <= k.fill_rate <= 1.0


def test_a_runner_that_never_orders_has_a_worse_fill_rate_than_the_oracle():
    w_a, outs_a = _run(lambda w, s, o: {})
    w_b, outs_b = _run(_oracle)
    assert score_run(w_a, outs_a).fill_rate < score_run(w_b, outs_b).fill_rate


def test_total_cost_decomposes_exactly():
    w, outs = _run(_naive)
    k = score_run(w, outs)
    parts = (k.procurement_cost + k.holding_cost + k.shortage_cost + k.fixed_order_cost)
    assert k.total_cost == pytest.approx(parts)


def test_weekly_cost_sums_to_total_cost():
    w, outs = _run(_naive)
    k = score_run(w, outs)
    assert weekly_cost(w, outs).sum() == pytest.approx(k.total_cost)


def test_weekly_cost_has_one_entry_per_week():
    w, outs = _run(_naive, weeks=30)
    assert weekly_cost(w, outs).shape == (30,)


def test_bullwhip_of_a_constant_order_stream_is_near_zero():
    w, outs = _run(lambda wo, s, o: {sk.sku: 10.0 for sk in wo.skus})
    assert score_run(w, outs).bullwhip < 0.1


def test_a_constant_order_stream_does_not_chase_demand():
    w, outs = _run(lambda wo, s, o: {sk.sku: 10.0 for sk in wo.skus})
    assert score_run(w, outs).demand_chasing == pytest.approx(0.0)


def test_ordering_exactly_last_weeks_sales_chases_demand_hard():
    def echo(w, st, outs):
        if not outs:
            return {}
        return {s.sku: float(outs[-1].sold[i]) for i, s in enumerate(w.skus)}

    w, outs = _run(echo)
    assert score_run(w, outs).demand_chasing > 0.7


def test_anchoring_is_zero_when_orders_match_mean_demand():
    w, outs = _run(lambda wo, s, o: {})
    mean_d = float(np.stack([o.demand for o in outs], axis=1).sum(axis=0).mean())
    w2, outs2 = _run(lambda wo, s, o: {wo.skus[0].sku: mean_d})
    k = score_run(w2, outs2)
    assert k.anchoring < 0.05


def test_capture_is_zero_at_the_floor_and_one_at_the_ceiling():
    assert capture(floor_cost=100.0, kern_cost=100.0, oracle_cost=50.0) == pytest.approx(0.0)
    assert capture(floor_cost=100.0, kern_cost=50.0, oracle_cost=50.0) == pytest.approx(1.0)
    assert capture(floor_cost=100.0, kern_cost=75.0, oracle_cost=50.0) == pytest.approx(0.5)


def test_capture_can_be_negative_when_worse_than_the_floor():
    assert capture(floor_cost=100.0, kern_cost=120.0, oracle_cost=50.0) < 0.0


def test_capture_above_one_raises_because_the_oracle_is_unreachable():
    with pytest.raises(ValueError, match="oraculo"):
        capture(floor_cost=100.0, kern_cost=40.0, oracle_cost=50.0)


def test_capture_with_a_degenerate_span_raises():
    with pytest.raises(ValueError, match="span"):
        capture(floor_cost=50.0, kern_cost=50.0, oracle_cost=50.0)


def test_oracle_costs_less_than_naive_over_a_full_run():
    w_n, outs_n = _run(_naive)
    w_o, outs_o = _run(_oracle)
    assert score_run(w_o, outs_o).total_cost < score_run(w_n, outs_n).total_cost
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_simworld_scorer.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'src.simworld.scorer'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/simworld/scorer.py
"""El marcador: contrato de KPI portado de gym-invmgmt (MIT) mas la captura
normalizada entre piso y techo.

Un numero de costo absoluto es discutible. ``capture`` aguanta a un comprador
esceptico: 0 es no mejor que un punto de reorden fijo, 1 es igualar a un oraculo
que ve el futuro. El oraculo es inalcanzable por construccion, asi que superarlo
no es un logro sino una fuga de informacion — y por eso ``capture`` levanta en
vez de devolver un numero bonito.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.simworld.world import WeekOutcome, World

WEEKS_PER_YEAR = 52.0


@dataclass(frozen=True)
class Kpis:
    weeks: int
    service_level: float       # fraccion de semanas-SKU sin quiebre
    fill_rate: float           # fraccion de la demanda total satisfecha
    unfulfilled_units: float
    mean_on_hand: float
    # --- sesgo, portado de AIM-Bench ---
    bullwhip: float            # var(ordenes) / var(demanda)
    anchoring: float           # |media(ordenes) - media(demanda)| / media(demanda)
    demand_chasing: float      # corr(orden_t, demanda_{t-1})
    procurement_cost: float
    holding_cost: float
    shortage_cost: float
    fixed_order_cost: float
    total_cost: float
    revenue: float
    profit: float


def weekly_cost(
    world: World,
    outcomes: list[WeekOutcome],
    *,
    order_cost: float = 75.0,
    holding_rate: float = 0.25,
    shortage_penalty_rate: float = 1.0,
) -> np.ndarray:
    """Costo total de cada semana. La suma es ``Kpis.total_cost``.

    La atribucion de la degradacion anunciada es por periodo, asi que el costo
    tiene que existir semana a semana, no solo agregado.
    """
    unit_cost = np.array([s.unit_cost for s in world.skus], dtype=float)
    margin = np.array([s.price - s.unit_cost for s in world.skus], dtype=float)
    weekly_holding = unit_cost * holding_rate / WEEKS_PER_YEAR

    out = np.zeros(len(outcomes), dtype=float)
    for j, o in enumerate(outcomes):
        procurement = float((o.ordered * unit_cost).sum())
        holding = float((o.on_hand_end * weekly_holding).sum())
        shortage = float((o.lost * margin * shortage_penalty_rate).sum())
        fixed = float(order_cost * int((o.ordered > 0).sum()))
        out[j] = procurement + holding + shortage + fixed
    return out


def score_run(
    world: World,
    outcomes: list[WeekOutcome],
    *,
    order_cost: float = 75.0,
    holding_rate: float = 0.25,
    shortage_penalty_rate: float = 1.0,
) -> Kpis:
    """El contrato de KPI completo para una corrida."""
    if not outcomes:
        raise ValueError("no hay semanas que puntuar")

    unit_cost = np.array([s.unit_cost for s in world.skus], dtype=float)
    price = np.array([s.price for s in world.skus], dtype=float)
    margin = price - unit_cost
    weekly_holding = unit_cost * holding_rate / WEEKS_PER_YEAR

    demand = np.stack([o.demand for o in outcomes], axis=1)
    sold = np.stack([o.sold for o in outcomes], axis=1)
    lost = np.stack([o.lost for o in outcomes], axis=1)
    ordered = np.stack([o.ordered for o in outcomes], axis=1)
    on_hand = np.stack([o.on_hand_end for o in outcomes], axis=1)

    total_demand = float(demand.sum())
    procurement = float((ordered * unit_cost[:, None]).sum())
    holding = float((on_hand * weekly_holding[:, None]).sum())
    shortage = float((lost * margin[:, None] * shortage_penalty_rate).sum())
    fixed = float(order_cost * int((ordered > 0).sum()))
    revenue = float((sold * price[:, None]).sum())
    total_cost = procurement + holding + shortage + fixed

    demand_series = demand.sum(axis=0)
    order_series = ordered.sum(axis=0)
    demand_var = float(demand_series.var())
    order_var = float(order_series.var())
    bullwhip = order_var / demand_var if demand_var > 0 else 0.0

    return Kpis(
        weeks=len(outcomes),
        service_level=float((lost <= 0).mean()),
        fill_rate=float(sold.sum() / total_demand) if total_demand > 0 else 1.0,
        unfulfilled_units=float(lost.sum()),
        mean_on_hand=float(on_hand.mean()),
        bullwhip=bullwhip,
        anchoring=_anchoring(order_series, demand_series),
        demand_chasing=_demand_chasing(order_series, demand_series),
        procurement_cost=procurement,
        holding_cost=holding,
        shortage_cost=shortage,
        fixed_order_cost=fixed,
        total_cost=total_cost,
        revenue=revenue,
        profit=revenue - total_cost,
    )


def _anchoring(orders: np.ndarray, demand: np.ndarray) -> float:
    """Cuanto se pega el volumen pedido a la demanda media, ignorando su forma.

    AIM-Bench lo llama pull-to-centre. Un corredor que pide siempre lo mismo,
    igual a la media, marca 0 aca y aun asi puede ser pesimo: por eso convive con
    ``demand_chasing``, que mira la forma en vez del nivel.
    """
    mean_d = float(demand.mean())
    if mean_d <= 0:
        return 0.0
    return float(abs(orders.mean() - mean_d) / mean_d)


def _demand_chasing(orders: np.ndarray, demand: np.ndarray) -> float:
    """Correlacion entre lo que se pide hoy y lo que se vendio la semana pasada.

    Alto = el corredor persigue el ultimo dato en vez de anticipar. Un pronostico
    de media movil hace exactamente esto, asi que **un motor determinista puede
    perseguir demanda igual que un LLM**: no es un sesgo cognitivo, es una
    propiedad del estimador. Por eso se mide.
    """
    if len(orders) < 3:
        return 0.0
    x = orders[1:].astype(float)
    y = demand[:-1].astype(float)
    if x.std() == 0 or y.std() == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def capture(*, floor_cost: float, kern_cost: float, oracle_cost: float) -> float:
    """Donde cae Kern entre el piso y el techo.

    0 = no mejor que el punto de reorden fijo. 1 = igual al oraculo. Negativo es
    peor que el piso, y se informa con su signo. Por encima de 1 es imposible:
    el oraculo ve el futuro, asi que superarlo significa fuga de informacion del
    mundo hacia la lente. Falla ruidosa, no silenciosa.
    """
    span = floor_cost - oracle_cost
    if span <= 0:
        raise ValueError(
            f"span degenerado: el piso ({floor_cost:.2f}) no cuesta mas que el "
            f"oraculo ({oracle_cost:.2f}); el mundo no discrimina"
        )
    value = (floor_cost - kern_cost) / span
    if value > 1.0:
        raise ValueError(
            f"captura {value:.3f} > 1: Kern le gano al oraculo, que ve el futuro. "
            "Hay fuga de informacion del mundo hacia la lente."
        )
    return float(value)
```

Agregar al reexport y a `__all__`: `Kpis`, `capture`, `score_run`, `weekly_cost`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_simworld_scorer.py -v`
Expected: 14 passed

- [ ] **Step 5: Lint and commit**

```bash
ruff check src tests
git add src/simworld/scorer.py src/simworld/__init__.py tests/test_simworld_scorer.py
git commit -m "feat(simworld): contrato de KPI, sesgos de AIM-Bench y captura normalizada"
```

---

### Task 10: El marcador — degradacion anunciada

**Files:**
- Modify: `src/simworld/scorer.py`
- Test: `tests/test_simworld_scorer.py`

**Interfaces:**
- Consumes: `weekly_cost` de la Task 9
- Produces: `Degradation` (frozen: `level:int`, `drop:float`, `announced:float`, `silent:float`, `weeks_announced:int`) y `degradation(clean_weekly: np.ndarray, dirty_weekly: np.ndarray, announced_weeks: list[bool], *, level: int) -> Degradation`

**La atribucion es por periodo, no global.** Misma semilla, mismo mundo, asi que
la semana 17 del nivel 3 es comparable contra la semana 17 del nivel 0.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_simworld_scorer.py  (agregar)
from src.simworld.scorer import degradation


def test_no_extra_cost_means_no_drop():
    clean = np.array([10.0, 10.0, 10.0])
    d = degradation(clean, clean.copy(), [False, False, False], level=2)
    assert d.drop == pytest.approx(0.0)
    assert d.silent == pytest.approx(0.0)


def test_all_the_extra_cost_in_announced_weeks_leaves_zero_silence():
    clean = np.array([10.0, 10.0, 10.0])
    dirty = np.array([10.0, 30.0, 10.0])
    d = degradation(clean, dirty, [False, True, False], level=2)
    assert d.drop == pytest.approx(20.0)
    assert d.announced == pytest.approx(20.0)
    assert d.silent == pytest.approx(0.0)
    assert d.weeks_announced == 1


def test_extra_cost_in_a_silent_week_is_counted_as_silence():
    clean = np.array([10.0, 10.0, 10.0])
    dirty = np.array([10.0, 30.0, 10.0])
    d = degradation(clean, dirty, [False, False, False], level=3)
    assert d.silent == pytest.approx(20.0)
    assert d.announced == pytest.approx(0.0)


def test_a_negative_week_is_summed_with_its_sign_not_clipped():
    clean = np.array([10.0, 10.0])
    dirty = np.array([30.0, 2.0])   # +20 y -8
    d = degradation(clean, dirty, [False, False], level=3)
    assert d.drop == pytest.approx(12.0)


def test_mismatched_lengths_raise():
    with pytest.raises(ValueError, match="largo"):
        degradation(np.array([1.0, 2.0]), np.array([1.0]), [False, False], level=1)


def test_mismatched_announced_length_raises():
    with pytest.raises(ValueError, match="largo"):
        degradation(np.array([1.0, 2.0]), np.array([1.0, 2.0]), [False], level=1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_simworld_scorer.py -k degradation -v`
Expected: FAIL con `ImportError: cannot import name 'degradation'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/simworld/scorer.py  (agregar al final)


@dataclass(frozen=True)
class Degradation:
    """Cuanto le costo la suciedad a Kern, y cuanto de eso aviso."""

    level: int
    drop: float              # exceso de costo total contra el nivel 0
    announced: float         # la parte del exceso en semanas donde Kern aviso
    silent: float            # drop - announced. El numero que importa.
    weeks_announced: int


def degradation(
    clean_weekly: np.ndarray,
    dirty_weekly: np.ndarray,
    announced_weeks: list[bool],
    *,
    level: int,
) -> Degradation:
    """Atribuye el exceso de costo semana a semana, no en bloque.

    El exceso de una semana puede ser **negativo** — la suciedad puede abaratar
    una semana por accidente. No se recorta a cero: se suma con su signo. Si el
    total sale negativo, el reporte lo dice en vez de esconderlo detras de un
    ``max(0, ...)``.
    """
    clean = np.asarray(clean_weekly, dtype=float)
    dirty = np.asarray(dirty_weekly, dtype=float)
    if clean.shape != dirty.shape:
        raise ValueError(f"largo distinto: limpio {clean.shape} vs sucio {dirty.shape}")
    if len(announced_weeks) != len(clean):
        raise ValueError(
            f"largo distinto: {len(announced_weeks)} avisos para {len(clean)} semanas"
        )

    excess = dirty - clean
    flags = np.asarray(announced_weeks, dtype=bool)
    drop = float(excess.sum())
    announced = float(excess[flags].sum()) if flags.any() else 0.0
    return Degradation(
        level=level,
        drop=drop,
        announced=announced,
        silent=drop - announced,
        weeks_announced=int(flags.sum()),
    )
```

Agregar `Degradation` y `degradation` al reexport y a `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_simworld_scorer.py -v`
Expected: 20 passed

- [ ] **Step 5: Lint and commit**

```bash
ruff check src tests
git add src/simworld/scorer.py src/simworld/__init__.py tests/test_simworld_scorer.py
git commit -m "feat(simworld): degradacion anunciada con atribucion por periodo"
```

---

### Task 11: El CLI y la corrida completa

**Files:**
- Create: `examples/run_world_sim.py`
- Test: `tests/test_simworld_e2e.py`

**Interfaces:**
- Consumes: todo lo anterior
- Produces, en `examples/run_world_sim.py`: `simulate(world, plan, *, replan_every) -> tuple[list[WeekOutcome], list[bool]]` donde `plan` tiene firma `(world, outcomes, week) -> tuple[Policy, bool]`; `simulate_oracle(world) -> list[WeekOutcome]`; y `run_grid(...) -> dict`. Escribe `deliverables/simworld/report.{md,json}`.

**Por que dos funciones de simulacion y no una:** `simulate` sostiene una
**politica** entre replanificaciones y la aplica cada semana con `apply_policy`,
que es exactamente lo que hace la operacion real. El oraculo no es un corredor de
politica sino un clarividente: decide cantidades semana a semana mirando el
futuro, y no hay punto de reorden que lo represente. Forzarlo al mismo molde
obligaria a reinterpretar cantidades como niveles objetivo, que es incorrecto.
Dos funciones, cada una con un proposito claro.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_simworld_e2e.py
import json

import pytest

from examples.run_world_sim import run_grid, simulate, simulate_oracle
from src.simworld.runners import Policy, naive_policy
from src.simworld.world import generate_world


def _naive_plan(world, outcomes, week):
    return naive_policy(world, outcomes), False


def test_simulate_returns_one_outcome_and_one_flag_per_week():
    w = generate_world(seed=3, n_skus=4, n_suppliers=2, weeks=10, regime="seasonal_trend")
    outs, flags = simulate(w, _naive_plan, replan_every=4)
    assert len(outs) == 10
    assert len(flags) == 10


def test_replan_every_controls_how_often_the_policy_is_recomputed():
    w = generate_world(seed=3, n_skus=3, n_suppliers=1, weeks=12, regime="seasonal_trend")
    calls = []

    def plan(world, outcomes, week):
        calls.append(week)
        return naive_policy(world, outcomes), False

    simulate(w, plan, replan_every=4)
    assert calls == [0, 4, 8]


def test_the_held_policy_is_reapplied_against_the_current_position_each_week():
    """Entre replanificaciones la politica se aplica de nuevo, no se repite la
    orden a ciegas: si ya hay stock en camino, esta semana se pide menos."""
    w = generate_world(seed=3, n_skus=2, n_suppliers=1, weeks=6, regime="seasonal_trend")
    fixed = Policy(
        reorder_point={s.sku: 400.0 for s in w.skus},
        order_up_to={s.sku: 400.0 for s in w.skus},
        announced=False, note="", status="ok",
    )
    outs, _ = simulate(w, lambda wo, o, wk: (fixed, False), replan_every=99)
    assert outs[0].ordered.sum() > outs[1].ordered.sum()


def test_replan_every_below_one_raises():
    w = generate_world(seed=3, n_skus=2, n_suppliers=1, weeks=4, regime="seasonal_trend")
    with pytest.raises(ValueError, match="replan_every"):
        simulate(w, _naive_plan, replan_every=0)


def test_simulate_oracle_runs_the_full_horizon():
    w = generate_world(seed=3, n_skus=4, n_suppliers=2, weeks=10, regime="seasonal_trend")
    assert len(simulate_oracle(w)) == 10


def test_oracle_beats_naive_and_the_grid_reports_it(tmp_path):
    report = run_grid(
        seed=4, n_skus=6, n_suppliers=2, weeks=20, levels=(0,),
        out_dir=tmp_path, include_kern=False,
    )
    assert report["floor"]["total_cost"] > report["ceiling"]["total_cost"]


def test_grid_writes_a_json_and_a_markdown_report(tmp_path):
    run_grid(seed=4, n_skus=5, n_suppliers=2, weeks=16, levels=(0,),
             out_dir=tmp_path, include_kern=False)
    assert (tmp_path / "report.json").exists()
    assert (tmp_path / "report.md").exists()
    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["seed"] == 4


GOLDEN_FLOOR_COST = 0.0    # <- reemplazar en el Step 5 con el valor real medido
GOLDEN_CEILING_COST = 0.0  # <- idem


@pytest.mark.skip(reason="pendiente de fijar los valores medidos en el Step 5")
def test_golden_floor_and_ceiling_costs_are_stable(tmp_path):
    """Fija el marcador de una corrida chica. Si esto cambia, cambio la fisica
    o la generacion del mundo — y eso tiene que ser una decision, no un accidente."""
    report = run_grid(seed=4, n_skus=6, n_suppliers=2, weeks=20, levels=(0,),
                      out_dir=tmp_path, include_kern=False)
    assert report["floor"]["total_cost"] == pytest.approx(GOLDEN_FLOOR_COST, rel=1e-6)
    assert report["ceiling"]["total_cost"] == pytest.approx(GOLDEN_CEILING_COST, rel=1e-6)
    assert report["ceiling"]["fill_rate"] > report["floor"]["fill_rate"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_simworld_e2e.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'examples.run_world_sim'`

- [ ] **Step 3: Write minimal implementation**

```python
# examples/run_world_sim.py
"""Corre la grilla del marcador: piso, Kern y oraculo sobre el mismo mundo.

Usage:
    PYTHONPATH=. python examples/run_world_sim.py
    PYTHONPATH=. python examples/run_world_sim.py --weeks 52 --skus 120 --levels 0 1 2 3
    PYTHONPATH=. python examples/run_world_sim.py --no-kern     # solo piso vs oraculo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dataclasses import asdict  # noqa: E402

from src.simworld.runners import (  # noqa: E402
    Policy,
    apply_policy,
    kern_policy,
    naive_policy,
    oracle_orders,
)
from src.simworld.scorer import capture, degradation, score_run, weekly_cost  # noqa: E402
from src.simworld.world import World, advance, generate_world, initial_state  # noqa: E402

# (world, outcomes, week) -> (Policy, announced)
Plan = Callable[..., tuple[Policy, bool]]


def simulate(
    world: World,
    plan: Plan,
    *,
    replan_every: int = 4,
) -> tuple[list, list[bool]]:
    """Corre el horizonte sosteniendo una politica entre replanificaciones.

    La politica se **reaplica** todas las semanas contra la posicion de inventario
    del momento (``apply_policy`` ya cuenta lo que viene en camino), que es lo que
    hace la operacion real: el plan del mes se fija una vez y se ejecuta semana a
    semana. No se repite la orden a ciegas.

    El aviso se marca solo en la semana en que se replanifico: es cuando Kern
    efectivamente miro los datos y pudo decir algo.
    """
    if replan_every < 1:
        raise ValueError(f"replan_every debe ser >= 1, no {replan_every}")

    state = initial_state(world)
    outcomes: list = []
    flags: list[bool] = []
    policy = Policy(reorder_point={}, order_up_to={}, announced=False, note="", status="ok")

    for week in range(world.weeks):
        announced = False
        if week % replan_every == 0:
            policy, announced = plan(world, outcomes, week)
        state, out = advance(world, state, apply_policy(world, state, policy))
        outcomes.append(out)
        flags.append(bool(announced))
    return outcomes, flags


def simulate_oracle(world: World) -> list:
    """El techo. No es un corredor de politica: decide cantidades cada semana
    mirando la demanda futura verdadera, asi que tiene su propio bucle."""
    state = initial_state(world)
    outcomes: list = []
    for _ in range(world.weeks):
        state, out = advance(world, state, oracle_orders(world, state))
        outcomes.append(out)
    return outcomes


def run_grid(
    *,
    seed: int = 4,
    n_skus: int = 120,
    n_suppliers: int = 4,
    weeks: int = 52,
    levels: tuple[int, ...] = (0, 1, 2, 3),
    replan_every: int = 4,
    out_dir: str | Path = "deliverables/simworld",
    include_kern: bool = True,
) -> dict:
    """La grilla completa: 1 regimen x len(levels) niveles x 3 corredores."""
    world = generate_world(seed=seed, n_skus=n_skus, n_suppliers=n_suppliers,
                           weeks=weeks, regime="seasonal_trend")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    empty = Policy(reorder_point={}, order_up_to={}, announced=False, note="", status="ok")

    def naive_plan(w, outs, week):
        # El piso se calcula una sola vez, en la semana 4, y no se revisa mas:
        # esa es su definicion. replan_every=4 hace que la semana 4 sea la primera
        # replanificacion con historia suficiente.
        if len(outs) < 4:
            return empty, False
        return naive_policy(w, outs), False

    floor_outs, _ = simulate(world, naive_plan, replan_every=replan_every)
    ceil_outs = simulate_oracle(world)
    floor_k = score_run(world, floor_outs)
    ceil_k = score_run(world, ceil_outs)

    report: dict = {
        "seed": seed, "weeks": weeks, "n_skus": n_skus, "n_suppliers": n_suppliers,
        "regime": world.regime, "replan_every": replan_every,
        "floor": asdict(floor_k), "ceiling": asdict(ceil_k), "kern": {},
    }

    if include_kern:
        clean_weekly = None
        for level in levels:
            for lane in ("brief", "forced"):
                def kern_plan(w, outs, week, _lvl=level, _lane=lane):
                    if len(outs) < 4:
                        return empty, False
                    pol = kern_policy(
                        w, outs, level=_lvl, seed=seed,
                        out_dir=out / f"lane-{_lane}-l{_lvl}" / f"w{week}",
                        forced=(_lane == "forced"),
                    )
                    return pol, pol.announced

                outs, flags = simulate(world, kern_plan, replan_every=replan_every)
                kpis = score_run(world, outs)
                wk = weekly_cost(world, outs)
                entry = {"kpis": asdict(kpis)}
                try:
                    entry["capture"] = capture(
                        floor_cost=floor_k.total_cost,
                        kern_cost=kpis.total_cost,
                        oracle_cost=ceil_k.total_cost,
                    )
                except ValueError as exc:
                    entry["capture"] = None
                    entry["capture_error"] = str(exc)
                if lane == "brief":
                    if level == 0:
                        clean_weekly = wk
                    elif clean_weekly is not None:
                        entry["degradation"] = asdict(
                            degradation(clean_weekly, wk, flags, level=level)
                        )
                report["kern"][f"{lane}-L{level}"] = entry

    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "report.md").write_text(_markdown(report), encoding="utf-8")
    return report


def _markdown(report: dict) -> str:
    lines = [
        "# Marcador del mundo simulado",
        "",
        f"- Semilla: `{report['seed']}` (toda corrida es reproducible o no vale)",
        f"- Horizonte: {report['weeks']} semanas, {report['n_skus']} SKUs, "
        f"{report['n_suppliers']} proveedores",
        f"- Regimen: {report['regime']}. Replanificacion cada "
        f"{report['replan_every']} semanas.",
        "",
        "| Corredor | Costo total | Fill rate | Latigo | Captura |",
        "|---|---:|---:|---:|---:|",
        f"| Piso (reorden fijo) | {report['floor']['total_cost']:,.0f} | "
        f"{report['floor']['fill_rate']:.3f} | {report['floor']['bullwhip']:.2f} | 0.000 |",
    ]
    for name, entry in report.get("kern", {}).items():
        k = entry["kpis"]
        cap = entry.get("capture")
        cap_txt = f"{cap:.3f}" if cap is not None else "ERROR"
        lines.append(
            f"| Kern {name} | {k['total_cost']:,.0f} | {k['fill_rate']:.3f} | "
            f"{k['bullwhip']:.2f} | {cap_txt} |"
        )
    lines.append(
        f"| Techo (oraculo) | {report['ceiling']['total_cost']:,.0f} | "
        f"{report['ceiling']['fill_rate']:.3f} | "
        f"{report['ceiling']['bullwhip']:.2f} | 1.000 |"
    )
    degs = [(n, e["degradation"]) for n, e in report.get("kern", {}).items()
            if "degradation" in e]
    if degs:
        lines += ["", "## Degradacion anunciada", "",
                  "| Nivel | Caida | Anunciado | Silencio |", "|---|---:|---:|---:|"]
        for name, d in degs:
            lines.append(
                f"| {name} | {d['drop']:,.0f} | {d['announced']:,.0f} | "
                f"{d['silent']:,.0f} |"
            )
        lines += ["", "Silencio = lo que la suciedad costo sin que Kern dijera nada."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Corre la grilla del marcador simulado.")
    parser.add_argument("--seed", type=int, default=4)
    parser.add_argument("--skus", type=int, default=120)
    parser.add_argument("--suppliers", type=int, default=4)
    parser.add_argument("--weeks", type=int, default=52)
    parser.add_argument("--levels", type=int, nargs="+", default=[0, 1, 2, 3])
    parser.add_argument("--replan-every", type=int, default=4)
    parser.add_argument("--out", type=str, default="deliverables/simworld")
    parser.add_argument("--no-kern", action="store_true", help="solo piso vs oraculo")
    args = parser.parse_args()

    print("Generando el mundo y corriendo la grilla...")
    report = run_grid(
        seed=args.seed, n_skus=args.skus, n_suppliers=args.suppliers,
        weeks=args.weeks, levels=tuple(args.levels), replan_every=args.replan_every,
        out_dir=args.out, include_kern=not args.no_kern,
    )
    print("")
    print(f"  Piso  (reorden fijo)  costo {report['floor']['total_cost']:>14,.0f}  "
          f"fill {report['floor']['fill_rate']:.3f}")
    for name, entry in report.get("kern", {}).items():
        cap = entry.get("capture")
        cap_txt = f"{cap:.3f}" if cap is not None else "ERROR"
        print(f"  Kern  {name:<16} costo {entry['kpis']['total_cost']:>14,.0f}  "
              f"fill {entry['kpis']['fill_rate']:.3f}  captura {cap_txt}")
    print(f"  Techo (oraculo)       costo {report['ceiling']['total_cost']:>14,.0f}  "
          f"fill {report['ceiling']['fill_rate']:.3f}")
    print("")
    print(f"Reporte en: {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_simworld_e2e.py -v`
Expected: 8 passed

- [ ] **Step 5: Fijar el golden con valores reales**

Correr una vez y anotar los numeros:

```bash
PYTHONPATH=. python -c "from examples.run_world_sim import run_grid; import json; r=run_grid(seed=4,n_skus=6,n_suppliers=2,weeks=20,levels=(0,),out_dir='/tmp/g',include_kern=False); print(json.dumps({'floor':r['floor']['total_cost'],'ceiling':r['ceiling']['total_cost']},indent=2))"
```

Poner esos dos numeros en `GOLDEN_FLOOR_COST` y `GOLDEN_CEILING_COST`, y
**borrar el `@pytest.mark.skip`**. El test queda activo con valores literales.
Un golden que se compara contra si mismo no asegura nada: si al terminar el Step
5 el `skip` sigue puesto o las constantes siguen en `0.0`, la tarea no esta
hecha.

Run: `PYTHONPATH=. pytest tests/test_simworld_e2e.py::test_golden_floor_and_ceiling_costs_are_stable -v`
Expected: PASS (no SKIPPED)

- [ ] **Step 6: Correr la grilla completa de verdad**

```bash
PYTHONPATH=. python examples/run_world_sim.py --weeks 52 --skus 120 --levels 0 1 2 3
```

Expected: la tabla en consola con las cuatro filas de Kern por carril y la tabla
de degradacion anunciada. **Si `capture` levanta porque Kern supero al oraculo,
es un bug de fuga de informacion — parar y buscarlo, no ajustar el umbral.**

- [ ] **Step 7: Suite completo, lint y commit**

```bash
PYTHONPATH=. pytest tests/ -q
ruff check src tests examples
git add examples/run_world_sim.py tests/test_simworld_e2e.py
git commit -m "feat(simworld): CLI de la grilla, reporte y golden de regresion"
```

---

### Task 12: Documentar el arnes

**Files:**
- Modify: `documentation/TESTING_ENVIRONMENT.md`
- Modify: `CLAUDE.md` (fila de `src/simworld/` en el mapa del proyecto)

**Interfaces:**
- Consumes: el CLI de la Task 11
- Produces: nada de codigo

- [ ] **Step 1: Agregar la seccion 5 a TESTING_ENVIRONMENT.md**

Insertar despues de la seccion 4 (el webapp), antes de "Where to look when
something is red":

```markdown
---

## 5. Marcador contra verdad conocida — `examples/run_world_sim.py`

Las capas 1-4 responden "funciona". Esta responde **"decide bien"**.

Genera un mundo de reposicion con semilla — demanda verdadera y lead times
reales sorteados una sola vez, asi que los corredores enfrentan la realizacion
identica — y corre tres estrategias sobre el mismo mundo durante 52 semanas:

| Corredor | Que es |
|---|---|
| Piso | Punto de reorden fijo, congelado. Lo que hace el cliente real |
| Kern | `Orchestrator.run` completo, brief en lenguaje natural, replanifica cada 4 semanas |
| Techo | Oraculo: ve la demanda futura y el lead time real. Inalcanzable por construccion |

```bash
PYTHONPATH=. python examples/run_world_sim.py                        # grilla completa
PYTHONPATH=. python examples/run_world_sim.py --no-kern              # solo piso vs techo
PYTHONPATH=. python examples/run_world_sim.py --levels 0 2 --weeks 26
```

**Captura normalizada:** `(costo_piso - costo_kern) / (costo_piso - costo_oraculo)`.
0 es no mejor que el reorden fijo, 1 es igualar al oraculo. Si sale mayor que 1
el CLI levanta: superar a quien ve el futuro significa fuga de informacion del
mundo hacia la lente, no un logro.

**Suciedad escalonada.** Kern nunca ve el mundo: ve un CSV exportado con nivel
0 (canonico), 1 (desprolijo), 2 (tipico de PyME: SKU partida en dos grafias,
unidades mezcladas, devoluciones negativas) o 3 (caotico). El marcador se
calcula siempre contra la verdad limpia.

**Degradacion anunciada** — el numero que ningun banco publicado mide. Por cada
nivel salen dos: cuanto costo la suciedad, y cuanto de eso Kern anuncio (via
`IntakeQuality`, `needs_data`, `qa_failed` o un `GuidedOutcome` bloqueante). La
diferencia es el silencio: un numero con cara de seguro construido sobre un
exporte roto.

Escribe `deliverables/simworld/report.{md,json}` (gitignored).
```

- [ ] **Step 2: Agregar la fila al mapa de proyecto en CLAUDE.md**

En la tabla "Project map", despues de la fila de `tests/`:

```markdown
| `src/simworld/` | Arnes de marcador: mundo con verdad conocida, lente sucia, corredores, KPIs. **No es una capacidad del agente** — nada registrado en `tools.py` |
```

- [ ] **Step 3: Verificar que los comandos documentados corren**

```bash
PYTHONPATH=. python examples/run_world_sim.py --no-kern --weeks 12 --skus 8
```

Expected: imprime la tabla piso/techo sin errores, con ASCII puro.

- [ ] **Step 4: Commit**

```bash
git add documentation/TESTING_ENVIRONMENT.md CLAUDE.md
git commit -m "docs(simworld): documentar el marcador como capa 5 del entorno de prueba"
```

---

## Cobertura del spec

| Requisito del spec | Tarea |
|---|---|
| Mundo generado con semilla, verdad inmutable | 1 |
| Misma realizacion para todos los corredores | 1 (test de reproducibilidad) + 11 (mismo `world`) |
| Fisica semanal, venta perdida, lead time real | 2 |
| Conservacion de masa | 2 |
| Lente nivel 0 canonico que `intake` lee entero | 3 |
| La lente no muta el mundo | 3 |
| Suciedad nivel 1 y 2 | 4 |
| Suciedad nivel 3, niveles acumulan | 5 |
| Suciedad reproducible por semilla | 5 |
| Corredor piso | 7 |
| Corredor techo (oraculo) | 7 |
| Corredores Kern: brief y forzado | 8 |
| Traduccion del `GuidedOutcome`, handoff = semana sin accion | 8 |
| Writeback no puenteado / politica aplicada por el mundo | 7 (`apply_policy`) + 8 |
| Contrato de KPI de gym-invmgmt | 9 |
| Metricas de sesgo de AIM-Bench (latigo, anclaje, persecucion de demanda) | 9 |
| Captura normalizada | 9 |
| Superar al oraculo falla ruidosamente | 9 |
| Corredor vacio no produce NaN neutro | 9 (`score_run` con `{}` da el peor fill rate) + 11 |
| Degradacion anunciada, atribucion por periodo | 10 |
| Exceso negativo no se recorta | 10 |
| CLI, reporte md/json, curva | 11 |
| Golden de regresion | 11 |
| SKU desconocida es error, no se ignora | 2 |
| ASCII en consola | 11 (prints) + constraint global |
| No registrar como herramienta del agente | constraint global + 12 (documentado) |

## Correcciones hechas durante la auto-revision del plan

Tres, anotadas para que no se re-introduzcan:

1. **`simulate` sostenia las ordenes entre replanificaciones, no la politica.**
   Reinterpretaba una cantidad pedida como un nivel objetivo, que son cosas
   distintas: la corrida habria pedido de menos sistematicamente. Corregido
   sosteniendo la `Policy` y reaplicandola con `apply_policy` cada semana. El
   oraculo salio a su propia funcion (`simulate_oracle`) porque no es un
   corredor de politica.

2. **Las metricas de sesgo de AIM-Bench casi quedan afuera con un argumento
   falso.** El razonamiento descartado era "Kern es determinista, no puede tener
   sesgos". Es incorrecto: la persecucion de demanda no es un sesgo cognitivo
   sino una propiedad del estimador, y **un pronostico de media movil persigue
   demanda por definicion**. Un motor determinista puede marcarla igual de alto
   que un LLM. Las tres metricas entran en la Task 9.

3. **El umbral de calidad de intake estaba hardcodeado en el arnes.** Se toma de
   `JobReport.intake_quality_threshold`, que es el que el propio tool declara. Si
   no, el marcador mediria nuestra idea de "aviso" en vez de la de Kern.

---

## Adendo: Tasks 13-14 — puente hacia el estado general del Tower

> **Spec:** `docs/superpowers/specs/2026-07-31-marcador-state-bridge-design.md`
> (adicional al spec original de arriba, no lo reemplaza). Opcional respecto
> del marcador primario: `scorer.py` (Tasks 9-10) sigue siendo la medida de si
> Kern decidio bien. Esto prueba que la plomeria general del Tower (seis
> dominios de `src.state` + `verify/backtest.py` + los monitores de
> `scm_agent/monitors.py`) funciona de punta a punta con un productor real, en
> vez de solo con DataFrames de test escritos a mano.
>
> **Depende de Task 8 (corredor Kern) ya implementada.** No modifica ni
> reordena las Tasks 1-12 de arriba.

### Estructura de archivos (adendo)

| Archivo | Responsabilidad |
|---|---|
| `src/state/record.py` | Wrappers delgados sobre `system_state.snapshot()`, uno por dominio. Agnostico de simworld: reusable por un backfill de dataset real o instrumentacion en vivo despues |
| `src/state/__init__.py` | **Modificar**: reexportar los cuatro `record_*` |
| `src/simworld/runners.py` | **Modificar**: `Policy` gana el campo `forecast`; `kern_policy()` lo puebla |
| `examples/run_world_sim.py` | **Modificar**: `simulate()` gana el parametro opcional `state_store` |
| `tests/test_state_record.py` | Los cuatro `record_*`, mas la propiedad de orden de `cycle_id` |
| `tests/test_simworld_state_bridge.py` | Integracion: los tres consumidores generales devuelven no-vacio contra una corrida de simworld |

### Task 13: El escritor general — `src/state/record.py`

**Files:**
- Create: `src/state/record.py`
- Modify: `src/state/__init__.py`
- Test: `tests/test_state_record.py`

**Interfaces:**
- Consumes: `src.state.system_state.snapshot`, `StateSnapshot`, `SchemaValidationError`; `src.state.store.StateStore`; `src.state.system_state.history` (solo en el test, para la propiedad de orden)
- Produces: `record_stock(store, cycle_id, rows: list[dict]) -> StateSnapshot`,
  `record_forecast(store, cycle_id, rows) -> StateSnapshot`,
  `record_decision(store, cycle_id, rows) -> StateSnapshot`,
  `record_outcome(store, cycle_id, rows) -> StateSnapshot`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_state_record.py
from __future__ import annotations

import pytest

from src.state import record
from src.state.store import StateStore
from src.state.system_state import SchemaValidationError, history


@pytest.fixture
def store(tmp_path):
    return StateStore(tmp_path / "state")


def test_record_stock_writes_a_snapshot_the_domain_accepts(store):
    rows = [
        {"product_id": "A1", "on_hand": 40.0, "reorder_point": 20.0, "avg_daily_demand": 5.0},
        {"product_id": "A2", "on_hand": 12.0, "reorder_point": 8.0, "avg_daily_demand": 2.0},
    ]
    snap = record.record_stock(store, "0", rows)
    assert snap.domain == "stock"
    assert snap.cycle_id == "0"
    assert set(snap.payload["product_id"]) == {"A1", "A2"}


def test_record_forecast_writes_product_period_forecast_qty(store):
    rows = [{"product_id": "A1", "period": "0", "forecast_qty": 5.0}]
    snap = record.record_forecast(store, "0", rows)
    assert list(snap.payload["forecast_qty"]) == [5.0]


def test_record_decision_writes_the_required_text_field_and_the_extra_order_qty(store):
    rows = [{"product_id": "A1", "tool": "inventory_optimization",
             "decision": "reponer", "period": "0", "order_qty": 30.0}]
    snap = record.record_decision(store, "0", rows)
    assert snap.payload.loc[0, "order_qty"] == 30.0
    assert snap.payload.loc[0, "decision"] == "reponer"


def test_record_outcome_writes_both_metrics_as_separate_rows(store):
    rows = [
        {"product_id": "A1", "tool": "inventory_optimization", "metric": "actual_qty",
         "value": 12.0, "period": "0"},
        {"product_id": "A1", "tool": "inventory_optimization", "metric": "sold_qty",
         "value": 9.0, "period": "0"},
    ]
    snap = record.record_outcome(store, "0", rows)
    assert set(snap.payload["metric"]) == {"actual_qty", "sold_qty"}


def test_a_missing_required_column_raises_the_domains_own_validation_error(store):
    with pytest.raises(SchemaValidationError):
        record.record_stock(store, "0", [{"product_id": "A1", "on_hand": 40.0}])


def test_sequential_integer_cycle_ids_beyond_nine_still_order_correctly(store):
    for week in range(11):
        record.record_stock(store, str(week), [
            {"product_id": "A1", "on_hand": float(week), "reorder_point": 1.0,
             "avg_daily_demand": 1.0},
        ])
    hist = history("stock", store=store)
    assert [s.cycle_id for s in hist] == [str(w) for w in range(11)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_state_record.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'src.state.record'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/state/record.py
"""Escritores ergonomicos, uno por dominio de src.state.system_state.

Cada ``record_*`` es un wrapper delgado sobre ``snapshot()``: arma un
DataFrame a partir de dicts planos y lo valida/agrega bajo el contrato de
esquema propio del dominio. Sin logica de negocio, sin texto inventado, sin
valores por defecto -- el llamador decide que significa cada fila. Es lo que
permite que un backfill de dataset real o una instrumentacion en vivo,
despues, enchufen la misma API que usa simworld hoy.
"""

from __future__ import annotations

import pandas as pd

from src.state.store import StateStore
from src.state.system_state import StateSnapshot
from src.state.system_state import snapshot as _snapshot


def _record(domain: str, store: StateStore, cycle_id: str, rows: list[dict]) -> StateSnapshot:
    return _snapshot(domain, pd.DataFrame(rows), cycle_id, store=store)


def record_stock(store: StateStore, cycle_id: str, rows: list[dict]) -> StateSnapshot:
    """Cada fila: ``product_id``, ``on_hand``, ``reorder_point``, ``avg_daily_demand``."""
    return _record("stock", store, cycle_id, rows)


def record_forecast(store: StateStore, cycle_id: str, rows: list[dict]) -> StateSnapshot:
    """Cada fila: ``product_id``, ``period``, ``forecast_qty``."""
    return _record("forecast", store, cycle_id, rows)


def record_decision(store: StateStore, cycle_id: str, rows: list[dict]) -> StateSnapshot:
    """Cada fila: ``product_id``, ``tool``, ``decision`` (texto libre), mas las
    columnas extra que un join necesite (``period``, ``order_qty``)."""
    return _record("decisions", store, cycle_id, rows)


def record_outcome(store: StateStore, cycle_id: str, rows: list[dict]) -> StateSnapshot:
    """Cada fila: ``product_id``, ``tool``, ``metric``, ``value``, mas ``period``
    cuando un join posterior necesite calzarla con una fila de forecast o decision."""
    return _record("outcomes", store, cycle_id, rows)
```

Agregar a `src/state/__init__.py` (import y `__all__`):

```python
from .record import record_decision, record_forecast, record_outcome, record_stock
```

y en `__all__`: `"record_decision"`, `"record_forecast"`, `"record_outcome"`, `"record_stock"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_state_record.py -v`
Expected: 6 passed

- [ ] **Step 5: Lint and commit**

```bash
ruff check src tests
git add src/state/record.py src/state/__init__.py tests/test_state_record.py
git commit -m "feat(state): escritores generales por dominio (record_stock/forecast/decision/outcome)"
```

---

### Task 14: El puente enchufado — `simulate()` escribe, los tres consumidores generales leen

**Files:**
- Modify: `src/simworld/runners.py:24` (`Policy`), y el cuerpo de `kern_policy()`
- Modify: `examples/run_world_sim.py` (`simulate()`)
- Test: `tests/test_simworld_state_bridge.py`

**Interfaces:**
- Consumes: `record_stock/forecast/decision/outcome` (Task 13), `Policy`/`apply_policy`/`kern_policy`/`naive_policy` (Tasks 7-8), `simulate` (Task 11), `scm_agent.monitors.run_all_monitors`, `src.verify.backtest.run_forecast_backtest`/`match_decision_actuals`, `src.state.system_state.history`, `src.state.store.StateStore`
- Produces: `Policy.forecast: dict[str, float]` (nuevo campo, default `{}`); `simulate(world, plan, *, replan_every=4, state_store: StateStore | None = None)`

**Por que `decisions` se escribe cada semana y `forecast` no:** `apply_policy`
recalcula la orden contra la posicion de inventario vigente en cada llamada a
`simulate()` — replan o no — asi que es una decision real cada semana, nunca
un numero repetido. `forecast`, en cambio, solo cambia cuando `plan()`
efectivamente corre (las semanas de replanificacion); escribirlo cada semana
falsificaria el dato, haciendo parecer que Kern predijo de nuevo cuando la
politica seguia congelada.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_simworld_state_bridge.py
from __future__ import annotations

from src.simworld.runners import Policy, kern_policy, naive_policy
from src.simworld.world import generate_world
from src.state.store import StateStore
from src.state.system_state import history
from scm_agent.events import EventLedger
from scm_agent.monitors import run_all_monitors
from src.verify.backtest import match_decision_actuals, run_forecast_backtest

from examples.run_world_sim import simulate

EMPTY = Policy(reorder_point={}, order_up_to={}, announced=False, note="", status="ok")


def _kern_plan(agent_dir, seed):
    def plan(world, outcomes, week):
        if len(outcomes) < 4:
            return EMPTY, False
        pol = kern_policy(world, outcomes, level=0, seed=seed,
                          out_dir=agent_dir / f"w{week}", forced=True)
        return pol, pol.announced
    return plan


def test_simulate_writes_all_four_domains_when_a_state_store_is_given(tmp_path):
    world = generate_world(seed=7, n_skus=4, n_suppliers=1, weeks=12, regime="seasonal_trend")
    store = StateStore(tmp_path / "state")

    simulate(world, _kern_plan(tmp_path / "agent", seed=7), replan_every=4, state_store=store)

    assert len(history("stock", store=store)) == 12
    assert len(history("outcomes", store=store)) == 12
    assert len(history("decisions", store=store)) == 12
    assert len(history("forecast", store=store)) >= 1


def test_the_three_general_consumers_return_non_empty_against_the_bridge(tmp_path):
    world = generate_world(seed=7, n_skus=4, n_suppliers=1, weeks=12, regime="seasonal_trend")
    store = StateStore(tmp_path / "state")

    simulate(world, _kern_plan(tmp_path / "agent", seed=7), replan_every=4, state_store=store)

    events = run_all_monitors(store=store, ledger=EventLedger(":memory:"))
    forecast_results = run_forecast_backtest(store=store)
    decision_obs = match_decision_actuals(
        history("decisions", store=store), history("outcomes", store=store),
        decision_value_column="order_qty",
    )

    assert len(events) > 0
    assert len(forecast_results) > 0
    assert len(decision_obs) > 0


def test_state_store_none_does_not_change_existing_behavior():
    world = generate_world(seed=3, n_skus=3, n_suppliers=1, weeks=8, regime="seasonal_trend")

    def naive_plan(w, outs, week):
        if len(outs) < 4:
            return EMPTY, False
        return naive_policy(w, outs), False

    outs_default, flags_default = simulate(world, naive_plan, replan_every=4)
    outs_none, flags_none = simulate(world, naive_plan, replan_every=4, state_store=None)

    assert [o.on_hand_end.tolist() for o in outs_default] == [o.on_hand_end.tolist() for o in outs_none]
    assert flags_default == flags_none
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_simworld_state_bridge.py -v`
Expected: FAIL con `TypeError: simulate() got an unexpected keyword argument 'state_store'`

- [ ] **Step 3: Write minimal implementation**

En `src/simworld/runners.py`, agregar el campo a `Policy` (justo despues de `extras`):

```python
@dataclass(frozen=True)
class Policy:
    reorder_point: dict[str, float]
    order_up_to: dict[str, float]
    announced: bool = False
    note: str = ""
    status: str = "ok"
    extras: dict[str, float] = field(default_factory=dict)
    forecast: dict[str, float] = field(default_factory=dict)  # nuevo: SkuRecommendation.forecast por SKU
```

En `kern_policy()`, dentro del bucle que arma `reorder`/`target` desde
`result.report.recommendations`, agregar un tercer acumulador y pasarlo al
`Policy` final:

```python
    reorder: dict[str, float] = {}
    target: dict[str, float] = {}
    forecast: dict[str, float] = {}
    for rec in result.report.recommendations:
        rop = float(rec.reorder_point)
        reorder[rec.product_id] = rop
        forecast[rec.product_id] = float(rec.forecast)
        if rec.order_up_to is not None:
            target[rec.product_id] = float(rec.order_up_to)
        else:
            target[rec.product_id] = rop + float(rec.order_quantity or 0.0)

    return Policy(reorder_point=reorder, order_up_to=target, forecast=forecast,
                  announced=announced, note=result.summary, status="ok")
```

En `examples/run_world_sim.py`, `simulate()` gana el parametro y las cuatro
escrituras. La Task 11 ya importa de `src.simworld.runners` (`Policy`,
`apply_policy`, `kern_policy`, `naive_policy`, `oracle_orders`) -- agregar
`NAIVE_LOOKBACK_WEEKS` a esa MISMA tupla de import, no una linea nueva
separada, y sumar los dos imports de `src.state`:

```python
from src.simworld.runners import (
    NAIVE_LOOKBACK_WEEKS,  # el mismo umbral que usa naive_policy, Task 7 -- no redefinir
    Policy,
    apply_policy,
    kern_policy,
    naive_policy,
    oracle_orders,
)
from src.state.record import record_decision, record_forecast, record_outcome, record_stock
from src.state.store import StateStore

TOOL_NAME = "inventory_optimization"  # el job_type real que kern_policy(forced=True) pasa


def simulate(
    world: World,
    plan: Plan,
    *,
    replan_every: int = 4,
    state_store: StateStore | None = None,
) -> tuple[list, list[bool]]:
    """Corre el horizonte sosteniendo una politica entre replanificaciones.

    Cuando ``state_store`` no es ``None``, ademas escribe ``stock``,
    ``outcomes`` y ``decisions`` cada semana, y ``forecast`` solo en las
    semanas de replanificacion (ver ``docs/superpowers/specs/
    2026-07-31-marcador-state-bridge-design.md``). ``state_store=None``
    (default) no escribe nada -- el comportamiento existente no cambia.

    Advertencia para quien lea un backtest corrido sobre este puente: un MAPE
    alto en ``decisions`` (``order_qty`` vs. ``actual_qty``) no significa que
    Kern decida mal -- una politica de punto de reorden pide en bloque para
    cubrir varios periodos, no para igualar la demanda semana a semana. El
    marcador honesto de si la decision fue buena sigue siendo
    ``src/simworld/scorer.py`` (captura normalizada contra el oraculo).
    """
    if replan_every < 1:
        raise ValueError(f"replan_every debe ser >= 1, no {replan_every}")

    state = initial_state(world)
    outcomes: list = []
    flags: list[bool] = []
    policy = Policy(reorder_point={}, order_up_to={}, announced=False, note="", status="ok")

    for week in range(world.weeks):
        cycle_id = str(week)
        announced = False
        replanned = week % replan_every == 0
        if replanned:
            policy, announced = plan(world, outcomes, week)

        orders = apply_policy(world, state, policy)
        state, out = advance(world, state, orders)
        outcomes.append(out)
        flags.append(bool(announced))

        if state_store is not None:
            # outcomes.append(out) ya corrio arriba, asi que window tiene
            # como minimo la semana actual -- nunca esta vacio aca.
            window = outcomes[-NAIVE_LOOKBACK_WEEKS:]
            record_stock(state_store, cycle_id, [
                {
                    "product_id": sku.sku,
                    "on_hand": float(out.on_hand_end[i]),
                    "reorder_point": float(policy.reorder_point.get(sku.sku, 0.0)),
                    # promedio semanal / 7: el dominio espera una tasa DIARIA
                    # (scm_agent/monitors.py calcula "dias de cobertura" con esto).
                    "avg_daily_demand": float(sum(w.sold[i] for w in window) / len(window) / 7.0),
                }
                for i, sku in enumerate(world.skus)
            ])
            record_outcome(state_store, cycle_id, [
                row
                for i, sku in enumerate(world.skus)
                for row in (
                    {"product_id": sku.sku, "tool": TOOL_NAME, "metric": "actual_qty",
                     "value": float(out.demand[i]), "period": cycle_id},
                    {"product_id": sku.sku, "tool": TOOL_NAME, "metric": "sold_qty",
                     "value": float(out.sold[i]), "period": cycle_id},
                )
            ])
            record_decision(state_store, cycle_id, [
                {
                    "product_id": sku.sku, "tool": TOOL_NAME,
                    "decision": "reponer" if orders.get(sku.sku, 0.0) > 0 else "sin orden",
                    "period": cycle_id, "order_qty": float(orders.get(sku.sku, 0.0)),
                }
                for sku in world.skus
            ])
            if replanned and policy.forecast:
                record_forecast(state_store, cycle_id, [
                    {"product_id": product_id, "period": cycle_id, "forecast_qty": qty}
                    for product_id, qty in policy.forecast.items()
                ])
    return outcomes, flags
```

**Si `test_the_three_general_consumers_return_non_empty_against_the_bridge`
devuelve `run_all_monitors(...)` vacio:** revisar `config/monitors.yaml` --
si algun monitor relevante (`rop_breach`, `stockout_projected`,
`forecast_error_out_of_band`) tiene un umbral mas estricto que lo que un mundo
de 4 SKUs / 12 semanas produce, subir `n_skus`/`weeks` en el test o pasar un
`config` explicito con umbrales mas laxos -- no relajar el monitor de
produccion para que el test pase.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_simworld_state_bridge.py -v`
Expected: 3 passed

- [ ] **Step 5: Verificar que nada del suite existente se rompio**

Run: `PYTHONPATH=. pytest tests/test_simworld_runners.py tests/test_simworld_e2e.py -v`
Expected: todos pasan (la firma de `Policy` y `simulate()` solo gano campos
con default, ningun call site existente cambia)

- [ ] **Step 6: Lint and commit**

```bash
ruff check src tests examples
git add src/simworld/runners.py examples/run_world_sim.py tests/test_simworld_state_bridge.py
git commit -m "feat(simworld): puente hacia src.state -- simulate() escribe los 4 dominios, el Tower general los lee"
```

---

## Cobertura del spec (adendo, 2026-07-31)

| Requisito del spec | Tarea |
|---|---|
| `record_stock`/`record_forecast`/`record_decision`/`record_outcome`, agnosticos de simworld | 13 |
| `cycle_id` numerico, orden correcto mas alla de la semana 9 | 13 (test de propiedad) |
| `stock`/`outcomes`/`decisions` cada semana; `forecast` solo en replan | 14 |
| `outcomes` con dos metricas (`actual_qty` no censurada, `sold_qty` censurada) | 14 |
| Backtest de forecast usa `actual_qty`, nunca `sold_qty` | 14 (`run_forecast_backtest` via `ACTUAL_QTY_METRIC`) |
| `decisions.order_qty` explicito en 0.0, nunca fila ausente | 14 |
| `Policy.forecast` poblado desde `SkuRecommendation.forecast` | 14 |
| `tool` = `"inventory_optimization"` consistente entre `decisions` y `outcomes` | 14 |
| `avg_daily_demand` convertido a tasa diaria (/7) | 14 |
| `StateStore` siempre aislado, nunca `default_store()` | 13 (constructor explicito) + 14 (parametro, nunca el default global) |
| `state_store=None` no cambia el comportamiento existente | 14 (test de propiedad) |
| Los tres consumidores generales devuelven no-vacio | 14 (criterio de aceptacion) |
| Punto de integracion es `simulate()`, no `runners.py` ni `scorer.py` | 14 (unico archivo modificado para el enchufe) |
| Advertencia interpretativa (MAPE de `decisions` no es precision) documentada donde se lee, no solo en el spec | 14 (docstring de `simulate()`) |
| No se registra como herramienta del agente | constraint global (ya cubierta arriba) |

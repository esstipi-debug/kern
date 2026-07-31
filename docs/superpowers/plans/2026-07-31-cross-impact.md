# cross_impact Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar el tool #47 `cross_impact`, que dada una decisión ya propuesta por un área mide el impacto en las otras tres y emite un veredicto de objeción contra un umbral declarado por el operador.

**Architecture:** Dos módulos puros nuevos (`src/objection.py` juzga, `src/cross_impact.py` mide) bajo un job (`jobs/cross_impact_job.py`) que reusa `jobs/hats_job.prepare` para la lectura del CSV y `src/hats.hat_kpis` para todo número. El tool se registra en `scm_agent/tools.py` y ancla citas en `scm_agent/citation_gate.py`. Los umbrales viven en `src/client_profile.ClientProfile`.

**Tech Stack:** Python 3.11+, pytest, pandas (sólo en `jobs/`), stdlib en `src/`.

**Spec:** `docs/superpowers/specs/2026-07-31-cross-impact-design.md` (commit be99009). Cuando este plan y el spec discrepen, gana el spec.

## Global Constraints

- **Rama:** `feat/cross-impact`, ya creada sobre `origin/main`. Nunca push directo a `main`. Flujo: rama → draft PR → CI verde (py3.11/3.12/3.13) → squash-merge.
- **Tests:** `pytest tests/ -q` con `PYTHONPATH=.`. Lint: `ruff check src tests examples`.
- **ASCII-only en prints de consola** (Windows cp1252 rompe con em dashes). Los `.md` en utf-8 están bien.
- **`src/` nunca importa `scm_agent`** (D7 del spec de hats). `src/objection.py` y `src/cross_impact.py` no importan pandas — no pueden entrar a la cadena de boot de prod con una dep opcional.
- **Sin defaults silenciosos.** Un dato que falta produce `needs_clarification` o un campo vacío, nunca un cero ni un valor asumido.
- **Umbral inclusivo:** `deterioration == threshold` ⇒ `ACEPTA`. Sólo `>` objeta.
- Las 4 claves de área son exactamente `src.hats.HAT_KEYS` = `("comprador", "planner", "cfo", "comercial")`.
- Firmas ya existentes que este plan consume, verbatim del código:
  - `src.hats.Candidate(order_quantity: float, service_level: float)` — frozen; valida `q > 0` y `0 < sl < 1` en `__post_init__`.
  - `src.hats.hat_kpis(inputs: HatInputs, hat_key: str, cand: Candidate) -> dict[str, float]`
  - `src.hats.headline_kpi(hat_key: str) -> str` → `effective_unit_cost` / `policy_cost` / `capital_charge_usd` / `fill_rate`
  - `src.hats.decision_cost(inputs: HatInputs, cand: Candidate) -> float`
  - `src.hats.evaluate(inputs: HatInputs) -> GridEvaluation` (`.candidates: tuple[Candidate, ...]`)
  - `src.hats.HatConfig.sl_target: float` (default 0.95)
  - `jobs.hats_job.prepare(data_path: str, params: dict | None) -> dict` → `{"inputs": tuple[HatInputs, ...], "weights": ..., "config": HatConfig, "skipped": tuple[str, ...]}`
  - `src.export.write_summary_csv(rows: list[dict], path: Path | str) -> Path` — vive en `src/export.py`, **no** en `src/deliverable.py` (así lo importa `jobs/hats_job.py:23`). Aplica `defuse_formula` a cada celda y escribe vía pandas, así que una celda `""` sale vacía en el CSV.
  - `src.decision_options.Scenario(label, summary, metrics, action, tradeoffs)` / `Objective(metric, weight, maximize)` / `decide(summary, scenarios, objectives, *, confidence, residuals) -> GuidedOutcome`
  - `src.guided.verify_guided(outcome) -> list[str]`, `Residual(description, owner, risk_if_skipped)`
  - **NO existe** `src.guided.confidence_from_coverage` ni `COVERAGE_PENALTY` en esta rama. Sí existen en `feat/simworld-marcador` (commit `25cd8ed`, sin mergear a main y sin PR). Este plan **no** los usa: la Tarea 5 deriva su confianza con una constante local. No los importes.

---

## File Structure

| Archivo | Responsabilidad | Tarea |
|---|---|---|
| `src/objection.py` | **Crear.** Juzga un delta contra un umbral. Tres estados. No sabe de SKUs ni de demanda. | 1 |
| `tests/test_objection.py` | **Crear.** | 1 |
| `src/cross_impact.py` | **Crear.** Mide el delta por área entre dos `Candidate`. No juzga. | 2 |
| `tests/test_cross_impact.py` | **Crear.** | 2 |
| `src/client_profile.py` | **Modificar.** Campo `objection_thresholds` + validación + `as_params()`. | 3 |
| `tests/test_client_profile.py` | **Modificar.** | 3 |
| `jobs/cross_impact_job.py` | **Crear.** `prepare`/`run`/`verify`/`write`/`build_deck`. Único lugar con pandas e I/O. | 4, 5, 6 |
| `tests/test_cross_impact_job.py` | **Crear.** | 4, 5, 6 |
| `scm_agent/tools.py` | **Modificar.** `cross_impact_tool()` + `reg.register(...)` cerca de la línea 2601. | 7 |
| `scm_agent/citation_gate.py` | **Modificar.** Entrada en `TOOL_CONCEPTS`. | 7 |
| `tests/test_cross_impact_tool.py` | **Crear.** Guardas de integración vía `Orchestrator`. | 7 |
| `examples/run_cross_impact.py` | **Crear.** CLI operativo. | 8 |
| `README.md`, `CLAUDE.md`, `documentation/` | **Modificar.** Conteo de tools 46 → 47. | 8 |

**Orden y dependencias:** 1 y 2 son independientes entre sí. 3 es independiente. 4 depende de 1+2+3. 5 y 6 dependen de 4. 7 depende de 6. 8 depende de 7.

---

### Task 1: `src/objection.py` — el juicio

**Files:**
- Create: `src/objection.py`
- Test: `tests/test_objection.py`

**Interfaces:**
- Consumes: `src.hats.HAT_KEYS`, `src.hats.HAT_COMERCIAL`, `src.hats.HAT_PLANNER`, `src.hats.headline_kpi`.
- Produces: `ACEPTA`, `OBJETA`, `NO_EVALUABLE`, `MAXIMIZED_HATS`, `Verdict`, `deterioration(hat_key, kpi_current, kpi_proposed) -> float`, `judge(hat_key, kpi_current, kpi_proposed, threshold, *, breaches_sl_target=False) -> Verdict`.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_objection.py`:

```python
"""Verdict rules for one area's KPI delta (spec 2026-07-31, sec 6.1 + 9)."""

import math

import pytest

from src.hats import HAT_CFO, HAT_COMERCIAL, HAT_COMPRADOR, HAT_KEYS, HAT_PLANNER
from src.objection import (
    ACEPTA,
    MAXIMIZED_HATS,
    NO_EVALUABLE,
    OBJETA,
    Verdict,
    deterioration,
    judge,
)


def test_only_comercial_is_maximized():
    assert MAXIMIZED_HATS == frozenset({HAT_COMERCIAL})


@pytest.mark.parametrize("hat", [HAT_COMPRADOR, HAT_PLANNER, HAT_CFO])
def test_minimized_hats_deteriorate_when_kpi_rises(hat):
    # cost-like KPI: going up is worse
    assert deterioration(hat, 100.0, 130.0) == pytest.approx(30.0)
    assert deterioration(hat, 100.0, 80.0) == pytest.approx(-20.0)


def test_comercial_deteriorates_when_fill_rate_falls():
    # fill rate: going DOWN is worse - the sign must invert
    assert deterioration(HAT_COMERCIAL, 0.95, 0.90) == pytest.approx(0.05)
    assert deterioration(HAT_COMERCIAL, 0.90, 0.95) == pytest.approx(-0.05)


def test_unknown_hat_raises():
    with pytest.raises(ValueError):
        deterioration("almacen", 1.0, 2.0)


def test_improvement_accepts_even_beyond_threshold():
    v = judge(HAT_CFO, 100.0, 10.0, threshold=1.0)
    assert v.status == ACEPTA
    assert v.deterioration == pytest.approx(-90.0)


def test_deterioration_equal_to_threshold_accepts():
    # the threshold is INCLUSIVE - only strictly greater objects
    assert judge(HAT_CFO, 100.0, 112.0, threshold=12.0).status == ACEPTA


def test_deterioration_above_threshold_objects():
    v = judge(HAT_CFO, 100.0, 112.01, threshold=12.0)
    assert v.status == OBJETA
    assert v.kpi_name == "capital_charge_usd"
    assert v.threshold == pytest.approx(12.0)
    assert v.reason


def test_missing_threshold_is_unevaluable_with_empty_fields():
    v = judge(HAT_COMPRADOR, 10.0, 99.0, threshold=None)
    assert v.status == NO_EVALUABLE
    assert v.kpi_current is None
    assert v.kpi_proposed is None
    assert v.deterioration is None
    assert v.threshold is None
    assert "umbral" in v.reason.lower()


def test_planner_objects_on_sl_breach_despite_improving_cost():
    # spec 9.1: policy_cost is the RAW cost - dropping SL makes it CHEAPER,
    # so the delta alone would report an improvement on a service breach.
    v = judge(HAT_PLANNER, 50_000.0, 41_000.0, threshold=8_000.0,
              breaches_sl_target=True)
    assert v.status == OBJETA
    assert v.deterioration == pytest.approx(-9_000.0)
    assert "sl_target" in v.reason or "servicio" in v.reason.lower()


def test_sl_breach_is_ignored_for_non_planner_hats():
    v = judge(HAT_CFO, 100.0, 90.0, threshold=5.0, breaches_sl_target=True)
    assert v.status == ACEPTA


def test_sl_breach_does_not_override_unevaluable():
    v = judge(HAT_PLANNER, 50_000.0, 41_000.0, threshold=None,
              breaches_sl_target=True)
    assert v.status == NO_EVALUABLE


def test_non_finite_kpi_raises():
    with pytest.raises(ValueError):
        judge(HAT_CFO, 100.0, math.nan, threshold=1.0)


def test_verdict_is_frozen():
    v = judge(HAT_CFO, 100.0, 101.0, threshold=5.0)
    assert isinstance(v, Verdict)
    with pytest.raises(Exception):
        v.status = ACEPTA


def test_every_hat_key_is_judgeable():
    for k in HAT_KEYS:
        assert judge(k, 1.0, 1.0, threshold=0.5).status == ACEPTA
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=. pytest tests/test_objection.py -q
```

Esperado: FAIL — `ModuleNotFoundError: No module named 'src.objection'`.

- [ ] **Step 3: Write minimal implementation**

Crear `src/objection.py`:

```python
"""Objection verdict for one area over one decision change (spec 2026-07-31).

Pure: frozen dataclasses + pure functions, no pandas, no I/O, no scm_agent
imports. Knows nothing about SKUs or demand - it takes one area's KPI at two
points and the operator's declared tolerance, and says whether that area would
object.

Two rules that are easy to get wrong and are therefore explicit here:

1. Direction is NOT uniform. Three hats minimize a cost-like KPI; `comercial`
   maximizes fill rate. A plain ``abs(delta) > threshold`` would report an
   objection when the CFO's capital charge FALLS.
2. The planner's headline KPI does not capture its own service constraint.
   ``src.hats.hat_kpis`` returns ``policy_cost = -_u_planner_valid(...)``, the
   RAW policy cost; the ``sl_target`` penalty lives in ``src.hats.utilities``,
   not here. So a proposal that drops the service level below target makes the
   planner's KPI CHEAPER. ``breaches_sl_target`` is the caller-supplied flag
   that forces an objection in that case, regardless of the delta.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.hats import HAT_COMERCIAL, HAT_KEYS, HAT_PLANNER, headline_kpi

ACEPTA = "acepta"
OBJETA = "objeta"
NO_EVALUABLE = "no_evaluable"

# The only hat whose headline KPI is better when it goes UP.
MAXIMIZED_HATS = frozenset({HAT_COMERCIAL})


@dataclass(frozen=True)
class Verdict:
    """One area's stance on a proposed change.

    Every optional field is ``None`` - never 0.0 - when the verdict is
    NO_EVALUABLE: a declared gap, not a fabricated number.
    """

    hat_key: str
    status: str
    kpi_name: str
    kpi_current: float | None
    kpi_proposed: float | None
    deterioration: float | None
    threshold: float | None
    reason: str


def _require_known_hat(hat_key: str) -> None:
    if hat_key not in HAT_KEYS:
        raise ValueError(f"unknown hat: {hat_key!r} (expected one of {HAT_KEYS})")


def _require_finite(value: float, name: str) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")


def deterioration(hat_key: str, kpi_current: float, kpi_proposed: float) -> float:
    """How much worse the proposal is for this hat, in the KPI's own units.

    Positive = worse. Sign flips for the maximized hats so that "worse" always
    reads the same way downstream.
    """
    _require_known_hat(hat_key)
    _require_finite(kpi_current, "kpi_current")
    _require_finite(kpi_proposed, "kpi_proposed")
    if hat_key in MAXIMIZED_HATS:
        return kpi_current - kpi_proposed
    return kpi_proposed - kpi_current


def judge(
    hat_key: str,
    kpi_current: float,
    kpi_proposed: float,
    threshold: float | None,
    *,
    breaches_sl_target: bool = False,
) -> Verdict:
    """Verdict for one hat. ``threshold`` None => NO_EVALUABLE (never a default).

    ``threshold`` is INCLUSIVE: a deterioration exactly equal to it accepts.
    """
    _require_known_hat(hat_key)
    kpi_name = headline_kpi(hat_key)

    if threshold is None:
        return Verdict(
            hat_key=hat_key, status=NO_EVALUABLE, kpi_name=kpi_name,
            kpi_current=None, kpi_proposed=None, deterioration=None, threshold=None,
            reason=(f"{hat_key}: sin umbral de objecion declarado en el perfil del "
                    "cliente - esta area no es evaluable"),
        )

    _require_finite(threshold, "threshold")
    delta = deterioration(hat_key, kpi_current, kpi_proposed)

    if hat_key == HAT_PLANNER and breaches_sl_target:
        status = OBJETA
        reason = ("planner: el nivel de servicio propuesto cae bajo config.sl_target - "
                  "objeta por restriccion de servicio, no por el delta de policy_cost "
                  f"({delta:+,.2f})")
    elif delta > threshold:
        status = OBJETA
        reason = (f"{hat_key}: {kpi_name} empeora {delta:,.4f}, sobre el umbral "
                  f"declarado {threshold:,.4f}")
    else:
        status = ACEPTA
        reason = (f"{hat_key}: {kpi_name} varia {delta:+,.4f}, dentro del umbral "
                  f"declarado {threshold:,.4f}")

    return Verdict(
        hat_key=hat_key, status=status, kpi_name=kpi_name,
        kpi_current=kpi_current, kpi_proposed=kpi_proposed,
        deterioration=delta, threshold=threshold, reason=reason,
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH=. pytest tests/test_objection.py -q && ruff check src/objection.py tests/test_objection.py
```

Esperado: 12 passed, ruff limpio.

- [ ] **Step 5: Commit**

```bash
git add src/objection.py tests/test_objection.py
git commit -m "feat(objection): veredicto de objecion por area con umbral en unidad nativa"
```

---

### Task 2: `src/cross_impact.py` — la medición

**Files:**
- Create: `src/cross_impact.py`
- Test: `tests/test_cross_impact.py`

**Interfaces:**
- Consumes: `src.hats.{HAT_KEYS, HatInputs, Candidate, hat_kpis, headline_kpi, decision_cost}`.
- Produces: `ImpactRow(hat_key, kpi_name, value_current, value_proposed, delta)`, `CrossImpact(sku, proposed_by, current, proposed, rows, judge_cost_current, judge_cost_proposed)`, `measure(inputs, current, proposed, proposed_by) -> CrossImpact`.
- **No importa `src/objection.py`.** Mide, no juzga.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_cross_impact.py`:

```python
"""Cross-area impact measurement between two decision points (spec sec 6.2)."""

import pytest

from src.cross_impact import CrossImpact, ImpactRow, measure
from src.hats import (
    HAT_COMPRADOR,
    HAT_KEYS,
    Candidate,
    HatConfig,
    build_inputs,
    decision_cost,
    hat_kpis,
    headline_kpi,
)


@pytest.fixture
def inputs():
    return build_inputs(
        sku="SKU-1",
        annual_demand=5_200.0,
        mean_weekly=100.0,
        std_weekly=20.0,
        lead_time_weeks=2.0,
        unit_cost=10.0,
        config=HatConfig(),
    )


def test_one_row_per_hat_in_hat_keys_order(inputs):
    ci = measure(inputs, Candidate(200.0, 0.95), Candidate(400.0, 0.95), HAT_COMPRADOR)
    assert isinstance(ci, CrossImpact)
    assert tuple(r.hat_key for r in ci.rows) == HAT_KEYS
    assert all(isinstance(r, ImpactRow) for r in ci.rows)


def test_values_come_straight_from_hat_kpis(inputs):
    cur, prop = Candidate(200.0, 0.95), Candidate(400.0, 0.975)
    ci = measure(inputs, cur, prop, HAT_COMPRADOR)
    for row in ci.rows:
        name = headline_kpi(row.hat_key)
        assert row.kpi_name == name
        assert row.value_current == pytest.approx(hat_kpis(inputs, row.hat_key, cur)[name])
        assert row.value_proposed == pytest.approx(hat_kpis(inputs, row.hat_key, prop)[name])


def test_delta_is_raw_and_signed(inputs):
    cur, prop = Candidate(200.0, 0.95), Candidate(400.0, 0.95)
    ci = measure(inputs, cur, prop, HAT_COMPRADOR)
    for row in ci.rows:
        assert row.delta == pytest.approx(row.value_proposed - row.value_current)


def test_identical_points_give_zero_deltas(inputs):
    cand = Candidate(300.0, 0.95)
    ci = measure(inputs, cand, cand, HAT_COMPRADOR)
    assert all(r.delta == 0.0 for r in ci.rows)
    assert ci.judge_cost_current == pytest.approx(ci.judge_cost_proposed)


def test_judge_costs_match_decision_cost(inputs):
    cur, prop = Candidate(200.0, 0.95), Candidate(400.0, 0.99)
    ci = measure(inputs, cur, prop, HAT_COMPRADOR)
    assert ci.judge_cost_current == pytest.approx(decision_cost(inputs, cur))
    assert ci.judge_cost_proposed == pytest.approx(decision_cost(inputs, prop))


def test_unknown_proposer_raises(inputs):
    with pytest.raises(ValueError):
        measure(inputs, Candidate(200.0, 0.95), Candidate(400.0, 0.95), "almacen")


def test_sku_and_proposer_are_carried(inputs):
    ci = measure(inputs, Candidate(200.0, 0.95), Candidate(400.0, 0.95), HAT_COMPRADOR)
    assert ci.sku == "SKU-1"
    assert ci.proposed_by == HAT_COMPRADOR
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=. pytest tests/test_cross_impact.py -q
```

Esperado: FAIL — `ModuleNotFoundError: No module named 'src.cross_impact'`.

- [ ] **Step 3: Write minimal implementation**

Crear `src/cross_impact.py`:

```python
"""Cross-area impact of ONE proposed decision change (spec 2026-07-31, sec 6.2).

Measures, never judges: no import of ``src.objection``. Every number comes from
``src.hats`` (``hat_kpis`` / ``decision_cost``) - this module does no arithmetic
of its own beyond the subtraction, so a KPI it cannot find propagates a KeyError
instead of being filled in.

Unlike ``src.hat_council``, which computes each hat's IDEAL from the grid, this
takes two points the caller already has: what the client runs today and what an
area proposes. Neither has to lie on the ``src.hats`` grid.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.hats import (
    HAT_KEYS,
    Candidate,
    HatInputs,
    decision_cost,
    hat_kpis,
    headline_kpi,
)


@dataclass(frozen=True)
class ImpactRow:
    """One area's headline KPI at both points. ``delta`` is raw and signed
    (proposed - current); interpreting its direction is src.objection's job."""

    hat_key: str
    kpi_name: str
    value_current: float
    value_proposed: float
    delta: float


@dataclass(frozen=True)
class CrossImpact:
    """The measured effect of moving from ``current`` to ``proposed``."""

    sku: str
    proposed_by: str
    current: Candidate
    proposed: Candidate
    rows: tuple[ImpactRow, ...]          # one per HAT_KEYS, in HAT_KEYS order
    judge_cost_current: float
    judge_cost_proposed: float


def measure(
    inputs: HatInputs,
    current: Candidate,
    proposed: Candidate,
    proposed_by: str,
) -> CrossImpact:
    """Every hat's headline KPI at both points, plus the neutral judge cost."""
    if proposed_by not in HAT_KEYS:
        raise ValueError(
            f"proposed_by must be one of {HAT_KEYS}, got {proposed_by!r}")

    rows = []
    for hat_key in HAT_KEYS:
        name = headline_kpi(hat_key)
        # KeyError here is deliberate: a missing KPI is a contract break with
        # src.hats, not something to paper over with a default.
        v_cur = hat_kpis(inputs, hat_key, current)[name]
        v_prop = hat_kpis(inputs, hat_key, proposed)[name]
        rows.append(ImpactRow(
            hat_key=hat_key, kpi_name=name,
            value_current=v_cur, value_proposed=v_prop,
            delta=v_prop - v_cur,
        ))

    return CrossImpact(
        sku=inputs.sku,
        proposed_by=proposed_by,
        current=current,
        proposed=proposed,
        rows=tuple(rows),
        judge_cost_current=decision_cost(inputs, current),
        judge_cost_proposed=decision_cost(inputs, proposed),
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH=. pytest tests/test_cross_impact.py -q && ruff check src/cross_impact.py tests/test_cross_impact.py
```

Esperado: 7 passed, ruff limpio.

- [ ] **Step 5: Commit**

```bash
git add src/cross_impact.py tests/test_cross_impact.py
git commit -m "feat(cross-impact): medicion del delta por area entre dos puntos de decision"
```

---

### Task 3: `objection_thresholds` en `ClientProfile`

**Files:**
- Modify: `src/client_profile.py` (dataclass en la línea 103, `__post_init__` en 138, `as_params` en 151, `load_profile` en 191)
- Test: `tests/test_client_profile.py` (extender)

**Interfaces:**
- Produces: `ClientProfile.objection_thresholds: dict[str, float] | None`, incluido en `as_params()` bajo la key `"objection_thresholds"`.

- [ ] **Step 1: Write the failing test**

Agregar al final de `tests/test_client_profile.py`:

```python
# -- objection_thresholds (spec 2026-07-31, sec 5.2) --------------------------

import pytest

from src.client_profile import ClientProfile, load_profile, merge_params, upsert_profile


def test_objection_thresholds_round_trip(tmp_path):
    upsert_profile("Acme Corp", "Acme Corp", root=tmp_path,
                   objection_thresholds={"cfo": 12_000.0, "comercial": 0.03})
    loaded = load_profile("acme-corp", root=tmp_path)
    assert loaded.objection_thresholds == {"cfo": 12_000.0, "comercial": 0.03}


def test_partial_thresholds_are_legal(tmp_path):
    # a partial dict is the honest case: the missing areas become NO_EVALUABLE
    p = upsert_profile("Acme Corp", "Acme Corp", root=tmp_path,
                       objection_thresholds={"cfo": 1.0})
    assert set(p.objection_thresholds) == {"cfo"}
    assert load_profile("acme-corp", root=tmp_path).objection_thresholds == {"cfo": 1.0}


def test_unknown_area_key_rejected():
    with pytest.raises(ValueError, match="objection_thresholds"):
        ClientProfile(client_id="x", display_name="X",
                      objection_thresholds={"almacen": 1.0})


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_non_positive_or_non_finite_threshold_rejected(bad):
    with pytest.raises(ValueError, match="objection_thresholds"):
        ClientProfile(client_id="x", display_name="X",
                      objection_thresholds={"cfo": bad})


def test_thresholds_reach_as_params():
    p = ClientProfile(client_id="x", display_name="X",
                      objection_thresholds={"cfo": 5.0})
    assert p.as_params()["objection_thresholds"] == {"cfo": 5.0}


def test_none_thresholds_absent_from_as_params():
    p = ClientProfile(client_id="x", display_name="X")
    assert "objection_thresholds" not in p.as_params()


def test_explicit_params_beat_the_profile():
    p = ClientProfile(client_id="x", display_name="X",
                      objection_thresholds={"cfo": 5.0})
    merged = merge_params({"objection_thresholds": {"cfo": 99.0}}, p)
    assert merged["objection_thresholds"] == {"cfo": 99.0}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=. pytest tests/test_client_profile.py -q -k objection
```

Esperado: FAIL — `TypeError: ClientProfile.__init__() got an unexpected keyword argument 'objection_thresholds'`.

- [ ] **Step 3: Write minimal implementation**

En `src/client_profile.py`, agregar el import de `HAT_KEYS` junto a los existentes:

```python
from src.hats import HAT_KEYS
```

Nota sobre el test de arriba: los `import` van al **bloque de imports del archivo**, no al final (ruff `E402`). Fusionalos con los que ya existen en `tests/test_client_profile.py`.

Agregar el campo al dataclass `ClientProfile`, justo después de `warehouse_capacity` (línea 115):

```python
    # Per-area tolerance for the cross_impact tool (spec 2026-07-31, sec 5.2).
    # Keys are exactly src.hats.HAT_KEYS; values are the maximum tolerated
    # DETERIORATION in that area's own headline-KPI units, always positive:
    #   comprador -> effective_unit_cost (usd/unit)   planner -> policy_cost (usd/yr)
    #   cfo       -> capital_charge_usd (usd/yr)      comercial -> fill_rate (fraction)
    # A PARTIAL dict is legal and intended: an area with no threshold comes out
    # NO_EVALUABLE rather than silently taking an assumed tolerance. Unlike
    # warehouse_capacity this IS included in as_params() - it is a plain dict of
    # floats, so it does not break JSON-serializing the merged params.
    objection_thresholds: dict[str, float] | None = None
```

Agregar la validación en `__post_init__`, antes del check de `contingent_fee_pct`:

```python
        if self.objection_thresholds is not None:
            for key, value in self.objection_thresholds.items():
                if key not in HAT_KEYS:
                    raise ValueError(
                        f"objection_thresholds key {key!r} is not a known area "
                        f"(expected one of {HAT_KEYS})")
                if not (isinstance(value, (int, float)) and math.isfinite(value) and value > 0):
                    raise ValueError(
                        f"objection_thresholds[{key!r}] must be a finite number > 0, "
                        f"got {value!r}")
```

En `as_params`, extender la tupla de campos y el docstring:

```python
        fields = ("service_level", "holding_rate", "order_cost", "lead_time_days",
                  "objection_thresholds")
```

En `load_profile`, agregar el campo al constructor (junto a `warehouse_capacity`):

```python
            objection_thresholds=raw.get("objection_thresholds"),
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH=. pytest tests/test_client_profile.py -q && ruff check src/client_profile.py tests/test_client_profile.py
```

Esperado: todo el archivo verde (los tests previos no deben romperse), ruff limpio.

- [ ] **Step 5: Verificar que no rompiste la suite completa**

```bash
PYTHONPATH=. pytest tests/ -q
```

Esperado: todo verde. `as_params()` ahora emite una key más; si algún test asertaba el dict completo, actualizalo — no relajes la aserción a un `in`.

- [ ] **Step 6: Commit**

```bash
git add src/client_profile.py tests/test_client_profile.py
git commit -m "feat(client-profile): umbrales de objecion por area, persistidos y parciales"
```

---

### Task 4: `jobs/cross_impact_job.py` — `prepare` y sus validaciones

**Files:**
- Create: `jobs/cross_impact_job.py`
- Test: `tests/test_cross_impact_job.py`

**Interfaces:**
- Consumes: `jobs.hats_job.{prepare, config_from_params}`, `src.hats.{Candidate, HAT_KEYS}`.
- Produces: `prepare(data_path: str, params: dict | None) -> dict` con las claves `{"inputs": HatInputs, "current": Candidate, "proposed": Candidate, "proposed_by": str, "thresholds": dict[str, float], "config": HatConfig}`. Levanta `ValueError` en toda entrada inválida — el wrapper del tool lo traduce a `needs_clarification`.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_cross_impact_job.py`:

```python
"""prepare()'s validation contract (spec sec 5.1). Every gap is fatal."""

import pandas as pd
import pytest

from jobs import cross_impact_job
from src.hats import HAT_COMPRADOR, Candidate


@pytest.fixture
def demand_csv(tmp_path):
    path = tmp_path / "demanda.csv"
    pd.DataFrame({
        "product_id": ["SKU-1"] * 30 + ["SKU-2"] * 30,
        "quantity": ([100, 110, 95, 105, 120] * 6)[:30] * 2,
        "unit_cost": [10.0] * 60,
        "lead_time_days": [14.0] * 60,
    }).to_csv(path, index=False)
    return str(path)


def _params(**over):
    base = {
        "sku": "SKU-1",
        "current": {"q": 200.0, "sl": 0.95},
        "proposed": {"q": 400.0, "sl": 0.95},
        "proposed_by": HAT_COMPRADOR,
        "objection_thresholds": {"comprador": 0.2, "planner": 5_000.0,
                                 "cfo": 3_000.0, "comercial": 0.03},
    }
    base.update(over)
    return base


def test_happy_path_builds_the_payload(demand_csv):
    p = cross_impact_job.prepare(demand_csv, _params())
    assert p["inputs"].sku == "SKU-1"
    assert p["current"] == Candidate(200.0, 0.95)
    assert p["proposed"] == Candidate(400.0, 0.95)
    assert p["proposed_by"] == HAT_COMPRADOR
    assert p["thresholds"]["cfo"] == 3_000.0


@pytest.mark.parametrize("missing", ["sku", "current", "proposed", "proposed_by"])
def test_each_required_param_is_fatal_and_named(demand_csv, missing):
    params = _params()
    del params[missing]
    with pytest.raises(ValueError, match=missing):
        cross_impact_job.prepare(demand_csv, params)


def test_missing_current_does_not_fall_back_to_baseline(demand_csv):
    params = _params()
    del params["current"]
    with pytest.raises(ValueError) as exc:
        cross_impact_job.prepare(demand_csv, params)
    msg = str(exc.value).lower()
    assert "baseline" not in msg          # never silently assumes the theoretical (s,Q)
    assert "current" in msg


def test_unknown_proposer_is_fatal(demand_csv):
    with pytest.raises(ValueError, match="proposed_by"):
        cross_impact_job.prepare(demand_csv, _params(proposed_by="almacen"))


@pytest.mark.parametrize("bad", [{"q": 0.0, "sl": 0.95}, {"q": 200.0, "sl": 1.0},
                                 {"q": 200.0, "sl": 0.0}, {"q": -5.0, "sl": 0.95}])
def test_out_of_range_points_are_fatal(demand_csv, bad):
    with pytest.raises(ValueError):
        cross_impact_job.prepare(demand_csv, _params(proposed=bad))


def test_incomplete_point_is_fatal(demand_csv):
    with pytest.raises(ValueError, match="proposed"):
        cross_impact_job.prepare(demand_csv, _params(proposed={"q": 400.0}))


def test_identical_points_are_fatal(demand_csv):
    with pytest.raises(ValueError, match="cambio"):
        cross_impact_job.prepare(demand_csv, _params(proposed={"q": 200.0, "sl": 0.95}))


def test_sku_not_in_csv_is_fatal(demand_csv):
    with pytest.raises(ValueError):
        cross_impact_job.prepare(demand_csv, _params(sku="SKU-404"))


def test_absent_thresholds_are_not_fatal(demand_csv):
    # honesty does not depend on strict_params: the run proceeds and the
    # verdict comes out indeterminado (Task 5).
    params = _params()
    del params["objection_thresholds"]
    p = cross_impact_job.prepare(demand_csv, params)
    assert p["thresholds"] == {}


def test_partial_thresholds_survive(demand_csv):
    p = cross_impact_job.prepare(demand_csv, _params(objection_thresholds={"cfo": 1.0}))
    assert p["thresholds"] == {"cfo": 1.0}


def test_bad_threshold_key_is_fatal(demand_csv):
    with pytest.raises(ValueError, match="objection_thresholds"):
        cross_impact_job.prepare(demand_csv, _params(objection_thresholds={"almacen": 1.0}))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=. pytest tests/test_cross_impact_job.py -q
```

Esperado: FAIL — `ImportError: cannot import name 'cross_impact_job' from 'jobs'`.

- [ ] **Step 3: Write minimal implementation**

Crear `jobs/cross_impact_job.py` con el encabezado y `prepare` (el resto se agrega en las tareas 5 y 6):

```python
"""cross_impact playbook: one proposed decision -> the measured effect on the
other areas + an objection verdict (spec docs/superpowers/specs/2026-07-31-
cross-impact-design.md).

Scope boundary vs the two hat tools (READ BEFORE TOUCHING EITHER). ``hat_tension``
answers "where is the disagreement?" - it computes each hat's IDEAL from the grid
and never judges whether a magnitude is tolerable. This module answers a different
question: an area has ALREADY proposed a change, and we measure what it does to
the others and whether any of them would object, against a threshold the operator
declared in the client profile. Neither module imports the other beyond reusing
``hats_job.prepare`` for the CSV read.
"""

from __future__ import annotations

from src.hats import HAT_KEYS, Candidate

from . import hats_job

_POINT_KEYS = ("q", "sl")


def _parse_point(raw: object, name: str) -> Candidate:
    """Turn a {'q': ..., 'sl': ...} mapping into a Candidate, or raise."""
    if raw is None:
        raise ValueError(
            f"'{name}' is required: pass {{'q': <order quantity>, 'sl': <service "
            "level>}}. There is no default - assuming one would state a policy "
            "nobody verified.")
    if not isinstance(raw, dict):
        raise ValueError(f"'{name}' must be a mapping with keys {_POINT_KEYS}, got {type(raw).__name__}")
    missing = [k for k in _POINT_KEYS if k not in raw]
    if missing:
        raise ValueError(f"'{name}' is missing {', '.join(missing)} (needs {_POINT_KEYS})")
    try:
        return Candidate(order_quantity=float(raw["q"]), service_level=float(raw["sl"]))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"'{name}' is not a valid decision point: {exc}") from exc


def _parse_thresholds(raw: object) -> dict[str, float]:
    """Validate the per-area thresholds. Absent/empty is LEGAL - those areas
    come out NO_EVALUABLE rather than taking an assumed tolerance."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("'objection_thresholds' must be a mapping of area -> tolerance")
    out: dict[str, float] = {}
    for key, value in raw.items():
        if key not in HAT_KEYS:
            raise ValueError(
                f"objection_thresholds key {key!r} is not a known area (expected one of {HAT_KEYS})")
        try:
            num = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"objection_thresholds[{key!r}] must be a number: {exc}") from exc
        if not num > 0:
            raise ValueError(f"objection_thresholds[{key!r}] must be > 0, got {num}")
        out[key] = num
    return out


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Build the single-SKU payload. Every gap raises ValueError - the tool
    wrapper turns that into needs_clarification, never a default."""
    params = params or {}

    sku = params.get("sku")
    if not sku:
        raise ValueError("'sku' is required: cross_impact evaluates ONE decision at a time")

    proposed_by = params.get("proposed_by")
    if proposed_by not in HAT_KEYS:
        raise ValueError(
            f"'proposed_by' is required and must be one of {HAT_KEYS}, got {proposed_by!r}")

    current = _parse_point(params.get("current"), "current")
    proposed = _parse_point(params.get("proposed"), "proposed")
    if current == proposed:
        raise ValueError(
            "'current' and 'proposed' are the same point - no hay cambio que evaluar")

    thresholds = _parse_thresholds(params.get("objection_thresholds"))

    # Reuse hats_job's CSV read wholesale; params['sku'] narrows it to one SKU.
    payload = hats_job.prepare(data_path, params)
    inputs = payload["inputs"]
    if len(inputs) != 1:
        raise ValueError(
            f"expected exactly one usable SKU for {sku!r}, got {len(inputs)}")

    return {
        "inputs": inputs[0],
        "current": current,
        "proposed": proposed,
        "proposed_by": proposed_by,
        "thresholds": thresholds,
        "config": payload["config"],
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH=. pytest tests/test_cross_impact_job.py -q && ruff check jobs/cross_impact_job.py tests/test_cross_impact_job.py
```

Esperado: 15 passed, ruff limpio.

- [ ] **Step 5: Commit**

```bash
git add jobs/cross_impact_job.py tests/test_cross_impact_job.py
git commit -m "feat(cross-impact-job): prepare con validacion fatal, sin defaults silenciosos"
```

---

### Task 5: `run` — veredicto global, contra-propuesta y opciones rankeadas

**Files:**
- Modify: `jobs/cross_impact_job.py`
- **No** se toca `src/guided.py` (ver la nota de Global Constraints: el helper que este plan citaba no existe en esta rama)
- Test: `tests/test_cross_impact_job.py` (agregar)

**Interfaces:**
- Consumes: `src.cross_impact.measure`, `src.objection.{judge, ACEPTA, OBJETA, NO_EVALUABLE}`, `src.decision_options.{Scenario, Objective, decide}`, `src.guided.{GuidedOutcome, Residual}`, `src.hats.{evaluate, decision_cost}`.
- Produces: `EJECUTAR`, `BILATERAL`, `REUNION`, `INDETERMINADO`, `CrossImpactReport`, `run(payload: dict) -> CrossImpactReport`.

- [ ] **Step 1: Write the failing test**

Agregar a `tests/test_cross_impact_job.py`:

```python
# -- run(): verdict, counterproposal, options (spec sec 7) --------------------

from src.guided import OPTIONS, verify_guided
from src.hats import HAT_KEYS
from src.objection import NO_EVALUABLE, OBJETA


def _payload(demand_csv, **over):
    return cross_impact_job.prepare(demand_csv, _params(**over))


def test_zero_objections_verdict_is_ejecutar(demand_csv):
    # generous thresholds: nobody objects to anything
    huge = {k: 1e9 for k in HAT_KEYS}
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds=huge))
    assert r.n_objections == 0
    assert r.n_unevaluable == 0
    assert r.verdict == cross_impact_job.EJECUTAR


def test_one_objection_verdict_is_bilateral(demand_csv):
    thr = {k: 1e9 for k in HAT_KEYS}
    thr["cfo"] = 1e-9                      # only the CFO can object
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds=thr))
    assert r.n_objections == 1
    assert r.verdict == cross_impact_job.BILATERAL


def test_two_objections_verdict_is_reunion(demand_csv):
    # Q must OVERSHOOT the EOQ for two areas to worsen together. With the
    # fixture (D=5200, K=75, h=0.25, c=10) the EOQ is ~559: at Q=400 the
    # planner's policy_cost actually FALLS (400 is closer to the EOQ than the
    # current 200), so only the CFO objects. At Q=2000 both the planner and the
    # CFO worsen. Verified by arithmetic during the plan's pre-flight scan.
    thr = {k: 1e-9 for k in HAT_KEYS}      # everyone objects to any deterioration
    r = cross_impact_job.run(_payload(demand_csv, proposed={"q": 2000.0, "sl": 0.95},
                                      objection_thresholds=thr))
    assert r.n_objections >= 2
    assert r.verdict == cross_impact_job.REUNION


def test_unevaluable_beats_zero_objections(demand_csv):
    # THE honesty rule: a declared gap outranks an optimistic verdict
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds={"cfo": 1e9}))
    assert r.n_unevaluable == 3
    assert r.n_objections == 0
    assert r.verdict == cross_impact_job.INDETERMINADO


def test_unevaluable_verdicts_have_empty_fields(demand_csv):
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds={"cfo": 1e9}))
    for v in r.verdicts:
        if v.status == NO_EVALUABLE:
            assert v.kpi_current is None
            assert v.kpi_proposed is None
            assert v.deterioration is None
            assert v.threshold is None


def test_one_verdict_per_hat_in_order(demand_csv):
    r = cross_impact_job.run(_payload(demand_csv))
    assert tuple(v.hat_key for v in r.verdicts) == HAT_KEYS


def test_planner_objects_when_proposal_breaches_sl_target(demand_csv):
    # config default sl_target = 0.95; proposing 0.90 breaches it while making
    # the planner's raw policy_cost CHEAPER (spec 9.1)
    thr = {k: 1e9 for k in HAT_KEYS}
    r = cross_impact_job.run(_payload(demand_csv, proposed={"q": 200.0, "sl": 0.90},
                                      objection_thresholds=thr))
    planner = next(v for v in r.verdicts if v.hat_key == "planner")
    assert planner.status == OBJETA
    assert planner.deterioration < 0          # the KPI actually improved
    assert r.verdict == cross_impact_job.BILATERAL


def test_counterproposal_absent_when_no_grid_point_is_clean(demand_csv):
    thr = {k: 1e-9 for k in HAT_KEYS}
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds=thr))
    assert r.counterproposal is None
    assert len(r.outcome.options) == 2        # never fabricated to fill the slot


def test_counterproposal_when_unevaluable_is_never_searched(demand_csv):
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds={"cfo": 1e9}))
    assert r.counterproposal is None


def test_counterproposal_really_has_zero_objections(demand_csv):
    # The default fixture's current (Q=200) sits BELOW the whole grid (~280-700
    # around the ~559 EOQ), so every grid point raises the CFO's capital charge
    # and no clean point can exist - a counterproposal assertion there would
    # pass vacuously forever. Put `current` INSIDE the grid and give the CFO
    # real headroom so a clean point genuinely exists, then assert it is found.
    thr = {k: 1e9 for k in HAT_KEYS}
    thr["cfo"] = 5_000.0
    r = cross_impact_job.run(_payload(demand_csv,
                                      current={"q": 560.0, "sl": 0.95},
                                      proposed={"q": 700.0, "sl": 0.99},
                                      objection_thresholds=thr))
    assert r.counterproposal is not None      # never vacuous
    assert len(r.outcome.options) == 3


def test_outcome_is_protected_options_with_one_recommendation(demand_csv):
    r = cross_impact_job.run(_payload(demand_csv))
    assert r.outcome.status == OPTIONS
    assert verify_guided(r.outcome) == []
    assert len(r.outcome.options) >= 2
    assert sum(1 for o in r.outcome.options if o.recommended) == 1


def test_confidence_drops_with_unevaluable_areas(demand_csv):
    full = cross_impact_job.run(_payload(demand_csv))
    partial = cross_impact_job.run(_payload(demand_csv, objection_thresholds={"cfo": 1e9}))
    assert full.outcome.confidence == 1.0
    assert partial.outcome.confidence < full.outcome.confidence


def test_summary_names_the_verdict(demand_csv):
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds={"cfo": 1e9}))
    assert cross_impact_job.INDETERMINADO in r.summary
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=. pytest tests/test_cross_impact_job.py -q -k "verdict or counterproposal or outcome or confidence or summary or planner or unevaluable"
```

Esperado: FAIL — `AttributeError: module 'jobs.cross_impact_job' has no attribute 'run'`.

- [ ] **Step 3: Write the implementation**

Agregar a `jobs/cross_impact_job.py` (imports arriba, resto al final):

```python
from dataclasses import dataclass

from src.cross_impact import CrossImpact, measure
from src.decision_options import Objective, Scenario, decide
from src.guided import GuidedOutcome, Residual
from src.hats import decision_cost, evaluate
from src.objection import NO_EVALUABLE, OBJETA, Verdict, judge

EJECUTAR = "ejecutar"
BILATERAL = "bilateral"
REUNION = "reunion"
INDETERMINADO = "indeterminado"
VERDICTS = (EJECUTAR, BILATERAL, REUNION, INDETERMINADO)

# Confidence cost of ONE area we could not judge at all (no declared threshold in
# the client profile). Deliberately local rather than imported: a general
# `confidence_from_coverage` helper exists on the unmerged branch feat/simworld-
# marcador (commit 25cd8ed) but NOT on main, and this capability must not depend
# on another branch's unmerged work. Unify them if that commit ever lands.
UNEVALUABLE_PENALTY = 0.20
CONFIDENCE_FLOOR = 0.10


def _confidence(n_unevaluable: int) -> float:
    """1.0 when all four areas were judged; one penalty per area we could not."""
    return max(CONFIDENCE_FLOOR, 1.0 - UNEVALUABLE_PENALTY * n_unevaluable)


@dataclass(frozen=True)
class CrossImpactReport:
    impact: CrossImpact
    verdicts: tuple[Verdict, ...]
    n_objections: int
    n_unevaluable: int
    verdict: str
    counterproposal: Candidate | None
    outcome: GuidedOutcome
    summary: str
    # Carried so verify() (Task 6) can re-check that the counterproposal really
    # delivers the zero objections it claims. Not rendered anywhere.
    inputs: HatInputs


def _inputs_of(report: CrossImpactReport) -> HatInputs:
    """The HatInputs behind a report - used only by verify()'s self-check."""
    return report.inputs


def _judge_all(inputs, impact: CrossImpact, thresholds: dict[str, float],
               proposed: Candidate) -> tuple[Verdict, ...]:
    """One verdict per hat. The planner also gets the service-constraint flag
    its headline KPI cannot express (spec 9.1)."""
    breach = proposed.service_level < inputs.config.sl_target
    return tuple(
        judge(row.hat_key, row.value_current, row.value_proposed,
              thresholds.get(row.hat_key), breaches_sl_target=breach)
        for row in impact.rows
    )


def _global_verdict(n_objections: int, n_unevaluable: int) -> str:
    """First match wins. `indeterminado` outranks `ejecutar` on purpose: a
    declared gap must never be reported as "nobody objects"."""
    if n_unevaluable > 0:
        return INDETERMINADO
    if n_objections == 0:
        return EJECUTAR
    if n_objections == 1:
        return BILATERAL
    return REUNION


def _find_counterproposal(inputs, thresholds: dict[str, float],
                          current: Candidate, proposed_by: str,
                          n_unevaluable: int) -> Candidate | None:
    """Cheapest grid point (by judge cost) where NO area objects.

    Returns None when no such point exists - the option is simply not offered.
    The threshold is never loosened to manufacture one. Not even searched when
    any area is NO_EVALUABLE: "zero objections" is not determinable then.
    """
    if n_unevaluable > 0:
        return None
    best: Candidate | None = None
    best_cost = float("inf")
    for cand in evaluate(inputs).candidates:
        if cand == current:
            continue
        impact = measure(inputs, current, cand, proposed_by)
        verdicts = _judge_all(inputs, impact, thresholds, cand)
        if any(v.status == OBJETA for v in verdicts):
            continue
        cost = decision_cost(inputs, cand)
        if cost < best_cost:
            best, best_cost = cand, cost
    return best


def _scenarios(inputs, impact: CrossImpact, thresholds: dict[str, float],
               n_objections: int, counterproposal: Candidate | None) -> list[Scenario]:
    cur, prop = impact.current, impact.proposed
    out = [
        Scenario(
            label=f"Ejecutar la propuesta de {impact.proposed_by}",
            summary=(f"{impact.sku}: Q={prop.order_quantity:,.0f}, "
                     f"SL={prop.service_level:.1%}. Objeciones: {n_objections}."),
            metrics={"n_objections": float(n_objections),
                     "judge_cost": impact.judge_cost_proposed},
            action=(f"adoptar Q={prop.order_quantity:,.0f} y SL={prop.service_level:.1%} "
                    f"para {impact.sku}"),
            tradeoffs="ver el detalle por area en cross_impact.csv",
        ),
        Scenario(
            label="Mantener el estado actual",
            summary=(f"{impact.sku}: Q={cur.order_quantity:,.0f}, "
                     f"SL={cur.service_level:.1%}. Objeciones: 0 (nada cambia)."),
            metrics={"n_objections": 0.0, "judge_cost": impact.judge_cost_current},
            action=f"no cambiar la politica de {impact.sku}",
            tradeoffs="se conserva el costo actual y se pierde el efecto buscado",
        ),
    ]
    if counterproposal is not None:
        out.append(Scenario(
            label="Contra-propuesta sin objeciones",
            summary=(f"{impact.sku}: Q={counterproposal.order_quantity:,.0f}, "
                     f"SL={counterproposal.service_level:.1%}. Ninguna area objeta."),
            metrics={"n_objections": 0.0,
                     "judge_cost": decision_cost(inputs, counterproposal)},
            action=(f"adoptar Q={counterproposal.order_quantity:,.0f} y "
                    f"SL={counterproposal.service_level:.1%} para {impact.sku}"),
            tradeoffs="el punto mas barato de la grilla que nadie objeta",
        ))
    return out


def run(payload: dict) -> CrossImpactReport:
    """Measure, judge, rank. Nothing is executed and nothing is written back."""
    inputs = payload["inputs"]
    current, proposed = payload["current"], payload["proposed"]
    proposed_by, thresholds = payload["proposed_by"], payload["thresholds"]

    impact = measure(inputs, current, proposed, proposed_by)
    verdicts = _judge_all(inputs, impact, thresholds, proposed)
    n_obj = sum(1 for v in verdicts if v.status == OBJETA)
    n_unk = sum(1 for v in verdicts if v.status == NO_EVALUABLE)
    verdict = _global_verdict(n_obj, n_unk)
    counter = _find_counterproposal(inputs, thresholds, current, proposed_by, n_unk)

    reading = {
        EJECUTAR: "ninguna area objeta - no hace falta convocar a nadie",
        BILATERAL: "una sola area objeta - alcanza una conversacion bilateral",
        REUNION: "dos o mas areas objetan - la reunion si hace falta",
        INDETERMINADO: (f"{n_unk} area(s) sin umbral declarado - no se puede afirmar "
                        "que nadie objeta"),
    }[verdict]
    summary = (
        f"{impact.sku}: {proposed_by} propone Q={proposed.order_quantity:,.0f}, "
        f"SL={proposed.service_level:.1%} (hoy Q={current.order_quantity:,.0f}, "
        f"SL={current.service_level:.1%}). Veredicto: {verdict} - {reading}. "
        "Los umbrales son POLITICA DEL OPERADOR, no un consenso objetivo. "
        "Alcance: un SKU, el espacio (Q, SL), cuatro areas - almacen no es una de ellas."
    )

    outcome = decide(
        summary,
        _scenarios(inputs, impact, thresholds, n_obj, counter),
        [Objective("n_objections", weight=2.0), Objective("judge_cost", weight=1.0)],
        confidence=_confidence(n_unk),
        residuals=[
            Residual(
                description="La eleccion final la hace un humano; esto mide y ordena, no ejecuta.",
                risk_if_skipped="adoptar un cambio que otra area objeta sin que nadie lo haya aceptado",
            ),
            Residual(
                description=("Almacen no es uno de los cuatro sombreros: su impacto no fue medido."),
                risk_if_skipped="un cambio de Q sin capacidad fisica para absorberlo",
            ),
        ],
    )

    return CrossImpactReport(
        impact=impact, verdicts=verdicts, n_objections=n_obj, n_unevaluable=n_unk,
        verdict=verdict, counterproposal=counter, outcome=outcome, summary=summary,
        inputs=inputs,
    )
```

Agregá `HatInputs` al import de `src.hats` que ya existe en el archivo:

```python
from src.hats import HAT_KEYS, Candidate, HatInputs, decision_cost, evaluate
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tests/test_cross_impact_job.py -q && ruff check jobs/cross_impact_job.py
```

Esperado: todo verde. `src/guided.py` no se toca en esta tarea.

- [ ] **Step 5: Commit**

```bash
git add jobs/cross_impact_job.py tests/test_cross_impact_job.py
git commit -m "feat(cross-impact-job): veredicto global, contra-propuesta y opciones rankeadas"
```

---

### Task 6: `verify` (el gate de QA) + `write` + `build_deck`

**Files:**
- Modify: `jobs/cross_impact_job.py`
- Test: `tests/test_cross_impact_job.py` (agregar)

**Interfaces:**
- Produces: `verify(report) -> list[str]`, `write(report, out_dir, client="Client") -> dict[str, Path]`, `build_deck(report, *, client, citations, confidence, lang) -> Deliverable`.

- [ ] **Step 1: Write the failing test**

Agregar a `tests/test_cross_impact_job.py`:

```python
# -- verify(): the QA gate (spec sec 8) --------------------------------------

import csv
import math
from dataclasses import replace

from src.guided import ExecutionOption, GuidedOutcome
from src.objection import Verdict


def _good_report(demand_csv):
    return cross_impact_job.run(_payload(demand_csv))


def test_honest_report_passes(demand_csv):
    assert cross_impact_job.verify(_good_report(demand_csv)) == []


def test_indeterminado_with_gaps_passes(demand_csv):
    # a declared gap is a valid delivery, NOT a failure
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds={"cfo": 1e9}))
    assert r.verdict == cross_impact_job.INDETERMINADO
    assert cross_impact_job.verify(r) == []


def test_veto_optimistic_verdict_with_unevaluable_areas(demand_csv):
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds={"cfo": 1e9}))
    bad = replace(r, verdict=cross_impact_job.EJECUTAR)
    issues = cross_impact_job.verify(bad)
    assert any("no_evaluable" in i or "indeterminado" in i for i in issues)


def test_veto_judged_verdict_with_empty_fields(demand_csv):
    r = _good_report(demand_csv)
    broken = replace(r.verdicts[0], kpi_current=None)
    bad = replace(r, verdicts=(broken,) + r.verdicts[1:])
    assert cross_impact_job.verify(bad)


def test_veto_unevaluable_verdict_carrying_numbers(demand_csv):
    r = _good_report(demand_csv)
    fake = Verdict(hat_key=r.verdicts[0].hat_key, status=NO_EVALUABLE,
                   kpi_name=r.verdicts[0].kpi_name, kpi_current=1.0,
                   kpi_proposed=2.0, deterioration=1.0, threshold=None,
                   reason="fabricado")
    bad = replace(r, verdicts=(fake,) + r.verdicts[1:],
                  n_unevaluable=1, verdict=cross_impact_job.INDETERMINADO)
    assert cross_impact_job.verify(bad)


def test_veto_wrong_verdict_count_or_keys(demand_csv):
    r = _good_report(demand_csv)
    assert cross_impact_job.verify(replace(r, verdicts=r.verdicts[:3]))


def test_veto_non_finite_number(demand_csv):
    r = _good_report(demand_csv)
    broken = replace(r.impact, judge_cost_proposed=math.nan)
    assert cross_impact_job.verify(replace(r, impact=broken))


def test_veto_unprotected_outcome(demand_csv):
    r = _good_report(demand_csv)
    dead = GuidedOutcome(status="options", summary="x", options=[])
    assert cross_impact_job.verify(replace(r, outcome=dead))


def test_real_counterproposal_passes_the_self_check(demand_csv):
    # the positive side of the self-check: a genuinely clean counterproposal
    # (same setup as test_counterproposal_really_has_zero_objections) must not
    # be flagged. Without this, only the negative path is covered.
    thr = {k: 1e9 for k in HAT_KEYS}
    thr["cfo"] = 5_000.0
    r = cross_impact_job.run(_payload(demand_csv,
                                      current={"q": 560.0, "sl": 0.95},
                                      proposed={"q": 700.0, "sl": 0.99},
                                      objection_thresholds=thr))
    assert r.counterproposal is not None
    assert cross_impact_job.verify(r) == []


def test_veto_counterproposal_that_still_draws_objections(demand_csv):
    thr = {k: 1e-9 for k in HAT_KEYS}
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds=thr))
    assert r.counterproposal is None
    liar = replace(r, counterproposal=Candidate(999.0, 0.99))
    assert cross_impact_job.verify(liar)


def test_veto_unknown_verdict_string(demand_csv):
    r = _good_report(demand_csv)
    assert cross_impact_job.verify(replace(r, verdict="quiza"))


def test_veto_option_without_recommendation(demand_csv):
    r = _good_report(demand_csv)
    flat = replace(r.outcome, options=[
        ExecutionOption(label="a", summary="a", score=1.0),
        ExecutionOption(label="b", summary="b", score=0.0),
    ])
    assert cross_impact_job.verify(replace(r, outcome=flat))


# -- write(): the deliverables (spec sec 7.4) --------------------------------


def test_write_emits_both_csvs(demand_csv, tmp_path):
    paths = cross_impact_job.write(_good_report(demand_csv), tmp_path, "Acme")
    assert set(paths) == {"csv", "verdict_csv"}
    assert all(p.exists() for p in paths.values())


def test_unevaluable_cells_are_empty_not_zero(demand_csv, tmp_path):
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds={"cfo": 1e9}))
    paths = cross_impact_job.write(r, tmp_path, "Acme")
    rows = list(csv.DictReader(paths["csv"].open(encoding="utf-8")))
    unevaluable = [row for row in rows if row["status"] == NO_EVALUABLE]
    assert len(unevaluable) == 3
    for row in unevaluable:
        assert row["value_current"] == ""
        assert row["deterioration"] == ""
        assert row["threshold"] == ""


def test_verdict_csv_is_one_countable_row(demand_csv, tmp_path):
    r = _good_report(demand_csv)
    paths = cross_impact_job.write(r, tmp_path, "Acme")
    rows = list(csv.DictReader(paths["verdict_csv"].open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["verdict"] == r.verdict
    assert int(rows[0]["n_objections"]) == r.n_objections
    assert int(rows[0]["n_unevaluable"]) == r.n_unevaluable


def test_deck_carries_lang_and_options(demand_csv):
    r = _good_report(demand_csv)
    deck = cross_impact_job.build_deck(r, client="Acme", citations=("c1", "c2"),
                                       confidence=0.9, lang="en")
    assert deck.lang == "en"
    assert deck.citations == ("c1", "c2")
    assert deck.confidence == 0.9
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=. pytest tests/test_cross_impact_job.py -q -k "veto or honest or indeterminado_with or write or deck or countable or cells"
```

Esperado: FAIL — `AttributeError: module 'jobs.cross_impact_job' has no attribute 'verify'`.

- [ ] **Step 3: Write the implementation**

Agregar a `jobs/cross_impact_job.py`:

```python
import math
from pathlib import Path

from src.deliverable import Deliverable, Finding, Kpi
from src.export import write_summary_csv      # NOTE: export.py, not deliverable.py
from src.guided import verify_guided


def _finite_issues(report: CrossImpactReport) -> list[str]:
    issues = []
    numbers = [("judge_cost_current", report.impact.judge_cost_current),
               ("judge_cost_proposed", report.impact.judge_cost_proposed)]
    for row in report.impact.rows:
        numbers += [(f"{row.hat_key}.value_current", row.value_current),
                    (f"{row.hat_key}.value_proposed", row.value_proposed),
                    (f"{row.hat_key}.delta", row.delta)]
    for v in report.verdicts:
        for name in ("kpi_current", "kpi_proposed", "deterioration", "threshold"):
            value = getattr(v, name)
            if value is not None:
                numbers.append((f"{v.hat_key}.{name}", value))
    for name, value in numbers:
        if not math.isfinite(value):
            issues.append(f"non-finite number in {name}: {value}")
    return issues


def verify(report: CrossImpactReport) -> list[str]:
    """QA gate. Empty list = passed. QA fails => the orchestrator ships nothing.

    Vetoes the DISHONEST combination, not the gap: reporting `indeterminado`
    with the evaluable areas measured and the rest empty is a valid delivery.
    """
    issues: list[str] = []

    if report.verdict not in VERDICTS:
        issues.append(f"unknown verdict {report.verdict!r} (expected one of {VERDICTS})")

    if tuple(v.hat_key for v in report.verdicts) != HAT_KEYS:
        issues.append(
            f"expected exactly one verdict per hat in {HAT_KEYS}, got "
            f"{tuple(v.hat_key for v in report.verdicts)}")

    n_unk = sum(1 for v in report.verdicts if v.status == NO_EVALUABLE)
    if n_unk > 0 and report.verdict != INDETERMINADO:
        issues.append(
            f"{n_unk} area(s) are no_evaluable but the verdict is "
            f"{report.verdict!r} - a declared gap can never be reported as a "
            "clean verdict; it must be 'indeterminado'")

    for v in report.verdicts:
        empty = [n for n in ("kpi_current", "kpi_proposed", "deterioration", "threshold")
                 if getattr(v, n) is None]
        if v.status == NO_EVALUABLE:
            if empty != ["kpi_current", "kpi_proposed", "deterioration", "threshold"]:
                issues.append(
                    f"verdict '{v.hat_key}' is no_evaluable but still carries "
                    f"numbers (populated: {[n for n in ('kpi_current', 'kpi_proposed', 'deterioration', 'threshold') if n not in empty]})")
        elif empty:
            issues.append(f"verdict '{v.hat_key}' is judged ({v.status}) but "
                          f"{', '.join(empty)} is empty")

    issues += _finite_issues(report)
    issues += verify_guided(report.outcome)

    if sum(1 for o in report.outcome.options if o.recommended) != 1:
        issues.append("the options must carry exactly one recommended default")

    if report.counterproposal is not None:
        if n_unk > 0:
            issues.append("a counterproposal was offered while areas are no_evaluable - "
                          "'zero objections' is not determinable")
        else:
            # self-check: the counterproposal must deliver what it promises
            thresholds = {v.hat_key: v.threshold for v in report.verdicts
                          if v.threshold is not None}
            recheck = _judge_all(
                _inputs_of(report), measure(_inputs_of(report), report.impact.current,
                                            report.counterproposal, report.impact.proposed_by),
                thresholds, report.counterproposal)
            if any(v.status == OBJETA for v in recheck):
                issues.append("the counterproposal still draws objections on re-check")

    return issues


def write(report: CrossImpactReport, out_dir, client: str = "Client") -> dict[str, Path]:
    """Two CSVs: the per-area detail, and the ONE countable verdict row.

    An unevaluable area's cells are written EMPTY (''), never 0 - the gap has to
    look like a gap in the spreadsheet too.
    """
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)

    def cell(value: float | None, digits: int) -> object:
        return "" if value is None else round(value, digits)

    rows = []
    for row, v in zip(report.impact.rows, report.verdicts):
        rows.append({
            "sku": report.impact.sku,
            "hat": row.hat_key,
            "kpi": row.kpi_name,
            "value_current": cell(v.kpi_current, 4),
            "value_proposed": cell(v.kpi_proposed, 4),
            "delta": cell(None if v.kpi_current is None else row.delta, 4),
            "deterioration": cell(v.deterioration, 4),
            "threshold": cell(v.threshold, 4),
            "status": v.status,
            "reason": v.reason,
        })

    verdict_row = {
        "sku": report.impact.sku,
        "proposed_by": report.impact.proposed_by,
        "q_current": round(report.impact.current.order_quantity, 1),
        "sl_current": report.impact.current.service_level,
        "q_proposed": round(report.impact.proposed.order_quantity, 1),
        "sl_proposed": report.impact.proposed.service_level,
        "n_objections": report.n_objections,
        "n_unevaluable": report.n_unevaluable,
        "verdict": report.verdict,
        "judge_cost_current": round(report.impact.judge_cost_current, 2),
        "judge_cost_proposed": round(report.impact.judge_cost_proposed, 2),
    }

    return {
        "csv": write_summary_csv(rows, d / "cross_impact.csv"),
        "verdict_csv": write_summary_csv([verdict_row], d / "cross_impact_verdict.csv"),
    }


def build_deck(report: CrossImpactReport, *, client: str = "Client",
               citations: tuple[str, ...] = (), confidence: float | None = None,
               lang: str = "es") -> Deliverable:
    """The premium deck. ``lang`` is passed explicitly - the PR #194 lesson."""
    findings = [
        Finding(f"{v.hat_key}: {v.status}", v.reason,
                impact=("sin medir" if v.deterioration is None
                        else f"{v.deterioration:+,.4f} {v.kpi_name}"))
        for v in report.verdicts
    ]
    findings.append(Finding(
        "Alcance declarado",
        "Un SKU, el espacio (Q, SL) y cuatro areas. Almacen NO es uno de los cuatro "
        "sombreros: su impacto no fue medido.",
        impact="pedir la capacidad fisica antes de mover cantidades"))

    kpis = [
        Kpi("Veredicto de coordinacion", report.verdict,
            rationale="cero objeciones = la reunion no se hace"),
        Kpi("Areas que objetan", str(report.n_objections), target="0"),
        Kpi("Areas sin umbral declarado", str(report.n_unevaluable), target="0",
            rationale="cada una impide afirmar que nadie objeta"),
    ]

    return Deliverable(
        title=f"Impacto cruzado - {report.impact.sku}",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=tuple(kpis),
        recommendations=(
            "Los umbrales son POLITICA DEL OPERADOR, no un consenso objetivo: revisalos "
            "con cada area antes de tratarlos como un veto.",
            "Declarar el umbral de las areas faltantes convierte un 'indeterminado' en un "
            "veredicto accionable.",
        ),
        options=tuple(report.outcome.options),
        citations=tuple(citations),
        confidence=confidence,
        lang=lang,
    )
```

Agregar también el helper que `verify` necesita para re-evaluar la contra-propuesta. Como `CrossImpactReport` no guarda el `HatInputs`, sumale el campo:

```python
# En CrossImpactReport, agregar como ultimo campo:
    inputs: HatInputs | None = None
```

y en `run(...)`, pasarlo: `inputs=inputs`. Después, `_inputs_of` es:

```python
def _inputs_of(report: CrossImpactReport) -> HatInputs:
    if report.inputs is None:
        raise ValueError("report carries no HatInputs - cannot re-check the counterproposal")
    return report.inputs
```

Importá `HatInputs` desde `src.hats` en el bloque de imports.

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tests/test_cross_impact_job.py -q && ruff check jobs/cross_impact_job.py tests/test_cross_impact_job.py
```

Esperado: todo verde (los ~15 de la Task 4 + ~13 de la 5 + ~15 de esta).

- [ ] **Step 5: Commit**

```bash
git add jobs/cross_impact_job.py tests/test_cross_impact_job.py
git commit -m "feat(cross-impact-job): gate de QA que veta la combinacion deshonesta, csv y deck"
```

---

### Task 7: Registrar el tool y anclar las citas

**Files:**
- Modify: `scm_agent/tools.py` (nueva función cerca de la línea 1098; `reg.register(cross_impact_tool())` dentro de `build_default_registry()`, justo después de `reg.register(hat_settlement_tool())` y antes del `return reg`)
- Modify: `scm_agent/citation_gate.py` (`TOOL_CONCEPTS`, línea 54)
- Test: `tests/test_cross_impact_tool.py` (crear)
- **Modify: `tests/test_price_watch_tool.py:165`** — asserta `len(reg.list()) == 46`. Pasa a **47**. No relajes la aserción a `>=`: ese número exacto es lo que hace que agregar un tool sea un diff revisable.
- **Modify: `tests/test_capability_smoke.py`** — `EXPECTED_OFFLINE_BUCKETS` (línea 25) exige una fila por tool registrado (`assert set(EXPECTED_OFFLINE_BUCKETS) == registered`, línea 109), así que sin fila el test falla a propósito. Agregar `"cross_impact": "GATED",` junto a `"hat_tension"` y `"hat_settlement"`, que son los casos análogos: `requires_data=True` y sin fixture en el harness, así que no ejecutan su propio job. También actualizar el comentario de la línea 17 de "46 registered tools" a 47.

**Interfaces:**
- Consumes: todo lo de las tareas 1-6.
- Produces: el tool `key="cross_impact"` en el registry.

- [ ] **Step 1: Write the failing test**

Crear `tests/test_cross_impact_tool.py`:

```python
"""Integration guards for the cross_impact tool (spec sec 6.4 + 9.3)."""

import pandas as pd
import pytest

from scm_agent.citation_gate import TOOL_CONCEPTS
from scm_agent.knowledge import KnowledgeBase
from scm_agent.orchestrator import Orchestrator
from scm_agent.tools import build_default_registry


@pytest.fixture
def demand_csv(tmp_path):
    path = tmp_path / "demanda.csv"
    pd.DataFrame({
        "product_id": ["SKU-1"] * 30,
        "quantity": ([100, 110, 95, 105, 120] * 6)[:30],
        "unit_cost": [10.0] * 30,
        "lead_time_days": [14.0] * 30,
    }).to_csv(path, index=False)
    return str(path)


def _params(**over):
    base = {
        "sku": "SKU-1",
        "current": {"q": 200.0, "sl": 0.95},
        "proposed": {"q": 400.0, "sl": 0.95},
        "proposed_by": "comprador",
    }
    base.update(over)
    return base


def test_tool_is_registered():
    assert build_default_registry().get("cross_impact").key == "cross_impact"


def test_tool_declares_the_threshold_as_a_required_client_param():
    tool = build_default_registry().get("cross_impact")
    assert tool.required_client_params == ("objection_thresholds",)


def test_tool_exposes_ranked_options():
    assert build_default_registry().get("cross_impact").options is not None


def test_brief_routes_to_cross_impact():
    reg = build_default_registry()
    top, _ = reg.match("que impacto tiene esta decision en las otras areas")[0]
    assert top.key == "cross_impact"


def test_end_to_end_run_delivers(demand_csv, tmp_path):
    result = Orchestrator().run(
        brief="impacto cruzado de la decision propuesta",
        data_path=demand_csv, job_type="cross_impact",
        overrides=_params(objection_thresholds={"comprador": 1e9, "planner": 1e9,
                                                "cfo": 1e9, "comercial": 1e9}),
        out_dir=tmp_path)
    assert result.status == "ok"
    assert result.guided.status == "options"
    assert "cross_impact.csv" in {p.name for p in result.deliverables.values()}


def test_strict_params_blocks_without_thresholds(demand_csv, tmp_path):
    result = Orchestrator().run(
        brief="impacto cruzado", data_path=demand_csv, job_type="cross_impact",
        overrides=_params(), strict_params=True, out_dir=tmp_path)
    assert result.status == "needs_clarification"
    assert any("objection_thresholds" in c for c in result.clarifications)


def test_without_strict_params_it_still_runs_and_says_indeterminado(demand_csv, tmp_path):
    # honesty must NOT depend on the opt-in flag
    result = Orchestrator().run(
        brief="impacto cruzado", data_path=demand_csv, job_type="cross_impact",
        overrides=_params(), out_dir=tmp_path)
    assert result.status == "ok"
    assert "indeterminado" in result.summary


def test_missing_current_is_needs_clarification(demand_csv, tmp_path):
    params = _params()
    del params["current"]
    result = Orchestrator().run(
        brief="impacto cruzado", data_path=demand_csv, job_type="cross_impact",
        overrides=params, out_dir=tmp_path)
    assert result.status == "needs_clarification"


def test_anchor_concepts_exist_in_the_graph():
    kb = KnowledgeBase()
    for concept in TOOL_CONCEPTS["cross_impact"]:
        assert kb.node_exists(concept), f"anchor {concept} missing from the books graph"


def test_anchors_are_the_expected_four():
    assert TOOL_CONCEPTS["cross_impact"] == (
        "safety_stock", "service_level", "cycle_service_level",
        "cross_functional_integration")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=. pytest tests/test_cross_impact_tool.py -q
```

Esperado: FAIL — `KeyError: 'cross_impact'`.

- [ ] **Step 3a: Anclar las citas**

En `scm_agent/citation_gate.py`, agregar a `TOOL_CONCEPTS` justo después de las entradas `hat_tension` / `hat_settlement`:

```python
    # cross_impact (tool #47) decides the SAME (Q, SL) space as hat_tension, so it
    # inherits those three anchors; cross_functional_integration adds the
    # coordination lens this tool is actually about. Measured by BFS over the
    # 3002-node graph (2026-07-31): the three inherited anchors close 853 nodes
    # (28.4%) at 2 hops; adding cross_functional_integration admits only 16 more
    # (869, 28.9%) and it is hub-free (degree 3; neighbors Supply Chain
    # Orientation / Supply Chain Agility / Tiger Team, all on topic).
    # sales_and_operations_planning (own 2-hop closure 618) and functional_silos
    # (degree 2 but closure 266 - one hop from a hub) were REJECTED as magnets,
    # by measurement, not intuition.
    "cross_impact": ("safety_stock", "service_level", "cycle_service_level",
                     "cross_functional_integration"),
```

- [ ] **Step 3b: Registrar el tool**

En `scm_agent/tools.py`, agregar después de `hat_settlement_tool()`:

```python
def _cross_impact_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    """Param problems are needs_clarification; missing/unusable data is needs_data.

    The split matters: a missing 'current' is the OPERATOR's answer to give, not a
    data-file problem - and it is never defaulted to baseline_plan().
    """
    if not request.data_path:
        return Prepared(status="needs_data", messages=[
            "a weekly demand CSV (product_id, quantity, unit_cost, lead_time_days) is required"])
    try:
        payload = cross_impact_job.prepare(request.data_path, request.params)
    except FileNotFoundError as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    except ValueError as exc:
        return Prepared(status="needs_clarification", messages=[str(exc)])
    return Prepared(status="ok", payload=payload)


def _cross_impact_run(payload: object, params: dict) -> Produced:
    report = cross_impact_job.run(payload)
    return Produced(report=report, summary=report.summary)


def cross_impact_tool() -> Tool:
    return Tool(
        key="cross_impact",
        title="Cross-Area Impact of a Proposed Decision",
        description="Take a replenishment decision ALREADY proposed by one area "
                    "(order quantity + service level) and measure what it does to the "
                    "other three, then judge whether each would object against the "
                    "threshold the operator declared. Zero objections means the "
                    "meeting does not need to happen. Nothing is executed.",
        intent_keywords=(
            "impacto cruzado", "impacto en las otras areas", "quien objeta",
            "costo de coordinacion", "cross-area impact", "objection threshold",
            "si cambio esto que pasa", "necesito convocar",
        ),
        requires_data=True,
        prepare=_cross_impact_prepare,
        run=_cross_impact_run,
        qa=lambda report: cross_impact_job.verify(report),
        deliver=lambda report, out_dir, client: cross_impact_job.write(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            cross_impact_job.build_deck(report, client=client, citations=tuple(citations),
                                        confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
        options=lambda report: report.outcome,
        required_client_params=("objection_thresholds",),
    )
```

Agregar `cross_impact_job` al bloque de imports de `jobs` al inicio del archivo, y la línea de registro después de la 2601:

```python
    reg.register(cross_impact_tool())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=. pytest tests/test_cross_impact_tool.py -q && ruff check scm_agent/tools.py scm_agent/citation_gate.py tests/test_cross_impact_tool.py
```

Esperado: 10 passed. Si `test_brief_routes_to_cross_impact` falla porque otro tool empata, agregá una `intent_keyword` más distintiva y multi-palabra — no bajes la aserción.

- [ ] **Step 5: Correr el barrido de citas de los 45+1 tools anclados**

```bash
PYTHONPATH=. pytest tests/test_citation_gate.py tests/test_packages_citations.py -q
```

Esperado: todo verde, incluido `test_no_anchored_tool_regressed_to_zero`. **Si algún tool regresó a cero**, el pool empírico de 16 (`scm_agent/packages.py:75`) se movió por sumar un tool anclado: re-medí el techo y actualizá `_CANDIDATE_POOL` con el comentario correspondiente. No silencies el test.

- [ ] **Step 6: Medir si algún imán suave entra al pool real de este tool**

```bash
PYTHONPATH=. python -c "
from scm_agent.knowledge import KnowledgeBase
from scm_agent.tools import build_default_registry
from scm_agent import citation_gate
tool = build_default_registry().get('cross_impact')
kb = KnowledgeBase()
cands = kb.ground_citations_detailed(tool.intent_keywords, tool.title, limit=16)
for c in cands:
    print(c.node_id, '|', c.text[:70])
print('--- kept ---')
print(citation_gate.filter_citations(kb, 'cross_impact', cands).kept[:3])
"
```

Si aparece alguno de los cuatro imanes suaves (`Remote Working Mode`, `Product Reengineering`, `Ripple Effect`, `Scale/Scope Agility`) entre los `kept`, agregalo a `EXCLUDED_CONCEPTS["cross_impact"]` con el id verificado y un comentario que cite esta medición. Si no aparece ninguno, **no** pre-excluyas nada: adivinar es peor que medir.

- [ ] **Step 7: Suite completa**

```bash
PYTHONPATH=. pytest tests/ -q
```

Esperado: todo verde.

- [ ] **Step 8: Commit**

```bash
git add scm_agent/tools.py scm_agent/citation_gate.py tests/test_cross_impact_tool.py tests/test_price_watch_tool.py tests/test_capability_smoke.py
git commit -m "feat(cross-impact): registrar el tool #47 y anclar sus citas medidas"
```

---

### Task 8: CLI operativo y documentación

**Files:**
- Create: `examples/run_cross_impact.py`
- Modify: `README.md`, `CLAUDE.md`, `documentation/METHODOLOGY.md`
- Test: `tests/test_cross_impact_tool.py` (agregar un smoke)

**Interfaces:**
- Consumes: `Orchestrator`, `cross_impact_job`.

- [ ] **Step 1: Write the failing test**

Agregar a `tests/test_cross_impact_tool.py`:

```python
def test_cli_module_imports_and_declares_its_flags():
    import examples.run_cross_impact as cli
    parser = cli.build_parser()
    args = parser.parse_args([
        "--data", "x.csv", "--sku", "S1",
        "--current-q", "200", "--current-sl", "0.95",
        "--proposed-q", "400", "--proposed-sl", "0.95",
        "--proposed-by", "comprador",
    ])
    assert args.sku == "S1"
    assert args.proposed_q == 400.0
    assert args.proposed_by == "comprador"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=. pytest tests/test_cross_impact_tool.py -q -k cli
```

Esperado: FAIL — `ModuleNotFoundError: No module named 'examples.run_cross_impact'`.

- [ ] **Step 3: Write the implementation**

Crear `examples/run_cross_impact.py`. **Prints en ASCII puro** (Windows cp1252):

```python
"""Run cross_impact end to end: one proposed decision -> who would object.

    python examples/run_cross_impact.py --data data/demanda.csv --sku SKU-1 \
        --current-q 200 --current-sl 0.95 --proposed-q 400 --proposed-sl 0.95 \
        --proposed-by comprador --client "Acme Corp"

Thresholds come from the client profile (clients/<slug>/profile.json). Areas
without one come out no_evaluable and the verdict is 'indeterminado' - that is
the honest answer, not a failure.
"""

from __future__ import annotations

import argparse
import sys

from scm_agent.orchestrator import Orchestrator
from src.hats import HAT_KEYS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Cross-area impact of a proposed decision")
    p.add_argument("--data", required=True, help="weekly demand CSV")
    p.add_argument("--sku", required=True)
    p.add_argument("--current-q", type=float, required=True, dest="current_q",
                   help="order quantity the client runs TODAY (no default)")
    p.add_argument("--current-sl", type=float, required=True, dest="current_sl",
                   help="service level the client runs TODAY (no default)")
    p.add_argument("--proposed-q", type=float, required=True, dest="proposed_q")
    p.add_argument("--proposed-sl", type=float, required=True, dest="proposed_sl")
    p.add_argument("--proposed-by", required=True, dest="proposed_by", choices=list(HAT_KEYS))
    p.add_argument("--client", default="Client")
    p.add_argument("--out", default="deliverables/cross_impact")
    p.add_argument("--strict-params", action="store_true", dest="strict_params",
                   help="block when the client profile has no objection thresholds")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = Orchestrator().run(
        brief=f"impacto cruzado de la decision propuesta por {args.proposed_by}",
        data_path=args.data,
        job_type="cross_impact",
        client=args.client,
        strict_params=args.strict_params,
        overrides={
            "sku": args.sku,
            "current": {"q": args.current_q, "sl": args.current_sl},
            "proposed": {"q": args.proposed_q, "sl": args.proposed_sl},
            "proposed_by": args.proposed_by,
        },
        out_dir=args.out,
    )
    print(f"status: {result.status}")
    print(result.summary)
    for c in result.clarifications:
        print(f"  - {c}")
    if result.guided is not None:
        print("\nopciones:")
        for o in result.guided.options:
            mark = "*" if o.recommended else " "
            print(f" {mark} {o.label}  (score {o.score:.3f})")
    for name, path in result.deliverables.items():
        print(f"  {name}: {path}")
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH=. pytest tests/test_cross_impact_tool.py -q && ruff check examples/run_cross_impact.py
```

Esperado: 11 passed, ruff limpio.

- [ ] **Step 5: Probar el CLI de verdad**

```bash
PYTHONPATH=. python examples/run_cross_impact.py --data examples/data/demanda_semanal.csv --sku SKU-1 --current-q 200 --current-sl 0.95 --proposed-q 400 --proposed-sl 0.95 --proposed-by comprador --out /tmp/ci-smoke
```

Si no existe un CSV semanal de ejemplo en el repo, usá el que consumen los tests de hats (`grep -rn "run_hats" examples/`) o generá uno con el fixture de `tests/test_cross_impact_job.py`. Esperado: imprime el veredicto y las rutas de los dos CSV. Sin perfil de cliente, el veredicto debe salir `indeterminado`.

- [ ] **Step 6: Actualizar la documentación**

Cambiar el conteo de tools de 46 a 47 en:
- `README.md` — buscá `46` / `45 agent-routable` y la lista de tools.
- `CLAUDE.md` — la línea "**45 agent-routable tools**" en la sección *What Kern is*; sumá `cross_impact` a la enumeración entre paréntesis.
- `documentation/METHODOLOGY.md` — si lista tools por dominio.

```bash
grep -rn "45 agent-routable\|46 tools\|41 tools" README.md CLAUDE.md documentation/ scm_agent/README.md
```

Y la página `/how-it-works` (spec §6.4 punto 7): si enumera tools, sumá `cross_impact`.

```bash
grep -rln "hat_tension" webapp/
```

Si aparece en una plantilla o en un diccionario de tools de `webapp/app.py`, agregá la entrada equivalente para `cross_impact` con la misma forma que la de `hat_tension`. Si no aparece en ningún lado de `webapp/`, la página se arma sola desde el registry y no hay nada que tocar — verificalo y seguí.

Agregar a `CLAUDE.md`, bajo *Never-unprotected*, una línea:

> `cross_impact` mide el impacto de una decisión propuesta sobre las otras áreas y emite un veredicto de objeción contra umbrales del perfil de cliente. Un área sin umbral sale `no_evaluable` y el veredicto global es `indeterminado` — nunca "nadie objeta".

- [ ] **Step 7: Suite completa + lint**

```bash
PYTHONPATH=. pytest tests/ -q && ruff check src tests examples
```

Esperado: todo verde.

- [ ] **Step 8: Commit y draft PR**

```bash
git add examples/run_cross_impact.py README.md CLAUDE.md documentation/ tests/test_cross_impact_tool.py
git commit -m "feat(cross-impact): CLI operativo y documentacion (46 -> 47 tools)"
git push -u origin feat/cross-impact
gh pr create --draft --title "feat: cross_impact - impacto cruzado con veredicto de objecion (tool #47)" --body "$(cat <<'EOF'
## Qué

Tool #47 `cross_impact`: dada una decisión **ya propuesta** por un área sobre un SKU, mide el impacto en las otras tres y emite un **veredicto de objeción** contra un umbral que el operador declaró en el perfil del cliente.

Spec: `docs/superpowers/specs/2026-07-31-cross-impact-design.md`
Plan: `docs/superpowers/plans/2026-07-31-cross-impact.md`

## Frontera con hat_tension

`hat_tension` calcula los ideales de cada sombrero desde la grilla y **nunca juzga** si una magnitud es tolerable. `cross_impact` parte de un punto que un humano propuso, lo compara contra el estado que el cliente hoy corre, y juzga. El umbral de objeción es el delta que justifica un tool nuevo.

## Dos trampas de corrección fijadas contra el código

- `hat_kpis` devuelve `policy_cost` **crudo** (`src/hats.py:481`); la penalización por `sl_target` vive en `utilities()` (`417-420`). Bajar el SL abarata el KPI del planner, que registraría una mejora ante una violación de su propio servicio. Fix: `breaches_sl_target` fuerza la objeción.
- La dirección del deterioro no es uniforme: `comercial` maximiza fill rate, las otras tres minimizan costo. `abs(delta) > umbral` habría sido un bug.

## Honestidad

- `indeterminado` **precede** a `ejecutar`: un área sin umbral impide afirmar "nadie objeta".
- El QA veta la combinación deshonesta, no el hueco. Reportar `indeterminado` con celdas vacías es una entrega válida.
- La contra-propuesta no se emite si no existe un punto de grilla con cero objeciones; el umbral nunca se afloja.
- Alcance declarado en el deck: un SKU, espacio (Q, SL), cuatro áreas. **Almacén no es una de ellas.**

## Anclas de citas (medidas, no supuestas)

BFS sobre el grafo L3 (3002 nodos): las tres heredadas de `hat_tension` cierran 853 nodos a 2 saltos; `cross_functional_integration` suma sólo 16 y es hub-free. `sales_and_operations_planning` y `functional_silos` descartadas por imán.

## Test plan

- [ ] `pytest tests/ -q` verde en py3.11/3.12/3.13
- [ ] `ruff check src tests examples` limpio
- [ ] `test_no_anchored_tool_regressed_to_zero` verde (el pool de 16 es empírico y se mueve)
- [ ] CLI corrido a mano contra un CSV real
- [ ] Verificado que sin perfil de cliente el veredicto sale `indeterminado`, no `ejecutar`
EOF
)"
```

---

## Notas para quien ejecute

- **No toques `src/hats.py`, `src/hat_council.py` ni `jobs/hats_job.py`** más allá de importar de ellos. El `policy_cost` crudo no es un bug ahí: la penalización está en el lugar correcto para lo que ese módulo hace.
- **Si un test falla, arreglá la implementación, no el test** — salvo que el test esté mal, y en ese caso decilo explícitamente en el commit.
- **El pool de citas de 16 es empírico.** Si el barrido de la Task 7 Step 5 falla, es una medición nueva que hacer, no un test que silenciar.
- Los sub-proyectos que **no** entran acá (`coordination_load`, almacén como 5º sombrero, lote de decisiones, `citation_gate` en la ruta suelta, `flow_map`, `intake_scan`, `discovery_brief`, `fairness_guard`) están listados en la §11 del spec. No los agregues.

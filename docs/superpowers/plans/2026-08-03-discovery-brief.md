# `discovery_brief` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir `discovery_brief` (tool #51), la auditoría de parámetros de cliente que dice qué herramientas correrían hoy con números reales y cuáles tomarían un default en silencio, y guía la entrevista que cierra el hueco.

**Architecture:** Tres capas, espejo de `intake_scan`. Motor puro y determinista en `src/discovery_brief.py` (catálogo por parámetro, veredictos, un único ranker). Capa de job en `jobs/discovery_brief_job.py` con el único I/O (cargar el `ClientProfile`), el QA gate, los CSVs y el deck. `Tool` registrado en `scm_agent/tools.py` con `requires_data=False`.

**Tech Stack:** Python 3.11+, `pytest`, `pandas` (solo en la capa de job, vía `src.export`), `nosilo-core` como dependencia externa pineada (`v0.1.0`) de donde salen `Tool`, `GuidedOutcome`, `ExecutionOption`, `Residual`, `as_options`, `as_executed` y `confidence_from_coverage`.

**Spec:** `docs/superpowers/specs/2026-08-03-discovery-brief-design.md`
**Worktree:** `C:\Users\Gamer\Music\scm\.wt-discovery-brief` · **Rama:** `feat/discovery-brief` (parte de `origin/main` en `3e47b43`)

## Global Constraints

- **El motor puro no toca disco ni llama a un LLM.** `src/discovery_brief.py` no importa `pandas`, `pathlib`, `os` ni `src.llm`. La capa que audita tiene que devolver el mismo veredicto dos veces para la misma entrada.
- **Determinismo total.** Toda salida ordenada explícitamente; los empates se rompen por nombre. Nunca confiar en el orden de iteración de un `dict` ni de un `set`.
- **Dataclasses congeladas.** `@dataclass(frozen=True)` en todo tipo nuevo, igual que `src/intake_scan.py` y `src/client_profile.py`.
- **Esta herramienta NO escribe el perfil.** Ningún paso puede llamar a `client_profile.upsert_profile` fuera de un test. La salida propone; el operador ejecuta.
- **Numerador y denominador siempre.** Todo `summary` que hable de cobertura dice "X de Y", nunca solo X.
- **`nosilo_core` es externo y pineado.** No editar nada bajo `nosilo_core/`; si algo parece necesitarlo, es señal de que el diseño se torció.
- **Correr la suite completa antes del último commit.** `python -m pytest -q` desde la raíz del worktree.
- **Comandos:** usar el intérprete del venv del repo principal: `/c/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe`. En los ejemplos aparece como `python`.

## Estructura de archivos

| Archivo | Responsabilidad | Tarea |
|---|---|---|
| `src/discovery_brief.py` (crear) | Catálogo `ParamSpec`, veredictos, ranker, `build_report`. Puro. | 1-4 |
| `tests/test_discovery_brief.py` (crear) | Tests del motor puro. | 1-4 |
| `jobs/newsvendor_job.py` (modificar) | Fallback escalar `params["goodwill"]`. | 5 |
| `src/client_profile.py` (modificar) | Campo `goodwill` + `as_params()`. | 6 |
| `scm_agent/tools.py` (modificar) | 4 declaraciones nuevas + registro de `discovery_brief`. | 7, 9 |
| `tests/test_discovery_brief_params.py` (crear) | Marcador `UNDECLARED_PARAM_TOOLS`. | 7 |
| `jobs/discovery_brief_job.py` (crear) | Carga de perfil, `verify`, CSVs, deck. | 8 |
| `tests/test_discovery_brief_job.py` (crear) | Tests de la capa de job. | 8 |
| `tests/test_discovery_brief_tool.py` (crear) | Tests del registro. | 9 |
| Los 5 espejos del registro + docs | Ver Tarea 10. | 10 |

**Orden obligatorio:** la Tarea 5 (`newsvendor` lee `params["goodwill"]`) va **antes** de la Tarea 6 (el campo entra a `ClientProfile`), y las dos antes de la Tarea 7 (la declaración). Invertirlo agrega un campo muerto al perfil o una declaración insatisfacible.

---

### Task 1: El catálogo por parámetro

**Files:**
- Create: `src/discovery_brief.py`
- Test: `tests/test_discovery_brief.py`

**Interfaces:**
- Consumes: nada.
- Produces: `ParamSpec` (dataclass congelada), `CATALOG: tuple[ParamSpec, ...]`, `UNDECLARED_USE: tuple[ParamSpec, ...]`, `spec_for(name: str) -> ParamSpec | None`.

- [ ] **Step 1: Escribir el test que falla**

En `tests/test_discovery_brief.py`:

```python
"""Tests del motor puro de discovery_brief. Nada aquí toca disco."""
from src.discovery_brief import CATALOG, UNDECLARED_USE, ParamSpec, spec_for


def test_el_catalogo_no_repite_nombres():
    """Indexado POR PARAMETRO: un nombre canónico aparece una sola vez.

    Duplicar un parámetro por herramienta es exactamente la repetición que
    produjo las cinco instancias del mismo defecto en intake_scan.
    """
    nombres = [s.name for s in CATALOG + UNDECLARED_USE]
    assert len(nombres) == len(set(nombres)), f"nombres repetidos: {nombres}"


def test_todo_parametro_declara_que_cifra_mueve():
    """`moves` es el producto real: sin él el catálogo es una lista de nombres."""
    sin_moves = [s.name for s in CATALOG + UNDECLARED_USE if not s.moves.strip()]
    assert sin_moves == []


def test_todo_parametro_tiene_rango_plausible():
    for s in CATALOG + UNDECLARED_USE:
        assert s.low < s.high, f"{s.name}: rango vacío o invertido"


def test_order_cost_registra_su_default_por_sitio():
    """Los defaults son una TUPLA a propósito: order_cost vale 100.0 en
    simulation_job mientras client_profile documenta 75. Un default único
    mentiría en la columna `moves`."""
    s = spec_for("order_cost")
    assert s is not None
    sitios = {tool_key for tool_key, _, _ in s.defaults}
    assert "simulation" in sitios
    valores = {valor for _, valor, _ in s.defaults}
    assert 100.0 in valores


def test_objection_thresholds_es_granular():
    s = spec_for("objection_thresholds")
    assert s is not None
    assert s.subkeys == ("comprador", "planner", "cfo", "comercial")


def test_spec_for_devuelve_none_para_un_desconocido():
    assert spec_for("no_existe_este_parametro") is None


def test_las_areas_copiadas_no_derivaron_de_hats():
    """El motor puro copia HAT_KEYS en vez de importarlo (src.hats arrastra numpy
    y scipy). Esta aserción es lo único que impide que la copia derive."""
    from src.hats import HAT_KEYS

    assert spec_for("objection_thresholds").subkeys == HAT_KEYS
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `python -m pytest tests/test_discovery_brief.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'src.discovery_brief'`

- [ ] **Step 3: Escribir la implementación mínima**

Crear `src/discovery_brief.py`:

```python
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

from dataclasses import dataclass

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
        defaults=(("inventory_optimization", 0.25, "src/client_profile.py (docstring)"),),
    ),
    ParamSpec(
        name="service_level",
        question="¿Qué probabilidad de NO quebrar stock durante el lead time quieren "
                 "sostener por ciclo?",
        unit="fracción",
        low=0.50, high=0.999,
        moves="El z del stock de seguridad: mueve el inventario objetivo de toda la red.",
        defaults=(
            ("inventory_optimization", 0.95, "src/client_profile.py (docstring)"),
            ("flow_map", 0.95, "jobs/flow_map_job.py:163"),
            ("multi_echelon", 0.95, "jobs/multi_echelon_job.py:31"),
        ),
    ),
    ParamSpec(
        name="order_cost",
        question="¿Cuánto les cuesta emitir y recibir UNA orden de compra, todo incluido?",
        unit="usd/pedido",
        low=1.0, high=5000.0,
        moves="El lote económico y la frecuencia de reposición de toda la simulación.",
        defaults=(("simulation", 100.0, "jobs/simulation_job.py:31"),),
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
            ("simulation", 1.0, "jobs/simulation_job.py:33"),
        ),
    ),
    ParamSpec(
        name="backorder_cost",
        question="¿Cuánto les cuesta dejar una unidad pendiente hasta poder entregarla?",
        unit="usd/unidad/período",
        low=0.0, high=10000.0,
        moves="El balance faltante-vs-exceso de la simulación.",
        defaults=(("simulation", 5.0, "jobs/simulation_job.py:32"),),
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
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `python -m pytest tests/test_discovery_brief.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Verificar a mano los defaults citados**

Run:
```bash
sed -n '163p' jobs/flow_map_job.py; sed -n '31,33p' jobs/multi_echelon_job.py; sed -n '31,33p' jobs/simulation_job.py; sed -n '93p' jobs/newsvendor_job.py
```
Expected: cada línea muestra el valor que el catálogo dice. Si alguna difiere, **corregir el catálogo, no la cita**: el número que gobierna es el del código.

- [ ] **Step 6: Commit**

```bash
git add src/discovery_brief.py tests/test_discovery_brief.py
git commit -m "feat(discovery_brief): catalogo de parametros indexado por parametro"
```

---

### Task 2: Veredicto por herramienta

**Files:**
- Modify: `src/discovery_brief.py`
- Test: `tests/test_discovery_brief.py`

**Interfaces:**
- Consumes: `ParamSpec`, `spec_for` de la Tarea 1.
- Produces: constantes `GREEN = "verde"`, `YELLOW = "amarillo"`, `RED = "rojo"`, `UNDECLARED = "no_declarado"`, `NO_PROFILE = "sin_perfil"`; `ParamVerdict`; `ToolVerdict`; `verdicts_for(tools: list, params: dict | None) -> tuple[ToolVerdict, ...]`.

`params` es el dict que sale de `ClientProfile.as_params()`, o `None` cuando el cliente no tiene perfil.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_discovery_brief.py`:

```python
from dataclasses import dataclass

from src.discovery_brief import (
    GREEN, NO_PROFILE, RED, UNDECLARED, YELLOW, ToolVerdict, verdicts_for,
)


@dataclass(frozen=True)
class _FakeTool:
    """Lo mínimo del contrato de nosilo_core.registry.Tool que este motor lee."""
    key: str
    required_client_params: tuple[str, ...] = ()


def test_una_tool_sin_declaracion_sale_no_declarado():
    """No podemos hablar por ella. Nunca 'verde' por omisión."""
    (v,) = verdicts_for([_FakeTool("forecast")], {"holding_rate": 0.3})
    assert v.state == UNDECLARED


def test_todos_los_parametros_presentes_y_en_rango_sale_verde():
    tool = _FakeTool("inventory_optimization", ("holding_rate", "service_level"))
    (v,) = verdicts_for([tool], {"holding_rate": 0.28, "service_level": 0.97})
    assert v.state == GREEN
    assert v.missing == ()


def test_un_parametro_ausente_sale_rojo_y_lo_nombra():
    tool = _FakeTool("inventory_optimization", ("holding_rate", "service_level"))
    (v,) = verdicts_for([tool], {"service_level": 0.97})
    assert v.state == RED
    assert v.missing == ("holding_rate",)


def test_fuera_de_rango_es_rojo_pero_distinguible_de_ausente():
    """holding_rate=25.0 -- porcentaje tipeado donde va una fracción -- pasa la
    validación de ClientProfile y multiplica por cien el costo de mantener.
    Pedirlo de nuevo no sirve: hay que corregirlo, y por eso viaja aparte."""
    tool = _FakeTool("inventory_optimization", ("holding_rate", "service_level"))
    (v,) = verdicts_for([tool], {"holding_rate": 25.0, "service_level": 0.97})
    assert v.state == RED
    assert v.missing == ()
    assert v.out_of_range == (("holding_rate", 25.0),)


def test_sin_perfil_no_se_colapsa_en_rojo():
    """La acción es distinta: crear el perfil, no pedirle un número al cliente."""
    tool = _FakeTool("inventory_optimization", ("holding_rate", "service_level"))
    (v,) = verdicts_for([tool], None)
    assert v.state == NO_PROFILE


def test_objection_thresholds_parcial_es_amarillo_no_rojo():
    """Un dict parcial es legal a propósito: cross_impact devuelve NO_EVALUABLE
    en voz alta para el área sin umbral. NO toma un default en silencio, así que
    no es rojo -- corre y cubre menos, que es exactamente lo que amarillo dice."""
    tool = _FakeTool("cross_impact", ("objection_thresholds",))
    (v,) = verdicts_for([tool], {"objection_thresholds": {"cfo": 1000.0, "planner": 500.0}})
    assert v.state == YELLOW
    assert v.incomplete == (("objection_thresholds", ("comercial", "comprador")),)


def test_objection_thresholds_completo_es_verde():
    tool = _FakeTool("cross_impact", ("objection_thresholds",))
    completo = {k: 1.0 for k in ("comprador", "planner", "cfo", "comercial")}
    (v,) = verdicts_for([tool], {"objection_thresholds": completo})
    assert v.state == GREEN


def test_objection_thresholds_ausente_es_rojo():
    tool = _FakeTool("cross_impact", ("objection_thresholds",))
    (v,) = verdicts_for([tool], {"holding_rate": 0.25})
    assert v.state == RED
    assert v.missing == ("objection_thresholds",)


def test_los_veredictos_salen_ordenados_por_key():
    tools = [_FakeTool("zeta"), _FakeTool("alfa"), _FakeTool("media")]
    keys = [v.tool_key for v in verdicts_for(tools, {})]
    assert keys == ["alfa", "media", "zeta"]
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_discovery_brief.py -v`
Expected: FAIL con `ImportError: cannot import name 'verdicts_for'`

- [ ] **Step 3: Implementar**

Agregar a `src/discovery_brief.py`, después de `spec_for`:

```python
GREEN = "verde"
YELLOW = "amarillo"
RED = "rojo"
UNDECLARED = "no_declarado"
NO_PROFILE = "sin_perfil"


@dataclass(frozen=True)
class ToolVerdict:
    """Si una herramienta correría hoy con números del cliente, y qué le falta.

    Los tres modos de falla viajan SEPARADOS porque cada uno tiene un arreglo
    distinto, y decirle al operador el equivocado le hace perder la reunión:

    - `missing`: el parámetro nunca se declaró. Hay que PEDIRLO.
    - `out_of_range`: está cargado pero es implausible (el clásico holding_rate
      de 25.0 en vez de 0.25). Pedirlo de nuevo no sirve: hay que CORREGIRLO.
    - `incomplete`: parámetro granular presente pero parcial. La corrida es
      válida y cubre menos -- por eso amarillo, nunca rojo.
    """

    tool_key: str
    state: str
    missing: tuple[str, ...] = ()
    out_of_range: tuple[tuple[str, float], ...] = ()
    incomplete: tuple[tuple[str, tuple[str, ...]], ...] = ()


def _out_of_range(spec: ParamSpec, value: object) -> bool:
    """Un escalar fuera del rango plausible del catálogo.

    Los granulares y los no-numéricos no se evalúan por rango aquí: su
    completitud se juzga en `_missing_subkeys`. Un bool es un int en Python y
    nunca es un valor legítimo para estos parámetros, así que se rechaza.
    """
    if spec.subkeys or isinstance(value, bool) or not isinstance(value, (int, float)):
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
            if _out_of_range(spec, value):
                bad_range.append((name, float(value)))
                continue
            faltan = _missing_subkeys(spec, value)
            if faltan:
                incomplete.append((name, faltan))

        if missing or bad_range:
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
        ))
    return tuple(out)
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `python -m pytest tests/test_discovery_brief.py -v`
Expected: PASS, 16 tests.

- [ ] **Step 5: Commit**

```bash
git add src/discovery_brief.py tests/test_discovery_brief.py
git commit -m "feat(discovery_brief): veredicto por herramienta con sin_perfil y granular parcial"
```

---

### Task 3: El ranker de causa única

**Files:**
- Modify: `src/discovery_brief.py`
- Test: `tests/test_discovery_brief.py`

**Interfaces:**
- Consumes: `ToolVerdict`, `RED`, `NO_PROFILE` de la Tarea 2.
- Produces: `ParamGap` (campos `param: str`, `unlocks: int`, `tools: tuple[str, ...]`); `rank_gaps(verdicts: tuple[ToolVerdict, ...]) -> tuple[ParamGap, ...]`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_discovery_brief.py`:

```python
from src.discovery_brief import ParamGap, rank_gaps


def test_ranking_por_cuantas_tools_desbloquea():
    tools = [
        _FakeTool("a", ("holding_rate",)),
        _FakeTool("b", ("holding_rate",)),
        _FakeTool("c", ("order_cost",)),
    ]
    gaps = rank_gaps(verdicts_for(tools, {}))
    assert [(g.param, g.unlocks) for g in gaps] == [("holding_rate", 2), ("order_cost", 1)]
    assert gaps[0].tools == ("a", "b")


def test_causa_unica_dos_faltantes_no_acreditan_a_ninguno():
    """Pedir UNO deja la herramienta roja igual, así que acreditarle el
    desbloqueo prometería algo que no va a ocurrir. Es la regla que en
    intake_scan quedó reimplementada por separado en cuatro rankers."""
    tools = [
        _FakeTool("dos_faltantes", ("holding_rate", "order_cost")),
        _FakeTool("uno_solo", ("holding_rate",)),
    ]
    gaps = rank_gaps(verdicts_for(tools, {}))
    assert [(g.param, g.unlocks) for g in gaps] == [("holding_rate", 1)]
    assert gaps[0].tools == ("uno_solo",)


def test_fuera_de_rango_tambien_bloquea_la_acreditacion():
    """Una tool con un parámetro faltante Y otro fuera de rango no se desbloquea
    pidiendo el faltante: el otro sigue corrompido."""
    tools = [_FakeTool("mixta", ("holding_rate", "order_cost"))]
    gaps = rank_gaps(verdicts_for(tools, {"holding_rate": 25.0}))
    assert gaps == ()


def test_sin_perfil_no_produce_ranking_de_parametros():
    """La acción es crear el perfil; rankear preguntas sería el consejo equivocado."""
    tools = [_FakeTool("a", ("holding_rate",))]
    assert rank_gaps(verdicts_for(tools, None)) == ()


def test_empates_se_rompen_por_nombre_de_parametro():
    tools = [_FakeTool("x", ("order_cost",)), _FakeTool("y", ("holding_rate",))]
    gaps = rank_gaps(verdicts_for(tools, {}))
    assert [g.param for g in gaps] == ["holding_rate", "order_cost"]
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_discovery_brief.py -v`
Expected: FAIL con `ImportError: cannot import name 'ParamGap'`

- [ ] **Step 3: Implementar**

Agregar a `src/discovery_brief.py`:

```python
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
    fuera de rango, pedir uno solo la deja roja igual -- acreditarla prometería
    un desbloqueo que no va a ocurrir.

    ``sin_perfil`` no entra: ahí la acción es crear el perfil, no preguntar.
    Un solo ranker a propósito, para no repetir la regla en varios lugares (ver
    la deuda #1 declarada de intake_scan, donde cuatro rankers la
    reimplementaron y quedaron simétricos a mano).

    Empates por nombre de parámetro, para que dos corridas coincidan.
    """
    by_param: dict[str, list[str]] = {}
    for v in verdicts:
        if v.state != RED or v.out_of_range or len(v.missing) != 1:
            continue
        (param,) = v.missing
        by_param.setdefault(param, []).append(v.tool_key)
    gaps = [
        ParamGap(param, len(keys), tuple(sorted(keys)))
        for param, keys in by_param.items()
    ]
    return tuple(sorted(gaps, key=lambda g: (-g.unlocks, g.param)))
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `python -m pytest tests/test_discovery_brief.py -v`
Expected: PASS, 21 tests.

- [ ] **Step 5: Commit**

```bash
git add src/discovery_brief.py tests/test_discovery_brief.py
git commit -m "feat(discovery_brief): ranker unico con regla de causa unica"
```

---

### Task 4: El reporte y el `GuidedOutcome`

**Files:**
- Modify: `src/discovery_brief.py`
- Test: `tests/test_discovery_brief.py`

**Interfaces:**
- Consumes: `ToolVerdict`, `ParamGap`, `rank_gaps`, `spec_for`, todos los estados.
- Produces: `BriefReport` (campos `verdicts`, `gaps`, `undeclared_use`, `n_tools_evaluated`, `n_tools_total`, `has_profile`, `outcome`, `summary`); `build_report(*, tools, params, client_id="") -> BriefReport`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_discovery_brief.py`:

```python
from src.discovery_brief import BriefReport, build_report


def test_el_summary_siempre_lleva_numerador_y_denominador():
    tools = [_FakeTool("a", ("holding_rate",)), _FakeTool("b"), _FakeTool("c")]
    r = build_report(tools=tools, params={})
    assert "1 de 3" in r.summary
    assert r.n_tools_evaluated == 1
    assert r.n_tools_total == 3


def test_sin_ninguna_declaracion_no_dice_que_esta_todo_bien():
    """Cero evaluadas no es 'el perfil alcanza para todo'. No se evaluó nada."""
    r = build_report(tools=[_FakeTool("a"), _FakeTool("b")], params={"holding_rate": 0.25})
    assert r.n_tools_evaluated == 0
    assert r.outcome.status == "executed"
    assert "no se evaluó" in r.outcome.summary.lower()


def test_con_huecos_el_outcome_es_un_menu_rankeado():
    tools = [_FakeTool("a", ("holding_rate",)), _FakeTool("b", ("holding_rate",))]
    r = build_report(tools=tools, params={})
    assert r.outcome.status == "options"
    assert r.outcome.options[0].action == "ask_param:holding_rate"
    assert r.outcome.options[0].recommended is True


def test_la_opcion_dice_que_cifra_se_mueve():
    """Sin eso la entrevista es una lista de nombres sin razón para el cliente."""
    r = build_report(tools=[_FakeTool("a", ("holding_rate",))], params={})
    assert "costo de mantener" in r.outcome.options[0].summary


def test_sin_perfil_propone_crear_el_perfil_no_preguntar_numeros():
    r = build_report(tools=[_FakeTool("a", ("holding_rate",))], params=None, client_id="acme")
    assert r.has_profile is False
    assert r.outcome.status == "options"
    assert r.outcome.options[0].action == "create_profile:acme"


def test_el_residual_declara_que_no_juzga_la_verdad():
    r = build_report(tools=[_FakeTool("a", ("holding_rate",))], params={})
    texto = " ".join(res.description for res in r.outcome.residuals).lower()
    assert "verdad" in texto


def test_el_reporte_lleva_el_backlog_de_no_declarados():
    r = build_report(tools=[_FakeTool("a", ("holding_rate",))], params={})
    assert "review_period" in [s.name for s in r.undeclared_use]


def test_build_report_es_determinista():
    tools = [_FakeTool("b", ("order_cost",)), _FakeTool("a", ("holding_rate",))]
    uno = build_report(tools=tools, params={})
    dos = build_report(tools=list(reversed(tools)), params={})
    assert uno.summary == dos.summary
    assert [g.param for g in uno.gaps] == [g.param for g in dos.gaps]
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_discovery_brief.py -v`
Expected: FAIL con `ImportError: cannot import name 'BriefReport'`

- [ ] **Step 3: Implementar**

Agregar al tope de `src/discovery_brief.py`, junto a los imports:

```python
from dataclasses import dataclass, replace

from nosilo_core.guided import ExecutionOption, GuidedOutcome, Residual, as_executed, as_options
```

(Reemplaza el `from dataclasses import dataclass` de la Tarea 1.)

Y al final del módulo:

```python
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
        return as_executed(
            f"El perfil cubre todo lo declarado ({n_evaluated} herramienta(s) evaluada(s)); "
            "nada que preguntar.",
            residuals=[_TRUTH_RESIDUAL],
        )

    ranked = [_gap_option(g) for g in gaps[:_TOP_GAPS]]
    options = [replace(o, recommended=(i == 0)) for i, o in enumerate(ranked)]
    return as_options(
        "Parámetros ausentes, ordenados por cuántas herramientas desbloquea cada uno.",
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
    top = (
        f"lo más valioso: preguntar '{gaps[0].param}' desbloquea {gaps[0].unlocks}"
        if gaps else "nada que preguntar"
    )
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
        has_profile=params is not None,
        outcome=_outcome(gaps, verdicts, params is not None, client_id),
        summary=summary,
    )
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `python -m pytest tests/test_discovery_brief.py -v`
Expected: PASS, 29 tests.

Si `test_sin_ninguna_declaracion_no_dice_que_esta_todo_bien` falla por el `status`, verificar el valor real con:
```bash
python -c "from nosilo_core.guided import as_executed; print(as_executed('x').status)"
```
y ajustar la aserción del test al valor real, **no** el módulo.

- [ ] **Step 5: Commit**

```bash
git add src/discovery_brief.py tests/test_discovery_brief.py
git commit -m "feat(discovery_brief): reporte con numerador/denominador y menu rankeado"
```

---

### Task 5: `newsvendor` acepta `goodwill` como parámetro escalar

**Files:**
- Modify: `jobs/newsvendor_job.py:82-95`
- Test: `tests/test_newsvendor.py`

**Interfaces:**
- Consumes: nada de tareas previas.
- Produces: `prepare_records(df, params)` respeta `params["goodwill"]` como fallback cuando la columna no está. La columna, si existe y tiene valor, sigue ganando.

Esta tarea va **antes** de la Tarea 6: el criterio de `ClientProfile` es que un campo entra solo cuando una herramienta lo lee.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_newsvendor.py`:

```python
import pandas as pd

from jobs.newsvendor_job import prepare_records


def _df_sin_goodwill():
    return pd.DataFrame([
        {"product_id": "A", "mean_demand": 100.0, "price": 20.0, "unit_cost": 12.0},
    ])


def test_goodwill_escalar_se_usa_cuando_no_hay_columna():
    """Sin este fallback, un goodwill=0 asumido dice que quebrar stock no cuesta
    nada más allá del margen perdido, y empuja la cantidad óptima hacia abajo en
    todos los SKUs a la vez."""
    (rec,) = prepare_records(_df_sin_goodwill(), {"goodwill": 3.5})
    assert rec["goodwill"] == 3.5


def test_sin_columna_ni_parametro_sigue_siendo_cero():
    """Compatibilidad hacia atrás: el comportamiento previo no cambia."""
    (rec,) = prepare_records(_df_sin_goodwill(), {})
    assert rec["goodwill"] == 0.0


def test_la_columna_le_gana_al_parametro():
    """Misma prioridad que el resto del motor: el dato por SKU manda sobre el
    default de cliente."""
    df = _df_sin_goodwill().assign(goodwill=[7.0])
    (rec,) = prepare_records(df, {"goodwill": 3.5})
    assert rec["goodwill"] == 7.0


def test_una_celda_vacia_cae_al_parametro_no_a_cero():
    df = _df_sin_goodwill().assign(goodwill=[None])
    (rec,) = prepare_records(df, {"goodwill": 3.5})
    assert rec["goodwill"] == 3.5
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_newsvendor.py -k goodwill -v`
Expected: FAIL — `test_goodwill_escalar_se_usa_cuando_no_hay_columna` da `0.0 != 3.5`.

- [ ] **Step 3: Implementar**

En `jobs/newsvendor_job.py`, dentro de `prepare_records`, después de la línea que define `goodwill`:

```python
    goodwill = _pick_column(df, params.get("goodwill_col"), _GOODWILL_COLS)
    # Fallback escalar de cliente para el costo de reputación por unidad faltante.
    # La columna por SKU, cuando existe y tiene valor, siempre gana: misma
    # prioridad que el resto del motor (dato por SKU > default de cliente >
    # default del motor). Sin este fallback el cero asumido dice que quebrar
    # stock no cuesta nada más allá del margen perdido -- ver
    # src/discovery_brief.py, donde `goodwill` está catalogado por eso mismo.
    goodwill_default = float(params.get("goodwill") or 0.0)
```

y en el diccionario del comprehension, reemplazar la línea de `goodwill`:

```python
            "goodwill": (
                float(row[goodwill])
                if goodwill and pd.notna(row[goodwill])
                else goodwill_default
            ),
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `python -m pytest tests/test_newsvendor.py -v`
Expected: PASS, todos (incluidos los previos, que no deben cambiar).

- [ ] **Step 5: Commit**

```bash
git add jobs/newsvendor_job.py tests/test_newsvendor.py
git commit -m "feat(newsvendor): aceptar goodwill como parametro escalar de cliente"
```

---

### Task 6: `goodwill` entra a `ClientProfile`

**Files:**
- Modify: `src/client_profile.py` (dataclass `ClientProfile`, `__post_init__`, `as_params`)
- Test: `tests/test_client_profile.py`

**Interfaces:**
- Consumes: la Tarea 5 (la herramienta ya lee el parámetro).
- Produces: `ClientProfile.goodwill: float | None`, incluido en `as_params()`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_client_profile.py`:

```python
import pytest

from src.client_profile import ClientProfile


def test_goodwill_viaja_en_as_params():
    """Sin esto la declaración de newsvendor sería insatisfacible desde el perfil:
    quedaría en rojo permanente, imposible salvo pasándolo a mano cada corrida."""
    p = ClientProfile(client_id="acme", display_name="Acme", goodwill=4.0)
    assert p.as_params()["goodwill"] == 4.0


def test_goodwill_cero_es_legitimo_y_viaja_igual():
    """A diferencia de los otros, cero es una AFIRMACION del cliente ('quebrar
    stock no nos cuesta reputación'), no un silencio -- y esa es toda la
    diferencia que esta capacidad existe para marcar."""
    p = ClientProfile(client_id="acme", display_name="Acme", goodwill=0.0)
    assert p.as_params()["goodwill"] == 0.0


def test_goodwill_negativo_se_rechaza():
    with pytest.raises(ValueError, match="goodwill"):
        ClientProfile(client_id="acme", display_name="Acme", goodwill=-1.0)


def test_goodwill_ausente_no_aparece_en_params():
    p = ClientProfile(client_id="acme", display_name="Acme")
    assert "goodwill" not in p.as_params()
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_client_profile.py -k goodwill -v`
Expected: FAIL con `TypeError: __init__() got an unexpected keyword argument 'goodwill'`

- [ ] **Step 3: Implementar**

En `src/client_profile.py`, dentro de `ClientProfile`, después de `lead_time_days`:

```python
    # Costo de reputación por unidad faltante, leído por jobs/newsvendor_job.py
    # como fallback cuando el CSV no trae la columna. Entra al perfil recién
    # ahora, cuando una herramienta lo lee -- el criterio de este módulo.
    # A diferencia del resto, CERO es un valor legítimo: significa "quebrar
    # stock no nos cuesta reputación", una afirmación del cliente y no un
    # silencio. Por eso valida >= 0 y no > 0.
    goodwill: float | None = None
```

En `__post_init__`, después de la validación de `lead_time_days`:

```python
        if self.goodwill is not None and not (
            math.isfinite(self.goodwill) and self.goodwill >= 0
        ):
            raise ValueError("goodwill must be a finite number >= 0")
```

En `as_params`, agregar `"goodwill"` a la tupla `fields`:

```python
        fields = ("service_level", "holding_rate", "order_cost", "lead_time_days",
                  "goodwill", "objection_thresholds")
```

> **Ojo con el filtro de `as_params`.** Usa `if (v := getattr(self, k)) is not None`, así que `goodwill=0.0` sí sobrevive (es `0.0`, no `None`). Si el filtro fuera por truthiness, un cero legítimo desaparecería — el test `test_goodwill_cero_es_legitimo_y_viaja_igual` es la red que lo detecta.

- [ ] **Step 4: Correr y verificar que pasa**

Run: `python -m pytest tests/test_client_profile.py -v`
Expected: PASS, todos.

- [ ] **Step 5: Verificar que nada más se rompió**

Run: `python -m pytest tests/test_client_profile.py tests/test_newsvendor.py tests/test_scm_agent.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/client_profile.py tests/test_client_profile.py
git commit -m "feat(client_profile): campo goodwill, ahora que newsvendor lo lee"
```

---

### Task 7: Las cuatro declaraciones y el marcador de deuda

**Files:**
- Modify: `scm_agent/tools.py` (`flow_map_tool`, `multi_echelon_tool`, `simulation_tool`, `newsvendor_tool`)
- Create: `tests/test_discovery_brief_params.py`

**Interfaces:**
- Consumes: Tareas 5 y 6 (el parámetro se lee y el perfil lo puede proveer).
- Produces: `UNDECLARED_PARAM_TOOLS: frozenset[str]` en `tests/test_discovery_brief_params.py`.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_discovery_brief_params.py`:

```python
"""Marcador de cobertura de `required_client_params`, vaciable por tandas.

Mismo mecanismo que UNDECLARED_TOOLS en tests/test_tool_data_contract.py: sin
un test de igualdad de conjuntos, la cobertura se estanca en silencio y nada
falla. Cuando una herramienta declara sus parámetros, se la saca de acá.
"""
from scm_agent.tools import build_default_registry
from src.discovery_brief import CATALOG, UNDECLARED_USE

# Herramientas cuyo default silencioso todavía NO fue catalogado ni declarado.
# Backlog de la tanda 2 en adelante. El criterio para sacar una: su default
# ENGAÑA (mueve la cifra de portada sin dejar rastro), no que exista.
UNDECLARED_PARAM_TOOLS = frozenset({
    "abc_xyz", "acceptance_sampling", "cost_to_serve", "cycle_count",
    "data_quality", "ddmrp", "dea", "digital_twin", "disruption_scan",
    "drp", "earned_value", "excel_replenishment", "excess_obsolete",
    "facility_location", "fefo", "financial_kpis", "forecast",
    "hat_settlement", "hat_tension", "intake_scan", "landed_cost",
    "launch_readiness", "leadership_chain", "learning_curve",
    "markdown_liquidation", "network_design", "odoo_replenishment",
    "org_friction_audit", "price_intelligence", "price_watch", "pricing",
    "queuing", "reconciliation", "returns", "risk", "scheduling", "slotting",
    "sop", "sourcing", "supplier_management", "transportation",
    "vehicle_routing", "warehouse_layout", "whatif",
})

_DECLARADAS = frozenset({
    "inventory_optimization", "cross_impact",
    "flow_map", "multi_echelon", "simulation", "newsvendor",
})


def _registry():
    return build_default_registry()


def test_la_tanda_1_declara_lo_que_prometio():
    reg = _registry()
    for key in ("flow_map", "multi_echelon"):
        assert reg.get(key).required_client_params == ("service_level",), key
    assert reg.get("simulation").required_client_params == ("order_cost",)
    assert reg.get("newsvendor").required_client_params == ("goodwill",)


def test_toda_tool_declara_sus_params_o_esta_en_el_marcador():
    """La dirección peligrosa: una tool nueva que no declara y nadie listó."""
    reg = _registry()
    huerfanas = sorted(
        t.key for t in reg.list()
        if not t.required_client_params
        and t.key not in UNDECLARED_PARAM_TOOLS
        and t.key != "discovery_brief"
    )
    assert huerfanas == [], f"sin declarar y sin listar: {huerfanas}"


def test_el_marcador_no_tiene_entradas_rancias():
    """Una tool que ya declaró tiene que salir del marcador."""
    reg = _registry()
    rancias = sorted(
        k for k in UNDECLARED_PARAM_TOOLS
        if reg.get(k) and reg.get(k).required_client_params
    )
    assert rancias == [], f"declaradas pero todavía listadas: {rancias}"


def test_todo_parametro_declarado_esta_en_el_catalogo():
    """El guard que impide que discovery_brief se convierta en lo que denuncia.

    Una herramienta que declara un parámetro que el catálogo no conoce produce
    un veredicto mudo: sin pregunta, sin rango, sin `moves`. Es el mismo guard
    de igualdad de conjuntos que en /how-it-works encontró NUEVE herramientas
    faltantes, no las cinco que se suponían.
    """
    conocidos = {s.name for s in CATALOG + UNDECLARED_USE}
    reg = _registry()
    huerfanos = sorted({
        p for t in reg.list() for p in t.required_client_params if p not in conocidos
    })
    assert huerfanos == [], f"declarados pero fuera del catálogo: {huerfanos}"


def test_el_set_declarado_es_exactamente_el_esperado():
    reg = _registry()
    declaradas = {t.key for t in reg.list() if t.required_client_params}
    assert declaradas == _DECLARADAS
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_discovery_brief_params.py -v`
Expected: FAIL — `test_la_tanda_1_declara_lo_que_prometio` da `() != ('service_level',)`.

- [ ] **Step 3: Implementar las declaraciones**

En `scm_agent/tools.py`, agregar una línea a cada `Tool(...)`. Ubicarlas junto a `requires_data=` de cada herramienta (el orden de los kwargs no importa, pero mantené el estilo de `inventory_optimization` en la línea 152).

En `flow_map_tool()` (cerca de `key="flow_map"`, línea ~1428):
```python
        # El buffer de esta herramienta escala con z, y hasta acá su 0.95 era
        # silencioso: la cifra de portada se movía sin una línea que lo dijera.
        required_client_params=("service_level",),
```

En `multi_echelon_tool()` (línea ~1914):
```python
        required_client_params=("service_level",),
```

En `simulation_tool()` (línea ~2094):
```python
        # 100.0 usd/pedido asumido (jobs/simulation_job.py:31) gobierna el lote y
        # la frecuencia de reposición de toda la corrida.
        required_client_params=("order_cost",),
```

En `newsvendor_tool()` (línea ~1824):
```python
        # Un goodwill de 0 asumido baja la cantidad óptima de TODOS los SKUs.
        required_client_params=("goodwill",),
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `python -m pytest tests/test_discovery_brief_params.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Verificar que declarar no rompió ninguna corrida existente**

`strict_params` es opt-in (`False` por defecto en `JobRequest`, prendido solo por flag de CLI), así que declarar no debería cambiar ningún camino existente. Confirmarlo:

Run: `python -m pytest tests/test_scm_agent.py tests/test_packages.py -q`
Expected: PASS. Si algo falla acá, **es un hallazgo real** (alguien prende `strict_params` sin que lo veamos): parar y reportarlo, no aflojar la declaración.

- [ ] **Step 6: Commit**

```bash
git add scm_agent/tools.py tests/test_discovery_brief_params.py
git commit -m "feat(tools): declarar los params de cliente de flow_map, multi_echelon, simulation y newsvendor"
```

---

### Task 8: La capa de job

**Files:**
- Create: `jobs/discovery_brief_job.py`
- Test: `tests/test_discovery_brief_job.py`

**Interfaces:**
- Consumes: `build_report`, `BriefReport`, todos los estados de `src.discovery_brief`; `load_profile`, `is_generic_client_label` de `src.client_profile`; `write_summary_csv` de `src.export`; `Deliverable`, `Finding`, `Kpi` de `src.deliverable`; `confidence_from_coverage` de `nosilo_core.guided`.
- Produces: `prepare(client: str, params: dict | None) -> LoadedProfile`; `run(payload, params) -> BriefReport`; `verify(report) -> list[str]`; `write_operational(report, out_dir, client) -> dict[str, Path]`; `build_deck(report, *, client, prepared, citations, confidence, lang) -> Deliverable`.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_discovery_brief_job.py`:

```python
"""Tests de la capa de job de discovery_brief: el único lugar que toca disco."""
import json
from dataclasses import dataclass

import pytest

from jobs import discovery_brief_job as job
from src.discovery_brief import RED, build_report


@dataclass(frozen=True)
class _FakeTool:
    key: str
    required_client_params: tuple[str, ...] = ()


def _escribir_perfil(root, client_id, **campos):
    d = root / client_id
    d.mkdir(parents=True, exist_ok=True)
    payload = {"client_id": client_id, "display_name": client_id.title(),
               "schema_version": 1, "source": "manual", "lang": "es", **campos}
    (d / "profile.json").write_text(json.dumps(payload), encoding="utf-8")
    return d


def test_carga_el_perfil_del_cliente(tmp_path):
    _escribir_perfil(tmp_path, "acme", holding_rate=0.3, service_level=0.97)
    cargado = job.prepare("acme", {"clients_root": str(tmp_path)})
    assert cargado.params["holding_rate"] == 0.3
    assert cargado.has_profile is True


def test_un_cliente_sin_perfil_no_hereda_el_de_nadie(tmp_path):
    cargado = job.prepare("cliente-nuevo", {"clients_root": str(tmp_path)})
    assert cargado.has_profile is False
    assert cargado.params is None


def test_etiqueta_generica_se_rechaza(tmp_path):
    """Frontera de confianza, no cosmética: un llamador anónimo no puede
    heredar los números reales de otro cliente."""
    with pytest.raises(ValueError, match="etiqueta"):
        job.prepare("Client", {"clients_root": str(tmp_path)})


def test_perfil_corrupto_no_pasa_por_ausente(tmp_path):
    """Un JSON roto no es 'el cliente no lo cargó': es otra conversación."""
    d = tmp_path / "acme"
    d.mkdir(parents=True)
    (d / "profile.json").write_text("{roto", encoding="utf-8")
    cargado = job.prepare("acme", {"clients_root": str(tmp_path)})
    assert cargado.unreadable is True
    assert cargado.has_profile is False


def test_verify_grita_ante_una_declaracion_huerfana():
    """El guard que impide que discovery_brief se convierta en lo que denuncia."""
    tools = [_FakeTool("rara", ("parametro_que_nadie_catalogo",))]
    report = build_report(tools=tools, params={})
    issues = job.verify(report)
    assert any("catálogo" in i for i in issues)


def test_verify_no_se_queja_de_que_falten_parametros():
    """Ese es el hallazgo normal, el producto mismo -- no un fallo de QA."""
    tools = [_FakeTool("a", ("holding_rate",))]
    report = build_report(tools=tools, params={})
    assert any(v.state == RED for v in report.verdicts)
    assert job.verify(report) == []


def test_write_operational_emite_los_dos_csv(tmp_path):
    report = build_report(tools=[_FakeTool("a", ("holding_rate",))], params={})
    salidas = job.write_operational(report, tmp_path, "acme")
    assert salidas["params_csv"].exists()
    assert salidas["tools_csv"].exists()
    assert "holding_rate" in salidas["params_csv"].read_text(encoding="utf-8")


def test_el_deck_penaliza_la_confianza_cuando_dominan_las_no_declaradas():
    """6 de 50 declarando: el veredicto que da es sólido, su COBERTURA no."""
    tools = [_FakeTool(f"t{i}") for i in range(9)] + [_FakeTool("a", ("holding_rate",))]
    report = build_report(tools=tools, params={})
    deck = job.build_deck(report, client="Acme")
    assert deck.confidence < 0.9
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_discovery_brief_job.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'jobs.discovery_brief_job'`

- [ ] **Step 3: Implementar**

Crear `jobs/discovery_brief_job.py`:

```python
"""Job de discovery_brief -- carga el perfil del cliente y corre la auditoría.

Único I/O de la capacidad: leer el ``ClientProfile`` del disco. Todo el juicio
vive en ``src.discovery_brief``, que es puro. Esta capa mide y entrega; nunca
decide, y NUNCA escribe el perfil: cargarlo sigue siendo un paso deliberado del
operador (``src.client_profile.upsert_profile``).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nosilo_core.guided import confidence_from_coverage
from src.client_profile import DEFAULT_CLIENTS_ROOT, is_generic_client_label, load_profile
from src.deliverable import Deliverable, Finding, Kpi
from src.discovery_brief import (
    CATALOG,
    GREEN,
    NO_PROFILE,
    RED,
    UNDECLARED,
    UNDECLARED_USE,
    YELLOW,
    BriefReport,
    build_report,
    spec_for,
)
from src.export import write_summary_csv

# El techo de esta capacidad cuando la cobertura del registro NO es el problema.
_DECK_BASE_CONFIDENCE = 0.9


@dataclass(frozen=True)
class LoadedProfile:
    """Lo que se pudo leer del disco, antes de que el motor juzgue."""

    client_id: str
    params: dict | None
    has_profile: bool
    unreadable: bool = False


def prepare(client: str, params: dict | None = None) -> LoadedProfile:
    """Resuelve el perfil del cliente. El único paso que toca disco.

    Rechaza las etiquetas genéricas ("Client", "mcp-client") en vez de
    resolverlas: son una frontera de confianza, no cosmética -- un llamador
    anónimo no puede heredar los números reales de otro cliente.
    """
    params = params or {}
    if is_generic_client_label(client):
        raise ValueError(
            f"etiqueta de cliente genérica ({client!r}): esta auditoría necesita la "
            "etiqueta real del cliente para no leer el perfil de otro"
        )
    root = Path(params.get("clients_root") or DEFAULT_CLIENTS_ROOT)
    try:
        profile = load_profile(client, root=root)
    except Exception:
        # Un profile.json roto o de schema desconocido no es "el cliente no lo
        # cargó": la acción es arreglar el archivo, no pedirle números a nadie.
        return LoadedProfile(client, None, has_profile=False, unreadable=True)
    if profile is None:
        return LoadedProfile(client, None, has_profile=False)
    return LoadedProfile(client, profile.as_params(), has_profile=True)


def run(payload: LoadedProfile, params: dict | None = None) -> BriefReport:
    """Corre la auditoría sobre el registro completo."""
    from scm_agent.tools import build_default_registry

    tools = list(build_default_registry().list())
    return build_report(tools=tools, params=payload.params, client_id=payload.client_id)


def verify(report: BriefReport) -> list[str]:
    """QA gate. Dos condiciones, y ninguna es "faltan parámetros".

    Que falten parámetros es el hallazgo normal -- el producto mismo. Lo que sí
    es un fallo es que el reporte se contradiga:

    1. Declaración huérfana: una herramienta declara un parámetro que el
       catálogo no conoce, así que su veredicto sale mudo (sin pregunta, sin
       rango, sin `moves`). Sin este guard, la próxima herramienta que alguien
       declare sin tocar el catálogo convierte a discovery_brief en lo que
       denuncia.
    2. Invariante de verde: un verde cuyo parámetro no está cargado. No puede
       ocurrir por construcción (verdicts_for lo rutea a rojo); queda como red
       de regresión, igual que las condiciones defensivas de intake_scan.verify.
    """
    issues: list[str] = []

    conocidos = {s.name for s in CATALOG + UNDECLARED_USE}
    huerfanos = sorted({
        p
        for v in report.verdicts
        for p in (*v.missing, *(n for n, _ in v.out_of_range), *(n for n, _ in v.incomplete))
        if p not in conocidos
    })
    if huerfanos:
        issues.append(
            f"parámetro(s) declarado(s) fuera del catálogo: {', '.join(huerfanos)} "
            "-- el veredicto sale mudo hasta que entren a src/discovery_brief.py"
        )

    for v in report.verdicts:
        if v.state == GREEN and (v.missing or v.out_of_range or v.incomplete):
            issues.append(
                f"invariante roto: '{v.tool_key}' está verde con obstáculos declarados"
            )

    return issues


def _param_rows(report: BriefReport) -> list[dict[str, object]]:
    """Una fila por parámetro del catálogo. `moves` y `unlocks` en columnas
    separadas: el primero es la razón que el cliente entiende, el segundo es la
    prioridad del operador, y colapsarlos pierde una de las dos."""
    unlocks = {g.param: (g.unlocks, g.tools) for g in report.gaps}
    filas = []
    for spec, declarado in [(s, True) for s in CATALOG] + [(s, False) for s in UNDECLARED_USE]:
        n, tools = unlocks.get(spec.name, (0, ()))
        filas.append({
            "param": spec.name,
            "declarado": declarado,
            "question": spec.question,
            "unit": spec.unit,
            "low": spec.low,
            "high": spec.high,
            "moves": spec.moves,
            "defaults": "; ".join(f"{k}={v:g} ({sitio})" for k, v, sitio in spec.defaults),
            "subkeys": ", ".join(spec.subkeys),
            "unlocks": n,
            "unlocks_tools": ", ".join(tools),
        })
    return filas


def _tool_rows(report: BriefReport) -> list[dict[str, object]]:
    """Una fila por herramienta. `missing`, `out_of_range` e `incomplete` van en
    columnas SEPARADAS -- nunca colapsadas -- porque cada una nombra un problema
    distinto: pedir el número, corregirlo, o completarlo."""
    return [
        {
            "tool_key": v.tool_key,
            "state": v.state,
            "missing": ", ".join(v.missing),
            "out_of_range": ", ".join(f"{n}={val:g}" for n, val in v.out_of_range),
            "incomplete": ", ".join(f"{n}:{'/'.join(faltan)}" for n, faltan in v.incomplete),
        }
        for v in report.verdicts
    ]


def write_operational(
    report: BriefReport, out_dir: str | Path, client: str = "Client",
) -> dict[str, Path]:
    """El entregable legible por máquina: un CSV por parámetro, uno por herramienta."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    return {
        "params_csv": write_summary_csv(_param_rows(report), d / "discovery_brief_params.csv"),
        "tools_csv": write_summary_csv(_tool_rows(report), d / "discovery_brief_tools.csv"),
    }


def build_deck(
    report: BriefReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float | None = None,
    lang: str = "es",
) -> Deliverable:
    """Arma el deck: qué corre hoy con números reales, y qué preguntar primero.

    ``confidence`` es DERIVADA cuando no se pasa, nunca un literal: una
    auditoría que pudo evaluar 6 de 50 herramientas no puede reclamar la misma
    certeza que una que las evaluó a todas. El veredicto que da es sólido; su
    COBERTURA del registro no lo es, y eso es lo que la penalización pone en
    precio.
    """
    if confidence is None:
        gaps: list[str] = []
        if report.n_tools_total and report.n_tools_evaluated * 2 < report.n_tools_total:
            gaps.append("undeclared_tools_dominate")
        confidence = confidence_from_coverage(gaps, base=_DECK_BASE_CONFIDENCE)

    findings: list[Finding] = []
    # 1) El pedido #1, reusando el ranking del outcome para que el deck no pueda
    # contradecir al menú de opciones (misma disciplina que el deck de intake_scan).
    opciones = list(report.outcome.options)
    if opciones:
        top = opciones[0]
        findings.append(Finding(title=top.label, detail=top.summary))

    # 2) La cobertura, con numerador y denominador siempre.
    findings.append(Finding(
        title="Alcance de esta auditoría",
        detail=(
            f"Se evaluaron {report.n_tools_evaluated} de {report.n_tools_total} "
            "herramientas registradas. Las demás no declaran parámetros de cliente "
            "todavía: esta auditoría no puede hablar por ellas, y no lo hace."
        ),
    ))

    # 3) El backlog, nombrado y no escondido.
    if report.undeclared_use:
        findings.append(Finding(
            title="El motor lo usa, nadie lo declara",
            detail=(
                "Parámetros con default silencioso que ninguna herramienta declara "
                f"todavía: {', '.join(s.name for s in report.undeclared_use)}."
            ),
        ))

    kpis = [
        Kpi(name="Herramientas evaluadas", value=float(report.n_tools_evaluated)),
        Kpi(name="Herramientas registradas", value=float(report.n_tools_total)),
        Kpi(name="Parámetros a preguntar", value=float(len(report.gaps))),
    ]

    return Deliverable(
        title="Auditoría de Parámetros de Cliente",
        client=client,
        prepared=prepared,
        summary=report.summary,
        findings=findings,
        kpis=kpis,
        citations=tuple(citations),
        confidence=confidence,
        options=tuple(report.outcome.options),
    )
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `python -m pytest tests/test_discovery_brief_job.py -v`
Expected: PASS, 8 tests.

> **Si `Deliverable`, `Finding` o `Kpi` no aceptan alguno de esos kwargs**, mirar la firma real y adaptarse a ella:
> ```bash
> python -c "import dataclasses; from src.deliverable import Deliverable, Finding, Kpi; [print(t.__name__, [f.name for f in dataclasses.fields(t)]) for t in (Deliverable, Finding, Kpi)]"
> ```
> Copiar el patrón de `jobs/intake_scan_job.py::build_deck`, que ya lo usa correctamente. **No** inventar campos.

- [ ] **Step 5: Commit**

```bash
git add jobs/discovery_brief_job.py tests/test_discovery_brief_job.py
git commit -m "feat(discovery_brief): capa de job con QA gate, CSVs y deck"
```

---

### Task 9: Registrar la herramienta

**Files:**
- Modify: `scm_agent/tools.py` (import, `_discovery_brief_prepare`, `_discovery_brief_run`, `discovery_brief_tool`, `build_default_registry`)
- Create: `tests/test_discovery_brief_tool.py`

**Interfaces:**
- Consumes: todo `jobs/discovery_brief_job.py`.
- Produces: `discovery_brief_tool() -> Tool` con `key="discovery_brief"` y `requires_data=False`, registrada en `build_default_registry()`.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_discovery_brief_tool.py`:

```python
"""Tests del registro de discovery_brief."""
from scm_agent.tools import build_default_registry


def test_esta_registrada_y_no_pide_datos():
    """La entrada es la etiqueta del cliente, no una carpeta -- la primera
    diferencia real con intake_scan."""
    tool = build_default_registry().get("discovery_brief")
    assert tool is not None
    assert tool.requires_data is False


def test_expone_su_propio_outcome_como_opciones():
    tool = build_default_registry().get("discovery_brief")
    assert tool.options is not None


def test_no_se_declara_a_si_misma_como_dependiente_de_params():
    """Auditar los parámetros no debería requerir parámetros."""
    tool = build_default_registry().get("discovery_brief")
    assert tool.required_client_params == ()


def test_una_corrida_de_punta_a_punta_sin_perfil(tmp_path):
    """El camino que un operador realmente va a correr primero."""
    from jobs import discovery_brief_job as job

    payload = job.prepare("acme-sin-perfil", {"clients_root": str(tmp_path)})
    report = job.run(payload, {})
    assert report.has_profile is False
    assert report.n_tools_total > 0
    assert job.verify(report) == []
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `python -m pytest tests/test_discovery_brief_tool.py -v`
Expected: FAIL — `assert tool is not None` da `None`.

- [ ] **Step 3: Implementar**

En `scm_agent/tools.py`, agregar `discovery_brief_job` a la lista de imports de `jobs` (orden alfabético, va después de `disruption_scan_job` si existe, o donde corresponda alfabéticamente).

Agregar, siguiendo el bloque de `intake_scan` (~línea 2733):

```python
# ---- discovery_brief (auditoría de parámetros de cliente) --------------------

def _discovery_brief_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    try:
        loaded = discovery_brief_job.prepare(request.client, request.params)
    except ValueError as exc:
        return Prepared(status="needs_clarification", messages=[str(exc)])
    return Prepared(status="ok", payload=loaded)


def _discovery_brief_run(payload: object, params: dict) -> Produced:
    report = discovery_brief_job.run(payload, params)
    return Produced(report=report, summary=report.summary)


def discovery_brief_tool() -> Tool:
    return Tool(
        key="discovery_brief",
        title="Client Parameter Audit",
        description="Audit whether a client's business parameters are on file or the engine "
                    "would silently substitute a generic default: a per-tool verdict "
                    "(green/yellow/red/undeclared/no-profile) plus the single highest-value "
                    "question to ask next, with what headline figure moves if they don't answer. "
                    "Diagnoses and prioritises only -- loading the profile stays a deliberate step.",
        intent_keywords=(
            "parametros del cliente", "client parameters", "que le pregunto al cliente",
            "what should i ask the client", "perfil del cliente", "client profile",
            "descubrimiento", "discovery", "entrevista de arranque", "kickoff interview",
            "defaults asumidos", "assumed defaults",
        ),
        requires_data=False,
        prepare=_discovery_brief_prepare,
        run=_discovery_brief_run,
        qa=lambda report: discovery_brief_job.verify(report),
        deliver=lambda report, out_dir, client: discovery_brief_job.write_operational(
            report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            discovery_brief_job.build_deck(report, client=client, citations=tuple(citations),
                                          confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
        options=lambda report: report.outcome,
    )
```

Y en `build_default_registry()`, después de `reg.register(intake_scan_tool())`:

```python
    reg.register(discovery_brief_tool())
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `python -m pytest tests/test_discovery_brief_tool.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add scm_agent/tools.py tests/test_discovery_brief_tool.py
git commit -m "feat(tools): registrar discovery_brief (tool #51)"
```

---

### Task 10: Los espejos del registro y los conteos

**Files:**
- Modify: `src/i18n.py` (`TOOL_TITLES`)
- Modify: `tests/test_scm_agent.py:124` (set pinneado)
- Modify: `tests/test_capability_smoke.py` (`EXPECTED_OFFLINE_BUCKETS`)
- Modify: `webapp/how_it_works_data.py` (`TOOLS`)
- Modify: `README.md` (4 lugares), `CLAUDE.md`, `scm_agent/README.md`

**Interfaces:**
- Consumes: la Tarea 9 (la herramienta ya está registrada).
- Produces: nada de código; cierra los espejos que ya tienen guard de igualdad de conjuntos.

> **Por qué esta tarea existe.** Cada uno de estos lugares es un espejo del registro mantenido a mano. Cuatro ya tienen un guard de igualdad de conjuntos y van a fallar solos; los conteos de la documentación **no tienen guard** y son exactamente los que se desfasan en silencio. `/how-it-works` se quedó nueve herramientas atrás así.

- [ ] **Step 1: Correr la suite y dejar que los guards digan qué falta**

Run: `python -m pytest tests/test_scm_agent.py tests/test_capability_smoke.py tests/test_how_it_works.py -q`
Expected: FAIL en cada espejo que falte, nombrando `discovery_brief`. Anotar cuáles fallaron — esa es la lista de trabajo real de esta tarea, más confiable que la de acá abajo.

- [ ] **Step 2: `TOOL_TITLES` en `src/i18n.py`**

Junto a la entrada de `intake_scan` (línea ~275):

```python
    "discovery_brief": {"es": "Auditoría de Parámetros de Cliente",
                        "en": "Client Parameter Audit"},
```

- [ ] **Step 3: El set pinneado de `tests/test_scm_agent.py:124`**

Agregar `"discovery_brief"` al literal, en orden alfabético (va entre `"digital_twin"` y `"disruption_scan"`).

- [ ] **Step 4: `EXPECTED_OFFLINE_BUCKETS` en `tests/test_capability_smoke.py`**

`discovery_brief` tiene `requires_data=False`, así que corre en el harness offline sin fixture. Agregar la fila con el bucket que corresponda; si no estás seguro, correr el smoke y usar el valor que el propio test reporta como observado:

Run: `python -m pytest tests/test_capability_smoke.py -q -k discovery`

- [ ] **Step 5: `TOOLS` en `webapp/how_it_works_data.py`**

Agregar la fila siguiendo el formato existente (`ToolInfo(key, título, área, etapa, descripción)`), en la sección de área que corresponda:

```python
    ToolInfo("discovery_brief", "Client Parameter Audit", "Planning cadence", "Plan",
             "Audits whether a client's parameters are on file or the engine would "
             "silently substitute a default, and ranks what to ask next."),
```

Si el área o la etapa no encajan, mirar cómo quedó `intake_scan` y espejarlo — pertenecen a la misma familia.

- [ ] **Step 6: Los conteos de la documentación (sin guard, ojo)**

Cambiar 50 → 51 en:
- `README.md:44` ("50 registered tools")
- `README.md:62` (el `alt` del donut; el gráfico en sí queda pendiente de regenerar, igual que ya está declarado ahí)
- `README.md:66` ("The full list, by area (50 tools)") **y agregar la fila a la lista**
- `README.md:102` ("any of the 50 registered tools")
- `README.md:338` ("50 registered")
- `CLAUDE.md:18` ("**50 agent-routable tools**")
- `scm_agent/README.md` (buscar con `grep -n "50" scm_agent/README.md`)

Verificar que no quedó ninguno:
```bash
grep -rn "50 registered\|50 tools\|50 agent-routable\|(50 " README.md CLAUDE.md scm_agent/README.md
```
Expected: sin resultados.

- [ ] **Step 7: Correr la suite COMPLETA**

Run: `python -m pytest -q`
Expected: PASS, sin fallos. Este es el paso que no se saltea: `main` se mueve rápido y un merge sin conflictos no prueba que el código importe.

- [ ] **Step 8: Commit**

```bash
git add src/i18n.py tests/test_scm_agent.py tests/test_capability_smoke.py webapp/how_it_works_data.py README.md CLAUDE.md scm_agent/README.md
git commit -m "chore(registry): cerrar los espejos y los conteos para discovery_brief"
```

---

### Task 11: Ejemplo corrible y revisión final de rama

**Files:**
- Create: `examples/run_discovery_brief.py`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: un CLI que un operador corre de verdad.

- [ ] **Step 1: Escribir el ejemplo**

Crear `examples/run_discovery_brief.py`, espejando `examples/run_intake_scan.py`:

```python
"""Corre la auditoría de parámetros de un cliente y escribe el entregable.

    python -m examples.run_discovery_brief --client acme --out out/acme
"""
import argparse
from pathlib import Path

from jobs import discovery_brief_job as job


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--client", required=True, help="etiqueta real del cliente")
    p.add_argument("--out", default="out/discovery_brief", help="carpeta de salida")
    p.add_argument("--clients-root", default=None, help="raíz de clients/ (opcional)")
    args = p.parse_args()

    params = {"clients_root": args.clients_root} if args.clients_root else {}
    payload = job.prepare(args.client, params)
    report = job.run(payload, params)

    print(report.summary)
    for issue in job.verify(report):
        print(f"  QA: {issue}")
    for i, opt in enumerate(report.outcome.options, 1):
        print(f"  {i}. {opt.label}\n     {opt.summary}")

    salidas = job.write_operational(report, Path(args.out), args.client)
    for nombre, ruta in salidas.items():
        print(f"  {nombre}: {ruta}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Correrlo de verdad contra un cliente sin perfil**

Run: `python -m examples.run_discovery_brief --client demo-sin-perfil --out /tmp/db-demo`
Expected: imprime el summary con "X de 51", la opción `create_profile`, y escribe los dos CSVs.

- [ ] **Step 3: Correrlo contra un cliente CON perfil**

Run:
```bash
mkdir -p /tmp/db-clients/demo && printf '{"client_id":"demo","display_name":"Demo","schema_version":1,"source":"manual","lang":"es","holding_rate":0.3}' > /tmp/db-clients/demo/profile.json
python -m examples.run_discovery_brief --client demo --clients-root /tmp/db-clients --out /tmp/db-demo2
```
Expected: `inventory_optimization` sale rojo por `service_level` ausente; la opción #1 es preguntar un parámetro real. **Leer el CSV de parámetros y confirmar a ojo que la columna `moves` dice algo que un cliente entendería.** Si no, arreglar el texto del catálogo — ese campo es el producto.

- [ ] **Step 4: Suite completa una vez más**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add examples/run_discovery_brief.py
git commit -m "feat(discovery_brief): ejemplo corrible del CLI de auditoria"
```

- [ ] **Step 6: Revisión final de rama**

Correr una revisión sobre el diff completo de la rama contra `origin/main`, **no** tarea por tarea:

```bash
git fetch origin && git diff origin/main...HEAD --stat
```

> **Esto no es trámite.** En `flow_map` y otra vez en `intake_scan`, las siete revisiones por tarea aprobaron todo y el final review encontró igual dos Críticos — un falso verde certificado por un test del propio plan, y un cero que le pedía al cliente algo que ya había mandado. Buscar específicamente: un `verde` que no debería serlo, un pedido al cliente por algo que ya está cargado, y un texto de `moves` que promete un efecto que el código no produce.

---

## Self-Review

**Cobertura del spec:**

| Sección del spec | Tarea |
|---|---|
| 2 — tres capas | 1-4 (motor), 8 (job), 9 (tool) |
| 2 — `requires_data=False`, entrada por `client` | 9 |
| 2 — catálogo `ParamSpec` por parámetro, `defaults` plural | 1 |
| 2 — semáforo de 5 estados | 2 |
| 2 — fuera de rango | 2 |
| 2 — granular `subkeys`, parcial = amarillo | 2 |
| 2 — ranker único con causa única | 3 |
| 2 — salida como opciones, nunca escritura | 4 |
| 3 — las 4 declaraciones | 7 |
| 3 — `newsvendor` fallback escalar | 5 |
| 3 — `ClientProfile.goodwill` y el orden obligatorio | 6 (después de 5) |
| 3 — catálogo con dos listas | 1 (`CATALOG` + `UNDECLARED_USE`), 4 (viaja en el reporte) |
| 4 — dos CSVs, deck que reusa el ranking | 8 |
| 4 — confianza derivada | 8 |
| 5 — QA gate, dos condiciones | 8 |
| 6 — errores (etiqueta genérica, perfil corrupto, registro vacío) | 8, 4 |
| 7 — tests, `UNDECLARED_PARAM_TOOLS` | 1-9, marcador en 7 |
| 8 — residual declarado | 4 |

Sin huecos.

**Placeholders:** ninguno. Cada paso de código lleva el código.

**Consistencia de tipos:** `ToolVerdict.missing` / `.out_of_range` / `.incomplete` se definen en la Tarea 2 y se consumen con esos mismos nombres en las Tareas 3, 4 y 8. `ParamGap.param` / `.unlocks` / `.tools` (Tarea 3) se consumen igual en 4 y 8. `BriefReport` (Tarea 4) se consume en 8 y 9. `LoadedProfile.params` / `.has_profile` / `.unreadable` (Tarea 8) se consumen en 8 y 9. `spec_for` (Tarea 1) se usa en 2, 4 y 8.

**Nota de alcance añadida durante la revisión:** la Tarea 10 no estaba en el spec como sección propia, pero cerrar los espejos del registro es obligatorio — cuatro de ellos tienen guard y van a poner la rama en rojo, y los conteos de documentación no lo tienen y son los que se desfasan en silencio.

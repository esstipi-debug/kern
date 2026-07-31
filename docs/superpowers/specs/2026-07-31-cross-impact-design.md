# cross_impact — motor de impacto cruzado con veredicto de objeción

Fecha: 2026-07-31
Estado: spec para aprobación (cero código escrito)
Rama sugerida: `feat/cross-impact`

---

## 1. Qué problema resuelve

La tesis: la mayor parte de las reuniones de una cadena de suministro existen para
resolver tres cosas que un sistema puede resolver solo — (1) qué dato es el
correcto, (2) qué pasa aguas abajo si cambio esto, (3) por qué el sistema
recomienda este número.

Este spec ataca **sólo la (2)**, y sólo sobre el espacio de decisión que Kern ya
modela. Las otras dos quedan listadas en §11 como specs aparte.

La capacidad: dada una decisión **ya propuesta por un área** sobre un SKU,
`cross_impact` mide qué le pasa a las otras tres áreas y emite un **veredicto de
objeción** por área contra un umbral que el operador declaró. El veredicto es lo
que convierte la capacidad en contable: cero objeciones ⇒ la reunión no se hace.

---

## 2. Estado verificado del repo (todo leído de un archivo, no de memoria)

| Hecho | Fuente |
|---|---|
| **46** tools registrados (no 45 ni 47) | conteo de `key="..."` en `scm_agent/tools.py` |
| El ciclo es `prepare → run → qa → deliver`; un `Tool` toma **un solo** `request.data_path` | `scm_agent/registry.py:36-59` |
| `jobs/integrated_plan.py` quedó **fuera** del registry precisamente por necesitar 2+ archivos | su propio docstring, líneas 24-35 |
| `hats_job.prepare` no acepta ninguna decisión de entrada: sólo lee el CSV y arma `HatInputs` por SKU | `jobs/hats_job.py:105-158` |
| `hat_tension` produce 5 opciones = 4 ideales calculados + baseline; **nunca juzga** si una magnitud es tolerable | `jobs/hats_job.py:187-209` |
| `hat_council.tension_map` da 4 ideales + 6 choques en $ y fill rate | `src/hat_council.py:67-90` |
| El `citation_gate` corre **sólo** en `packages.py::_run_step`; la ruta de tool suelto (webapp / MCP / `run_agent.py`) usa `ground_citations` sin veto | `scm_agent/citation_gate.py:26-31` |
| Techo del pool de citas: `_CANDIDATE_POOL = 16`, `_MAX_CITATIONS = 3`. **Empírico, se mueve en ambas direcciones** con cada fuente nueva | `scm_agent/packages.py:61-76` |
| Grafo L3: 3002 nodos / 6141 links | `knowledge/scm-books/graph.json` |
| `ClientProfile.as_params()` exporta sólo 4 campos; `warehouse_capacity` se excluye por ser un **dataclass** (envenenaría la serialización), no por ser compuesto | `src/client_profile.py:151-162` |
| `required_client_params` se valida contra las keys del `params` fusionado, y **sólo** si el caller pasa `strict_params=True` (default `False`) | `scm_agent/orchestrator.py:135-145` |
| Nada en el repo persiste un `GuidedOutcome` ni cuenta sus estados | búsqueda sobre `src/ jobs/ scm_agent/ webapp/` |

---

## 3. Frontera con `hat_tension` (la razón por la que esto es un tool y no un parámetro)

|  | `hat_tension` (existe) | `cross_impact` (este spec) |
|---|---|---|
| Entrada | CSV de demanda. Ninguna decisión. | El mismo CSV **+ decisión propuesta + estado actual real** |
| Pregunta | ¿Dónde está el desacuerdo? | ¿Este movimiento a quién lastima y cuánto? |
| Punto de partida | Ideales calculados sobre grilla 25Q × 5SL | El punto que el cliente **hoy** corre |
| Comparación | vs `baseline_plan()` — la (s,Q) teórica al 95% | vs el estado actual declarado |
| Salida | 5 opciones = 4 ideales + baseline | Ejecutar / mantener / contra-propuesta |
| Veredicto | **Ninguno** | **Objeta / acepta / no evaluable**, por área |

El delta que justifica un tool nuevo es **el umbral de objeción**. `hats` mide
magnitudes y nunca juzga si son tolerables; sin ese juicio no hay forma de
afirmar "esta reunión no hace falta".

`cross_impact` **no** modifica `src/hats.py`, `src/hat_council.py` ni
`jobs/hats_job.py` salvo lo indicado en §6 (reuso de `prepare`). Ambos tools
coexisten.

---

## 4. Decisiones cerradas

| # | Decisión | Elegida | Descartadas y por qué |
|---|---|---|---|
| D1 | Alcance de "reducir reuniones" | Motor de impacto cruzado con veredicto | Contrato de decisión (es consolidación de piezas existentes); métrica de carga (requiere un ledger que no existe, y mide al sistema en vez de ayudar a decidir); seguir con `flow_map` (no ataca coordinación) |
| D2 | Origen del umbral de objeción | `src/client_profile.py`, campo nuevo persistido | Parámetro del request (no reproducible, se re-tipea cada corrida); derivarlo de los `weights` de hats (inventa tolerancia a partir de prioridad = degradación silenciosa) |
| D3 | Unidad del umbral | **Unidad nativa de cada área** | % de deterioro (distorsiona el fill rate, que ya es porcentual); todo en $ (obliga a convertir fill rate vía `gross_margin_rate`, asumiendo que cada unidad no vendida es margen perdido) |
| D4 | Forma de entrada | Una decisión por corrida, vía `params` | CSV de decisiones aparte (2 archivos ⇒ fuera del registry, como `integrated_plan`); columnas extra en el mismo CSV (mezcla granularidad semanal con decisión por SKU) |
| D5 | Anclas de citas | `safety_stock`, `service_level`, `cycle_service_level`, `cross_functional_integration` | `sales_and_operations_planning` (cierre propio 618, imán); `functional_silos` (grado 2 pero cierre 266 — a 1 salto de un hub) |
| D6 | Estado actual no declarado | `needs_clarification` | Caer a `baseline_plan()` afirmaría que el cliente corre la (s,Q) teórica al 95% sin que nadie lo verificara |

---

## 5. Contrato de datos

### 5.1 Entrada

`request.data_path` = el mismo CSV semanal que consume `hats_job.prepare`
(`product_id`, `quantity`, `unit_cost`, `lead_time_days`; mínimo `MIN_OBS` filas
usables por SKU).

`request.params`:

```
sku            : str          # obligatorio, un solo SKU
current        : {q: float, sl: float}   # obligatorio, el estado que el cliente hoy corre
proposed       : {q: float, sl: float}   # obligatorio
proposed_by    : str          # obligatorio, una de las 4 claves de HAT_KEYS
```

Validaciones, todas fatales (`needs_clarification`, nunca un default):

- `sku` ausente o sin filas usables ⇒ needs_clarification.
- `current` ausente ⇒ needs_clarification (D6). **No** cae a `baseline_plan()`.
- `proposed` ausente ⇒ needs_clarification.
- `proposed_by` ausente o fuera de `HAT_KEYS` ⇒ needs_clarification.
- `q <= 0` o `sl` fuera de `(0, 1)` en cualquiera de los dos puntos ⇒ needs_clarification.
- `current == proposed` ⇒ needs_clarification ("no hay cambio que evaluar"); no se
  emite un veredicto trivial de cero objeciones.

`current` y `proposed` **no** tienen que caer en la grilla de `src/hats.py`.
`hat_kpis` evalúa cualquier `Candidate`, así que ambos puntos se construyen
directamente. La grilla se usa sólo para la contra-propuesta (§7.3).

### 5.2 Umbrales — `ClientProfile`

Campo nuevo:

```python
objection_thresholds: dict[str, float] | None = None
```

Claves: exactamente las de `HAT_KEYS`. Valores: el deterioro máximo tolerado, en
**unidad nativa del KPI cabecera de esa área**, siempre positivo:

| Área | KPI (`headline_kpi`) | Unidad | Dirección |
|---|---|---|---|
| `comprador` | `effective_unit_cost` | usd/unidad | menor es mejor |
| `planner` | `policy_cost` | usd/año | menor es mejor |
| `cfo` | `capital_charge_usd` | usd/año | menor es mejor |
| `comercial` | `fill_rate` | fracción (0-1) | **mayor es mejor** |

Validación en `__post_init__`: cada clave debe estar en `HAT_KEYS` y cada valor
debe ser finito y `> 0`. Un dict parcial es **legal** — las áreas ausentes salen
como `NO_EVALUABLE` (§7.2). Se incluye en `as_params()` bajo la key
`objection_thresholds`; es un dict plano de floats, JSON-serializable, así que no
tiene el problema documentado de `warehouse_capacity` (que es un dataclass).

`required_client_params = ("objection_thresholds",)` en el `Tool`. Ojo: eso sólo
bloquea con `strict_params=True`, que es opt-in. **La honestidad no depende de
eso**: el propio tool marca las áreas sin umbral como `NO_EVALUABLE` y el QA veta
el veredicto global (§8).

---

## 6. Módulos

Cuatro piezas, cada una con un propósito y testeable sola.

### 6.1 `src/objection.py` (nuevo, puro, ~80 líneas)

Sin pandas, sin I/O, sin imports de `scm_agent`. Frozen dataclasses + funciones puras.

```
ACEPTA = "acepta"
OBJETA = "objeta"
NO_EVALUABLE = "no_evaluable"

MAXIMIZED_HATS = frozenset({HAT_COMERCIAL})   # los únicos donde subir es mejorar

@dataclass(frozen=True)
class Verdict:
    hat_key: str
    status: str                 # uno de los tres de arriba
    kpi_name: str
    kpi_current: float | None   # None sólo si NO_EVALUABLE
    kpi_proposed: float | None
    deterioration: float | None # >0 = empeoró, en unidad nativa
    threshold: float | None
    reason: str

def deterioration(hat_key, kpi_current, kpi_proposed) -> float
def judge(hat_key, kpi_current, kpi_proposed, threshold: float | None,
          *, breaches_sl_target: bool = False) -> Verdict
```

`deterioration` invierte el signo para las áreas de `MAXIMIZED_HATS`:

- área minimizadora: `deterioration = proposed - current`
- área maximizadora: `deterioration = current - proposed`

Un `deterioration <= 0` es una mejora (o empate) y siempre da `ACEPTA`.
`threshold is None` ⇒ `NO_EVALUABLE`, con `kpi_*` y `deterioration` en `None`
(campos vacíos, no ceros).

**Regla especial del planner (no negociable).** Ver §9.1: cuando
`breaches_sl_target` es `True`, `judge` devuelve `OBJETA` cualquiera sea el delta,
con `reason` explícito. Es el único caso donde el veredicto no se deriva del
umbral, y existe porque el KPI cabecera del planner no captura su propia
restricción de servicio.

### 6.2 `src/cross_impact.py` (nuevo, puro, ~120 líneas)

Mide. **No juzga** — no importa `src/objection.py`.

```
@dataclass(frozen=True)
class ImpactRow:
    hat_key: str
    kpi_name: str
    value_current: float
    value_proposed: float
    delta: float            # proposed - current, crudo y con signo

@dataclass(frozen=True)
class CrossImpact:
    sku: str
    proposed_by: str
    current: Candidate
    proposed: Candidate
    rows: tuple[ImpactRow, ...]     # una por HAT_KEY, en orden HAT_KEYS
    judge_cost_current: float
    judge_cost_proposed: float

def measure(inputs: HatInputs, current: Candidate, proposed: Candidate,
            proposed_by: str) -> CrossImpact
```

Usa `hats.hat_kpis(inputs, k, cand)[headline_kpi(k)]` y `hats.decision_cost`.
Ningún número propio: si `hat_kpis` no devuelve la key, propaga el `KeyError` en
vez de rellenar (§8).

### 6.3 `jobs/cross_impact_job.py` (nuevo, pandas + I/O)

`prepare` / `run` / `verify` / `write` / `build_deck` / `options`, con la forma de
`jobs/hats_job.py`.

`prepare` **reusa `hats_job.prepare`** con `params["sku"]` para obtener el único
`HatInputs`, y después valida y parsea `current` / `proposed` / `proposed_by` /
`objection_thresholds` (§5.1). No re-implementa la lectura del CSV.

### 6.4 Los sitios fijados que hay que tocar

Cada tool nuevo toca estos lugares — ninguno es opcional:

1. `scm_agent/tools.py` — `cross_impact_tool()` + su `register()`.
2. `scm_agent/citation_gate.py` — entrada en `TOOL_CONCEPTS` (§9.2).
3. `src/client_profile.py` — campo `objection_thresholds` + validación + `as_params()`.
4. `examples/run_cross_impact.py` — CLI operativo.
5. `tests/` — §10.
6. `documentation/` / `README.md` — el conteo de tools pasa de 46 a 47.
7. `webapp/templates` de `/how-it-works` — si lista tools, sumar el nuevo.

---

## 7. Salida

### 7.1 El reporte

```
@dataclass(frozen=True)
class CrossImpactReport:
    impact: CrossImpact
    verdicts: tuple[Verdict, ...]      # una por HAT_KEY
    n_objections: int                  # cuenta de OBJETA
    n_unevaluable: int                 # cuenta de NO_EVALUABLE
    verdict: str                       # ejecutar | bilateral | reunion | indeterminado
    counterproposal: Candidate | None
    outcome: GuidedOutcome
    summary: str
```

### 7.2 La regla del veredicto global

Se evalúa en este orden, y el primero que aplica gana:

| Condición | `verdict` | Lectura |
|---|---|---|
| `n_unevaluable > 0` | `indeterminado` | Hay áreas sin umbral declarado. **Nunca** se afirma "sin objeciones". |
| `n_objections == 0` | `ejecutar` | Reunión evitada |
| `n_objections == 1` | `bilateral` | Se convoca a UNA persona |
| `n_objections >= 2` | `reunion` | La reunión sí hace falta |

`indeterminado` gana incluso si las áreas evaluables no objetan. Esa precedencia
es el corazón de la honestidad de este tool: un hueco declarado por encima de un
veredicto optimista.

### 7.3 Opciones rankeadas

Vía `src/decision_options.py` (`Scenario` + `Objective` + `decide`), nunca
construyendo `ExecutionOption` a mano.

Escenarios:

1. **Ejecutar la propuesta** — `action` = adoptar (q, sl) propuestos.
2. **Mantener el estado actual** — `action` = no cambiar.
3. **Contra-propuesta** — el punto de la grilla de `src/hats.py`
   (`hats.evaluate(inputs).candidates`) con **cero objeciones** y menor
   `decision_cost`. **Sólo se emite si existe.** Si ningún punto de la grilla da
   cero objeciones, la opción no aparece; no se afloja el umbral ni se emite la
   "menos mala" haciéndola pasar por libre de objeciones.

   Si alguna área es `NO_EVALUABLE`, la contra-propuesta **no se busca**: "cero
   objeciones" no es determinable.

Objetivos: `Objective("n_objections", weight=2.0)` y
`Objective("judge_cost", weight=1.0)`, ambos cost-like (minimizar). Siempre
quedan ≥2 escenarios, así que `as_options` siempre tiene con qué recomendar.

`confidence`: `1.0` cuando las cuatro áreas son evaluables. Con áreas
`NO_EVALUABLE` se baja usando `guided.confidence_from_coverage`. Eso requiere una
penalización nueva en `COVERAGE_PENALTY` — `"unevaluable_area": 0.20` — porque
`confidence_from_coverage` levanta `KeyError` ante un nombre desconocido a
propósito. Se repite el nombre una vez por área sin umbral.

`residuals`: uno declarando que la elección es humana y que los umbrales son
**política del operador**, no un consenso objetivo (mismo encuadre que el D4 del
spec de hats para los `weights`).

### 7.4 Entregables

- `cross_impact.csv` — una fila por área: `hat_key`, `kpi_name`, `value_current`,
  `value_proposed`, `delta`, `deterioration`, `threshold`, `status`, `reason`.
  Las celdas de un área `NO_EVALUABLE` van **vacías**, no en cero.
- `verdict.csv` — una fila: `sku`, `proposed_by`, `q_current`, `sl_current`,
  `q_proposed`, `sl_proposed`, `n_objections`, `n_unevaluable`, `verdict`,
  `judge_cost_current`, `judge_cost_proposed`. Esta fila es la unidad contable de
  la capacidad (§11).
- Deck vía `Deliverable`, con `lang` pasado explícitamente (ver la lección de
  `PR #194`: los `build_deck` existentes no lo pasaban).

---

## 8. Gate de QA (`verify`)

Devuelve `list[str]`; vacío = pasó. QA falla ⇒ **no hay entregable** (lo aplica el
orchestrator en un solo lugar).

**Veta:**

1. `verdict != "indeterminado"` con `n_unevaluable > 0`. La combinación
   deshonesta, explícitamente.
2. Un `Verdict` con `status != NO_EVALUABLE` y algún `kpi_*` / `threshold` en
   `None`.
3. Un `Verdict` con `status == NO_EVALUABLE` y algún `kpi_*` / `deterioration` /
   `threshold` **no** `None` (el hueco tiene que estar vacío de verdad).
4. Menos de 4 verdicts, o claves que no sean exactamente `HAT_KEYS`.
5. Un valor no finito en cualquier KPI, delta o costo juez (`math.isfinite`).
6. `verify_guided(report.outcome)` no vacío.
7. Una `counterproposal` no-`None` cuya re-evaluación no dé cero objeciones
   (auto-chequeo: la contra-propuesta tiene que cumplir lo que promete).
8. `verdict` fuera del conjunto de cuatro valores.

**No veta:** reportar `indeterminado` con las áreas evaluables medidas y las
otras vacías. Esa es la entrega correcta, no una falla.

---

## 9. Dos trampas de corrección que el spec fija

### 9.1 El `policy_cost` del planner no penaliza su propio nivel de servicio

`hat_kpis` devuelve `policy_cost = -_u_planner_valid(inputs, cand)`
(`src/hats.py:481`), que es el costo **crudo**. La penalización por caer bajo
`sl_target` no vive ahí: vive en `utilities()` (`src/hats.py:417-420`), que es lo
que impulsa los ideales de `hat_tension`.

Consecuencia si se reusa `headline_kpi` sin más: una propuesta que baja el SL por
debajo de `config.sl_target` **reduce** el `policy_cost` (menos safety stock), o
sea el planner registraría una *mejora* y no objetaría — cuando su objetivo
declarado en `HATS[HAT_PLANNER].objetivo` es literalmente "minimizar costo de
ordenar + mantener **cumpliendo el nivel de servicio objetivo**".

Regla: `jobs/cross_impact_job.py` calcula
`breaches_sl_target = proposed.service_level < config.sl_target` y se lo pasa a
`judge`. Cuando es `True`, el planner sale `OBJETA` cualquiera sea el delta, con
`reason` = el SL propuesto vs el objetivo. Test dedicado en §10.

Esto **no** es un bug de `src/hats.py` y no se lo toca: ahí la penalización está
en el lugar correcto para lo que ese módulo hace.

### 9.2 La dirección del deterioro no es uniforme

Tres áreas minimizan, `comercial` maximiza. Un `abs(delta) > threshold` sería un
bug de corrección: declararía objeción cuando el costo del CFO **baja**. De ahí
`MAXIMIZED_HATS` en §6.1, con test de ambos signos para las cuatro áreas.

---

### 9.3 Anclas de citas (medición, 2026-07-31)

Medido por BFS sobre `knowledge/scm-books/graph.json` (3002 nodos / 6141 links):

| Conjunto | Grado | Cierre a 2 saltos |
|---|---|---|
| `safety_stock` + `service_level` + `cycle_service_level` | 26 / 10 / 9 | 853 (28,4%) |
| **+ `cross_functional_integration`** | 3 | **869 (28,9%)** — sólo +16 nodos |
| `sales_and_operations_planning` (descartada) | 14 | 618 sola — imán |
| `functional_silos` (descartada) | 2 | 266 sola — a 1 salto de un hub |

Las tres primeras se heredan de `hat_tension` porque `cross_impact` decide el
**mismo** espacio (Q, SL) — el mismo criterio que la sección 10 del spec de hats.
`cross_functional_integration` aporta la lente de coordinación: grado 3, sus tres
vecinos (`Supply Chain Orientation`, `Supply Chain Agility`,
`Tiger Team`) son on-topic y no hay hub de libro.

Los 16 nodos que admite están enumerados; cuatro son imanes suaves
(`Remote Working Mode`, `Product Reengineering`, `Ripple Effect`,
`Scale/Scope Agility`). No se pre-excluyen: la tarea de implementación debe
**medir** si alguno entra al pool de 16 con las `intent_keywords` reales de este
tool, y sólo entonces sumarlo a `EXCLUDED_CONCEPTS`. Pre-excluir sin medir sería
adivinar.

**Obligación al implementar:** re-correr el barrido de los 45 tools anclados
(`tests/test_packages_citations.py::test_no_anchored_tool_regressed_to_zero`).
El techo de 16 es empírico y se mueve; sumar un tool anclado obliga a verificar
que ninguno regresó a cero.

`cross_impact` corre por la ruta de tool suelto, donde el `citation_gate` **no**
se aplica (verificado, `citation_gate.py:26-31`). O sea: la entrada en
`TOOL_CONCEPTS` sólo surte efecto si el tool se compone dentro de un paquete. Se
registra igual, para que el día que la ruta suelta se gatee (spec aparte, §11) no
quede sin anclas.

---

## 10. Tests

`pytest tests/ -q` con `PYTHONPATH=.`. Un archivo por módulo.

**`tests/test_objection.py`** (sin pandas, sin fixtures de demanda)

- `deterioration` con el signo correcto para las 4 áreas, mejora y empeora cada una.
- Mejora ⇒ `ACEPTA` aunque la magnitud supere el umbral.
- Deterioro exactamente igual al umbral ⇒ `ACEPTA` (el umbral es inclusivo; fijado acá para que no quede ambiguo).
- `threshold=None` ⇒ `NO_EVALUABLE` con `kpi_*` y `deterioration` en `None`.
- `breaches_sl_target=True` ⇒ planner `OBJETA` aun con `deterioration` negativo (§9.1).
- `breaches_sl_target=True` en un área que no es planner ⇒ se ignora.

**`tests/test_cross_impact.py`**

- `measure` devuelve exactamente 4 filas, en orden `HAT_KEYS`.
- Los KPIs coinciden con llamar `hat_kpis` directo (no hay aritmética propia).
- `current == proposed` ⇒ todos los deltas exactamente 0.
- Un KPI faltante propaga `KeyError` en vez de rellenar.

**`tests/test_cross_impact_job.py`**

- Cada validación de §5.1 corta con `needs_clarification` y un mensaje que nombra el campo.
- Ausencia de `current` **no** cae a `baseline_plan()` (assert sobre el mensaje).
- Umbrales parciales ⇒ `verdict == "indeterminado"` aunque las evaluables acepten.
- Los 8 vetos de §8, uno por test, cada uno construyendo el reporte inválido a mano.
- `indeterminado` honesto ⇒ `verify` vacío y entregables escritos.
- Contra-propuesta: existe ⇒ 3 opciones; no existe ⇒ 2 y ninguna la finge.
- `NO_EVALUABLE` presente ⇒ contra-propuesta `None`.
- Celdas vacías (no ceros) en el CSV de un área no evaluable.
- `verify_guided(outcome)` vacío y ≥2 opciones con exactamente una recomendada.

**`tests/test_client_profile.py`** (extender el existente)

- Round-trip de `objection_thresholds` por JSON.
- Clave fuera de `HAT_KEYS` ⇒ `ValueError`.
- Valor `<= 0`, `NaN` o infinito ⇒ `ValueError`.
- Dict parcial es legal y sobrevive el round-trip.
- Aparece en `as_params()`; un `params` explícito le gana (`merge_params`).

**Guardas de integración**

- `strict_params=True` sin `objection_thresholds` ⇒ `needs_clarification`.
- Sin `strict_params`, sin umbrales ⇒ corre y devuelve `indeterminado` (la honestidad no depende del flag).
- El barrido de citas de §9.3.
- Import lazy de pandas donde corresponda: `src/objection.py` y `src/cross_impact.py` no lo importan, así que no tocan la cadena de boot de prod.

Cobertura objetivo: ≥80% en los módulos nuevos.

---

## 11. Lo que NO entra — cada uno es un spec aparte

| Spec | Por qué queda afuera |
|---|---|
| **`coordination_load`** — acumular veredictos en el tiempo | Nada persiste hoy un `GuidedOutcome`; el ledger es trabajo propio. Este spec deja el número emitido y en CSV; la serie de tiempo es otra cosa. |
| **Almacén como 5º sombrero** | `HAT_KEYS` son 4. Agregar almacén sin dato de capacidad sería inventar una utilidad. |
| **Lote de decisiones** | La opción B de entrada: 2 archivos ⇒ fuera del registry, como `integrated_plan`. |
| **`citation_gate` en la ruta de tool suelto** | Cambio de comportamiento en una superficie viva (webapp + MCP) que nadie pidió; la misma lección de E4 documentada en el módulo. |
| **`flow_map`** — lead time medido por arista | Ya tiene dos secciones de diseño aprobadas; sigue sin spec ni código. |
| **`intake_scan` / `discovery_brief` / `fairness_guard`** | La cola previa, sin tocar. |
| **Impacto fuera del espacio (Q, SL)** | Precio, fecha de PO, asignación de red: cada uno necesita su propio sustrato de utilidades. |

---

## 12. Riesgo declarado

`cross_impact` hereda el techo de `src/hats.py`: **un SKU, un espacio de decisión
(Q, SL), cuatro áreas**. La frase de venta "si compras mueve esto, qué le pasa a
almacén y a ventas" se cumple **a medias**: ventas está (`comercial`), almacén
**no** — no es uno de los cuatro sombreros. Eso se declara en el deck y en el
summary, no se disimula.

Lo que sí entrega, entero: un veredicto medido, reproducible y citado sobre las
cuatro áreas que Kern modela hoy, con los huecos visibles.

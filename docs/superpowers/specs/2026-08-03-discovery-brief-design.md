# `discovery_brief` — auditoría de parámetros de cliente

**Fecha:** 2026-08-03 · **Sub-proyecto 3 de 4 del Plan Maestro SCM-FDE** · **Rama:** `feat/discovery-brief`
**Medido contra:** `origin/main` en `3e47b43`.

## 1. Qué es y por qué

`intake_scan` (tool #50) audita si el **dato** alcanza para correr el motor. `discovery_brief`
es su espejo sobre la otra mitad de la entrada: audita si el **contexto** alcanza — los
parámetros de negocio que el cliente tiene que declarar y que hoy, si no los declara, el motor
reemplaza en silencio por un default genérico.

Esto es una reformulación deliberada respecto del Plan Maestro, que lo pedía como formulario
(crux, costo de inacción, mapa de stakeholders, System of Record). Como formulario no agregaba
nada al motor: era prosa que nadie leía dos veces. Como auditoría de parámetros ataca un hueco
medible y su salida es **configuración**, no texto — alimenta el `ClientProfile` que el motor ya
lee en cada corrida.

### El hueco, medido

- `required_client_params` lo declaran **2 de 50** herramientas: `inventory_optimization`
  (`scm_agent/tools.py:152`, `holding_rate` + `service_level`) y `cross_impact`
  (`scm_agent/tools.py:1193`, `objection_thresholds`).
- La declaración **solo se hace cumplir si quien llama opta por `strict_params`**, en dos
  sitios: `scm_agent/orchestrator.py:135` y `scm_agent/packages.py:438`. El default es `False`
  en todos lados; solo lo prenden tres CLIs con flag explícito (`run_agent`, `run_package`,
  `run_cross_impact`).
- Consecuencia: hoy una corrida normal para un cliente que nunca declaró su costo de mantener
  produce una cifra de portada calculada con `0.25`, sin una sola línea que lo diga.

### Alcance: diagnostica y prioriza, NO escribe

Mismo contrato que `intake_scan`. La salida es un veredicto más una entrevista rankeada.
**Cargar el perfil sigue siendo un paso deliberado y auditable del operador**
(`src.client_profile.upsert_profile`). Esta herramienta nunca escribe un `profile.json`.

## 2. Arquitectura

Tres capas, igual que `intake_scan`:

| Capa | Archivo | Responsabilidad |
|---|---|---|
| Motor puro | `src/discovery_brief.py` | El catálogo, los veredictos, el ranking. Sin I/O, sin LLM, determinista. |
| Job | `jobs/discovery_brief_job.py` | Único I/O: carga el `ClientProfile` del disco y arma el registro. QA gate, CSVs, deck. |
| Tool | `scm_agent/tools.py` | Registro de `discovery_brief` con `requires_data=False`. |

`nosilo_core` es desde `3e47b43` una dependencia externa pineada (`nosilo-core @ v0.1.0`). Esta
capacidad **no necesita tocarla**: `Tool.required_client_params` ya existe en el registro
externo, y `GuidedOutcome` / `confidence_from_coverage` se consumen tal cual. Todo lo nuevo vive
en este repo.

### Entrada

`JobRequest.client` — la etiqueta del cliente, no una carpeta. Por eso `requires_data=False`,
la primera diferencia real con `intake_scan`.

### El catálogo, indexado por parámetro

Vive al tope de `src/discovery_brief.py` como una tupla de `ParamSpec` congelados. **Indexado por
parámetro y no por herramienta**: el parámetro es del dominio, y duplicarlo por herramienta es
exactamente la repetición que produjo las cinco instancias del mismo defecto en `intake_scan`.

```python
@dataclass(frozen=True)
class ParamSpec:
    name: str                                  # nombre canónico, exacto como lo lee el motor
    question: str                              # la pregunta al cliente, en castellano
    unit: str                                  # "fracción", "usd/pedido", "días", ...
    low: float                                 # piso plausible (inclusive)
    high: float                                # techo plausible (inclusive)
    moves: str                                 # QUÉ CIFRA DE PORTADA SE MUEVE si no lo dan
    defaults: tuple[tuple[str, float, str], ...]   # (tool_key, valor, sitio en el código)
    subkeys: tuple[str, ...] = ()              # para un parámetro granular (ver 2.4)
```

`moves` es el producto real. Sin ese campo el catálogo es una lista de nombres; con él, la
entrevista tiene una razón que el cliente entiende ("si no me das el costo de mantener, el
ahorro de la portada se calcula con 25% anual asumido").

`defaults` es una **tupla, no un escalar**, y esto no es sobre-diseño: `order_cost` vale `100.0`
en `jobs/simulation_job.py:31` mientras el docstring de `src/client_profile.py` habla de `$75`.
Un catálogo con un solo default por parámetro mentiría en la columna `moves`.

### Veredicto por herramienta

Espejo exacto del semáforo de `intake_scan`:

| Estado | Significado | Acción del operador |
|---|---|---|
| `verde` | Declara parámetros, el perfil los tiene todos y completos, todos en rango. | Ninguna. |
| `amarillo` | Un parámetro granular está presente pero **incompleto** (ver 2.4). | Completarlo; corre igual, cubre menos. |
| `rojo` | Declara parámetros y falta al menos uno, o uno cae fuera de rango. | Pedirlo (sale rankeado). |
| `no_declarado` | No declara nada. **No podemos hablar por ella.** | Declararla en una tanda futura. |
| `sin_perfil` | El cliente no tiene `ClientProfile` en absoluto. | Crear el perfil, no pedir un número. |

`sin_perfil` es un estado propio y **nunca se colapsa en `rojo`**: la acción es distinta, y
decirle al cliente "faltan tres parámetros" cuando el problema es que nadie creó su perfil manda
al operador a la conversación equivocada. Es el mismo criterio por el que `intake_scan` reporta
`orphan_keys` como no evaluado cuando no hay archivo maestro, en vez de como cero.

`no_declarado` domina el reporte en la tanda 1 y por eso **el numerador y el denominador viajan
siempre** en el `summary`, igual que en `intake_scan`.

### Fuera de rango es un estado real

`ClientProfile.__post_init__` valida solo signo y `(0,1)` para algunos campos. `holding_rate=25.0`
—porcentaje tipeado donde va una fracción— **pasa hoy sin chistar** y multiplica por cien el
costo de mantener. El catálogo aporta el techo plausible que la dataclass no tiene. Un valor
fuera de rango sale `rojo` con su propio detalle, nunca confundido con un valor ausente: pedir
el número otra vez no sirve, hay que corregirlo.

### Parámetros granulares: `objection_thresholds`

`cross_impact` declara `objection_thresholds`, un dict cuyas claves son los cuatro `HAT_KEYS`, y
**un dict parcial es legal a propósito** (un área sin umbral sale `NO_EVALUABLE` en vez de tomar
una tolerancia asumida). Para ese parámetro "presente" no es un booleano.

Por eso `ParamSpec.subkeys`: si las hay, `verde` exige las cuatro y un dict parcial sale
**`amarillo`** nombrando cuáles faltan. Colapsarlo a "está presente" le daría un falso verde al
único parámetro granular que existe hoy — exactamente el defecto que el final review de
`intake_scan` encontró después de que las siete revisiones por tarea aprobaran todo.

**Parcial es `amarillo` y no `rojo`, y la distinción es sustantiva.** `rojo` significa "la
corrida tomaría un default en silencio". Un `objection_thresholds` parcial no hace eso:
`cross_impact` devuelve `NO_EVALUABLE` para el área sin umbral, en voz alta. La corrida es
válida y cubre menos. Pintarla de rojo mandaría al operador a bloquear un entregable que está
bien, y vaciaría de significado al rojo que sí importa. Es el mismo lugar que ocupa `amarillo`
en `intake_scan` (columnas opcionales ausentes: corre, con menos).

### Ranking de la entrevista: un solo ranker

Hay una sola clase de pedido (un parámetro), ordenada por cuántas herramientas desbloquea, con
la **regla de causa única** desde el arranque: una herramienta a la que le faltan dos parámetros
no se acredita a ninguno de los dos, porque pedir uno la deja roja igual. Los empates se rompen
por nombre de parámetro para que dos corridas coincidan.

Un único ranker paga la deuda #1 declarada de `intake_scan`, donde cuatro rankers reimplementaron
la misma regla por separado y quedaron simétricos a mano.

### Salida: opciones, nunca escritura

Un `GuidedOutcome` con las opciones rankeadas (`action="ask_param:<nombre>"`), cada una con la
pregunta al cliente, la unidad, el rango sano y qué cifra se mueve. Cada opción incluye además
la línea de `upsert_profile` lista para copiar — sigue siendo el operador quien la ejecuta.

## 3. Cobertura: qué se declara en la tanda 1

Criterio: **se declara donde el default ENGAÑA, no donde existe.** Un default es engañoso cuando
mueve la cifra de portada sin dejar rastro.

| Herramienta | Parámetro | Default silencioso hoy | Trabajo |
|---|---|---|---|
| `inventory_optimization` | `holding_rate`, `service_level` | — | ya declarado |
| `cross_impact` | `objection_thresholds` | — | ya declarado |
| `flow_map` | `service_level` | `0.95` (`jobs/flow_map_job.py:163`) | una línea |
| `multi_echelon` | `service_level` | `0.95` (`jobs/multi_echelon_job.py:31`) | una línea |
| `simulation` | `order_cost` | `100.0` (`jobs/simulation_job.py:31`) | una línea |
| `newsvendor` | `goodwill` | `0.0` (`jobs/newsvendor_job.py:93`) | **fallback escalar nuevo** |

`newsvendor` no es una declaración de una línea y entra igual porque es la mitad del valor de la
tanda. Sus defaults silenciosos (`goodwill=0.0`, `salvage_value=0.0`) llegan por **columna
ausente**, no por `params`: `prepare_records` busca `goodwill_col` (un nombre de columna) y cae a
cero si no está. Un `goodwill=0` asumido dice que quedarse sin stock no cuesta nada más allá del
margen perdido, lo cual empuja la cantidad óptima hacia abajo en todos los SKUs a la vez.

Alcance del trabajo en `newsvendor`: aceptar `params["goodwill"]` como fallback escalar cuando la
columna no está (la columna, si existe, sigue ganando — misma prioridad que el resto del motor),
y recién entonces declararlo. `salvage_value` queda **fuera** de esta tanda: su cero es
defendible (un producto sin valor de recupero es el caso común) y no tiene el mismo efecto
sistemático.

**`goodwill` obliga a un campo nuevo en `ClientProfile`, y sin él la declaración sería un
callejón sin salida.** Los otros cinco parámetros de la tanda ya son campos del perfil y salen
por `as_params()`: `service_level`, `holding_rate`, `order_cost`, `objection_thresholds`.
`goodwill` no existe ahí, así que una herramienta que lo declarara sin agregarlo al perfil
quedaría permanentemente en `rojo` — imposible de satisfacer salvo pasándolo a mano en cada
corrida. El criterio del propio módulo es explícito (crece campo por campo, solo cuando una
herramienta lo lee) y esta tanda lo cumple: primero `newsvendor` lo lee, después el campo entra.
Orden obligatorio en el plan; invertirlo agrega un campo muerto.

Alcance concreto en `src/client_profile.py`: campo `goodwill: float | None = None`, validado
`>= 0` finito (a diferencia de los demás, cero es un valor legítimo y significa "quedarnos sin
stock no cuesta reputación" — la diferencia es que ahora es una afirmación del cliente y no un
silencio), incluido en `as_params()`.

Con la tanda 1: **6 de 50 herramientas declaran**. El reporte lo dice en cada corrida.

### El catálogo lleva dos listas

1. **Parámetros declarados** — producen veredictos que bloquean.
2. **Parámetros que el motor usa con default silencioso y nadie declara** — salen en una sección
   aparte, "el motor lo usa, nadie lo declara". No bloquean nada; son literalmente el backlog de
   la tanda 2.

Sin la segunda lista el catálogo sería una lista de deseos sin consecuencia; sin la primera, no
habría auditoría. Las dos juntas son el numerador y el denominador honestos que exige el
precedente.

## 4. Entregable

`write_operational` produce dos CSVs, espejo de `intake_scan`:

- `discovery_brief_params.csv` — una fila por parámetro del catálogo: pregunta, unidad, rango,
  defaults vigentes por sitio, qué cifra mueve, cuántas herramientas desbloquea.
- `discovery_brief_tools.csv` — una fila por herramienta con su veredicto y qué le falta.

`build_deck` reusa el ranking del `GuidedOutcome` y **no lo re-deriva**, para que no pueda
contradecir al menú de opciones. Misma disciplina que el deck de `intake_scan`.

### Confianza derivada, nunca literal

Vía `nosilo_core.guided.confidence_from_coverage`, con el gap `undeclared_tools_dominate` cuando
menos de la mitad de las herramientas registradas declaran algo. Con 6 de 50 la penalización
entra **siempre**, y eso es correcto: el veredicto que da es sólido, su cobertura del registro
no. La confianza baja se vacía sola a medida que las tandas avanzan.

## 5. QA gate

Dos condiciones vivas en `verify()`:

1. **Declaración huérfana** — una herramienta declara un `required_client_param` que el catálogo
   no conoce. Fallo duro. Sin esto, la próxima herramienta que alguien declare sin tocar el
   catálogo produce un veredicto mudo y `discovery_brief` se convierte en lo que denuncia. Es el
   mismo guard de igualdad de conjuntos que en `/how-it-works` encontró nueve herramientas
   faltantes, no las cinco que se suponían.
2. **Invariante de verde** — un veredicto `verde` cuyo parámetro no está en el perfil cargado. No
   puede ocurrir por construcción; queda como red de regresión, igual que las condiciones
   defensivas 3-4 de `intake_scan.verify`.

**Que falten parámetros NO es un fallo de QA.** Ese es el hallazgo normal, el producto mismo.

## 6. Errores

- **Etiqueta genérica de cliente** (`"Client"`, `"mcp-client"`): `is_generic_client_label` ya
  resuelve el caso. Nunca heredar el perfil de otro; devolver `needs_clarification` pidiendo la
  etiqueta real. Es una frontera de confianza, no cosmética.
- **Perfil ilegible o de `schema_version` desconocida**: estado propio en el reporte, nunca un
  `rojo` que parezca "el cliente no lo cargó".
- **Registro vacío o sin herramientas declaradas**: no es "todo en orden". El `GuidedOutcome`
  dice que no se evaluó nada, igual que la rama `n_evaluated == 0` de `intake_scan._outcome`.

## 7. Tests

Sobre el motor puro (`tests/test_discovery_brief.py`):

- Determinismo: dos corridas sobre la misma entrada devuelven el mismo orden.
- Regla de causa única: una herramienta con **dos** parámetros faltantes no se acredita a ninguno.
- Fuera de rango: `holding_rate=25.0` sale `rojo` por rango, distinguible de ausente.
- `sin_perfil` es distinto de `rojo`.
- `objection_thresholds` parcial (dos de cuatro `HAT_KEYS`) sale `amarillo` nombrando las
  faltantes, y **no** `rojo`.
- Guard de igualdad de conjuntos: una herramienta con un parámetro fuera del catálogo dispara el
  fallo de QA.

Sobre el job (`tests/test_discovery_brief_job.py`): perfil real escrito en `tmp_path`, etiqueta
genérica, perfil corrupto, CSVs.

Sobre el registro (`tests/test_discovery_brief_tool.py`): que las cuatro declaraciones nuevas
existan, que `newsvendor` respete la prioridad columna > `params` para `goodwill`, y que
`ClientProfile.as_params()` exponga `goodwill` — sin eso la declaración de `newsvendor` sería
insatisfacible desde el perfil.

**Marcador de deuda:** `UNDECLARED_PARAM_TOOLS` — la lista explícita de las herramientas que aún
no declaran, en un test que falla cuando el conjunto real difiere. Mismo mecanismo que
`UNDECLARED_TOOLS` en `tests/test_tool_data_contract.py`. Sin él la cobertura se estanca en
silencio.

## 8. Residual declarado

Esta auditoría dice si el parámetro **está**, nunca si es **verdad**. Un `holding_rate=0.25`
cargado y en rango puede haber sido inventado por el analista; `discovery_brief` no lo puede
detectar y no pretende hacerlo. Es el gemelo del residual "juzga la forma, no la verdad" de
`intake_scan`, y viaja en el deck en cada corrida.

## 9. Lo que este spec deja fuera a propósito

- **Escribir el perfil.** Decidido: la carga sigue siendo un paso deliberado del operador.
- **`salvage_value` de `newsvendor`.** Su cero es defendible; entra cuando haya evidencia de que
  engaña.
- **Las 44 herramientas sin declarar.** Se vacían por tandas, con `UNDECLARED_PARAM_TOOLS` como
  marcador visible.
- **Formulario de descubrimiento** (crux, stakeholders, System of Record). El Plan Maestro lo
  pedía; se descartó por no agregar nada al motor. Si alguna vez hace falta, es otra capacidad.

# nosilo / kern loop

> **Objetivo:** un loop de ingenieria continuo que mantenga viva la integracion
> entre `nosilo_core` (el plano de control agnostico de dominio) y `kern` (el
> agente de cadena de suministro), resolviendo conflictos, depurando fallas y
> mejorando las notas de **baterias** y **capacidades** ciclo tras ciclo, sin
> intervencion humana continua pero sin nunca auto-mergear nada.

## 0. Que problema resuelve

`nosilo_core` evoluciona en `main` de forma independiente: `writeback`, `guided`,
`escalation`, `registry`, `types`, `llm`, `access`, `entities` — todo puede
cambiar en un PR de otra area, y ninguno de esos PRs toca los consumidores en
`scm_agent/` o `jobs/`. El merge de `main` a una rama feature **no marca
conflictos de git** cuando el archivo no fue modificado en la rama, pero el
**codigo de la rama sigue importando de las rutas o APIs del antes** — rompiendose
al importar, o peor: importando pero con un contrato cambiado, degradando el
resultado en silencio.

El loop cierra ese ciclo: detecta el drift, lo mide contra las varas de medicion
externas (bateria de 20 tareas, simworld oracle, capability smoke), propone
fixes mecanicos vía TDD (test que reproduce -> fix -> verify) y deja un reporte
append-only para lo que necesita un juicio humano.

## 1. Arquitectura del loop

```
                     +------------------+
                     |  git: main mueve |
                     |  nosilo_core     |
                     +--------+---------+
                              |
                              v
                +-------------+-------------+
                | Phase 1: DETECT           |
                | - stale imports (paths   |
                |   viejos de src/scm_agent)|
                | - broken imports (API    |
                |   drift en nosilo_core)  |
                | - merge conflicts        |
                | - CI failures            |
                +-------------+-------------+
                              |
                              v
                +-------------+-------------+
                | Phase 2: SCORE            |
                | - bateria (20 tareas)     |
                | - simworld (capture)      |
                | - capability smoke        |
                | - test suite subset       |
                +-------------+-------------+
                              |
                              v
                +-------------+-------------+
                | Phase 3: DIAGNOSE         |
                | mechanical vs judgment-   |
                | heavy (misma regla del    |
                | loop de auto-mejora)      |
                +-------------+-------------+
                              |
                   +----------+----------+
                   |                     |
          mechanical (auto-fix)   judgment-heavy
                   |                     |
                   v                     v
        +----------+----------+          |
        | Phase 4: PROPOSE    |          |
        | - test que reproduce|          |
        | - fix minimo        |          |
        | - verify            |          |
        +----------+----------+          |
                   |                     |
                   v                     |
        +----------+----------+          |
        | Phase 5: REPORT     |<---------+
        | - reporte markdown  |
        | - estado del loop   |
        | - graph memory      |
        | - draft PR (opcional)|         |
        +---------------------+          |
                   |                     |
                   v                     |
        +----------+----------+          |
        | EXCLUSIONES         |          |
        | - writeback.py      |          |
        | - writeback_store   |          |
        | - connectors/       |          |
        | - secrets/billing   |          |
        | - prod config       |          |
        +---------------------+
```

## 2. Fases detalladas

### Phase 1: DETECT — conflictos e integracion

| Chequeo | Mecanismo | Clasificacion |
|---|---|---|
| **Stale imports** | grep de paths viejos (`src.guided`, `scm_agent.registry`, etc.) que `main` movio a `nosilo_core` | mechanical |
| **Broken imports** | `python -c "import <modulo>"` sobre los modulos que consumen `nosilo_core` | mechanical (si el path es el unico problema) / judgment-heavy (si la API cambio) |
| **Merge conflicts** | `git diff --name-only --diff-filter=U` | manual (nunca auto-resuelve) |
| **CI failures** | `gh run list --status failure --branch <current>` | judgment-heavy |
| **Git drift** | `git rev-parse origin/main` vs `merge-base` | informational |

**Paths viejos que moved a nosilo_core** (detallados en HANDOFF.md §2 del
02-ago):

```
src.guided              -> nosilo_core.guided
src.escalation          -> nosilo_core.escalation
src.writeback           -> nosilo_core.writeback
src.writeback_store     -> nosilo_core.writeback_store
scm_agent.registry      -> nosilo_core.registry
scm_agent.types         -> nosilo_core.types
scm_agent.llm           -> nosilo_core.llm
scm_agent.guided        -> nosilo_core.guided
```

Ninguno de estos deberia aparecer como import activo en la rama actual. Si aparece,
es un conflicto silencioso del merge.

### Phase 2: SCORE — baterias y capacidades

**La regla que ordena la fase: un componente que no corrio no puntua, y un
puntaje al que le falta un componente se publica como PARCIAL, con su escala a
la vista.** No es una precaucion teorica: la primera implementacion de este
script publicaba 10.0/10 sin correr una sola medida — el smoke apuntaba a un
`tests/test_smoke.py` inexistente (pytest `rc=4`), el simworld corria `pytest`
sobre un script que no colecta tests (`rc=5`), y la bateria nunca se invocaba.
El puntaje subia por omision. Por eso el loop hoy distingue **"midio y fallo"**
de **"no midio"**, y usa el codigo de salida de pytest y no el texto: la salida
arrastra warnings de pandas con literales como `errors="coerce"`, y buscar
`" error"` en el cuerpo marca como falla una corrida de 5 passed (verificado).

El puntaje va sobre 10 y se compone de tres piezas:

| Componente | Peso | Que mide |
|---|---|---|
| **bateria** | 6.0 | `src/bateria/scorer.py` con `Contexto()` vacio, via `puntuar()`. Hay que importar `src.bateria.comprobaciones` para registrar los checks. |
| **capacidades** | 3.0 | `pytest tests/test_capability_smoke.py` — 5 tests sobre los 49 tools registrados. Binario: el test tiene tabla de bucket esperado por tool, no un piso numerico, asi que "casi pasa" no existe. |
| **higiene** | 1.0 | Los imports viejos de la Phase 1. Es un lint, no el marcador — antes era el 100% del puntaje. |

La bateria se normaliza **contra su techo estructural (75%)**, no contra 100.
Cinco de las 20 tareas estan marcadas TAREA HUMANA, asi que ningun software pasa
de 75%; puntuar sobre 100 dejaria al loop con un maximo alcanzable de 7,5/10
para siempre y el numero no querria decir nada.

**El simworld no puntua.** `run_grid()` se importa de `examples/run_world_sim.py`
y se corre a escala chica con semilla fija, pero su nivel absoluto no dice si
Kern esta bien: a 8 SKUs la captura oscila decenas de puntos entre niveles
(medido sobre el mismo mundo: **-0.446 en L0 y +0.437 en L1**). Lo que si dice,
por ser reproducible, es si el camino de politica cambio. Entra entonces como
**alarma de regresion**: mismo mundo y otra captura fuera de
`TOLERANCIA_CAPTURA` (0.01) levanta un desvio `medium`.

Tendencia: solo se compara contra una corrida con la **misma firma** de
componentes. Un `--check` contra un `--full` daria un delta inventado.

### Phase 3: DIAGNOSE — mechanical vs judgment-heavy

Misma regla que el loop de auto-mejora (`docs/superpowers/specs/2026-07-07-loop-auto-mejora-design.md`):

- **Mechanical**: stale import path, missing import, claramente un rename.
  Se puede escribir un test que reproduce y un fix de una linea.
- **Judgment-heavy**: API drift en `nosilo_core` (firma de funcion cambiada,
  metodo removido, semantica alterada), o un score que bajo por un cambio de
  comportamiento no mecánico. Requiere revision humana.

### Phase 4: PROPOSE — fixes con TDD

`--fix` **propone e imprime, nunca reescribe**. Es una decision, no una etapa
pendiente: un loop que edita imports sin que nadie lea el diff es exactamente el
mecanismo por el que un conflicto silencioso se vuelve permanente.

La exclusion se aplica **aca**, no en DETECT: queremos *ver* el desvio en
writeback o en un conector, solo no queremos que un arreglo automatico lo toque.
Un hallazgo en zona excluida se imprime como `OMITIDO (zona excluida)`.

El operador aplica el fix siguiendo TDD:

1. Escribe un test que reproduzca el problema (`assertRaises(ImportError)` o
   verifica el path correcto).
2. Aplica el fix minimo (un rename de import, o una llamada a la nueva API).
3. Corre el test — debe pasar.
4. Corre `ruff check` sobre los archivos tocados.

### Phase 5: REPORT — append-only

Escribe `documentation/loops/NO_SILO_KERN_REPORT.md` con:
- Timestamp y commit de origen
- Drift de git (main vs HEAD)
- Hallazgos por categoria (conflictos, imports rotos, scores)
- Fixes aplicados con diff minimo
- Items que necesitan revision humana
- Estado de la bateria y simworld (antes/después si hubo cambio)

Guarda estado en `data/loops/nosilo_kern_state.json`. **Se versiona** (a
diferencia de lo que decia este doc: `data/loops/` no esta en `.gitignore`, solo
`data/kaggle/`, `data/state/`, `data/pricing_intel/` y `data/*.sqlite3`), y se
versiona a proposito — la tendencia solo es auditable si el historico viaja con
los commits que la movieron.

Campos de nivel raiz: `last_run_at`, `last_main_commit`, `last_bateria_pct`,
`last_capturas`. Los dos ultimos solo se pisan cuando la medida efectivamente
corrio, para que un `--check` no borre la ultima bateria conocida.

Cada entrada de `runs[]` guarda ademas `firma` (que componentes entraron),
`obtenido`, `escala`, `parcial`, `main_commit`, `bateria_pct` y `capturas`.

La salida del simworld va a `deliverables/simworld-loop/`, que si esta
gitignorado: la corrida es reproducible desde la semilla, no hay que versionar
el artefacto.

### Exclusiones (nunca auto-toque)

- `src/writeback.py` / `src/writeback_store.py` / `nosilo_core.writeback*`
- `src/connectors/` / `nosilo_core.access`
- `src/mcp_keys.py` / secretos
- `webapp/security.py` / `webapp/principal.py`
- `pyproject.toml` / `requirements*.txt` / `Dockerfile`
- Nada bajo `documentation/` que no sea el reporte del loop

## 3. Trigger y schedule

Los modos estan separados por **costo medido**, para que el barato se pueda
correr seguido sin que nadie confunda su puntaje parcial con el completo:

| Invocacion | Que corre | Costo medido |
|---|---|---|
| `--check` | DETECT solo | ~0,2 s → puntaje `1.00/1.0 (PARCIAL)` |
| (sin flag) | + bateria | ~15 s |
| `--full` | + capacidades + simworld | ~77 s |
| `--fix` | propone fixes mecanicos, nunca escribe | se combina con los de arriba |

- **On-demand**: `PYTHONPATH=. python scripts/nosilo_kern_loop.py`
- El reporte se genera **siempre**, en todos los modos (no hay `--report-only`).
- **Exit code**: 1 si hay desvios `critical`/`high`, 0 si no — para que un hook o
  un cron lo pueda usar como gate.
- **Cron** (opcional): el hook `session-start.sh` puede invocar `--check`
  periodicamente; el loop es barato cuando no hay drift.

## 4. Relacion con el loop de auto-mejora existente

Este loop (`NO_SILO_KERN_LOOP.md`) y el
[`2026-07-07-loop-auto-mejora-design.md`](kern-linea/docs/superpowers/specs/2026-07-07-loop-auto-mejora-design.md)
residen en proyectos distintos. El de auto-mejora opera sobre el producto
(`scm_agent/`, `jobs/`, `src/`) minando fallas reales de clientes y CI. Este
loop opera sobre la **integracion nosilo_core ↔ kern** — el punto donde el
control plano se consume. Son complementarios: el loop de auto-mejora no ve
conflictos de merge silencioso entre main y una rama; este loop no mina fallas
de cliente reales. Ambos comparten los mismos principios (nunca auto-merge,
TDD, zonas excluidas, verificacion adversarial, reporte siempre).

## 5. Testing

`tests/test_nosilo_kern_loop.py` — **49 tests, 4,0 s**. Ninguno corre la
bateria, el smoke ni el simworld de verdad (77 s entre los tres): se los
sustituye. Un instrumento de medicion que nadie mide es la falla que este loop
acaba de dejar atras, asi que la suite es parte del entregable, no un extra.

Casi todos los tests tienen la misma forma, porque defienden la misma
propiedad: **separan "no midio" de "midio y fallo"**. Confundirlos *era* el bug.

| Grupo | Que fija |
|---|---|
| Puntaje | Lo que no corrio no suma puntos **ni escala**; los tres pesos suman 10; un `--check` y un `--full` no comparten firma, asi que no se comparan |
| Capacidades | `rc=4` y `rc=5` → `obtenido is None` + `critical` (el bug original); `rc=1` → `0.0`, que consta como corrio; `rc=0` con el warning de pandas encima → puntaje completo (el bug de la primera correccion) |
| DETECT | Un `src.guided` se marca con su numero de linea; un `nosilo_core.guided` no; un consumidor ausente es `high` |
| PROPOSE | Ocho rutas sensibles dan `es_zona_excluida`; un hallazgo ahi se imprime `OMITIDO` y **no** propone el fix; uno fuera si lo propone |
| Estado | Corrupto → se archiva y devuelve vacio; no-objeto → se descarta; sano → se lee |
| Simworld | Desvio > tolerancia → `medium`; identico → nada; bajo tolerancia → nada; sin base previa → registra sin acusar; sin `run_grid` → se reporta y no tumba el loop |
| Contrato | `SIMWORLD_BASE` ⊆ la firma real de `run_grid` — el unico test que importa el modulo de verdad |
| REPORT | Un parcial publica el cartel PARCIAL, a quien le falto y su escala; un completo dice `(completo)` y no dice PARCIAL; el componente ausente sale `no corrio` y nunca `0.00`; sin previo no se inventa tendencia, con otra firma se declara `no comparable`, con la misma da delta; el desvio sale con severidad, ubicacion y arreglo; el de zona excluida se marca solo-reporte; el commit de `main` consta |
| `main()` | `--check` es parcial y sale 0; `--check` **no pisa** `last_bateria_pct` ni `last_capturas` de una corrida cara previa; `--full` compone 7,60/10 y guarda la cifra cruda; un desvio `high` sale 1 y uno `medium` sale 0 |

El contrato es el que mas importa: es la clase de bug que origino todo (el loop
llamando a algo que no existe con la forma que cree). Agarra un rename de
parametro en la firma, no como un `TypeError` a mitad de una corrida de 77 s.

**La suite se verifico por mutacion**, no solo por estar verde: romper el guard
que protege `last_bateria_pct` de un `--check`, y suprimir el cartel PARCIAL del
reporte, hacen fallar exactamente un test cada uno y ninguno mas. Una suite que
pasa a la primera merece esa desconfianza.

Verificado ademas a mano el 2026-08-10, end-to-end:

- `--check` sobre la rama sin drift: `1.00/1.0` PARCIAL, ~0,2 s.
- `--full`: `7.60/10.0`, 0 desvios, exit 0.
- Reproducibilidad del simworld: 4 corridas independientes, capturas identicas
  hasta el ultimo decimal — la propiedad que habilita usarlas como alarma.

Sigue sin cubrir: la corrida real de `--full` (la suite sustituye las tres
medidas caras, asi que nadie prueba que la bateria y el smoke de verdad se
integren — eso solo lo dice correr el loop), y el `--fix` sobre un repo con
imports viejos reales.

`scripts/` no esta en `[tool.coverage.run] source`, asi que esta suite no mueve
el gate de cobertura del 80% en ningun sentido.

## 6. Estado inicial

Re-medido el **2026-08-10** corriendo el loop, no copiado de una corrida previa.
Rama `feat/retencion-de-datos` (`65857fe`), con `main` en **`84f3798`** — el
`d8ba301` que decia este doc es un commit real pero dejo de ser la punta de
`main`, y `feat/retencion-de-datos` ya lo contiene.

| Medida | Valor | Nota |
|---|---|---|
| Bateria | **45,0% (9,0/20)** sobre `Contexto()` vacio | el doc decia 37,5% (7,5/20): subio |
| Techo de la bateria | 75,0% | 5 tareas TAREA HUMANA, derivado de `tareas_humanas()` |
| Cobertura de comprobaciones | 100% | ninguna tarea de software quedo `PENDIENTE` |
| Capability smoke | 5 passed, ~62 s | `tests/test_capability_smoke.py` |
| Simworld `forced-L0` | captura **-0,446** | escala chica, semilla 4 |
| Simworld `forced-L1` | captura **+0,437** | escala chica, semilla 4 |
| **Puntaje del loop** | **7,60 / 10,0** | 3,60 bateria + 3,00 capacidades + 1,00 higiene |

Desglose de la bateria: 8 CUMPLE, 2 PARCIAL, 3 NO_CUMPLE, 2 SIN_DATO, 5
FUERA_DE_ALCANCE.

El `+5.0% (nivel 2)` que este doc daba como captura no se pudo reproducir y no
es comparable con las cifras de arriba: el loop corre niveles 0 y 1 a 8 SKUs,
no el nivel 2. Queda retirado en vez de arrastrado.

Las capturas se reprodujeron identicas en cuatro corridas independientes, que es
la propiedad que habilita usarlas como alarma de regresion.

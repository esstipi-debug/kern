# intake_scan — lector de carpeta cruda con veredicto de aptitud por herramienta

**Fecha:** 2026-07-31 · **Rama:** `feat/intake-scan` (worktree `.wt-intake-scan`, off `origin/main` @ 96dc6f9)
**Estado:** diseño aprobado, cero código escrito.

## Origen

Sub-proyecto 2 de 4 del Plan Maestro SCM-FDE (Fase 2, auditoría de datos). El primero,
`flow_map`, está mergeado y en producción. Quedan después `discovery_brief` (Fase 1) y
`fairness_guard` (Fase 7), cada uno con su propio spec.

## El problema, medido

Verificado contra `origin/main` @ 96dc6f9, no de memoria:

| Hecho | Número |
|---|---|
| Tools registrados | 48 |
| Tools con `requires_data=True` | 43 |
| Tools que declaran **qué columnas** necesitan | 0 |

El requisito de datos de cada herramienta vive enterrado dentro de su `prepare()` y sale a la
luz recién cuando el tool falla, con el cliente esperando. Existe en prosa escrita a mano en
tres lugares distintos —las descripciones de `webapp/mcp_tool_specs.py` (34 tools), los
`PackageInput` de `scm_agent/package_specs.py` (por paquete) y los docstrings de cada job—
pero en ninguno como dato consultable.

`src/data_quality.py` son 63 líneas que validan GTIN y mapean columnas de **un** maestro de
SKU. No existe nada que mire una carpeta entera ni que declare qué se puede correr con lo
que el cliente mandó.

## Qué es intake_scan

Un tool que recibe la **carpeta cruda** del cliente, clasifica cada archivo por su firma de
columnas, le corre cortacircuitos, y emite dos cosas: un semáforo por herramienta y las
columnas faltantes ordenadas por cuántas herramientas desbloquea pedir cada una.

El entregable de venta es la segunda: "pedile la fecha de recepción y habilitás 11
herramientas" es una frase que se dice en una reunión.

## Decisiones de diseño

Cuatro decisiones cerradas, con la alternativa descartada y por qué:

1. **El requisito lo declara el Tool, por tandas.** `required_columns` / `optional_columns`
   en el dataclass `Tool`, con un test que exige la declaración y una lista explícita de
   no-declarados que arranca con 39 nombres y se vacía con el tiempo (precedente:
   `_EXPECTED_ZERO_TOOLS` en las citas). Descartada una tabla tool→columnas dentro de
   `intake_scan`: sería una segunda fuente de verdad que se desincroniza en silencio, la
   misma clase de bug que ya mordió tres veces con los conteos de tools y el doc del MCP.
2. **Clasificación por firma de columnas**, reusando `detect_columns` de `jobs/intake.py`.
   Determinista y testeable. Descartado el LLM: mete no-determinismo en la capa que audita
   el dato, que es el último lugar donde lo querés. Descartada la heurística por nombre de
   archivo: optimiza para carpetas prolijas, y los nombres reales son `Libro1 (3).xlsx`.
3. **Semáforo con la columna nombrada, sin score numérico.** Un 73 no le dice a nadie qué
   hacer. Descartado también el score global de carpeta: promedia herramientas sin relación
   y esconde justo lo que importa, que tenés dato de sobra para pricing y cero para servicio.
4. **Primera tanda = los 4 tools del Diagnóstico de Arranque** (`data_quality`, `abc_xyz`,
   `excess_obsolete`, `financial_kpis`). Es el paquete de primer contacto, o sea el momento
   exacto en que `intake_scan` sirve, y su contrato de entrada ya está escrito en
   `PackageInput`, así que las declaraciones se validan contra algo existente en vez de
   inventarse. Descartado arrancar con los 33 de paquetes: un error de declaración se
   esconde fácil entre 32 correctos.

## Contrato de entrada

`data_path` apunta a una **carpeta**, no a un archivo — el primer tool de Kern que recibe un
directorio. Lee `.csv`, `.xlsx`, `.xls`; cualquier otra extensión se lista como ignorada con
su nombre. Carpeta vacía o sin archivos legibles ⇒ `needs_data` diciendo qué encontró.

### Perfiles de dataset (primera tanda)

Derivados de lo que `PackageInput` del Diagnóstico ya declara:

| Perfil | Columnas obligatorias | Opcionales |
|---|---|---|
| `maestro` | `sku` | `name`, `gtin`, `unit_cost` |
| `ventas` | `date`, `product_id`, `quantity` | `unit_cost` |
| `stock` | `product_id`, `on_hand`, `daily_demand` | `unit_cost`, `days_since_last_sale` |
| `finanzas` | `product_id`, `cogs`, `avg_inventory_value` | `gross_margin`, `units_sold`, `net_sales` |

Un archivo pertenece a un perfil si matchea **todas** sus obligatorias. Si matchea dos
perfiles, o ninguno, sale como `sin_clasificar` con el detalle de qué le faltó. Nunca se
elige el "más parecido".

## Arquitectura

Tres módulos nuevos, chicos y testeables por separado:

- **`src/intake_scan.py`** — motor puro, sin I/O: perfiles, clasificación, cortacircuitos,
  cómputo del semáforo y del ranking.
- **`jobs/intake_scan_job.py`** — recorre la carpeta, lee con pandas, corre el QA gate,
  escribe entregables.
- **`scm_agent/registry.py`** — el `Tool` dataclass gana `required_columns` y
  `optional_columns`. Más las 4 declaraciones de la tanda inicial en `scm_agent/tools.py`.

## Cortacircuitos

Cinco, por archivo. Cada uno emite un hallazgo con **conteo y fracción**, nunca un booleano:
"3 de 4.812 filas", no "hay duplicados".

1. Fechas imposibles: no parseables, o fuera de una ventana declarada.
2. Cantidades negativas donde el perfil exige cantidad.
3. Filas duplicadas exactas.
4. Cobertura por columna canónica (% de celdas pobladas).
5. Huérfanos cruzados: `product_id` presente en ventas/stock/finanzas y ausente del maestro.
   Solo corre si el maestro se clasificó; si no, se declara **no evaluado**.

### Cuáles bloquean

Un cortacircuito es **bloqueante** cuando su fracción supera su umbral declarado. Todos los
umbrales son configurables por `params`:

| Cortacircuito | Umbral por defecto | ¿Bloquea? |
|---|---|---|
| Fechas imposibles | 1% de filas | sí |
| Cantidades negativas | 1% de filas | sí |
| Duplicados exactos | 5% de filas | sí |
| Huérfanos cruzados | 5% de claves | sí |
| Cobertura por columna | 50% poblada | no — actúa vía la regla de presencia de abajo |

Un archivo con un cortacircuito bloqueante disparado no puede sostener un `verde`: las
herramientas que dependen de él caen a `rojo` nombrando el cortacircuito, no la columna.

### Presencia no es tener la columna

Una columna que existe pero está vacía no cuenta como presente. El umbral de cobertura
arranca en **50%**, configurable. Por debajo, el reporte dice "presente pero 8% poblada", no
"faltante" — son dos problemas distintos y el cliente los arregla distinto.

## Veredicto

### Cuatro estados por herramienta

| Estado | Cuándo |
|---|---|
| `verde` | todas las obligatorias presentes en un archivo clasificado que pasó los cortacircuitos bloqueantes |
| `amarillo` | obligatorias completas, falta al menos una opcional — se nombra cuál y qué se pierde |
| `rojo` | falta al menos una obligatoria — se nombra |
| `no_declarado` | el tool no declaró columnas |

El cuarto estado es lo que impide la mentira más fácil de este diseño: **un tool sin declarar
no puede salir verde por omisión**. El día uno el reporte muestra 4 herramientas con veredicto
y 39 declaradas explícitamente como no evaluables. Eso es correcto y hay que mostrarlo con
numerador y denominador siempre, para que la cobertura parcial se lea como estado y no como
veredicto.

### El ranking (las opciones ejecutables)

Por cada columna canónica faltante se cuenta cuántas herramientas declaradas pasarían de
`rojo` a `verde`/`amarillo` si apareciera, y se ordena descendente. La primera es la
recomendada. Empates por nombre, para que dos corridas sobre el mismo dato den el mismo orden.

Si todo sale verde no hay opciones que ofrecer, y forzar un menú vacío rompe el contrato de la
capa guiada. Ahí el resultado es **ejecutado**: la auditoría corrió y no hay acción pendiente,
con el residual diciendo qué no se evaluó.

## QA gate

Veta la entrega si:

1. no se clasificó ningún archivo;
2. más de la mitad de los archivos legibles quedaron sin clasificar (el escaneo no entendió
   la carpeta, y decirlo es más honesto que reportar sobre el resto);
3. una herramienta figura `verde` apoyándose en un archivo que falló un cortacircuito
   bloqueante;
4. el ranking incluye una herramienta `no_declarado`.

## La defensa contra la deriva

Una declaración que dice una cosa y un `prepare()` que hace otra es el modo de falla obvio de
todo este diseño. La defensa no es revisar con cuidado: por cada tool declarado, un test
genera un CSV al que le falta **una** columna obligatoria por vez y afirma que su `prepare()`
real falla.

Cubre la dirección peligrosa. Si alguien declara una columna que el tool no necesita, el test
pasa y no prueba nada; si alguien omite una que sí necesita, el test la caza — y esa omisión
es la que produce un verde falso.

## Seguridad: por qué NO entra al MCP

`intake_scan` recibe una carpeta y lee todo lo que hay adentro. En la superficie MCP anónima
eso es una **primitiva de lectura arbitraria de archivos** — el mismo agujero que hubo que
tapar en `org_friction` con un guard sobre los parámetros `*_path`.

Este tool **no entra al MCP** en este PR, declarado en la lista de "qué no está acá todavía"
de `docs/MCP_SERVER.md` con esa razón. Misma clase de exclusión honesta que ya tienen los
tools de writeback y `warehouse_layout`. En consecuencia el spec MCP sale de los lugares
fijos y el conteo de la superficie (34) no se toca.

## Numeración

El registro tiene **48 tools** a la fecha (`flow_map` #47, `cross_impact` #48). `intake_scan`
sería el **#49**, pero el número se deriva corriendo el registro al momento de implementar,
no se asume — dos ramas concurrentes ya chocaron por esto esta semana.

## Citas L3

Anclas medidas antes de fijarlas, con el mismo techo de cierre a 2 saltos (~40%) que usó
`flow_map`. **Caveat que va escrito en el comentario:** `filter_citations` no está en el
camino del orchestrator (`orchestrator.py:193` cita sin gate), así que medir contra el gate no
predice las citas que ve el cliente.

## Fuera de alcance, a propósito

- Declarar las columnas de los 39 tools restantes. Es trabajo por tandas, cada una su PR.
- Exponer `intake_scan` por MCP (ver Seguridad).
- Reparar el dato: `intake_scan` diagnostica y prioriza, no limpia.
- Inferir el perfil de un archivo con el LLM.

## Riesgo principal

El valor crece con la cobertura de declaraciones y arranca en 4 de 43. El primer reporte le
dice a un cliente "puedo evaluar cuatro herramientas y de las otras 39 no sé". Es honesto y es
poco.

La mitigación no es inflar la tanda inicial —ahí es donde se cuelan las declaraciones mal
verificadas— sino que el numerador y el denominador viajen siempre juntos en el reporte.

# Brechas de capacidad de Kern (fuente #28 del grafo de libros)

Ocho capacidades que Kern **no** tiene hoy, cada una anclada por un lado a un
concepto de teoría que ya existe en el grafo de libros y por el otro al sitio de
código donde aterrizaría. Es la contraparte negativa del
[atlas de sombreros](KERN_ATLAS_SOMBREROS_SCM.md) (fuente #26): ese documento
dice qué cubre Kern, éste dice qué no.

**Documento interno. Nunca es cita para un cliente.** Su basename está en
`scm_agent/knowledge.py::_INTERNAL_SOURCES` y `ground_citations_detailed` lo
filtra en sus dos entradas (el hit de `search` y el de `advise`). El test que lo
prueba es `tests/test_capacity_gaps_graph.py`. La razón es la misma por la que
el atlas está en esa lista: un documento propio no puede aparecer como cita al
lado de Chopra & Meindl.

Es un documento de **build time**, no de query time. Se ingiere con
`scripts/merge_capacity_gaps.py` en un PR revisado. Esa es exactamente la línea
que trazó el PR #186 al desconectar `scm_agent/graph_memory.py`: escribir nodos
en el grafo de citas *durante una consulta* es la perturbación que el barrido de
pool de citas existe para prevenir. Escribirlos en un merge revisado y
commiteado no lo es — es como entraron las 27 fuentes anteriores.

Estado del código verificado contra el commit `47b7e1a` (main).

## Cómo leer cada ficha

- **Teoría** — id real de un nodo que ya existe en `knowledge/scm-books/graph.json`.
  Cuando el ajuste es fuerte la arista es `cites`; cuando es moderado es
  `conceptually_related_to`. Cuando no existe ningún concepto que ancle de
  verdad, la ficha lo dice y no hay arista. No se inventó ningún nodo de teoría.
- **Código** — `archivo::símbolo` que existe hoy, con su línea al commit `47b7e1a`.
- **Cerrada cuando** — una prueba observable, no una intención.

---

## Brecha 1 — Bucle ReAct sobre las 50 herramientas

**Teoría:** `knowledge::iterative_reevaluation_loop` — "Iterative Re-Evaluation
Loop" (Khan, *Supply Chain Resilience*, comunidad 48). Ajuste **fuerte**: su
racional es casi literal — una vez removido el primer cuello de botella, una
segunda corrida expone las siguientes restricciones. Arista `cites`.
Secundario: `knowledge::pdca_cycle` (Heizer, c94), el ciclo cerrado canónico.

**Código:** `scm_agent/orchestrator.py::Orchestrator.run` (línea 79) — la línea
`result = self._run(request, Path(out_dir))` es la única invocación, sin
condición y de una sola pasada, de todo el pipeline
classify -> prepare -> run -> qa -> deliver.

**Qué falta.** `intent.classify` devuelve un solo `job_type` (`IntentResult`,
`intent.py:20-26`; el prompt dice "Pick the single best capability"). `_run`
(`orchestrator.py:89-233`) es estrictamente lineal y cada falla es un `return`
temprano: nada realimenta la salida de una herramienta al ruteo. Los paquetes
(`packages.py:341`) iteran una tupla congelada de pasos.

**Riesgo de alcance que hay que decir.** El protocolo `LLMProvider`
(`nosilo_core/llm.py:21-23`, dependencia externa pineada) expone sólo
`available()`, `complete(prompt)` y `extract(prompt, schema)`. **No hay
superficie de tool-calling en la abstracción de LLM.** El bucle hay que
construirlo determinísticamente sobre `extract()`, o extender `nosilo_core`,
que no vive en este repo.

**Segunda entrada con la misma forma:** `scm_agent/event_intent.py::handle_event`
(:377-439) resuelve una ruta y hace exactamente un `orch.run(...)` (:425). Es la
superficie reactiva del sistema y reacciona una vez y para.

**Cerrada cuando** una prueba maneja `Orchestrator.run` con un solo brief y
verifica que se ejecutó más de una herramienta del registry en esa llamada, con
la clave o los params de la segunda derivados del `report`/`qa_issues` de la
primera — de modo que cambiar la salida de la primera cambia cuál corre segunda.

---

## Brecha 2 — Reranker en la recuperación

**Teoría:** **ninguna.** No existe en el grafo (3002 nodos) ningún concepto
sobre ordenar documentos recuperados por relevancia, ni sobre una búsqueda de
dos pasadas recall-barato -> precisión-cara. Se revisaron y se rechazaron por
forzados: `knowledge::abc_classification` y `knowledge::pareto_law` (asignar
esfuerzo a los pocos valiosos: la forma del racional, no el mecanismo),
`knowledge::supplier_scoring_assessment`, `knowledge::multi_criteria_analysis`
y `knowledge::factor_rating_method` (scoring multicriterio de alternativas de
negocio, no de texto recuperado), `knowledge::product_development_system`
(stage-gate de productos). **Esta brecha no tiene ancla de teoría y no se le
fabricó una.** Queda anclada sólo al código.

**Código:** `scm_agent/knowledge.py::KnowledgeBase.ground_citations_detailed`
(línea 343) — el único chokepoint de citas.

**Qué falta, con dos correcciones al planteo original.** No hay *una* tubería
sino **dos, y no se tocan**:

1. La tubería de **citas** es IDF pura. `ground_citations_detailed` (:343)
   llama en :372 a `self.search(...)`, el keyword search por IDF —
   nunca a `search_semantic` ni a `search_hybrid`.
2. La tubería de **embeddings** (`search_semantic` :433, `search_hybrid` :470)
   se alcanza sólo desde `bridge()` (:579) y `examples/query_knowledge.py:55`.
   `knowledge.py:575-578` lo dice explícitamente: "This is a discovery surface
   only — the client-facing citation path ... is untouched."

Además `fastembed` es el extra opt-in `[rag]` (`pyproject.toml:91-93`), ausente
de `requirements.txt` y excluido a propósito de `requirements-dev.txt` (:46-52).
**En producción y en CI no hay embeddings en absoluto**: `embeddings.py:73-75`
corta temprano y el índice nunca se construye.

**Costo real de cerrarla.** La clase `TextCrossEncoder` importa bien en
fastembed 0.8.0 y `list_supported_models()` devuelve 6 modelos, pero **no hay
pesos descargados**: el único snapshot en el cache es el embedder
`bge-small-en-v1.5-onnx-q`. La primera instanciación baja entre 80 MB
(`Xenova/ms-marco-MiniLM-L-6-v2`) y 1,11 GB, más inferencia CPU por consulta —
el mismo argumento de costo que ya mantiene al embedder fuera de CI.

**Cerrada cuando** el orden de candidatos de
`ground_citations_detailed(tool.intent_keywords, tool.title, limit=16)` difiere
medible del orden IDF puro con el reranker activo, es byte-idéntico al de hoy
cuando el reranker o sus pesos faltan, y una prueba fija un falso amigo conocido
que el reranker baja del top-5.

---

## Brecha 3 — MCP de escritura con tiers

**Teoría:** `knowledge::price_decision_authority` — "Price Decision Authority"
(Simon, *Price Management*, comunidad 17). Ajuste **moderado** y se dice como
tal: su racional, "asignar los derechos de descuento y precio entre la gerencia
y la fuerza de ventas", es el único nodo de los 3002 que codifica el mecanismo
que la brecha necesita — delegación acotada de derechos de decisión. Arista
`conceptually_related_to`, no `cites`. Secundario:
`knowledge::proactive_exception_handling` (Khan, c51), que es la justificación
de que un tier corra desatendido y sólo escale la excepción.

**Código:** `webapp/mcp_server.py::build_mcp_server` (línea 172).

**Qué falta.** Las dos mitades existen y no se hablan. `webapp/mcp_tool_specs.py`
expone **35 de las 50** herramientas del registry (contado por import) y excluye
todo writeback a propósito: `webapp/mcp_server.py:3-7` nombra
`odoo_replenishment` y `excel_replenishment` como deliberadamente no expuestas,
y `strip_filesystem_params` (:98) borra todo override `*_path` antes de que los
params lleguen a un job. Del otro lado, `scm_agent/autonomy.py` ya sabe
auto-aplicar un changeset reversible y degradar el irreversible a HANDOFF.

**Doctrina vigente:** `docs/MCP_SERVER.md`, sección "What's NOT here yet". Si la
propuesta la cambia, se cambia ahí también.

**Cerrada cuando** un cliente MCP que llama una herramienta de writeback sobre
`/mcp` con un changeset reversible recibe EXECUTED con el store efectivamente
mutado y una fila de ledger, y la llamada idéntica que produce un changeset
irreversible recibe HANDOFF con un id que `POST /api/approvals/{id}`
(`webapp/app.py:2163`) puede completar.

---

## Brecha 4 — RAG del cliente, no sólo del oficio

**Teoría:** `knowledge::supply_contract` — "Supply Contract" (Chopra & Meindl,
cap. 15 *Sourcing Decisions*, comunidad 1). Ajuste **fuerte**: es exactamente el
objeto que la brecha dice que no se puede recuperar. Arista `cites`.
Secundarios en la misma comunidad: `knowledge::quantity_flexibility_contract`
(el más literal para "¿esta compra cumple el contrato?"),
`knowledge::supply_chain_contracts`, y `knowledge::contract_redesign`
(Khan, c110) que es el único nodo que cubre la mitad SLA.

**Código:** `scm_agent/knowledge.py::KnowledgeBase.__init__` (línea 164) — el
`for name, path in (("books", ...), ("code", ...))` de :171 es donde el conjunto
de grafos está fijado a dos.

**Qué falta.** No hay corpus por cliente. `src/client_profile.py` guarda sólo
campos escalares o estructurados y su propio docstring (:8-10) se desmarca del
rol: "Not a connector to a client's own system of record". Así que "¿esta compra
cumple el contrato?" es incontestable.

**Frontera dura.** El dato del cliente no se mezcla con el corpus del oficio ni
alimenta el análisis de otro cliente. Hoy **nada la hace cumplir en
recuperación**, pero ya hay dos precedentes de la misma frontera en otras capas:
`orchestrator.py:50-53` (pasar `None` deshabilita el lookup de perfil, "the right
setting for multi-tenant surfaces where `client` is a caller-supplied display
label, not an authenticated identity"), cableado en `webapp/app.py:178-186`; y
`src/client_profile.py:77-86` `is_generic_client_label`.

**Cerrada cuando** dos `KnowledgeBase` construidos para clientes distintos, cada
uno con una cláusula de contrato distinta, responden distinto a "¿se permite una
compra de N unidades?" citando cada uno su propia cláusula, mientras una sonda
cruzada (el texto de la cláusula de A contra la instancia de B, y contra la ruta
de citas al cliente) no devuelve nada.

---

## Brecha 5 — Fan-out paralelo

**Teoría:** `knowledge::assembly_line_balancing` — "Assembly-Line Balancing"
(Jacobs & Chase, comunidad 35). Ajuste **fuerte**: asignar tareas con
precedencias a estaciones es literalmente el problema de esta brecha, y las
precedencias son justo lo que la gobierna. Arista `cites`. Secundarios:
`knowledge::bottleneck_time` (Heizer, c154 — el techo de speedup del fan-out) y
`knowledge::theory_of_constraints` (c41 — el lente para lo que pasa después).

**Por qué no BPR, que calza mejor en la prosa.**
`knowledge::business_process_reengineering` (Christopher, c126) dice textualmente
que se reforman los procesos "often by running activities in parallel", y fue el
ancla original. Se cambió porque **no es durable**: Christopher está en
`scripts/merge_l3_rebuild.py::REPLACE_STEMS`, y un rebuild real de la fuente
borra ese nodo y con él la arista — medido, no supuesto (ver el reporte del PR).
Un ancla a un libro que el rebuild re-extrae es un puente que se corta solo. El
test `test_theory_anchors_are_rebuild_durable` ahora lo impide para las ocho.

**Código:** `scm_agent/packages.py::run_package` (línea 271).

**Qué falta.** Cero primitivas de fan-out en `scm_agent/`, `jobs/` y `src/`. El
único `ThreadPoolExecutor` del repo está en `webapp/app.py:1950` con
`max_workers=1` — un serializador, no un fan-out. Y `src/forecasting_auto.py:141`
fija `StatsForecast(..., n_jobs=1)`, un opt-out explícito del paralelismo de una
dependencia, en un módulo por el que pasa el paso `forecast` de todo paquete
grande.

**Cuatro correcciones que cambian el diseño del arreglo.**

1. La fase 1 **no es libre de efectos**: es libre de escrituras a `out_root`. Sí
   escribe CSVs al tempdir (`packages.py:224` y `:235`) y hace **I/O de red por
   paso** (`packages.py:493` llama `narrative_rewrite` -> `provider.complete()`).
   El comentario del propio código en `:338` dice lo correcto: "compute
   everything; write nothing to out_root".
2. Por eso mismo el fan-out paga (el LLM domina la latencia por paso) y por eso
   mismo su concurrencia hay que acotar.
3. **Lo que gobierna la paralelizabilidad no es el diferimiento de escrituras
   sino el grafo de dependencias entre pasos, y no está vacío.** `reports` se
   acumula en `packages.py:352` y se consume en `:221` (`step.derive`) y `:449`
   (`step.derive_params`). Hay dos aristas de precedencia reales:
   `cycle_count` deriva de `abc_xyz` (`package_specs.py:350`, cuerpo en
   `:217-224`, `reports["abc_xyz"]` — KeyError duro si falta) y
   `excel_replenishment` deriva sus params de `inventory_optimization`
   (`package_specs.py:349`). Un ThreadPool ingenuo sobre `spec.steps` rompe
   `cycle_count`.
4. El fail-fast de media iteración (`packages.py:353-360`) hoy impide que los
   pasos posteriores corran. Bajo fan-out ya habrían corrido — quemando tokens
   y, en `odoo_replenishment`, pegándole a un Odoo vivo. Es un cambio de
   comportamiento que hay que decidir, no un beneficio gratis.

**Cerrada cuando** correr SCALE (35 pasos) con W>1 produce un `PackageResult`
con los mismos `steps`, `deliverables` y estados de QA que la corrida serial
sobre el mismo intake — con `abc_xyz` todavía antes de `cycle_count` — mientras
el makespan medido cae de la suma de tiempos hacia el máximo.

---

## Brecha 6 — MCP como cliente, no sólo como servidor

**Teoría:** `knowledge::data_integration` — "Data Integration" (Khan, comunidad
84). Ajuste **fuerte**: su racional habla de automatizar el flujo de datos entre
actores "eliminating the need to retrieve information from multiple sources".
Arista `cites`. Secundario: `knowledge::electronic_data_interchange`
(Christopher, c55), el análogo estructural histórico exacto.

**Código:** `src/connectors/__init__.py::InventorySource` (línea 60) — el
Protocol compartido que una fuente MCP tendría que satisfacer.

**Qué falta.** Kern publica herramientas y no consume ninguna: el grep de
`mcp.client` / `ClientSession` / `stdio_client` / `streamablehttp_client` /
`from mcp import` en código de producto da cero.

**Tres precisiones.** (a) El `.mcp.json` de la raíz **no** hace a Kern cliente de
nada: configura graphify, codegraph y serena para el asistente del
desarrollador, y ningún código de runtime lo lee. (b) `src/connectors/` no son
tres conectores sino **21 archivos / 7.401 líneas** — odoo (2.124), shopify
(3.178), `excel.py`, un subpaquete `browser/` de automatización CDP (1.004),
ledger, simulator, emulator, http_client, replenish. La superficie de
mantenimiento a mano es ~2,4x lo que sugiere "odoo, shopify, meli". (c) MeLi
está partido: `src/connectors/meli_prices.py` (220 líneas) es sólo el writeback
de precio del vendedor; el lado de lectura de competencia vive fuera, en
`src/pricing_intel/acquire/meli_api.py`.

**La brecha es más chica de lo que parece:** la mitad cliente de la dependencia
ya está instalada y sin usar — `mcp` 1.28.1 trae `mcp/client/` completo
(`session.py`, `sse.py`, `stdio/`, `streamable_http.py`, `websocket.py`) y
`pyproject.toml:79-81` ya pinea `mcp>=1.0,<2`. No hace falta dependencia nueva
ni tocar el cap.

**Cerrada cuando** una prueba construye una implementación de `InventorySource`
respaldada por una sesión cliente MCP contra un servidor stub, pasa
`isinstance(source, InventorySource)`, y obtiene `list_products()` /
`inventory_levels()` / `orders()` no vacíos cuyos valores provienen
demostrablemente del servidor remoto — sin HTTP ni XML-RPC nuevo por proveedor.

---

## Brecha 7 — Onboarding self-serve

**Teoría:** `knowledge::digitalization_of_supplier_data_collection` (Khan,
comunidad 14). Ajuste **moderado**: su racional — "las actividades manuales
durante la actualización de la base son el punto débil reconocido y se van a
remover con más digitalización" — es estructuralmente la misma frase que la
brecha. Arista `conceptually_related_to`. Secundario:
`knowledge::supplier_onboarding_scf` (c68), que nombra el onboarding como la
restricción que ata el tiempo de puesta en marcha.

**Código:** `src/mcp_keys.py::McpKeyStore.issue` (línea 67).

**Qué falta, y es más angosto que "no hay self-serve".** Ya existe un embudo
self-serve sin operador: `GET /demo` (`webapp/app.py:2381`) y
`POST /api/demo-scan` (`webapp/app.py:874`, explícitamente público en `:882`)
toman un email más un CSV/XLSX y devuelven hallazgos y un workbook. Lo que no
tiene ruta automática es **la credencial MCP paga**.

**El bloqueo real está aguas arriba:** el billing es un stub.
`webapp/billing.py:6-7` dice "it does NOT call the Stripe API";
`create_checkout_session` (:15) sólo resuelve una variable `STRIPE_LINK_<SLUG>`
configurada a mano y degrada a mailto. No hay handler de webhook de Stripe en
todo `webapp/`, así que hoy no existe el evento que dispararía un `issue()`
automático. Y `issue(client_name)` liga la key a nada más que un nombre libre —
sin email, sin id de pago, sin expiración, sin cuota (esquema en `:56-64`):
darle self-serve es una migración de esquema, no una ruta nueva.

**Respetar la razón de la exclusión.** `intake_scan` está fuera del MCP porque
lee archivos arbitrarios del disco del servidor
(`webapp/mcp_tool_specs.py`; `strip_filesystem_params` existe por lo mismo). Esa
razón no la cambia el self-serve: la vuelve más importante.

**Cerrada cuando** una prueba maneja una ruta HTTP pública de punta a punta sin
acción de operador — la llamada de alta devuelve una key `lpk_` en claro, y un
`POST /mcp/` inmediato con esa key en `X-API-Key` devuelve 200 en vez del 401 que
hoy emite `webapp/mcp_auth.py:61` — y `McpKeyStore.issue` tiene un llamador
dentro de `webapp/` y no sólo en `examples/issue_mcp_key.py:36`.

---

## Brecha 8 — Crítico sobre el narrativo

**Teoría:** `knowledge::forecast_value_added` — "Forecast Value Added (FVA)"
(Gilliland, comunidad 4). Ajuste **fuerte**: su racional en el grafo compara
cada paso del proceso contra un benchmark ingenuo para identificar y eliminar
actividades que no mejoran — o que empeoran — el resultado. `narrative_rewrite`
es exactamente un paso no medido montado sobre un benchmark ingenuo que ya
existe en el código: `base_summary` es literalmente lo que se devuelve cuando el
paso está ausente (`nosilo_core/llm.py:153`, `:175`). Arista `cites`.
Secundario: `knowledge::judgmental_adjustment` (c4) — ediciones humanas sobre un
pronóstico estadístico que "a menudo destruyen valor".

**Código:** `scm_agent/orchestrator.py::Orchestrator._narrative` (línea 244).

**Qué falta.** El gate de QA revisa números: `jobs/qa.py:1-5` lo dice en su
docstring y `verify` (:55) chequea sólo invariantes aritméticas y
estructurales. Ninguna de las 57 funciones `verify` del repo toma una cadena de
prosa. Después del gate, la prosa se reemplaza entera sin post-chequeo:
`orchestrator.py:205` corre QA, `:226` llama `_narrative`, `:251` llama
`narrative_rewrite`. Dentro de `narrative_rewrite` las únicas guardas son
disponibilidad del provider, un `try/except` ancho y
`return text.strip() or base_summary` — un chequeo de vacío. El prompt pide
"Keep every number" pero nada verifica que lo haya hecho. Prueba de ello:
`tests/test_scm_agent.py:586-592` pasa con `res.status == "ok"` y
`res.summary == "Upgraded narrative."`, una cadena sin un solo número.

**Hay dos sitios, no uno.** Un crítico agregado sólo en el orquestador deja la
ruta de paquetes sin revisar: `scm_agent/packages.py:493` hace la misma llamada
dentro de `_run_step`. Y `packages.py:486-490` ya documenta el precedente del
modelo de severidad correcto: un filtro de contenido **que no vetea** (el gate
de citas).

**Precedente que estudiar, no copiar.** El brief apunta a
`.claude/agents/auditor-de-procedencia.md` del repo nosilo — existe, 228 líneas,
y su regla definitoria es que **reporta y nunca reescribe**, justificada en tres
pasadas de auditoría sobre 62 borradores donde cada arreglo introdujo
afirmaciones nuevas. Pero hay un precedente **más cercano, en Python, dentro de
Kern**: `src/seo/pdp_writer.py::enhance_with_llm` (:512) post-chequea la prosa de
su LLM y revierte al template si falla — separa campos blandos de
`PROTECTED_CLAIM_FIELDS` (:522-523) y re-verifica en `:545-549` que el nombre del
producto sobrevivió la reescritura. Su docstring (:519-520) dice que "mirrors
nosilo_core.llm.narrative_rewrite's contract". El patrón ya está en el repo para
una superficie y falta en ésta.

**Nota adyacente que conviene arreglar:** `documentation/legal/dpa-lite.md:72`
cita `scm_agent/llm.py::narrative_rewrite` como el lugar donde los resúmenes de
cliente se mandan a Anthropic. Ese archivo no existe: la función se mudó al
paquete externo `nosilo_core`.

**Cerrada cuando** una prueba que inyecta un provider que devuelve un resumen
que borra o muta una cifra presente en el `base_summary` determinístico hace que
la corrida caiga de vuelta a `base_summary` o exponga un issue del crítico —
donde hoy `tests/test_scm_agent.py:586` pasa en verde.

---

## Tabla de anclaje

| # | Brecha | Teoría (id real) | Ajuste | Arista | Código |
|---|--------|------------------|--------|--------|--------|
| 1 | Bucle ReAct | `knowledge::iterative_reevaluation_loop` | fuerte | `cites` | `scm_agent/orchestrator.py::Orchestrator.run` |
| 2 | Reranker | — *(no existe ancla)* | — | — | `scm_agent/knowledge.py::KnowledgeBase.ground_citations_detailed` |
| 3 | MCP de escritura con tiers | `knowledge::price_decision_authority` | moderado | `conceptually_related_to` | `webapp/mcp_server.py::build_mcp_server` |
| 4 | RAG del cliente | `knowledge::supply_contract` | fuerte | `cites` | `scm_agent/knowledge.py::KnowledgeBase.__init__` |
| 5 | Fan-out paralelo | `knowledge::assembly_line_balancing` | fuerte | `cites` | `scm_agent/packages.py::run_package` |
| 6 | MCP como cliente | `knowledge::data_integration` | fuerte | `cites` | `src/connectors/__init__.py::InventorySource` |
| 7 | Onboarding self-serve | `knowledge::digitalization_of_supplier_data_collection` | moderado | `conceptually_related_to` | `src/mcp_keys.py::McpKeyStore.issue` |
| 8 | Crítico del narrativo | `knowledge::forecast_value_added` | fuerte | `cites` | `scm_agent/orchestrator.py::Orchestrator._narrative` |

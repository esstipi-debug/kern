# La ruta de citas: léxico vs. híbrido — medido, y descartado

> Medición del 2026-08-09 sobre el grafo de libros committeado. Reproducible con
> `PYTHONPATH=. python scripts/measure_citation_retrieval.py [reemplazo|union]`
> (requiere el extra `rag`). **Veredicto: no se aplica.**

## La hipótesis

`scm_agent/knowledge.py:368` — `ground_citations_detailed()` arma su pool de
candidatos con `self.search(...)`, que rankea por solapamiento de tokens ponderado
por IDF: léxico puro. En el mismo archivo existen `search_semantic()` (embeddings
`bge-small`, 3.002 nodos indexados) y `search_hybrid()`, y la ruta de citas no usa
ninguno.

Tres síntomas documentados apuntaban a que eso era el defecto:

1. `vehicle_routing` citando *"Logistics as the Vehicle for Change"* — un capítulo
   sobre cambio organizacional, no sobre vehículos. Falso amigo léxico puro.
2. El `_CANDIDATE_POOL` de `scm_agent/packages.py:76` subió 3 → 6 → 8 → 16
   parchando recall.
3. Tres regresiones donde ingerir un libro nuevo puso citas de otras tools en
   cero, porque etiquetas nuevas mueven el IDF y desplazan las anclas.

La hipótesis era razonable y tenía un mecanismo plausible a favor: como el
re-ranking posterior sigue siendo IDF y el gate (anclas / `MAX_HOPS` /
`MIN_CITATIONS`) queda intacto, lo semántico solo cambiaría **de dónde salen los
candidatos**. Recall gratis sin perder precisión.

## El método

Se corrió `_step_citations` — la ruta real de producción, la misma que usa
`tests/test_packages_citations.py` — sobre **las 49 tools ancladas y registradas**,
tres veces. El gate no se tocó en ninguna.

| Corrida | Cómo se arma el pool de 16 |
|---|---|
| **antes** | `KnowledgeBase.search` (IDF léxico). Es la ruta de hoy |
| **reemplazo** | `search_hybrid`: lo semántico compite por los mismos 16 slots |
| **unión** | lo léxico se conserva entero y lo semántico se **agrega** al final |

Las dos variantes existen porque un swap directo confunde dos preguntas
distintas: *¿ayuda el recall semántico?* y *¿conviene sacrificar slots léxicos por
él?*. La variante unión aísla la primera.

Las 49 se clasifican como los tests las fijan: **40 deben citar** (≥2 citas), 4 con
**cero intencional** (`data_quality`, `cycle_count`, `whatif`, `earned_value`) y 5
**anchor-islanded** (`dea`, `learning_curve`, `slotting`, `vehicle_routing`,
`org_friction_audit`).

## Los números

| | antes | reemplazo | unión |
|---|---|---|---|
| Citas totales | **116** | 114 | **116** |
| Tools "debe-citar" en cero | 0 | **3** | 0 |
| Tools que ganan citas | — | 3 | **0** |
| Tools que pierden citas | — | 1 | 0 |
| Tests que se romperían | — | **5** | **0** |

### Variante reemplazo: peor, y peor de la forma que importa

Tres tools que hoy citan bien caen a **cero**:

| Tool | Lo que pierde |
|---|---|
| `ddmrp` | *Demand Penetration Point (Decoupling Point)* · *Customer Order Decoupling Point* · *Drum-Buffer-Rope* — las tres exactamente sobre desacople, el corazón de DDMRP |
| `hat_tension` | *Capacity-Inventory-Backlog Trade-off* · *Efficiency-vs-Resilience Trade-off* · *Expected Total Cost vs Performance Trade-off* |
| `odoo_replenishment` | *ERP* · *From Push to Pull (Demand-Driven)* · *CPFR* |

Y dos ceros deliberados dejan de serlo con citas que confirman por qué eran cero:
`earned_value` empieza a citar *"Depreciation Schedule Methods"* y *"First-Hour
Shift Scheduling"*; `slotting` cita *"Assignment Method (Hungarian Algorithm)"*.
Falsos amigos semánticos — la misma patología que el léxico, con otra métrica.

El caso más claro es `cross_impact`, que pasa de dos citas correctas (*Tiger Team
(Cross-Functional Disruption Response Team)*, *Centralization of Decision-Making*)
a tres equivocadas (*Cross-Sectional Aggregation*, de un libro de pronóstico de
demanda intermitente; *Onshoring vs Offshoring*; *Competitive Factors in Location*).

**El mecanismo:** el pool está topeado en 16. Lo semántico no agrega candidatos,
los **desplaza**. Un nodo semánticamente cercano pero léxicamente ajeno entra y
empuja afuera a uno léxicamente preciso, y el re-rank por IDF solo puede ordenar
lo que quedó adentro. De ahí la pérdida neta.

### Variante unión: el resultado que decide

Con lo léxico intacto y lo semántico **sumado** — sin poder desplazar a nadie — el
resultado es que **no cambia nada**: 116 → 116 citas, ninguna tool gana una sola
cita, ninguna la pierde, ningún test se rompe. El único movimiento es
`cross_impact`, donde lo semántico *inyecta* un falso amigo que además desplaza a
una cita buena en el ranking final.

Ese es el dato que cierra la discusión. Aun cuando la recuperación semántica solo
puede **agregar**, no aporta una sola cita nueva a ninguna de las 49 tools.

## Por qué: el cuello de botella no es el recuperador

El gate exige que todo candidato quede a `MAX_HOPS` de un ancla declarada en
`citation_gate.TOOL_CONCEPTS`. Los nodos que los embeddings encuentran de más no
están cerca de ninguna ancla, así que el gate los descarta antes de que puedan
ayudar. Cambiar el recuperador no mueve nada porque **el filtro que decide no es
el recuperador: es el conjunto de anclas y la topología del grafo**.

Eso convierte en medición lo que los comentarios de
`tests/test_packages_citations.py` ya afirmaban sin medir:

> *"vehicle_routing ... The graph has no real VRP theory to anchor on:
> vollmann_traveling_salesman is an isolate with 0 edges ... Fix by ingesting VRP
> theory, not by a looser anchor."*

Las cinco tools anchor-islanded lo están **en el grafo**, no en el recuperador. La
única cura es ingerir la teoría que falta (VRP real; disfunción de medición de
desempeño para `org_friction_audit`), no cambiar cómo se buscan candidatos.

## El argumento de empaquetado, que solo, ya bastaba

Aunque la medición hubiera dado a favor, aplicarlo era inviable sin una decisión
mayor. `pyproject.toml:74-85` deja `fastembed` en el extra opcional `rag` y lo dice
con todas las letras:

> *"Deliberately opt-in and OUT of the prod `[web,mcp]` boot set and out of
> CI/requirements-dev ... **NOT wired into the client-facing citation path**
> (`jobs/*::gated_citations` -> `citation_gate`)."*

Y `requirements-dev.txt` — de donde instala CI — lo excluye explícitamente; los
tests RAG son `skipif(not _has_fastembed())`. Además `knowledge/embeddings_cache.json`
(24 MB) está **gitignoreado**, así que no viaja con el repo.

Consecuencia: hoy, en producción, `search_hybrid` **degrada a `search`**. Mover la
ruta de citas a híbrido no habría cambiado nada en prod, y habría hecho que las
citas dependan de si `fastembed` está instalado — o sea, resultados distintos entre
CI y una máquina local. Eso es exactamente la clase de regresión silenciosa que
`knowledge/scm-books/README.md` documenta tres veces.

Que la ruta de citas sea léxica **no es un descuido: está escrito y es deliberado.**
Lo que faltaba no era la decisión, era el número que la respalda. Ahora está.

## Qué sí haría diferencia

En orden de retorno, ninguna de estas es cambiar el recuperador:

1. **Ingerir la teoría que falta** para las 5 anchor-islanded. Es lo único que
   convierte un cero honesto en una cita real.
2. **Revisar las anclas de las 3 tools que el reemplazo desnuda** (`ddmrp`,
   `hat_tension`, `odoo_replenishment`): que pierdan las 3 citas al cambiar el pool
   muestra que dependen de un margen léxico estrecho, y ese margen es lo que se
   mueve cuando entra un libro nuevo. Son las candidatas más probables a la
   próxima regresión silenciosa.
3. **Dejar el pool en 16.** Subirlo más solo agrega falsos amigos: los ceros
   intencionales son justamente el techo de precisión que fija ese número.

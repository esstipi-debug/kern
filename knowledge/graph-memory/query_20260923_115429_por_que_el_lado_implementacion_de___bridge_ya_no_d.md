---
type: "query"
date: "2026-09-23T11:54:29.088392+00:00"
question: "Por que el lado implementacion de --bridge ya no devuelve tests, stubs ni documentos?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["KnowledgeBase", ".search()", ".bridge()", "_is_searchable_code()", "_is_non_implementation_path()"]
---

# Q: Por que el lado implementacion de --bridge ya no devuelve tests, stubs ni documentos?

## Answer

Porque el filtro vive en el lector, no en el grafo. KnowledgeBase.search() pasa cada nodo del grafo de codigo por _is_searchable_code(): los stubs sin source_file (Path, DataFrame, parametrize: 839 de 24.066 nodos; matcheaban por los tokens del id heredados del archivo que los importa) quedan fuera de TODA busqueda de codigo. Con implementation_only=True, que solo pide bridge(), tambien quedan fuera los tests/demos/examples/scripts (_is_non_implementation_path, la misma regla que implements()) y todo nodo cuyo file_type no sea code o rationale (graphify marca los .md como document). La busqueda general (query_knowledge.py --search --graph code) sigue devolviendo tests y documentos a proposito: preguntar donde se testea o donde se diseno algo es legitimo ahi, y los tests siguen en el grafo porque son la respuesta de graphify affected. El lado libros no cambia. Medido 2026-09-23 sobre 20 terminos de dominio, top-5 del bridge: implementacion 60 -> 100 de 100 posiciones; tests/examples/scripts 19 -> 0; stubs 7 -> 0; documentos 14 -> 0; terminos con implementacion 19/20 -> 20/20. El que faltaba era launch readiness: 18 secciones de su spec rankeaban antes que jobs/launch_readiness_job.py (puesto 19). Lo que QUEDA es de ranking, no de categoria: sales and operations planning devuelve scheduling/queuing/ddmrp porque src/sop.py se llama por la sigla; el primer nodo sop aparece en el puesto 14.

## Outcome

- Signal: useful

## Source Nodes

- KnowledgeBase
- .search()
- .bridge()
- _is_searchable_code()
- _is_non_implementation_path()
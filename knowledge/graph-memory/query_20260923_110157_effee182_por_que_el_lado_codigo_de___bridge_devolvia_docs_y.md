---
type: "query"
date: "2026-09-23T11:01:57.099464+00:00"
question: "Por que el lado codigo de --bridge devolvia docs y el blog antes que src/?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["KnowledgeBase", ".search()", ".implements()", "_is_non_implementation_path()"]
---

# Q: Por que el lado codigo de --bridge devolvia docs y el blog antes que src/?

## Answer

Porque el grafo de codigo indexaba texto que HABLA de los conceptos y .search() rankea por solapamiento de tokens del label: un documento que repite "safety stock" le gana a la funcion que lo implementa. Medido el 2026-09-23 sobre 20 terminos de dominio (top-5 de KnowledgeBase.search(graph="code")): solo 48% eran implementacion. El #1 para "safety stock" era documentation/blog/briefs/04-safety-stock.md; los tres primeros para "sales and operations planning" eran comunidades de knowledge/scm-books/GRAPH_REPORT.md -- el reporte del grafo de LIBROS, indexado como si fuera codigo, que ademas era hub top-12 del grafo de codigo ("Communities (206 total...)"). Arreglo: .graphifyignore en la raiz (reportes GRAPH_REPORT.md, *.min.js vendorizado, docs/superpowers/plans/ ya ejecutados, documentation/blog/, andamiaje de speckit y .cursor/). Resultado: implementacion 48% -> 59%, documentos 22 -> 9 de 100 slots, 20/20 terminos con una implementacion en el top-5. graphify update poda solo los nodos de archivos recien ignorados, sin --force. Lo que QUEDA y no es del grafo sino del lector: (1) los tests son ~24% del top-5 porque .search() no los filtra -- solo .implements() usa _is_non_implementation_path(); se dejaron en el grafo a proposito porque son la respuesta de `graphify affected`. (2) Nodos sin source_file tipo "DataFrame"/"Path" (stubs por archivo, id jobs_vehicle_routing_job_py_dataframe) matchean por los tokens del ID, que heredan las palabras de dominio del archivo: por eso "vehicle routing" devuelve dos "DataFrame@-".

## Outcome

- Signal: useful

## Source Nodes

- KnowledgeBase
- .search()
- .implements()
- _is_non_implementation_path()
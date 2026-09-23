---
type: "query"
date: "2026-09-23T11:42:39.024512+00:00"
question: "Que devuelve bridge().implementation y en que orden?"
contributor: "graphify"
outcome: "useful"
source_nodes: [".bridge()", "._rank()", "_is_code_implementation()", ".implements()"]
---

# Q: Que devuelve bridge().implementation y en que orden?

## Answer

Solo implementaciones, y los empates se rompen por nombre. .bridge() ya no llama a .search(graph="code"): usa ._rank(..., implementations_only=True), que filtra con _is_code_implementation() ANTES de cortar al limite (sin source_file, file_type document, o ruta de test/demo/example/script -> fuera; los nodos rationale quedan porque traen archivo y linea). Y rompe EMPATES de puntaje como .implements(): primero el archivo cuyo nombre comparte palabras de contenido con el termino, despues src/, recien despues el label. Los empates son la norma: 35 nodos empatan arriba para "safety stock" en el grafo real, y el orden alfabetico ponia src/safety_stock.py septimo. El puntaje no se toca: un nodo que matchea MAS del query gana aunque su archivo no se llame asi. .search() comun sigue devolviendo tests (se buscan a proposito desde el CLI) pero ya no rankea nodos de codigo sin source_file (stubs "DataFrame"/"Path" cuyo id hereda las palabras del archivo). Medido el 2026-09-23 sobre 20 terminos: implementacion en el top-5 del bridge 59% -> 100%; top-1 = implementacion en el archivo con nombre del termino 7/20 -> 14/20. Sigue flojo lo que ningun archivo nombra ("sales and operations planning" -> scheduling.py, queuing.py): eso es vocabulario, no filtro.

## Outcome

- Signal: useful

## Source Nodes

- .bridge()
- ._rank()
- _is_code_implementation()
- .implements()
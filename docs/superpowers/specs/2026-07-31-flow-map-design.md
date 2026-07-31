# flow_map — mapeo E2E de flujos con lead time medido por arista

**Fecha:** 2026-07-31 · **Rama:** `feat/flow-map` (worktree `.wt-flow-map`, off `origin/main` @ 1279434)
**Estado:** diseño aprobado, cero código escrito.

## Origen

Este spec es el primero de cuatro sub-proyectos en que se descompone el Plan Maestro
SCM-FDE de 7 fases. El plan no cabe en un spec: son capacidades independientes con
dependencias distintas. La cobertura verificada contra el código a la fecha:

| Fase del plan | Estado en Kern | Evidencia |
|---|---|---|
| 1 · descubrimiento estratégico (crux, costo de inacción, stakeholders) | hueco | ninguna capacidad |
| 2 · auditoría de datos (medallón, circuit breakers) | parcial | `src/data_quality.py` son 63 líneas: solo GTIN y mapeo de columnas de un maestro de SKU |
| 3 · mapeo E2E y gemelo digital | parcial | `src/digital_twin.py` modela redes **simuladas** (`NodeSpec`), ninguna leída del cliente |
| 4 · ejecución y optimización | cubierto | 46 herramientas registradas en `scm_agent/tools.py` (`reg.register` @ 1279434) |
| 5 · validación y grounding L3 | cubierto | QA gate por tool, `scm_agent/citation_gate.py`, 27 fuentes |
| 6 · nube y escalado | parcial | Fly con volumen persistente; sin monitor de drift |
| 7 · equidad y gestión del cambio | hueco | ninguna métrica DIR, SPD ni EOD en `src/` |

Los otros tres sub-proyectos (`intake_scan` para la fase 2, `discovery_brief` para la
fase 1, `fairness_guard` para la fase 7) quedan en cola, cada uno con su propio spec.

## Qué es flow_map

Un tool nuevo de Kern (el #47) que reconstruye la red proveedor→planta→CD→cliente
desde los movimientos observados del cliente, mide el tránsito real de cada tramo, y
emite las tres aristas que más capital inmovilizan por su variabilidad.

No es un dibujo: el mapa es el entregable visible, pero el producto es el sigma de
lead time **medido** por tramo, que hoy `src/safety_stock.py` y `src/multi_echelon.py`
asumen.

## Decisiones de diseño

Cuatro decisiones cerradas, con la alternativa descartada y por qué:

1. **Entrada = `movimientos.csv`**, no topología declarada ni inferencia heurística.
   Los nodos y aristas se derivan del dato observado, así el mapa es un hecho medido.
   Descartado inferir desde los archivos que Kern ya ingiere: adivinar quién abastece
   a quién produce exactamente el tipo de número afirmado-como-hecho que la autopsia
   de fallas marcó como problema.
2. **Veredicto = lead time medido por arista**, no anomalías de flujo ni fragilidad
   por concentración. Es el único de los tres que deja un parámetro reusable por el
   resto del motor. Los otros dos son specs aparte, no alcance de este PR.
3. **Mapa = SVG sankey autocontenido, sin CDN.** Un HTML de un solo archivo que abre
   offline, viaja por mail y sobrevive a un cliente cerrado. Descartado d3-sankey
   desde CDN: el entregable dejaría de ser autocontenido justo en el escenario de
   planta sin internet. Precedente en el repo: `warehouse/html_export.py`.
4. **Umbral duro de observaciones.** Menos de N movimientos en una arista (default 8,
   configurable) ⇒ su lead time sale como `no_medible`, nunca como número. Descartado
   reportar siempre con intervalo ancho: un intervalo ancho en una tabla se lee igual
   que un dato firme, y así es como se cuela la degradación silenciosa.

## Contrato de datos

`movimientos.csv` o `.xlsx`. Campos canónicos, detectados por alias con el mismo
mecanismo que `jobs/intake.py` usa hoy (`detect_columns`, tolerante a mayúsculas y
espaciado):

| Campo | Requerido | Uso |
|---|---|---|
| `fecha_salida` | sí | inicio del tránsito |
| `fecha_llegada` | sí | fin del tránsito |
| `origen` | sí | nodo aguas arriba |
| `destino` | sí | nodo aguas abajo |
| `producto` | sí | segmentación y valorización |
| `cantidad` | sí | ancho de banda del sankey, ponderación del ranking |
| `costo_unitario` | no | valorización del inventario de protección |
| `tipo_nodo` | no | enriquece el layout (proveedor / planta / CD / tienda) |

El lead time no se pide: se mide como `fecha_llegada − fecha_salida` por movimiento.
Si falta `fecha_llegada`, el tool devuelve `needs_data` nombrando la columna exacta.
No existe degradación a un tránsito supuesto.

## Arquitectura

Tres módulos nuevos, chicos y testeables por separado:

- **`src/flow_map.py`** — motor puro, sin I/O. Construye el grafo, asigna echelones,
  detecta ciclos, calcula la estadística de tránsito por arista.
- **`src/flow_svg.py`** — layout sankey determinista. Entrada: el reporte. Salida: un
  string SVG. Sin aleatoriedad y sin depender del orden de iteración de diccionarios,
  para que el golden test sea estable.
- **`jobs/flow_map_job.py`** — `prepare` / `run` / `verify` / `write_operational` /
  `build_deck`, siguiendo la forma de `jobs/disruption_scan_job.py`.

Flujo: `movimientos.csv` → `flow_map_job.prepare` (intake y calidad) → `src/flow_map`
(grafo y medición) → reporte → tres entregables (`mapa.html`, `aristas.csv`, deck con
citas L3) + `GuidedOutcome` con opciones rankeadas.

Consumidores aguas abajo: `safety_stock` y `multi_echelon` pasan a poder leer un sigma
de lead time medido en vez de asumirlo. Ese cableado **no** es alcance de este PR — es
un follow-up declarado.

## Motor

**Grafo.** Nodos = unión de `origen` y `destino`. Aristas = pares con al menos un
movimiento. Echelón = profundidad topológica desde los nodos sin entrada.

**Ciclos.** Si existen A→B y B→A (cross-shipping, devolución), no se rompe en
silencio: la arista se marca `bidireccional`, el cálculo de echelón ignora la de menor
volumen, y el reporte lo declara explícitamente.

**Medición por arista.** `n`, media, desvío muestral (denominador n−1), p50, p90. Con
`n < umbral` el estado es `no_medible` y los campos numéricos quedan **vacíos, no en
cero** — vacío es un hueco declarado, cero es una mentira.

**Traducción a plata.** El desvío del tránsito de un tramo obliga inventario de
protección aguas abajo: `z · sigma_LT · demanda_media`, donde `z` es el factor del nivel
de servicio objetivo del cliente, valorizado al costo unitario.
La fórmula sale de `src/safety_stock.py`, que ya existe y ya está anclada en L3 — no se
introduce una fórmula nueva.

**Opciones.** Los tres tramos que más capital inmovilizan por su variabilidad, el
primero marcado como recomendado, expuestos como `GuidedOutcome` (patrón `options=`
del `Tool`).

## QA gate

`verify` veta la entrega si se cumple cualquiera de:

1. más del 40% del volumen viaja por aristas `no_medible`;
2. más del 1% de los movimientos tiene `fecha_llegada` anterior a `fecha_salida`;
3. el grafo resultante tiene menos de dos aristas;
4. alguna de las tres opciones rankeadas se apoya en una arista `no_medible`.

Un gate que se puede pasar con datos malos no es un gate.

## Fuera de alcance, a propósito

- Anomalías de flujo (cross-shipping, retornos ascendentes, saltos de echelón).
- Fragilidad por concentración / puntos únicos de falla (Herfindahl por nodo).
- Costo por ruta — ya lo cubre `cost_to_serve`.
- El cableado de sigma medido hacia `safety_stock` y `multi_echelon`.

Cada uno es su propio spec. Este PR mide tránsito y dibuja.

## Integración: los lugares fijos

Verificados recorriendo `disruption_scan` y `launch_readiness` por el repo:

| # | Archivo | Qué se agrega |
|---|---|---|
| 1 | `src/flow_map.py`, `src/flow_svg.py` | motor y layout |
| 2 | `jobs/flow_map_job.py` | las cinco etapas del job |
| 3 | `scm_agent/tools.py` | `flow_map_tool()` + `reg.register(...)` |
| 4 | `scm_agent/citation_gate.py` | anclas de concepto en `TOOL_CONCEPTS` |
| 5 | `src/i18n.py` | título es / en |
| 6 | `webapp/mcp_tool_specs.py` | spec de la superficie MCP |
| 7 | `examples/run_flow_map.py` + tabla de capacidades del README | ejemplo corrible y capacidad listada |

Nota sobre el punto 6: `warehouse_layout` está deliberadamente fuera del MCP porque su
entregable es un visor HTML. `flow_map` sí entra — su entrada es tabular y su salida es
análisis; el HTML es un extra, no el producto.

## Citas L3

Las anclas apuntan a variabilidad de lead time y mapeo de red. **Riesgo declarado:** el
techo del pool de citas se movió a 16 con los 27 libros, y anclas mal elegidas devuelven
citas fuera de tema o cero. La primera tarea del plan de implementación es **medir** qué
devuelven las anclas candidatas contra el grafo real antes de fijarlas en
`TOOL_CONCEPTS`. No se asumen.

## Pruebas

| Archivo | Cubre |
|---|---|
| `tests/test_flow_map.py` | grafo, echelones, ciclos, estadística por arista, umbral |
| `tests/test_flow_map_job.py` | intake, columnas faltantes, las cuatro condiciones del gate |
| `tests/test_flow_map_tool.py` | registro, `prepare` sin datos, forma de las opciones |
| `tests/test_scm_agent.py` | el tool aparece en el registro |

El SVG se prueba con un golden: mismo input ⇒ mismo string. Por eso el layout debe ser
determinista.

## Riesgo principal

El tool vive o muere según si el cliente puede exportar movimientos con fecha de salida
**y** de llegada. Muchos ERP guardan solo la fecha de recepción. Si eso resulta común en
campo, `flow_map` dibuja el mapa pero no mide nada y el veredicto queda vacío.

La mitigación no es inventar el dato: es que el mensaje de `needs_data` nombre la
columna exacta y que el reporte de intake diga qué porcentaje de los movimientos la
tiene. Un hueco declarado es un resultado honesto; un tránsito supuesto no lo es.

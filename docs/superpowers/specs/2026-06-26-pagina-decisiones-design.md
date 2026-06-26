# Pagina de Decisiones — Design Spec

> Fecha: 2026-06-26
> Estado: aprobado para implementacion
> Autor: brainstorming colaborativo (usuario + agente)

## 1. Problema y objetivo

Linchpin cubre solido el trabajo recurrente analitico de los roles **Inventory**
(~80%) y **SCM** (~74% del cluster analitico), pero deja huecos que el agente
**no ejecuta** — unos por ser conversacion/decision/autoridad humana (negociar,
cambiar de proveedor, aprobar gasto) y otros por ejecucion fisica/operativa.

La idea central del usuario: **aunque el agente no tome la decision, si puede
entregar los parametros de decision** — minimos, rangos sanos, pisos, plazos
maximos — para que el humano decida/negocie con red de seguridad.

**Objetivo:** una pagina web amigable (form-first, en espanol) con calculadoras
interactivas. El usuario mete pocos datos reales y la pagina devuelve una
**Tarjeta de Decision**: semaforo + guardrails (parametros sanos) + >=2 opciones
rankeadas + el "por que" citado. Es la cara visible del contrato GuidedOutcome
(`src/guided.py`): nunca dejar al usuario desprotegido.

Proposito: **operar** (herramienta de uso real en un encargo), no solo vender.

## 2. Alcance v1

Cinco calculadoras de decision:

1. Viabilidad operativa / capacidad de operarios
2. Negociacion con proveedor
3. Cambiar de proveedor (si/no)
4. Niveles de inventario sanos
5. Aprobar compra / liquidar stock

Fuera de alcance v1: conexion a Odoo en vivo, carga de CSV (solo opcional a
futuro), persistencia/historial, autenticacion de usuarios, i18n (solo espanol).

## 3. Arquitectura (Opcion A — pagina nueva en el webapp FastAPI)

```
static/decisiones/        -> pagina amigable (5 tarjetas-formulario, espanol)
        |  POST /api/decide/<calc>
        v
webapp/decisions.py       -> router FastAPI delgado (valida con pydantic, llama al nucleo)
        |
        v
src/decision_support.py   -> 5 funciones PURAS que componen los motores existentes
        |                    y devuelven un GuidedOutcome
        v
motores ya construidos: queuing, capacity_planning, pricing, supplier_scorecard,
                        safety_stock, eoq, policies, working_capital, mcdm, classification
```

Regla de oro: la matematica vive en `src/` (pura, testeable, **citable**); el
webapp solo traduce form<->JSON; el front solo dibuja. Una calculadora nueva =
1 funcion pura + 1 endpoint + 1 tarjeta. Coherente con la convencion del repo
("un modulo = una pregunta", motor puro bajo el orquestador).

**El webapp es FastAPI** (no Flask): router montado en `webapp/app.py`, sirviendo
los estaticos de `static/decisiones/`.

## 4. Contrato comun "Tarjeta de Decision"

Las 5 calculadoras devuelven la misma forma — un `GuidedOutcome` serializado a
JSON — para que el front sea uno solo:

| Campo | Tipo | Que es |
|---|---|---|
| `veredicto` | `{nivel: verde\|amarillo\|rojo, frase: str}` | Semaforo + frase ("Dan abasto, con holgura") |
| `guardrails` | `list[{etiqueta, valor, unidad, explicacion}]` | Parametros con su minimo/rango sano — el corazon |
| `opciones` | `list[{accion, impacto, recomendada: bool}]` | >=2 acciones rankeadas, una recomendada |
| `por_que` | `{racional: str, cita: str\|null}` | Racional + cita del libro L3 (via KnowledgeBase) |
| `supuestos` | `dict` | Los datos que metio el usuario (transparencia) |

Se reusa `src/guided.py` (GuidedOutcome con status OPTIONS) y, donde aplique,
`src/decision_options.py`. La cita L3 se obtiene de `scm_agent/knowledge.py`
(`KnowledgeBase.search/explain`) de forma best-effort (si no hay grafo, `cita=null`,
nunca rompe).

## 5. Las 5 calculadoras (entrada -> motor -> guardrails)

### 5.1 Viabilidad operativa / capacidad
- **Entrada:** unidades que entran/dia (recepcion), unidades que salen/dia
  (despacho), numero de operarios, unidades/hora por operario, horas de turno/dia.
- **Motor:** `queuing.mmc` + `queuing.optimize_servers` + `capacity_planning.capacity_cushion`.
- **Calculo:** tasa de llegada = (entrada+salida)/dia convertida a por-hora;
  tasa de servicio = unidades/hora/operario; rho = llegada/(operarios*servicio).
- **Guardrails:** dan abasto si/no · utilizacion % (verde<85, amarillo 85-95,
  rojo>95) · tiempo aprox. para procesar la carga (Ws) · backlog/dia si rho>=1 ·
  minimo de operarios para zona sana (optimize_servers) y su costo.
- **No factible (rho>=1):** veredicto rojo "la carga supera la capacidad; faltan
  N operarios o M horas", con opcion ejecutable.

### 5.2 Negociacion con proveedor
- **Entrada:** costo unitario actual, precio de venta, margen minimo sano %,
  lead time actual (dias).
- **Motor:** `pricing` (piso de margen) + `safety_stock` (costo por dia extra de
  lead time) + `working_capital` (impacto de terminos de pago).
- **Guardrails:** **piso de precio de compra** = costo maximo que respeta el
  margen minimo (= precio_venta*(1-margen_min)); por encima = walk-away ·
  **plazo maximo de entrega aceptable** (cuanto safety stock extra cuesta cada
  dia adicional de lead time) · terminos de pago objetivo · semaforo del trato
  propuesto vs los pisos.

### 5.3 Cambiar de proveedor
- **Entrada:** proveedor actual vs alternativo: precio, OTIF/cumplimiento %,
  lead time, % defectos. (Form de 2 columnas.)
- **Motor:** `supplier_scorecard.score_supplier` + `mcdm` (TOPSIS) + breakeven.
- **Guardrails:** score comparado (TOPSIS) + recomendacion · **breakeven**
  (desde que diferencia de precio/OTIF conviene cambiar, considerando costo de
  switching) · semaforo: cambiar / quedarse / renegociar.

### 5.4 Niveles de inventario sanos
- **Entrada:** demanda media, variabilidad (desv. std. o CV), lead time, nivel
  de servicio objetivo, costos (unitario/ordenar/mantener), stock actual (opcional).
- **Motor:** `safety_stock.safety_stock` + `eoq` + `policies`.
- **Guardrails:** safety stock · punto de reorden (ROP) · Q optimo (EOQ) ·
  min/max sano · semaforo del stock actual (bajo / sano / exceso) si se ingresa.

### 5.5 Aprobar compra / liquidar stock
- **Entrada (comprar):** monto de compra propuesto, inventario actual, COGS,
  ventas. **(liquidar):** costo unitario, rotacion/antiguedad, elasticidad (opcional).
- **Motor:** `working_capital.working_capital` + `working_capital.cash_release_plan`
  + `pricing.markdown_price` + `classification` (slow/dead).
- **Guardrails:** **cuanto es sano comprar** (limite por capital de trabajo /
  impacto DIO-cash-to-cash) · cash liberado al liquidar X · **precio de
  liquidacion recomendado** + cuando.

## 6. Validacion y errores

- **Pydantic** por endpoint con rangos. No finito / negativo / texto -> **422 con
  mensaje en espanol claro** ("Las unidades/hora deben ser mayores a 0"), nunca 500.
- **"No factible" del motor no es error, es veredicto rojo.** Los `ValueError`
  de los motores (p.ej. rho>=1, tasas invalidas) se atrapan y se convierten en
  una Tarjeta roja con opcion ejecutable. El usuario nunca ve un stack trace.
- Reuso de los guardas existentes del webapp (rechazo de JSON no finito, limites
  de tamano, aislamiento por request) en `webapp/security.py`/`app.py`.
- **Sin datos suficientes** -> la tarjeta pide el dato faltante (estilo
  `needs_data`), no rompe.

## 7. Testing

| Capa | Archivo | Que prueba |
|---|---|---|
| Nucleo puro | `tests/test_decision_support.py` | Cada una de las 5 funciones contra numeros conocidos (rho=0.8 -> util. 80%, verde; rho=1.1 -> rojo no factible). Estructura AAA. |
| Invariante | (mismo) | Toda tarjeta devuelve >=2 opciones rankeadas con exactamente una recomendada. |
| HTTP | `tests/test_webapp_decisions.py` | 422 en entrada invalida · 200 + forma correcta en valida · umbrales del semaforo · mensajes en espanol. |

Meta: ~100% de cobertura en `decision_support.py` (es pura). Tests con
`PYTHONPATH=.`, `pytest -q`.

## 8. Front-end (`static/decisiones/`)

- Una pagina con 5 tarjetas (o tabs). Cada una abre su formulario corto.
- Submit -> `fetch` POST a `/api/decide/<calc>` -> render de la Tarjeta de
  Decision: semaforo grande, lista de guardrails, opciones rankeadas (recomendada
  destacada), "por que" plegable con la cita.
- Espanol, responsive, sin framework pesado (HTML + JS vanilla o lo que ya use
  `static/`); seguir el estilo visual existente del webapp.
- Sin build step nuevo si el resto de `static/` es no-build.

## 9. Archivos a crear

- `src/decision_support.py` — 5 funciones puras -> GuidedOutcome.
- `webapp/decisions.py` — router FastAPI (5 endpoints + modelos pydantic).
- `static/decisiones/` — `index.html` + JS + CSS (front amigable).
- `tests/test_decision_support.py` — unit del nucleo + invariante.
- `tests/test_webapp_decisions.py` — HTTP.
- Edicion minima en `webapp/app.py` — montar el router y servir los estaticos.

## 10. Deploy (con el loop autonomo)

El repo esta bajo un loop autonomo que cambia de branch bajo los pies. Patron
seguro (worktree aislado):

```
git worktree add -b feat/pagina-decisiones <tmp> origin/main
# crear los archivos de la seccion 9
.venv/Scripts/python.exe -m pytest tests/test_decision_support.py tests/test_webapp_decisions.py -q
git commit -> push -> gh pr create (draft) -> CI verde (3.11/3.12/3.13) -> gh pr merge --squash
git worktree remove   # cd al repo principal primero (Windows)
```

Regla del repo: branch -> draft PR -> CI verde -> squash-merge. Nunca push
directo a `main`. ASCII-only en prints de consola (Windows cp1252); markdown
utf-8 ok.

## 11. Decisiones de diseno (resueltas)

- Proposito: operar (herramienta interactiva), no vender. [usuario]
- Entrada: form-first, pocos campos; CSV opcional a futuro. [usuario]
- Arquitectura: Opcion A (FastAPI + motores citables), no reimplementar en JS. [usuario]
- Alcance v1: las 5 calculadoras anteriores (incl. la de capacidad operativa
  aportada por el usuario). [usuario]

## 12. Riesgos / futuro (no v1)

- CSV opcional para por-SKU / muchos proveedores (calc. 4 y 3).
- Conexion a Odoo en vivo para precargar formularios.
- Persistencia de escenarios / comparacion lado a lado.
- Mas huecos del Grupo 2 (logistica/marketplaces) cuando existan los conectores.

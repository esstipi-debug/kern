# Handoff — Contenido LinkedIn de Kern

> **Para retomar en ventana nueva sin re-derivar nada.** Escrito el 27-jul-2026.
>
> Documento aparte de `HANDOFF.md` a propósito: ese archivo es de ingeniería y hay sesiones concurrentes sobre este repo. Este track no toca código, así que no debe colisionar con ellas.

---

## 1. Estado en una línea

Se investigó si se puede hacer contenido de LinkedIn desde la filosofía de Kern (sí), se eligió un plan de tres carriles, y **se cerró el primer bloqueante**: las dos fuentes citables que faltaban ya existen en el repo, re-verificadas contra el código. **Cero posts escritos** — a propósito.

---

## 2. La trampa que te va a morder si no la leés

**El worktree está atrás de `main`.**

| | |
|---|---|
| Rama actual | `feat/scm-books-khan-resilience` @ `97347e4` |
| `origin/main` | `1279434` |
| No contiene | **#186** (Graph RAG), **#193** (`e1ff368`, degradación silenciosa), **#194** |

Leer el código de este worktree te va a mostrar bugs que en `main` ya están arreglados. Fue exactamente el error de la nota de autopsia previa. **Regla dura del track: toda afirmación sobre el código se verifica con `git show origin/main:<archivo>` o `git grep origin/main`, nunca leyendo el worktree.**

Todos los documentos de contenido están **untracked**. Nada de esto está commiteado.

---

## 3. Los tres documentos que ya existen

| Archivo | Qué es |
|---|---|
| [`KERN_PLAN_CONTENIDO_LINKEDIN.md`](KERN_PLAN_CONTENIDO_LINKEDIN.md) | el plan: tres carriles, riel de 8 semanas, regla de producción, chequeo de claims |
| [`KERN_AUTOPSIA_FALLAS.md`](KERN_AUTOPSIA_FALLAS.md) | fuente citable del carril B, re-verificada contra `origin/main` @ `1279434` |
| [`KERN_BATERIA_20_TAREAS.md`](KERN_BATERIA_20_TAREAS.md) | la vara común de 20 tareas; pieza de venta, no roadmap |

Anteriores, ya existentes y también untracked: `KERN_BRAND_PRODUCT_BIBLE.md` (§10, claims prohibidos), `KERN_MENSAJE_MADRE.md`, `KERN_VIDEO_PLAYBOOK.md` (28 guiones), `KERN_CALENDARIO_CONTENIDO.md` (12 semanas de video), `KERN_ATLAS_SOMBREROS_SCM.md` (29 roles, **sí commiteado**).

---

## 4. El plan, comprimido

**El hueco que se está llenando:** el calendario de 12 semanas trata LinkedIn como canal de video y carrusel. No hay un solo post de texto especificado. Es el formato más barato para un fundador solo y el que alcanza a Ops/COO en MOFU.

**Regla de producción de todo post — encuadre + sustancia:**
- **Encuadre** ← Atlas de sombreros (rol + certificación verificada): de qué se preocupa esa persona.
- **Sustancia** ← grafo L3 vía `KnowledgeBase.search_hybrid`: la afirmación técnica, con libro y capítulo.
- Los temarios CSCP/CPIM/CLTD/SCPro/CPSM **no están en el grafo**. La certificación presta el encuadre, nunca es la fuente citada. Fórmula obligatoria: *"se alinea con el temario de X"*, jamás *"cumple / está certificado"*.

**Los carriles:**

| Carril | Qué es | Cadencia | Estado |
|---|---|---|---|
| **A — el oficio** | qué exige el estándar, qué pasa en la realidad, dónde se rompe | semanal | listo, sin bloqueantes |
| **B — la autopsia** | dónde falla el oficio y dónde falla Kern | quincenal | **desbloqueado** |
| **D — la radiografía** | una línea de mercado real con datos públicos | serial por olas | ola 1 lista, ola 2 bloqueada |

**El riel de 8 semanas:**

| S1 | S2 | S3 | S4 |
|---|---|---|---|
| A pilar · Kern decide, no ejecuta | B1 · el NaN que se comía la varianza | A · zona Plan | D·R4 · el calendario prohibido |

| S5 | S6 | S7 | S8 |
|---|---|---|---|
| A · zona Source | B2 · el módulo de auditoría sin cablear | D·R5 · la exposición externa | D·R7 · el veredicto honesto |

---

## 5. La radiografía, en siete secciones y dos olas

La idea que hace viable el carril D: **una corrida rinde siete posts**, lo que convierte la pieza más cara por post en la más barata.

**Ola 1 — publicable sin construir nada:** R4 el calendario prohibido (vedas, dato regulatorio público) · R5 la exposición a disrupción (`src/disruption.py` sobre GDELT, corre hoy) · R7 el veredicto (editorial, incluye qué **no** se puede saber con datos públicos).

**Ola 2 — bloqueada:** R1 el censo · R2 la dispersión de precio · R3 el surtido · R6 el costo de servir. Todas necesitan el lector de catálogo.

**Sustrato real:** los 6 sitios CL están configurados y commiteados (`b88dcf9`): todofrio, frozt, mardeli, southwind, delmaratuhogar, mariscosdelaconcha. Cada config declara `tos_decision: limited` y `max_tier_allowed: L1` — se leyó el `robots.txt` de cada uno el 24-jul, pero **no hay revisión legal de ToS**. Ese estado es correcto y no debe forzarse. El lector tiene spec (417 líneas) y plan (5.860 líneas) en `feat/lector-catalogo-publico`, **cero código**.

---

## 6. Gotchas que no se pueden perder

1. **Sesgo del grafo.** 3.002 nodos, 36 fuentes, pero Khan (*Supply Chain Resilience*) pesa **32%**; Palamariu y Chopra 10% cada uno. Sin corregir, una serie larga suena toda a resiliencia y pricing. **Regla: ningún tramo de 4 posts consecutivos cita la misma fuente dos veces.**
2. **El Atlas ya tuvo certificaciones inventadas** — un "Laurels Institute" y una sigla "CUSECO" — corregidas por dos revisiones adversariales. **Solo se publican las filas con certificación verificada.** Las marcadas "por confirmar" (GBCI, programa IBF de S&OP) quedan fuera.
3. **§10 del bible manda sobre todo.** Nada de porcentaje de autonomía, "sin humano", casos de éxito con $ de clientes reales, integración Shopify/Amazon/inventario MELI, ni Control Tower autónomo como producto de hoy. Toda cifra de rival rotulada "ancla de mercado"; toda cifra de ejemplo rotulada "ej./estimado".
4. **El puntaje vigente de la batería es 27,50% · 5,5/20**, corrido el 02-ago sobre `73b912d` — posterior a #193/#194 y #203. Sustituye al 7,5% del 25-jul, que era manual. Re-correr y sellar el commit antes de publicarlo: nada congela la cifra, ni un test ni la CI corren la batería.
5. **`sourcing` con `confidence=1.00` sobre scorecard en 0.0** — desde la medición se incorporó allowlist de tokens y mapeo explícito. **No re-corrido:** no afirmar que está cerrado ni que sigue abierto.

---

## 7. Bloqueantes abiertos

| # | Bloqueante | Dueño |
|---|---|---|
| ~~1~~ | ~~commitear autopsia y batería como fuentes citables~~ | **cerrado 27-jul** |
| 2 | revisión legal de ToS de los 6 sitios CL (destraba la ola 2 de D) | operador |
| 3 | decidir si se ejecuta el plan del lector (5.860 líneas, ya escrito y revisado) | operador |
| 4 | confirmar o descartar las 2 certificaciones dudosas del Atlas (GBCI, IBF) | operador |
| 5 | cablear `audit_evidence.py` — hasta entonces no se puede afirmar que cada entregable llega con evidencia re-ejecutable | ingeniería |

---

## 8. Próxima acción, a elegir

Ninguna depende de las otras. En orden de valor por esfuerzo:

**a) Escribir el primer borrador (S1, pilar del carril A).** *Kern decide, no ejecuta* — el hallazgo estructural de la §5 del Atlas: en las 8 zonas SCOR, sin una sola excepción, falta la ejecución transaccional. Es el post que fija el posicionamiento antes que ningún otro. Necesita una consulta al grafo para la sustancia y el chequeo de claims de la §6 del plan.

**b) Correr R5 de verdad.** `disruption_scan` sobre los orígenes y proveedores de la línea de congelados CL, mirar la salida cruda, y recién después decidir si hay post. Es la única corrida real del carril D disponible hoy.

**c) Cerrar el bloqueante 5.** Cablear `audit_evidence.py` a una corrida real. Es trabajo de ingeniería, no de contenido, pero desbloquea la afirmación más fuerte de toda la doctrina de marca.

**Recomendada: (a).** Es el único que produce una pieza publicable esta semana y no depende de nadie más.

---

## 9. Cómo arrancar la ventana nueva

Leer, en este orden: este handoff → `KERN_PLAN_CONTENIDO_LINKEDIN.md` → la §10 del bible → el documento fuente del carril elegido.

Antes de escribir cualquier afirmación sobre el motor:

```bash
git fetch origin main && git log --oneline origin/main -1
```

Si el sha no es `1279434`, re-verificar los hallazgos de la autopsia antes de citarlos.

---

*Kern — el núcleo que tu cadena no tiene.*

# Kern — Plan de contenido LinkedIn (posts de texto)

> **El formato que el calendario no cubre.** 27-jul-2026 · Español LATAM (voseo neutral)
>
> [`KERN_CALENDARIO_CONTENIDO.md`](KERN_CALENDARIO_CONTENIDO.md) trata LinkedIn como canal de video y carrusel. Este documento cubre el hueco: **posts de texto**, el formato más barato de producir para un fundador solo y el que alcanza al Director de Ops y al COO/CFO en MOFU.
>
> **Fuentes de verdad:** [`KERN_ATLAS_SOMBREROS_SCM.md`](KERN_ATLAS_SOMBREROS_SCM.md) (29 roles) · [`KERN_BRAND_PRODUCT_BIBLE.md`](KERN_BRAND_PRODUCT_BIBLE.md) §10 (claims prohibidos) · [`KERN_MENSAJE_MADRE.md`](KERN_MENSAJE_MADRE.md) (la objeción) · `knowledge/scm-books/graph.json` (grafo L3, sustancia citable) · el código del motor.
>
> **Regla que gobierna todo:** la §10 del bible, sin excepción. Si un claim no se puede apuntar a un archivo de código o a una fuente citada del grafo, no se publica. La credibilidad ES el producto.

---

## 0. La regla de producción: encuadre + sustancia

Todo post de este plan se construye con el mismo par de dos piezas. No es una guía de estilo: es la única forma de escribir que no viola la §10.

| Pieza | De dónde sale | Qué aporta |
|---|---|---|
| **Encuadre** | Atlas de sombreros (rol + certificación verificada) | de qué se preocupa esa persona y en qué vocabulario lo dice |
| **Sustancia** | grafo L3, vía `KnowledgeBase.search_hybrid` | la afirmación técnica, con libro y capítulo |

**Lo que la certificación NO hace.** Los temarios CSCP, CPIM, CLTD, SCPro y CPSM **no están en el grafo** — el grafo tiene libros de texto. La certificación presta el encuadre del rol; nunca es la fuente citada. Copiar un temario sería además un problema de derechos. Fórmula obligatoria en copy: *"se alinea con lo que exige el temario de X"*, jamás *"cumple / está certificado en X"*.

**Sesgo del grafo a corregir.** 3.002 nodos sobre 36 fuentes, pero una sola pesa el 32%:

| Fuente | Nodos | % |
|---|--:|--:|
| Khan — *Supply Chain Resilience* | 948 | 32% |
| Palamariu-Alicke — *From Source to Sold* | 312 | 10% |
| Chopra-Meindl | 312 | 10% |
| Christopher | 247 | 8% |
| cola larga (32 fuentes) | 1.183 | 40% |

Sin corrección, una serie larga anclada al grafo termina sonando toda a resiliencia y pricing. **Regla:** ningún tramo de 4 posts consecutivos puede citar la misma fuente dos veces.

---

## 1. Los tres carriles

| Carril | Qué es | Cadencia | Riesgo §10 | Bloqueante |
|---|---|---|---|---|
| **A — El oficio** | qué exige el estándar, qué pasa en la realidad, dónde se rompe | semanal | bajo | ninguno |
| **B — La autopsia** | dónde falla el oficio y dónde falla Kern con el mismo patrón | quincenal | nulo (§10) / expone | commitear la autopsia y la batería |
| **D — La radiografía** | una línea de mercado real, analizada con datos públicos | serial por olas | bajo | el lector no está construido |

Se descartó por ahora el carril C (contraste comercial): la ventana Stocky ya está cubierta por las piezas de video C1-C6 del playbook, y es el único carril con riesgo §10 alto (toda cifra de rival exige rótulo de ancla de mercado).

---

## 2. Carril A — el oficio (riel semanal)

**Materia prima:** las 29 filas del Atlas. Cada una ya trae rol, certificación verificada, capacidad de Kern y **gap honesto**. El gap es el gancho, no la capacidad.

**Pieza pilar (publicar primera):** el hallazgo estructural de la §5 del Atlas — en las 8 zonas SCOR, sin una sola excepción, falta la ejecución transaccional. *Kern decide, no ejecuta.* Un post que dice dónde tu producto **no** sirve es el más creíble que vas a escribir en todo el año, y encima fija el posicionamiento correcto: la capa de decisión que se sienta arriba de quien ejecuta.

**Sub-series disponibles sobre la misma tabla:**

- **por zona SCOR** — 8 posts (Plan, Source, Make, Deliver, Return, Enable + Riesgo/Calidad + Finanzas/Analytics)
- **por nivel** — 3 posts (táctico, gerencial, estratégico): qué decide cada altura y con qué horizonte
- **por gap repetido** — 3 posts sobre lo que aparece en varios sombreros a la vez: sostenibilidad (E1), SRM profundo (E2), trazabilidad (E3)

**Regla de corte dura.** El Atlas pasó por dos revisiones adversariales que encontraron certificaciones inventadas o mal atribuidas — un "Laurels Institute" para logística inversa y una sigla "CUSECO" que no existe. En un post público, una certificación mal nombrada te quema con el único público que te importa. **Solo se publican las filas con certificación verificada.** Las marcadas "por confirmar" (GBCI para sostenibilidad, el programa IBF de S&OP) quedan fuera hasta confirmarlas.

---

## 3. Carril B — la autopsia (quincenal)

El único carril que nadie puede copiar, porque exige haber auditado las fallas propias. Es la filosofía de *autonomía ganada, no declarada* convertida en contenido.

### Criterio de corte

**Publicable:** falla propia, medida, con commit y fecha.
**No publicable:** cualquier cosa con datos de un cliente (no hay clientes), cualquier vector de ataque, cualquier porcentaje de autonomía (§10), y cualquier falla que no esté arreglada o sin fecha de arreglo comprometida.

### Inventario verificado contra `origin/main` @ `1279434` (27-jul-2026)

Las dos fuentes citables ya existen: [`KERN_AUTOPSIA_FALLAS.md`](KERN_AUTOPSIA_FALLAS.md) y [`KERN_BATERIA_20_TAREAS.md`](KERN_BATERIA_20_TAREAS.md). El bloqueante 1 quedó cerrado.

| # | Post | Estado verificado | Veredicto |
|---|---|---|---|
| B1 | El NaN que se comía la varianza | cerrado en #193 (`e1ff368`); sigma 2.626 usada vs 2.753 correcta; 57,17 u de buffer donde iban 62,09 | **primero** — arco completo: encontrado corriendo código, medido, arreglado con test primero, mergeado y deployado |
| B2 | El módulo de auditoría que nunca cablé | `src/audit_evidence.py`, 615 líneas, cero llamadores de producción | **sí** — publicar junto con la fecha en que se cablea |
| B3 | El KPI que sé calcular y nunca te muestro | `stockout_rate` en `financial_kpis.py:97`, cero llamadores de producción | **sí, condicionado** — el nivel de servicio realizado es lo primero que pide un planificador. Solo con fecha de cierre |
| B4 | La autopsia que caducó en tres días | cinco de siete "bugs vivos" estaban cerrados al re-verificar | **sí** — el post más honesto del carril: una autopsia sin sello de commit no vale nada |
| B5 | El diferencial que construí y no supe mostrar | `src/risk_period.py`, sigma_x con lead time estocástico (Vandeput 6.4-6.5) | **sí** — falla de comunicación, no de matemática |

**Nota de vigencia.** La batería se re-corrió: **27,50% · 5,5/20** sobre `73b912d` (02-ago), posterior a #193/#194 y #203. El 7,5% del 25-jul era manual y queda superado. Re-correr y sellar el commit antes de cada publicación — nada congela la cifra.

---

## 4. Carril D — la radiografía (serial por olas)

**La idea que lo cambia todo:** una radiografía bien hecha no es un post, es **una corrida que rinde siete**. Eso convierte al carril más caro por pieza en el más barato por post, y resuelve el problema real: con cero clientes, es la única prueba honesta del motor que no exige inventar un caso de éxito.

### Las siete secciones de una radiografía

| # | Sección | Qué muestra | ¿Corre hoy? |
|---|---|---|---|
| R1 | **El censo** | cuántos operadores, cuántos SKU, qué se publica y qué se esconde | necesita el lector |
| R2 | **La dispersión de precio** | el mismo producto a N precios; el rango ES el hallazgo | necesita el lector |
| R3 | **El surtido** | solapamiento real vs. surtido que cada uno cree tener | necesita el lector |
| R4 | **El calendario prohibido** | vedas y estacionalidad que rompen el supuesto de demanda estable | **sí** — dato regulatorio público, sin código |
| R5 | **La exposición a disrupción** | GDELT sobre los orígenes y proveedores de la línea | **sí** — `src/disruption.py`, tool #46 |
| R6 | **El costo de servir** | qué le cuesta al distribuidor la cola larga que igual publica | parcial — el motor existe, faltan los datos de R1 |
| R7 | **El veredicto** | qué haría un planificador con esto — y qué **no** puede saberse con datos públicos | **sí** — editorial, y es la sección más honesta del set |

### Estado real del sustrato

- **Los 6 sitios ya están configurados y commiteados** (`b88dcf9`): todofrio, frozt, mardeli, southwind, delmaratuhogar, mariscosdelaconcha.
- **Techo legal ya fijado, y es un freno real.** Cada config declara `tos_decision: limited` y `max_tier_allowed: L1` — se leyó el `robots.txt` real de cada sitio el 24-jul, pero **no hay revisión legal humana de los Términos de Servicio**. Ese es el estado correcto y no debe forzarse: "limited" significa exactamente "sin revisión legal todavía".
- **El lector de catálogo público no está construido.** La rama `feat/lector-catalogo-publico` tiene spec aprobado (417 líneas) y plan de implementación (5.860 líneas, salido de 10 agentes y 48 hallazgos), pero **cero código**.
- **Minas legales ya identificadas** en `docs/DATA_SOURCES_RESEARCH_2026-07.md`: IMF, ACLED, Comtrade, Kaggle y BOL tienen problemas de licencia. Publicar un análisis apoyado en una fuente de licencia restrictiva es peor que no publicarlo.

### Las dos olas

**Ola 1 — lo que se puede publicar sin construir nada:** R4, R5 y R7. Tres posts que se sostienen solos y ya cuentan una historia completa de la línea (el calendario que rompe la planificación · la exposición externa · qué haría un planificador y qué no se puede saber). No dependen del lector ni de la revisión legal, porque no leen catálogo.

**Ola 2 — después de que el lector aterrice:** R1, R2, R3 y R6, que son las secciones con el número que más duele (la dispersión de precio). Requieren el lector construido **y** la revisión legal de ToS hecha por el operador.

---

## 5. El riel de 8 semanas

Cadencia sostenible para un fundador solo: **1 post de A por semana + 1 de B por quincena + la ola 1 de D intercalada**. Ningún tramo de 4 posts consecutivos repite fuente del grafo.

| Semana | Post | Carril | Notas |
|---|---|---|---|
| S1 | El hallazgo estructural: Kern decide, no ejecuta | A (pilar) | fija el posicionamiento antes que nada |
| S2 | El NaN que se comía la varianza | B1 | arco completo, con el commit a la vista |
| S3 | Zona Plan: qué exige CPIM y qué pasa el lunes | A | |
| S4 | El calendario prohibido de los congelados | D · R4 | primera pieza de la radiografía |
| S5 | Zona Source: el gap que ningún software tapa | A | |
| S6 | El módulo de auditoría que nunca cablé | B2 | publicar con la fecha de cableado |
| S7 | La exposición externa de una línea de mercado | D · R5 | corrida real de GDELT |
| S8 | Qué haría un planificador con esto — y qué no se puede saber | D · R7 | cierra la ola 1 y ancla el carril |

---

## 6. Chequeo de claims antes de publicar (obligatorio)

Copiar este bloque al pie de cada borrador. Si una línea queda vacía, el post no sale.

```
- Claim principal → <archivo de código | nodo del grafo con libro y capítulo>
- Certificación mencionada → <fila del Atlas, marcada verificada; nunca "por confirmar">
- Redacción de la certificación → "se alinea con el temario de X" (jamás "cumple" / "certificado")
- Fuente del grafo usada → <no repetida en los 3 posts anteriores>
- Cifra de rival → rotulada "ancla de mercado / precio de lista"
- Cifra de ejemplo → rotulada "ej. / estimado / ilustrativo"
- Verificar ausencia de: % de autonomía · "sin humano" · casos de éxito con $ de clientes ·
  integración Shopify/Amazon/inventario MELI · Control Tower autónomo como producto de hoy
```

---

## 7. Bloqueantes abiertos (en orden)

1. **Commitear la autopsia de 8 patrones y la batería de 20 tareas** a `documentation/`. Sin esto, B3 y sus derivados no son publicables. Es la acción más barata de la lista y desbloquea medio carril.
2. **Revisión legal de ToS de los 6 sitios CL** por el operador. Sin esto, la ola 2 de D no arranca y los configs se quedan en `limited`/L1.
3. **Decidir si se ejecuta el plan del lector** (5.860 líneas, ya escrito y revisado). La ola 1 de D no lo necesita; la ola 2 no existe sin él.
4. **Confirmar las certificaciones marcadas "por confirmar"** (GBCI, programa IBF de S&OP) o dejarlas permanentemente fuera del carril A.

---

*Kern — el núcleo que tu cadena no tiene.*

# Fuentes de control documental y trabajo estándar (fuente L3 #30)

Registro auditable de la fuente que `scripts/merge_sop_document_control.py` mete en
`knowledge/scm-books/graph.json`. **Este documento no es el `source_file` de ningún nodo** —
es el input del script y el papel de trabajo contra el que se audita cada cita.

**Procedencia de la investigación:** barrido verificado del 2026-09-03, 61 hallazgos con
re-verificación adversarial (cada hallazgo re-buscado por un segundo agente con la consigna
de refutarlo). Datos crudos preservados fuera del repo en
`C:\Users\Gamer\Documents\kern-sop-research-2026-09-03\`.

## La regla que gobierna este archivo

**Nunca inventar capítulo, página, edición ni cita.** Un concepto entra al grafo solo si
esta ficha se puede llenar entera con lo que realmente se leyó. El `source_file` nombra
**el artefacto que se leyó**, no la obra que ese artefacto describe: leer la página de
producto del CQI-8 no es haber leído el CQI-8, y el nombre del archivo tiene que dejarlo
ver. El cliente solo ve `label — source_file source_location`, así que la honestidad tiene
que vivir ahí adentro.

**Tres conceptos se cayeron de la lista por esta regla** (la investigación los cubrió, pero
su procedencia no aguanta):

| Concepto | Por qué se cayó |
|---|---|
| ISO 9001 2008→2015, los seis procedimientos obligatorios | La re-verificación encontró que **dos de las tres citas no existen en las páginas citadas**. El hecho de fondo se re-consiguió por otra fuente y se sostiene, pero el rastro está contaminado. |
| Estado de la revisión ISO 9001:2026 (FDIS) | Confirmado por dos entes certificadores, pero es un **hecho fechado que caduca**: la publicación era el 16-sep-2026. Un grafo de teoría no es lugar para un estado transitorio. |
| SDCA y la atribución de "sin estándar no hay kaizen" | La única fuente es el sitio del propio Kaizen Institute (vendedor), y la atribución de la frase a Ohno tiene procedencia disputada y sin nota al pie. |

**Tipo de evidencia** usado abajo: `PRIMARIA` = se leyó el artefacto de la organización
que define el concepto · `SECUNDARIA` = página de catálogo, producto o divulgación de un
tercero · `TESTIMONIAL` = entrevista grabada al autor.

---

## Control Plan

- id: `knowledge::control_plan` · ancla: `knowledge::control_chart`, `knowledge::poka_yoke`
- artefacto leído: ASQ Certified Six Sigma Green Belt Body of Knowledge (mapa 2022), PDF
- `source_file`: `asq-cssgb-body-of-knowledge-2022-map.pdf`
- publisher: ASQ (American Society for Quality)
- source_url: https://www.asq.org/cert/resource/pdf/certification/2022-CSSGB-BoK-Map.pdf
- accedido: 2026-09-03 · ubicación exacta: Table 2, pp. 10-11
- evidencia: **PRIMARIA** — el PDF propio de ASQ, leído directo
- verificado: la fase Control del BoK enumera control plan, document control, training plans, audits y PDCA
- **NO se ingiere:** la lista de campos de IATF 16949 §8.5.1.1. Solo se corroboró en un blog de
  terceros, nunca contra el texto pago de la norma. El nodo describe el artefacto según ASQ.

## Reaction Plan

- id: `knowledge::reaction_plan` · ancla: `knowledge::poka_yoke`
- Misma ficha (`source_file`: `asq-cssgb-body-of-knowledge-2022-map.pdf`): es uno de sus campos, no una fuente aparte. Se separa como nodo
  porque es el campo que un SOP casero nunca escribe, y por eso vale citarlo solo.

## Documented Information (ISO 9001:2015, cláusula 7.5.2c)

- id: `knowledge::documented_information` · ancla: `knowledge::iso_9000`
- artefacto leído: entrada de blog de isoTracker sobre frecuencia de revisión documental
- `source_file`: `isotracker-iso-9001-document-review-frequency-blog.html`
- publisher: isoTracker (proveedor de software de QMS)
- source_url: https://www.isotracker.com/blog/iso-9001-how-often-to-review-documents/
- accedido: 2026-09-03 · ubicación exacta: cuerpo del artículo
- evidencia: **SECUNDARIA** — el texto de la cláusula se reconfirmó textual en **tres** fuentes
  practicantes independientes; **ningún ente certificador resultó alcanzable** (BSI, NQA, DNV,
  ASQ e iso.org devolvieron 403 o no tratan el punto)
- verificado: la cláusula exige "review and approval for suitability and adequacy" antes de usar
- **Límite declarado:** nadie leyó la norma. El `source_file` nombra la fuente secundaria, no
  `iso-9001-2015.pdf`.

## Document review cadence (la organización la fija, la norma no)

- id: `knowledge::document_review_cadence` · ancla: `knowledge::pdca_cycle`
- Misma ficha (`source_file`: `isotracker-iso-9001-document-review-frequency-blog.html`). Es el hallazgo de mayor valor de todo el barrido y por eso va como nodo
  propio: **ISO 9001:2015 no fija ningún intervalo de revisión.** La ausencia se verificó por el
  mismo camino de tres fuentes. Los 365 días que usa `procedure_audit` son convención
  (GDI, SweetProcess), y el nodo lo dice.

## ISO 10013:2021 — guía para información documentada

- id: `knowledge::iso_10013_documented_information_guidance` · ancla: `knowledge::iso_9000`
- artefacto leído: ficha de catálogo de BSI para BS ISO 10013:2021
- `source_file`: `bsi-iso-10013-2021-catalogue-page.html`
- publisher: BSI (organismo de normalización del Reino Unido, miembro de ISO)
- source_url: https://knowledge.bsigroup.com/products/quality-management-systems-guidance-for-documented-information
- accedido: 2026-09-03 · ubicación exacta: alcance / abstract de la ficha
- evidencia: **SECUNDARIA** (ficha de catálogo), pero de un ente certificador
- verificado textual: "guidance for the development and maintenance of the documented
  information...tailored to the specific needs of the organization"; publicada el 31-mar-2021
- **Límite declarado:** no se leyó el índice a nivel de cláusula — iso.org está bloqueado y el
  texto completo es pago.

## Segregación dueño / aprobador

- id: `knowledge::document_owner_approver_segregation` · ancla: `knowledge::iso_9000`
- artefacto leído: artículo de Compliant Ltd sobre documentos controlados en ISO 9001
- `source_file`: `compliant-ltd-controlled-documents-iso-9001-article.html`
- publisher: Compliant Ltd (consultora)
- source_url: https://compliantltd.com/insights/managing-controlled-documents-iso-9001/
- accedido: 2026-09-03 · ubicación exacta: cuerpo del artículo
- evidencia: **SECUNDARIA** — contenido de consultora. Las dos citas se reconfirmaron textuales,
  pero **ningún ente certificador corroboró** pese a 10+ intentos
- verificado: quien redacta o mantiene un procedimiento no debería ser también su único
  aprobador; la aprobación se liga a competencia, no a jerarquía
- **Límite declarado, y va en el `rationale` del nodo:** es **convención de práctica**, no un
  requisito de cláusula. Nunca se verificó que ISO 9001 lo exija.

## Leader Standard Work

- id: `knowledge::leader_standard_work` · ancla: `knowledge::kaizen`
- artefacto leído: entrevista grabada a David Mann (autor de *Creating a Lean Culture*), Lean Blog podcast 212
- `source_file`: `leanblog-podcast-212-david-mann-interview-2014.html`
- publisher: Lean Blog (Mark Graban)
- source_url: https://www.leanblog.org/2014/12/podcast-212-david-mann-creating-a-lean-culture-3rd-ed/
- accedido: 2026-09-03 · ubicación exacta: entrevista 2014 (y una segunda de 2006)
- evidencia: **TESTIMONIAL** — el autor del libro, en su propia voz, en dos entrevistas
- verificado textual: la estructura de chequeo por capas, e "initial the hour-by-hour chart at
  least four times a day"
- **CONTRADICCIÓN, preservada como nodo aparte:** el reparto de tiempo del líder. La entrevista
  de 2006 dice supervisor 50% / value-stream manager 25%; una reseña del mismo libro en IISE dice
  60% / 40%. **No citar ninguno de los dos como zanjado** sin la página del libro en la mano.

## Layered Process Audit

- id: `knowledge::layered_process_audit` · ancla: `knowledge::total_quality_management`
- artefacto leído: página de producto de AIAG para CQI-8
- `source_file`: `aiag-cqi-8-layered-process-audit-product-page.html`
- publisher: AIAG (Automotive Industry Action Group)
- source_url: https://www.aiag.org/store/publications/details?ProductCode=CQI-8
- accedido: 2026-09-03 · ubicación exacta: descripción de producto
- evidencia: **SECUNDARIA** — página de producto de la organización que publica la guía; **el
  CQI-8 en sí es pago y no se leyó**, y el nombre del archivo lo dice
- verificado textual: la guía "Recommends Roles and Responsibilities for LPA (RASIC chart)" y
  "Expands ownership beyond the Quality function" — o sea, **roles por matriz RASIC, no puestos fijos**
- corroboración cruzada: el patrón de auditoría diaria en primera línea aparece en 4 fuentes
  independientes, incluidos dos SOP reales de proveedores automotrices (Nexteer, PSA Groupe)
- **CONTRADICCIÓN, preservada como nodo aparte:** la cadencia del nivel superior. Cuatro fuentes
  reales dan cuatro respuestas (Nexteer: planta/GM semanal; PSA: 4 niveles diario/semanal/mensual/
  trimestral con un nivel de CEO; dos blogs de vendedores: mensual). **CQI-8 no la fija**: varía
  por organización.

## Job Breakdown Sheet (TWI Job Instruction)

- id: `knowledge::job_breakdown_sheet` · ancla: `knowledge::checklist`
- artefacto leído: centro de aprendizaje TWI del Bilas Group, sección Job Instruction
- `source_file`: `bilas-group-twi-job-instruction-learning-center.html`
- publisher: The Bilas Group (proveedor de formación TWI)
- source_url: https://www.thebilasgroup.com/twi-learning-center/job-instruction/
- accedido: 2026-09-03 · ubicación exacta: sección Job Instruction
- evidencia: **SECUNDARIA** — la página del propio TWI Institute quedó inalcanzable (fallo SSL),
  así que se usó un proveedor de formación TWI
- verificado textual: la estructura Pasos Importantes / Puntos Clave / Razones, y su paralelo con
  la "work element sheet" de Toyota

## JI Training Timetable

- id: `knowledge::ji_training_timetable` · ancla: `knowledge::checklist`
- Misma ficha (`source_file`: `bilas-group-twi-job-instruction-learning-center.html`).
- verificado: el registro liga **una persona con una tarea y una fecha objetivo**
- **Corrección explícita, y va en el `rationale`:** el borrador original de la investigación lo
  describía como un vínculo "versión-a-entrenamiento". **Es falso** y se corrigió: liga persona
  a tarea y fecha, **nunca a un número de versión de documento**. Rastrea entrenamiento, no
  control documental.

---

## Nodos de contradicción

Van al grafo como nodos citables con `file_type: "contradiction"`. Una cita que dice "las
fuentes no coinciden" vale más que una que finge un número.

| id | qué preserva |
|---|---|
| `knowledge::layered_process_audit_cadence_dispute` | Las 4 respuestas y su fuente cada una. `references` → `layered_process_audit` |
| `knowledge::leader_standard_work_time_allocation_dispute` | 50/25 (entrevista 2006) vs 60/40 (reseña IISE del mismo libro). `references` → `leader_standard_work` |

## Anclas: solo las durables

Las 10 se enganchan a conceptos de `heizer-render-operations-management.pdf`, que está **fuera**
de `merge_l3_rebuild.REPLACE_STEMS` — un rebuild de libros no las borra.

**Prohibidas como ancla:** `continuous_improvement` (palamariu) y `supplier_audit` (grant), ambas
en `REPLACE_STEMS`: un rebuild se las lleva y con ellas la arista. Y `procedure_model`, que es un
falso amigo documentado — el modelo de reconfiguración organizacional de Kuhn, no un procedimiento.

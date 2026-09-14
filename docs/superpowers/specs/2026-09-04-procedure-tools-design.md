# Procedimientos operativos (SOP) en Kern — diseño

Fecha: 2026-09-04 · Base: `main` @ `34b8e52` (51 tools) · Plan: `docs/superpowers/plans/2026-09-04-procedure-tools.md`

## Problema

Un equipo DTC de 5–30 personas guarda sus procedimientos operativos (SOP = Standard Operating
Procedure, **no** S&OP) en Notion, SweetProcess o Drive. Lo que no tiene es quien audite ese
registro (qué venció, qué no tiene dueño, qué "aprobado" no tiene aprobador), quien redacte un
borrador con control documental correcto, y quien avise cuando vence una revisión. Kern hoy no
tiene ninguna capacidad SOP: todo identificador `sop*` del repo es Sales & Operations Planning.

Kern es una **capa de decisión sobre el sistema de registro del cliente**. No guarda los SOP: lee
un export tabular del registro y devuelve hallazgos rankeados y un HANDOFF con dueño y fecha,
exactamente como hace en los otros dominios.

## Contenido de dominio (investigación verificada 2026-09-03)

Cinco controles de ciclo de vida de un SOP:

| Control | Quién lo exige | Cómo lo modela Kern |
|---|---|---|
| Dueño | convención (ISO no nombra "document owner"); dueño ≠ aprobador | columna `owner`; hallazgo `NO_OWNER`, `OWNER_IS_APPROVER` |
| Control de versión | ISO 9001:2015 §7.5.3.2(c) | columna `version`; `UNVERSIONED` |
| Aprobación antes de uso | **ISO 9001:2015 §7.5.2(c)** — el único obligatorio | `approver` + `approved_at`; `NO_APPROVAL` solo con `status == approved`; el borrador nace `draft` |
| Cadencia de revisión | ISO **no fija intervalo**; 365 días es convención (GDI, SweetProcess) | `review_cadence_days` por fila o param; default 365 etiquetado `convention` |
| Detección de vejez | único automatismo real: cron mensual que abre un issue deduplicado por SOP vencido | `OVERDUE_REVIEW` / `REVIEW_DUE_SOON` (30 días); Fase 2: job programado |

Vocabulario que usan los entregables: información documentada; revisión y aprobación para
adecuación y suficiencia; segregación dueño/aprobador; Job Breakdown Sheet (pasos importantes /
puntos clave / razones); control plan (paso, característica/KPI, spec, método de medición,
frecuencia, método de control, plan de reacción, dueño); cronograma de entrenamiento JI
(persona × tarea × fecha objetivo); Leader Standard Work; Layered Process Audit; RASIC; SDCA vs PDCA.

## Fases

- **Fase 1 (este spec + plan):** dos tools sobre un motor común `src/procedures/`:
  `procedure_audit` (auditoría del registro) y `procedure_draft` (borrador con control documental).
- **Fase 2:** `procedure_adherence` (el SOP dice X, los datos muestran Y), modo `procesos`,
  job programado `procedure_review_cycle` sobre `POST /api/jobs/run-scheduled`.
- **Fase 3:** `procedure_playbook`, sub-agente conversacional con LLM vivo — spec propia.

## Decisiones de diseño (alternativas rechazadas entre paréntesis)

- **D1 Nombres.** Keys `procedure_audit` y `procedure_draft`; paquete `src/procedures/`; jobs
  `jobs/procedure_audit_job.py`, `jobs/procedure_draft_job.py`; archivos `procedure_*.csv`. "SOP"
  solo en títulos y prosa junto a "procedimiento operativo". (Rechazado `sop_*` por colisión con
  `src/sop.py`, `jobs/sop_job.py`, key `sop`, L3 `vollmann_sop`, RB-7, T14; rechazado
  `standard_work_*` porque el cliente dice SOP.)
- **D2 Motor sin reloj.** `audit_register(records, *, as_of: date, ...)` exige `as_of`. El job
  resuelve `params["as_of"]` (ISO `YYYY-MM-DD`); si falta usa `date.today()` y lo declara:
  `as_of_source ∈ {"param", "clock"}` + `Residual`. (Rechazado default `today()` en el motor y
  exigir `as_of` siempre: `examples/run_agent.py` no tiene flag genérico de params.)
- **D3 Contrato de columnas.** `required_columns=("name", "last_reviewed_at")`; opcionales
  `procedure_id, process_area, owner, approver, version, approved_at, review_cadence_days,
  location, linked_kpi, reaction_plan, status`. Columna requerida ≠ celda obligatoria: una fecha
  en blanco produce `NO_REVIEW_DATE` (`sin_dato`), nunca se rellena con hoy. (Rechazado
  `("name",)` solo: `jobs/intake_scan_job.py::_map_headers` canonicaliza `name` como producto y un
  `maestro.csv` pondría la tool en verde falso. Consecuencia aceptada: `intake_scan` reporta
  `procedure_audit` en rojo sobre carpetas de ventas/stock. Perfil `procedimientos` = Fase 2.)
- **D4 Hallazgos con estado.** `state ∈ {cumple, no_cumple, sin_dato}`; `sin_dato` se lista como
  "no evaluable", nunca como incumplimiento (doctrina `SIN_DATO != NO_CUMPLE` de `src/bateria`).
  Códigos: `OVERDUE_REVIEW`, `REVIEW_DUE_SOON`, `NO_OWNER`, `OWNER_IS_APPROVER`, `NO_APPROVAL`,
  `NO_KPI_LINK`, `UNVERSIONED`, `NO_REVIEW_DATE`. Severidad: `OVERDUE_REVIEW` high si además sin
  dueño o `days_overdue >= 2 * cadence`, si no medium; `NO_APPROVAL` high solo con
  `status == approved`; `NO_OWNER`, `OWNER_IS_APPROVER` medium; resto low. Ranking
  (severidad, `days_overdue` desc, nombre). `status == retired` → sin chequeos de vigencia.
  (Rechazado reutilizar el dataclass `ControlPoint` de `src/org_friction.py`: se reutiliza el
  concepto de vacío de responsabilidad y el patrón del job, no el tipo.)
- **D5 Desenlace: siempre HANDOFF, nunca EXECUTED.** Packet "Revisar los N procedimientos
  vencidos" con un step por vencido `"revisar <name> - dueno <owner> - vence <YYYY-MM-DD>"`,
  `deadline` = la fecha vencida más antigua, `data={"items": [...]}`, `risk_if_skipped`
  obligatorio. Packet "Asignar dueño" si hay `NO_OWNER` (el step dice `SIN ASIGNAR`, nunca un
  nombre inventado). Packet "Programar revisiones próximas" si hay `REVIEW_DUE_SOON`. Sin
  hallazgos → HANDOFF "Confirmar la cadencia declarada y re-correr en 30 días". El CSV de
  hallazgos lleva `owner` y `due_date` poblados. (Rechazado `OPTIONS`: cada vencido es una acción
  con dueño y fecha, no una pregunta.)
- **D6 `EvidenceRecord` no en Fase 1.** `src/audit_evidence.py` es linaje re-ejecutable
  (hashes, `prepared_by` = motor) y arrastra `scipy.stats` a nivel de módulo; el encabezado de
  control documental es humano (dueño, aprobador, versión, fechas). Fase 1 define
  `DocumentControl` propio. `procedure_adherence` (Fase 2) será su primer llamador.
- **D7 Plantillas como datos; números por `params`, no por un CSV previo.** Tres plantillas
  (`replenishment`, `cycle_count`, `sop_cycle`) en `src/procedures/templates.py`, textos es/en,
  sustitución con `str.format_map` sobre un dict seguro. Cada default se etiqueta `convention` en
  `parameters_used (nombre, valor, fuente ∈ param|profile|convention)`. Un SOP enuncia la
  **regla** ("reponer cuando on-hand + on-order < ROP de la hoja de política vigente, versión X")
  y referencia la hoja como documento controlado externo; no congela 200 ROPs. (Rechazado
  `source_path` a un entregable previo: tres parsers, tres shapes. Anexo `policy_path` = Fase 2.)
- **D8 Entregables.** Deck estándar `deliverable.md` + `deliverable.xlsx` (`Deliverable.write_all`)
  más operacionales: auditoría → `procedure_audit_findings.csv` + `procedure_review_calendar.csv`;
  borrador → `procedure_draft_<process>.md`, `procedure_draft_<process>.xlsx` (hojas
  `Header/Steps/ControlPlan/Training`) y tres CSVs. (Rechazado extender `Deliverable`.)
- **D9 Ruteo.** `procedure_audit` posee los sustantivos de registro/vencimiento/control
  documental; `procedure_draft` los verbos (draft/write/redactar/escribir/armar/generar +
  borrador/plantilla), los artefactos (job breakdown sheet, control plan, work instruction,
  training timetable) y `cadencia de revision`/`review cadence`. Nada de `sop`, `draft`,
  `procedure`, `revision` a secas: `scm_agent/intent.py:59` exige victoria estricta. Los keyword
  sets exactos están en el plan (Task 5) y se probaron contra 25 briefs con 0 fallos.
- **D10 Citas (medido).** `procedure_audit → ("iso_9000", "checklist", "total_quality_management")`
  conserva 2 citas gated. `procedure_draft → ("checklist", "poka_yoke", "pdca_cycle")` +
  `EXCLUDED_CONCEPTS ("work_breakdown_structure",)` queda en 1 < `MIN_CITATIONS` → cero, y entra en
  `_CORRECTLY_ZERO_TOOLS`. `procedure_model` es un falso amigo (Kuhn, reconfiguración de red). El
  camino single-tool del orquestador cita sin gate (`orchestrator.py:236-243`): RB-16 lo advierte.
- **D11 Tests estructurales, no golden.** Asserts de estructura + un test de determinismo.
- **D12 Área `"Procedures & standard work"`** en `/how-it-works`, bucket SCOR `Orchestrate`.

## Fase 2 (alcance)

- **`procedure_adherence`:** parámetros declarados (registro/borrador) vs datos transaccionales
  vía `jobs/intake.py` (`RECEIPT_REQUIRED`, `service_lines`), `src.alerting.detect_events`,
  `src.policies`, `src.order_fulfillment`, `jobs.cycle_count_job`; findings `DECLARED_VS_OBSERVED`;
  HANDOFF con la bifurcación Lean "Reentrenar (JI)" o "Revisar el estándar" (SDCA vs PDCA);
  primer llamador de `src.audit_evidence.evidence_record`. Empezar por `replenishment`.
- **Modo `procesos`** en `scm_agent/modes.py` con `procedure_*`, `org_friction_audit`,
  `cycle_count`, `reconciliation`; pin en `tests/test_modes.py:53`.
- **`procedure_review_cycle`:** `jobs/procedure_review_job.py`, registro en
  `LINCHPIN_PROCEDURE_REGISTER_PATH` (`/data/procedures/register.csv` en Fly), un `Event`
  `procedure_review_due` por vencido con `dedup_key=f"procedure_review_due:{procedure_id}:{next_review}"`
  en un `EventLedger` propio (ventana 30 días), shape compatible con `_cycle_report_summary`,
  `events` devuelve `()`, registro en `jobs/scheduled_jobs.py`. Precondición: secret
  `KERN_JOBS_API_KEY` (el cron está rojo hoy).

## Fase 3 (solo spec)

`procedure_playbook`: `ProcedurePlaybook` derivado de `ProcedureTemplate` (cada paso → una
pregunta con `capture` y `escalate_when` = plan de reacción), runner con LLM vivo, ruta
nosilo-pack. Decisiones previas: dónde vive el registro de cumplimiento (eventos + artifact de
HANDOFF vs sqlite nuevo), contrato LLM, semántica de desviación, PII (rol + timestamp), superficie.

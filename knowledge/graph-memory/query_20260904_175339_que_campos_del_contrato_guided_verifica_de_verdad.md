---
type: "query"
date: "2026-09-04T17:53:39.934364+00:00"
question: "Que campos del contrato guided verifica de verdad verify_guided?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["GuidedOutcome", "Residual"]
---

# Q: Que campos del contrato guided verifica de verdad verify_guided?

## Answer

Hay una asimetria real en nosilo_core/guided.py::verify_guided (paquete instalado en .venv, NO codigo de este repo): un Residual SIN risk_if_skipped es un fallo de QA duro, pero un HandoffPacket sin risk_if_skipped pasa limpio -- del handoff solo se exige 'steps o artifact'. El campo HandoffPacket.risk_if_skipped existe en el dataclass y NUNCA se chequea. Un spec puede declararlo obligatorio (lo hace D5 del spec de procedure-tools) pero eso es convencion de autor, no un gate: un packet que se olvide del riesgo shipea igual. Por eso jobs/procedure_audit_job.py::verify y jobs/procedure_draft_job.py::verify agregan el assert ellos mismos, en vez de confiar en que los literales se mantengan correctos. Si escribis una tool nueva que emite handoffs, copiá ese assert. OJO PARA EL PROXIMO QUE BUSQUE: verify_guided() y HandoffPacket NO son nodos del grafo de codigo, porque viven en .venv/Lib/site-packages/nosilo_core/, fuera del arbol que graphify indexa -- no sirven como --nodes de una memoria.

## Outcome

- Signal: useful

## Source Nodes

- GuidedOutcome
- Residual
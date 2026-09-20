---
type: "query"
date: "2026-09-20T11:40:07.676241+00:00"
question: "por que un carril del marcador que le pasa lead_time_stds a Kern sale identico al carril de control"
contributor: "graphify"
outcome: "useful"
source_nodes: ["_inventory_run", "run", "demand_over_risk_period"]
---

# Q: por que un carril del marcador que le pasa lead_time_stds a Kern sale identico al carril de control

## Answer

Porque scm_agent/tools.py::_inventory_run no reenviaba lead_time_stds a jobs.inventory_optimization.run. El job lo acepta y lo usa (mueve el buffer de 11,9 a 238,4 con sigma_L=3), pero el camino del agente lo tiraba en silencio: sin error, sin aviso, y con el entregable diciendo igual '95% service level'. Leccion general: cuando un override no mueve el numero, chequear PRIMERO que el agente lo reenvie (comparar inspect.signature del job contra los kwargs de la llamada en tools.py) antes de sospechar del modelo. Una auditoria por AST de las 310 llamadas a jobs.* dentro de tools.py encontro 76 con parametros sin reenviar; la mayoria es plomeria (lang, prepared, now) pero abc_xyz_job.run::service_level_by_class es el mismo defecto confirmado.

## Outcome

- Signal: useful

## Source Nodes

- _inventory_run
- run
- demand_over_risk_period
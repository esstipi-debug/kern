---
type: "query"
date: "2026-07-06T14:01:21.595346+00:00"
question: "Como se corren varios tools del registry como UN solo paquete comercial (Diagnostico/Starter/Growth) preservando la garantia QA-falla-no-hay-entregable a nivel de paquete?"
contributor: "graphify"
source_nodes: ["run_package", "build_default_registry", "Tool", "Orchestrator"]
---

# Q: Como se corren varios tools del registry como UN solo paquete comercial (Diagnostico/Starter/Growth) preservando la garantia QA-falla-no-hay-entregable a nivel de paquete?

## Answer

scm_agent/packages.py (runner) + scm_agent/package_specs.py (specs DIAGNOSTICO/STARTER/GROWTH). El runner reusa los callbacks prepare/run/qa/deliver de cada Tool registrado via registry.get(key) - cero logica de job duplicada; solo se saltea intent.classify porque el operador eligio el paquete explicitamente (mismo espiritu que el override job_type del orquestador). Garantia a nivel paquete = DOS FASES: fase 1 computa prepare/run/qa de TODOS los pasos sin escribir nada al out_dir (inputs derivados van a un TemporaryDirectory scratch); fase 2 escribe deliver+deck por tool y el deck consolidado (jobs/package_deliverable.py, compone src.deliverable.Deliverable) SOLO si todo paso ejecutado paso QA. Un solo paso ejecutado con qa_failed (aun opcional) => no se escribe NADA; el escape es quitar el input opcional y re-correr. Pasos opcionales sin archivo => skipped, no bloquean. Gotcha clave: cycle_count NO puede recibir ventas.csv long-format directo - su derivacion de valor hace dict comprehension {product: demand*cost} que PISA filas duplicadas en vez de sumarlas; por eso el paquete deriva el input de cycle_count desde el reporte de abc_xyz (PackageStep.derive), que ya agrego bien. CLI: examples/run_package.py (--demo genera intake sintetico completo; --checklist imprime el checklist de intake por paquete). Tests: tests/test_packages.py (17).

## Source Nodes

- run_package
- build_default_registry
- Tool
- Orchestrator
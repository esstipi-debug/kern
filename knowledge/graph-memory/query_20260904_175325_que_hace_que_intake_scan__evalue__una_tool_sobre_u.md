---
type: "query"
date: "2026-09-04T17:53:25.319495+00:00"
question: "Que hace que intake_scan 'evalue' una tool sobre una carpeta de datos del cliente?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["PreparedRegister", "Tool", "build_default_registry()"]
---

# Q: Que hace que intake_scan 'evalue' una tool sobre una carpeta de datos del cliente?

## Answer

Nada mas que declarar required_columns en su Tool. src/intake_scan.py:339-340 marca UNDECLARED solo si la tool no declara ninguna columna, y n_tools_evaluated (:713) cuenta todo veredicto que no sea UNDECLARED. Hoy son 5 tools: abc_xyz, data_quality, excess_obsolete, financial_kpis y procedure_audit. O sea que declarar required_columns es optar a aparecer en el informe de cobertura de CUALQUIER carpeta, incluso una que no tenga nada que ver con la tool: procedure_audit sale RED sobre una carpeta de ventas porque comparte la columna generica 'name' con maestro.csv y le falta last_reviewed_at. Es honesto, no un bug. CONSECUENCIA PRACTICA que costo un fallo de suite en el PR #322: renombrar la columna canonica NO evita que la tool se cuente -- lo que dispara el conteo es declarar contrato, no que columnas se declaren. Y el test tiene que pinear la IDENTIDAD de la quinta tool, no solo el numero, porque una tool futura la reemplazaria sin mover el conteo.

## Outcome

- Signal: useful

## Source Nodes

- PreparedRegister
- Tool
- build_default_registry()
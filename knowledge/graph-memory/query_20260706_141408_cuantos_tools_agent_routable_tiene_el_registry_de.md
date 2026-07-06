---
type: "query"
date: "2026-07-06T14:14:08.521238+00:00"
question: "Cuantos tools agent-routable tiene el registry de Linchpin?"
contributor: "graphify"
outcome: "corrected"
correction: "Son 35 tools, no 34 -- contar con grep -c en vez de asumir; ver commit 143c906 (PR #114) que corrigio MONETIZATION_BRIEF.md."
source_nodes: ["build_default_registry()", "Tool"]
---

# Q: Cuantos tools agent-routable tiene el registry de Linchpin?

## Answer

El catalogo de tools agent-routable de Linchpin son 35, NO 34. Error propio de conteo cometido en esta sesion (2026-07-06) al enumerar el registry por primera vez, propagado a MONETIZATION_BRIEF.md, la entrada de memoria "como_se_deberian_empaquetar..." y la entrada "como_se_obtiene_el_catalogo...". La lista de 35 claves ya estaba completa en la memoria previa (el error era solo el numero declarado, "34" en vez de "35"): inventory_optimization, pricing, leadership_chain, cost_to_serve, sop, abc_xyz, sourcing, ddmrp, landed_cost, warehouse_layout, whatif, financial_kpis, reconciliation, returns, queuing, scheduling, risk, forecast, data_quality, dea, acceptance_sampling, earned_value, learning_curve, odoo_replenishment, excel_replenishment, newsvendor, cycle_count, multi_echelon, transportation, fefo, slotting, simulation, excess_obsolete, facility_location, drp -- son 35 nombres, contarlos explicitamente si hay duda en vez de asumir un numero redondo.

Verificacion: grep -c 'key="' scm_agent/tools.py y grep -c 'reg.register(' scm_agent/tools.py ambos dan 35. Corregido en MONETIZATION_BRIEF.md por otra sesion concurrente (PR #114) en el commit 143c906, e integrado aqui via rebase.

## Outcome

- Signal: corrected
- Correction: Son 35 tools, no 34 -- contar con grep -c en vez de asumir; ver commit 143c906 (PR #114) que corrigio MONETIZATION_BRIEF.md.

## Source Nodes

- build_default_registry()
- Tool
---
type: "query"
date: "2026-07-06T12:49:05.160948+00:00"
question: "Como se obtiene el catalogo exacto y actualizado de los tools agent-routable de Linchpin, con su descripcion de una linea?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["build_default_registry()", "Tool"]
---

# Q: Como se obtiene el catalogo exacto y actualizado de los tools agent-routable de Linchpin, con su descripcion de una linea?

## Answer

Como obtener el catalogo definitivo y exacto de los tools agent-routable de Linchpin (para catalogos comerciales, no adivinar desde README que puede desactualizarse):

grep -n 'key="' scm_agent/tools.py   -> lista las claves exactas (34 al 2026-07-06: inventory_optimization, pricing, leadership_chain, cost_to_serve, sop, abc_xyz, sourcing, ddmrp, landed_cost, warehouse_layout, whatif, financial_kpis, reconciliation, returns, queuing, scheduling, risk, forecast, data_quality, dea, acceptance_sampling, earned_value, learning_curve, odoo_replenishment, excel_replenishment, newsvendor, cycle_count, multi_echelon, transportation, fefo, slotting, simulation, excess_obsolete, facility_location, drp).

grep -n 'description="' -A2 scm_agent/tools.py   -> la descripcion de una linea (a veces partida en 2-3 lineas de string concatenado) de cada tool, en el mismo orden. CLAUDE.md dice explicitamente: "si el registry y cualquier tabla/README difieren, confiar en el codigo" -- build_default_registry() en scm_agent/tools.py es la unica fuente de verdad.

Cada tool tiene ademas: title, intent_keywords (para el matching de intent.classify), options, prepare/run/qa/deliver callbacks. Los jobs concretos (prepare/run) viven en jobs/<key>_job.py o modulos relacionados.

## Outcome

- Signal: useful

## Source Nodes

- build_default_registry()
- Tool
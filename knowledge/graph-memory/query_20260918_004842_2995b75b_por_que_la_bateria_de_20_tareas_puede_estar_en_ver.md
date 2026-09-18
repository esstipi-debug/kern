---
type: "query"
date: "2026-09-18T00:48:42.296099+00:00"
question: "Por que la bateria de 20 tareas puede estar en verde con un emisor de cantidad a pedir que no netea el en-transito?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["_veredicto_oc()", "_candidato_excel()", "build_default_registry()", "net_flow_position()"]
---

# Q: Por que la bateria de 20 tareas puede estar en verde con un emisor de cantidad a pedir que no netea el en-transito?

## Answer

_veredicto_oc() puntua T05 con el MEJOR de tres candidatos (max sobre _candidato_excel/_candidato_odoo/_candidato_ddmrp), asi que un tool que no es candidato -- inventory_optimization, shopify_replenishment, channel_replenishment, drp -- nunca puede ponerse rojo por esta clase de defecto. El diagnostico por-emisor vive aparte en src/bateria/barrido_neteo.py: sonda los 14 emisores y obliga a clasificar a los 60 de build_default_registry(); T05 consume esas sondas para que netear se defina una sola vez.

## Outcome

- Signal: useful

## Source Nodes

- _veredicto_oc()
- _candidato_excel()
- build_default_registry()
- net_flow_position()
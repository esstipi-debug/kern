---
type: "query"
date: "2026-09-23T12:15:39.588570+00:00"
question: "Kern aplica EOQ a ciegas cuando el brief pide EOQ y la demanda es intermitente?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["is_intermittent()", "periodic_review_rs()", "Order-Up-To (R,S) Policy", "Syntetos-Boylan-Croston (SBC) Classification"]
---

# Q: Kern aplica EOQ a ciegas cuando el brief pide EOQ y la demanda es intermitente?

## Answer

No. 'eoq' enruta a inventory_optimization, y jobs/inventory_optimization.py::run pone a los SKU intermitentes (ADI >= 1.32, src/forecasting.py::is_intermittent) en una politica periodica (R, S) con order_quantity=None; solo los regulares reciben (s, Q) con Q = EOQ (src/policies.py). Lo que faltaba era decirselo al que pidio EOQ: src/requested_method.py (names_eoq en prepare, eoq_reframe en run) agrega esa frase al resumen sin mover ningun numero. Sustento L3: knowledge::economic_order_quantity (Vandeput Ch.2), knowledge::sbc_classification (Boylan & Syntetos Ch.5), knowledge::order_up_to_policy (Ch.8). 'lote economico' y 'formula de Wilson' todavia NO enrutan sin LLM.

## Outcome

- Signal: useful

## Source Nodes

- is_intermittent()
- periodic_review_rs()
- Order-Up-To (R,S) Policy
- Syntetos-Boylan-Croston (SBC) Classification
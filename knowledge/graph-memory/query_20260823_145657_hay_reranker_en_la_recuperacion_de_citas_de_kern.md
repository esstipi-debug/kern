---
type: "query"
date: "2026-08-23T14:56:57.138061+00:00"
question: "Hay reranker en la recuperacion de citas de Kern?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["Brecha 2: reranker en la recuperacion de citas", "scm_agent/knowledge.py::KnowledgeBase.ground_citations_detailed"]
---

# Q: Hay reranker en la recuperacion de citas de Kern?

## Answer

No, y son dos tuberias que no se tocan: ground_citations_detailed llama search() IDF puro; los embeddings solo alimentan bridge(). fastembed es extra opt-in ausente de prod y CI. Sin ancla de teoria: el corpus no tiene ningun concepto de reranking.

## Outcome

- Signal: useful

## Source Nodes

- Brecha 2: reranker en la recuperacion de citas
- scm_agent/knowledge.py::KnowledgeBase.ground_citations_detailed
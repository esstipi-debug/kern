# SCM Books Knowledge Graph (L3 domain knowledge)

A graphify knowledge graph built from **17 supply-chain books** (forecasting,
pricing, revenue management, supply chain management, inventory optimization).
This is the **domain knowledge** layer for the agent — distinct from the repo's
`graphify-out/`, which graphs the *code*.

- `graph.json` — 430 nodes · 763 edges · 22 communities (GraphRAG-ready)
- `graph.html` — interactive visual (open in a browser)
- `GRAPH_REPORT.md` — communities, god nodes, surprising cross-book connections

## What's inside

Forecasting (Boylan & Syntetos, Gilliland, Hyndman FPP3, Box-Jenkins),
pricing (Nagle, Simon, Phillips), revenue management (Gallego & Topaloglu,
Talluri & van Ryzin), supply chain (Chopra & Meindl, Operations & SCM),
inventory optimization (Vandeput), plus 4 arXiv papers on dynamic/RL pricing.

The inventory concepts this repo's `src/` engine implements (EOQ, reorder
point, safety stock, (s,Q)/(R,S) policies, fill rate, cost/service-level
optimization, gamma demand, multi-echelon GSM, newsvendor, discrete demand,
simulation optimization) are attributed to **Vandeput** — the source the code
actually follows, chapter by chapter — so the agent's L3 citations and the
`bridge()` (theory ↔ code) point at the right book. Prior cross-book coverage
is kept as graph edges.

Concept node IDs are canonical
(`bullwhip_effect`, `crostons_method`, `dynamic_pricing`), so the same concept
across books merges into one node — that's what forms the cross-book bridges.

## Intended use (L3)

`scm_agent` should query this graph for domain grounding: definitions, which
method applies to a demand pattern, and which book/chapter to cite. The
graph's `source_location` carries chapter references for citations.

## Honest gaps

- **Box-Jenkins**: the source PDF was an OCR watermark scan with no text layer
  — present only as an isolated source node, no extracted concepts.
- **Hyndman FPP3**: extraction capped at the decomposition chapters; later
  chapters (ARIMA, ETS, regression) are not yet in the graph.
- **Chase (Demand-Driven Forecasting)**: image-only scan, excluded.

Regenerate / extend with `/graphify` over the book PDFs, then refresh these files.

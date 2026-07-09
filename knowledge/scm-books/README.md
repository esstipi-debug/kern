# SCM Books Knowledge Graph (L3 domain knowledge)

A graphify knowledge graph built from **24 supply-chain books** (forecasting,
pricing, revenue management, supply chain management, inventory optimization,
manufacturing planning & control, operations management, logistics & operations
strategy, sustainable logistics, **and supply-chain leadership**) plus a
**25th source, AI applied to supply chains** (see below). This is the
**domain knowledge** layer for the agent — distinct from the repo's
`graphify-out/`, which graphs the *code*.

- `graph.json` — 1953 nodes · 3810 edges · 123 communities (GraphRAG-ready)
- `graph.html` — interactive visual (open in a browser)
- `GRAPH_REPORT.md` — communities, god nodes, surprising cross-book connections

## What's inside

Forecasting (Boylan & Syntetos, Gilliland, Hyndman FPP3, Box-Jenkins),
pricing (Nagle, Simon, Phillips), revenue management (Gallego & Topaloglu,
Talluri & van Ryzin), supply chain (Chopra & Meindl, Operations & SCM),
inventory optimization (Vandeput), manufacturing planning & control (Vollmann),
global supply chain & operations (Ivanov), logistics strategy (Christopher),
sustainable logistics (Grant, Trautrims & Wong), operations management (Heizer,
Render & Munson), supply-chain leadership (Palamariu & Alicke, *From Source to
Sold*), plus arXiv papers on dynamic/RL pricing.

The inventory concepts this repo's `src/` engine implements (EOQ, reorder
point, safety stock, (s,Q)/(R,S) policies, fill rate, cost/service-level
optimization, gamma demand, multi-echelon GSM, newsvendor, discrete demand,
simulation optimization) are attributed to **Vandeput** — the source the code
actually follows, chapter by chapter — so the agent's L3 citations and the
`bridge()` (theory ↔ code) point at the right book. Prior cross-book coverage
is kept as graph edges.

The **leadership layer** (Palamariu & Alicke, *From Source to Sold*, 2022) adds
~312 concept nodes drawn from 26 supply-chain-leader interviews, plus the book's
**CHAIN model** as explicit citable nodes — `chain_model` and its five
dimensions `chain_collaborative` / `chain_holistic` / `chain_adaptable` /
`chain_influential` / `chain_narrative`. This is the L3 source that grounds the
`leadership_chain` capability (`jobs/leadership.py`); the model is bridged to
`supply_chain_strategy` so leadership links to the quantitative layer.

The **operations/strategy/sustainability layer** adds ~640 concept nodes across
four authoritative sources: **Vollmann** *Manufacturing Planning & Control*
(S&OP, MPS, MRP, DRP, capacity, JIT — the planning/manufacturing spine the graph
previously lacked), **Ivanov** *Global Supply Chain & Operations* (sourcing,
network design, risk & resilience, digital SC / Industry 4.0), **Christopher**
*Logistics & SCM* (agility, lead-time, network competition, 3PL/4PL), and
**Grant, Trautrims & Wong** *Sustainable Logistics & SCM* (green logistics,
reverse logistics, circular economy, sustainable procurement). Shared concepts
merge by canonical label into cross-book bridges.

**Chopra & Meindl** was then deepened from 32 to ~312 nodes (strategy, network
design, sourcing, transportation, coordination, revenue management), and
**Heizer, Render & Munson** *Operations Management* added ~161 nodes of the
operations layer the graph lacked — quality/SPC & Six Sigma, process & layout
design, facility location, project management (PERT/CPM), QFD, capacity/TOC, and
decision analysis.

**Jacobs & Chase** *Operations and Supply Chain Management* (15th ed., 2018) was
scanned in full and added as the 24th source — but only the methods genuinely
*new* to Linchpin (most of the book overlapped with Heizer/Chopra/Vollmann and was
left out). The additions are a **queuing / waiting-line** family (M/M/1, M/M/c,
M/D/1, finite-source, Kingman G/G/c) the graph entirely lacked, an **operational
scheduling** family (Johnson's rule, assignment/Hungarian, dispatching rules,
first-hour rostering), and **earned value**, the **learning curve**, **kanban /
takt** sizing, **capacity cushion**, **acceptance-sampling** plan design, and
**DEA**. Each is bridged to the `src/` module that implements it (`queuing.py`,
`scheduling.py`, `earned_value.py`, `learning_curve.py`, `kanban.py`,
`capacity_planning.py`, `acceptance_sampling.py`, `dea.py`).

Concept node IDs are canonical
(`bullwhip_effect`, `crostons_method`, `dynamic_pricing`), so the same concept
across books merges into one node — that's what forms the cross-book bridges.

**Cohen & Dai** (eds.), *AI in Supply Chains: Perspectives from Global Thought
Leaders* (Springer Series in Supply Chain Management vol. 27, 2026) was added
as the **25th source** — 106 nodes / 140 edges across 7 communities (AI Supply
Chain Research, Enterprise Supply Chain Tech, AI Hardware & Infrastructure,
Digital Supply Chain Innovation, Reinforcement Learning Tools, LLM Operations &
Planning, AI-Enabled Operations Management). This is the modern AI/LLM
application layer the graph previously lacked — the other 24 sources are
almost entirely classical OR/inventory theory, forecasting, and pricing, with
only scattered arXiv RL-pricing papers touching AI methods directly. The
Springer edition is **not Open Access** (paywalled per-chapter, ~$30/chapter),
so only the **10 of 20 chapters with a legitimate free preprint** were
ingested (author self-archive on SSRN/arXiv or an institutional repository —
no shadow-library sources): Cachon's "modest impact so far" framing essay,
Tang's AI-risk-management survey, Fransoo/Peels/Udenio and Netessine/Shunko on
the semiconductor/data-center supply chain *behind* AI, Simchi-Levi et al. on
LLMs for SC decisions, Hu & Liu on coupling AI with OM theory, Gijsbrechts/
Boute/Van Mieghem/Zhang on DRL for inventory (including a real Alibaba Tmall
deployment), Raman & Kwon on AI in retail labor scheduling, Lee/Shen/Qi/Chen's
JD.com AI-transformation case study, and Tayur's skeptical closing essay. Cost
to extract: $0.11 (Kimi backend, 35k in / 18k out tokens) — cheap enough that
buying the other 8 chapters was not worth it relative to what they'd add.

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
- **Ivanov (Global SC & Operations)**: partial (~70 nodes) — extraction was
  truncated by the Kimi backend's daily-token / concurrency limits; the strategy,
  risk and digital chapters are covered but not exhaustively.
- **Extraction backends**: forecasting/pricing/inventory books were built earlier;
  leadership + Christopher + Grant were extracted via host subagents (no API key),
  Vollmann + Ivanov via the Kimi backend. Mixed provenance, single canonical graph.
- **Leadership clustering**: the leadership concepts are genuinely distinct (no
  duplicate merging), but cross-chapter semantic bridges are sparse, so they
  split into many per-theme communities (each labeled by its dominant concept,
  e.g. *CHAIN Leadership Model*, *Talent and Leadership Development*, *Supply
  Chain Resilience*) rather than one block. Citations/grounding are unaffected.
- **Cohen & Dai (AI in Supply Chains) is half-covered on purpose**: 10 of 20
  chapters (Allon; Miller; Perakis/Harsha/Cristian's decision-focused-AI
  chapter; de Véricourt; Swaminathan/Xu; Smalley/Keskinocak on humanitarian
  SC; Olsen on agricultural SC; Robinson on AI readiness) have **no free
  legitimate version anywhere** — verified against SSRN, ResearchGate, Google
  Scholar, and every author's faculty page. Springer sells each for ~$30.
  Two more (Cohen/Agrawal/Deshpande's ML-planning chapter, Song's automation
  chapter) have an adjacent-but-not-verbatim free version (an HBR reprint and
  a Duke Fuqua plain-language adaptation, respectively) that was deliberately
  **not** ingested to avoid attributing paraphrased content to the wrong
  register/venue. If the missing 8 are ever purchased, re-run the extraction
  over the full 20-chapter corpus and re-merge.

Regenerate / extend with `/graphify` over the book PDFs, then refresh these files.

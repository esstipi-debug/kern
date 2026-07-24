# SCM Books Knowledge Graph (L3 domain knowledge)

A graphify knowledge graph built from **24 supply-chain books** (forecasting,
pricing, revenue management, supply chain management, inventory optimization,
manufacturing planning & control, operations management, logistics & operations
strategy, sustainable logistics, **and supply-chain leadership**) plus a
**25th source, AI applied to supply chains**, a **26th source, Kern's own
capability↔role atlas**, and a **27th source, supply chain risk & resilience**
(see below). This is the **domain knowledge** layer for the agent — distinct
from the repo's `graphify-out/`, which graphs the *code*.

- `graph.json` — 3002 nodes · 6141 links · 202 communities (GraphRAG-ready).
  `GRAPH_REPORT.md` says 3001 nodes / 6113 edges / 201 communities: graphify builds
  a simple undirected graph, which drops one malformed pre-existing node (a
  `reminder::` node with no `source_file`) and collapses 28 links that share
  endpoints but carry a different `relation`. Both numbers are correct for what
  they describe.
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

**Kern's own capability↔role atlas** (`documentation/KERN_ATLAS_SOMBREROS_SCM.md`)
was added as the **26th source** — 100 nodes / 185 edges (9 nodes were exact
canonical-concept duplicates of existing graph nodes, e.g. `cost_to_serve`,
`landed_cost`, `facility_location`; their edges were redirected to the
existing canonical node instead of creating a shadow copy) across 9
communities. Unlike the first 25 sources (external published works), this one
documents Kern's *own* 29-role↔certification↔capability mapping — every one
of the 29 SCM "hats" (Demand Planner through Sustainability/ESG Lead), the 27
distinct certifications they cite, and the bridge edges from each role to the
Kern capabilities the atlas says align with it. Also captured as concept/
rationale nodes: the atlas's structural finding (Kern is a decision/design
layer, never a transactional execution system, across all 8 SCOR+transversal
zones with zero exceptions) and its proposal for a "hat lens" mode in
`scm_agent/modes.py` (not yet built — a code feature, tracked separately).

**Merging a 26th source into an already-citable graph is not a copy-paste
operation** — it surfaced (and this ingestion fixed) a real latent bug:
`jobs/repricing.py::gated_citations` was still grounding its central pricing
guardrail on a **3-candidate pool**, the exact shallow-pool recall defect
already fixed at pool 6-8 in `jobs/integrated_plan.py`,
`jobs/price_intelligence.py`, and `scm_agent/packages.py::_run_step` (3.0-audit
finding #7) — this module was the one instance that fix never reached. Adding
this source's on-topic-sounding role/certification labels was enough to crowd
the real pricing-theory anchors out of the top 3 candidates, silently zeroing
every repricing guardrail's citations (17 test failures caught this before it
shipped). Widened to match the other three call sites' pool size; see
`jobs/repricing.py`'s `_CANDIDATE_POOL` comment for the full mechanism.
**Lesson for the next source added here:** a clean "0 id collisions" merge is
not sufficient proof of safety — run the *full* test suite (not just the
citation/knowledge-focused subset) before trusting a graph merge, since a new
source's node *labels* can silently move a shallow-pool grounding call's
top-N ranking anywhere in the codebase, not just at the merge site.

**Khan, Huth, Zsidisin & Henke (eds.)**, *Supply Chain Resilience:
Reconceptualizing Risk Management in a Post-Pandemic World* (Springer Series in
Supply Chain Management vol. 21, 2022) was added as the **27th source** — 948
new nodes / 2146 new links, extracted from **all 245 PDF pages** (front matter +
13 chapters, every page in exactly one chunk, verified contiguous with no gaps).
Of the 2260 extracted edges, 38 were pruned as low-confidence INFERRED (<0.75)
and 76 collapsed as duplicate parallel links. 947 of the 948 new nodes are
connected; one (`material_shortage`) is an orphan.
This closes the graph's largest thematic hole: the other 26 sources are almost
entirely classical OR/inventory/forecasting/pricing theory, and risk &
resilience appeared only in fragments (Ivanov's risk chapters are flagged
incomplete below). 77 of its concepts matched existing canonical ids and were
**bridged onto the existing nodes rather than duplicated** — the merge is
purely additive (an id-set guard confirmed zero pre-existing ids were lost).

What it genuinely adds, beyond restating resilience theory: a **supplier risk
tower** (Schaeffler's production early-warning architecture — signals, scoring,
alert cadence, escalation, the mitigation actions it triggers, Ch 9); the
**inventory / capacity / capability triad** for pandemic stockpiling from Sodhi
& Tang, including why pure physical stockpiling fails (Ch 11); a full
**supply-chain-finance instrument layer** the graph completely lacked — reverse
factoring, dynamic discounting, approved payables/receivables, inventory
financing, and distress propagation through the value network (Ch 7);
**simulation-driven rapid reconfiguration** under public-private partnership
(Fraunhofer OTD-NET, ventilator ramp-up, Ch 6); LSP/freight **visibility and
control-tower** practice (Kuehne+Nagel, Ch 5); an empirical **German SCRM
status-quo** study (Ch 4); and van Hoek's deliberately **skeptical closing
chapter** on how little of the pandemic response became structural rather than
temporary (Ch 13) — a useful counterweight to the rest of the corpus's
optimism. Citations carry chapter *and* PDF page (`Khan et al. (2022), Ch 9,
PDF p.177`).

**This merge again proved the "run the FULL suite" lesson — and then proved that
the full suite is still not enough.** The 948 new nodes are lexically strong for
risk/logistics queries, so they displaced previously-cited nodes in the top-N
candidate ranking of `scm_agent/packages.py::_step_citations`, which then failed
the citation gate's 2-hop anchor test — shipping **zero citations**. The test
suite caught two victims (`risk`, `odoo_replenishment`). An adversarial review
of the merge found **two more that no test covered** (`digital_twin`,
`launch_readiness`) plus `vehicle_routing`, because every existing test iterated
only tools used by a *package*, and those three are registered but not package
steps. `tests/test_packages_citations.py::test_no_anchored_tool_regressed_to_zero`
now pins all 45 anchored+registered tools so this class of regression cannot
recur silently.

Three things worth carrying forward:

1. **The pool ceiling is not a constant, and it moves in both directions.** The
   old comment pinned pool 8 because data_quality broke at 11 and cycle_count at
   12. After this merge, 8 *starves* four tools and the noise threshold rose to
   16 (at 17 data_quality re-admits "Cost of Quality" / "House of Quality").
   Re-measure with a 45-tool sweep on every source addition.
2. **The obvious canonical anchor was the wrong one.** `risk` needed a wider
   anchor, and `supply_chain_risk` looks like the correct parent — but it sits 1
   hop from three book hubs (Chopra/Christopher/Grant), which blows `risk`'s
   2-hop closure from 522 nodes (17.4%) to 1389 (46.3%), admitting
   reverse_auction / dynamic_pricing / revenue_management. That is exactly the
   shared-book-hub loophole the module rejects for `promotion_timing`. The
   hub-free `risk_assessment` yields the *same three citations* at closure 586
   (19.5%). **Check book-hub adjacency before adding any anchor.**
3. **Losing a citation is not always a regression.** `vehicle_routing`'s two
   prior citations were lexical false friends ("Logistics as the Vehicle for
   Change"; a "Route Sheet" from a product-design chapter). It is now recorded as
   anchor-islanded rather than papered over with a looser anchor — the graph has
   no real VRP theory to point at.

Net effect on citations: `digital_twin`, `price_watch` and `risk` improved
(price_watch recovered a pre-existing zero), `launch_readiness` and
`odoo_replenishment` held, `vehicle_routing` went to an honest zero.

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

- **Khan et al. (Supply Chain Resilience) is fully ingested but its community
  labels are heuristic**: all 245 pages were extracted, but re-clustering after
  the merge renumbered communities 132 → 201 and the labels in `GRAPH_REPORT.md`
  are now derived deterministically from each community's highest-degree node
  (`scripts/regen_books_report.py`) rather than an LLM naming pass. Node-level
  citations and grounding are unaffected — only the report's section titles are
  affected, and they are arguably better than the prior state (123 of the old 132
  were bare "Community N" placeholders). Re-run `graphify label` with a backend if
  you want prose community names.
- **Louvain clustering is stochastic**: re-running `scripts/recluster_books_graph.py`
  yields a slightly different community count (194-202 observed) and renumbers
  communities. Nothing in the query path depends on community ids, but
  `knowledge/community_summaries.json` is keyed by them — it self-invalidates on
  a node/community count change and rebuilds on the next `KnowledgeBase()`.
- **`graph.html` was not regenerated** for this merge; the committed copy renders
  the **26-source** graph (last rebuilt at the Kern-atlas ingestion). Rebuild it
  with `graphify export html` when a visual refresh matters.

Regenerate / extend with `/graphify` over the book PDFs, then refresh these files.
Per-source helper scripts live in `scripts/`, in run order:

1. `merge_khan_resilience.py` — chunk JSONs → canonical graph. Applies every
   transformation the committed artifact needs (namespacing, `source_file`
   normalisation, rubric snapping, low-confidence pruning, parallel-edge
   collapsing) so `graph.json` is reproducible from the script alone. Guards:
   id-set (no pre-existing id may vanish), parallel-edge, and an idempotency
   check that refuses a second run instead of silently doubling every edge.
2. `recluster_books_graph.py` — in-place re-cluster preserving node attributes.
3. `regen_books_report.py` — report rebuild, no API key needed.

Steps 1 needs the raw corpus and chunk JSONs under
`knowledge/scm-books-rebuild/<book>/`, which is **gitignored by design** (the raw
book text is not committed) — regenerate them locally before merging. All three
scripts need the `graphify` package, which lives in the graphify tool venv, not
the repo `.venv` (see `graphify-out/.graphify_python`).

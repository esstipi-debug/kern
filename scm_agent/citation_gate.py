"""Citation-grounding gate (capability M-E5): verifies each candidate L3
citation actually resolves to a real, topically-connected graph node before
it reaches a client deliverable.

The gap this closes: ``KnowledgeBase.ground_citations`` ranks candidates by
IDF-weighted token overlap with the tool's keywords/brief — a real, useful
signal, but nothing before this stopped a ranked-but-off-topic hit from
reaching the deck (the brief mentioning a shared word irrelevant to the
running tool). This module is the veto: a citation must resolve to a graph
node that exists AND sits within ``MAX_HOPS`` of one of the running tool's
own anchor concepts (``TOOL_CONCEPTS``, a static tool_key -> concept-id map
curated against ``knowledge/scm-books/graph.json`` — every id below is
verified to exist as of this module's own test suite).

A citation that fails EITHER check is omitted, never replaced: this gate
only removes candidates, it never invents a substitute. If fewer than
``MIN_CITATIONS`` survive, the whole batch is dropped to empty rather than
shipping a single, cherry-picked-looking citation — the caller (see
``scm_agent/packages.py``) then renders that step's methodology section
without citations instead. Every omission is logged (inspectable via the
``linchpin.citation_gate`` logger) AND returned structurally in
``GateResult.omitted``, so both a human reading logs and a test asserting
on the return value can see exactly what was dropped and why.

Deliberately scoped to the commercial-package runner only (per the 2.0
protocol): ``scm_agent/packages.py::_run_step`` is the only caller. The
Orchestrator's single-tool path (``webapp/app.py``, the MCP server,
``examples/run_agent.py``) keeps calling the ungated ``ground_citations`` —
gating it too was judged out of scope (and, per E4's own hard-learned
lesson, a good way to introduce an unplanned behavior change on a live
production surface with no callers asking for it).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .knowledge import GroundedCitation, KnowledgeBase

_LOG = logging.getLogger("linchpin.citation_gate")

MIN_CITATIONS = 2
MAX_HOPS = 2

# tool_key -> anchor concept ids (bare, e.g. "safety_stock" not
# "knowledge::safety_stock" - KnowledgeBase resolves the namespace). Every id
# here is verified to exist in knowledge/scm-books/graph.json by
# tests/test_citation_gate.py::test_every_anchor_concept_exists - update that
# test (it will fail loudly) if the graph is ever regenerated without one of
# these ids. A tool absent from this map has no anchors, so every one of its
# candidate citations is omitted (logged as "no concept map"): safer than a
# silently-empty degrade that looks identical to "everything resolved".
TOOL_CONCEPTS: dict[str, tuple[str, ...]] = {
    "inventory_optimization": ("safety_stock", "service_level", "cycle_service_level"),
    "pricing": ("basic_pricing_theory", "price_sensitivity_measurement", "markdown_pricing"),
    "leadership_chain": ("chain_model", "collaborative_leadership", "authentic_leadership"),
    "cost_to_serve": ("cost_to_serve", "activity_based_costing"),
    "sop": ("vollmann_sop", "aggregate_planning"),
    "abc_xyz": ("abc_classification", "vollmann_abc_analysis", "pareto_law"),
    "sourcing": ("outsourcing_decision", "local_sourcing", "offshore_sourcing"),
    "supplier_management": (
        "kraljic_matrix", "supplier_relationship_management",
        "supplier_development", "procurement",
    ),
    "ddmrp": ("demand_driven_supply_chain", "drum_buffer_rope", "vollmann_customer_order_decoupling_point"),
    "landed_cost": ("landed_cost",),
    "warehouse_layout": ("facility_layout", "load_distance_layout"),
    # --- MIN_CITATIONS starvation, measured 2026-08-24 --------------------------
    # Four tools below were filed as "anchor-islanded" (zero at any pool). Re-measured
    # on the committed 3019-node graph, the diagnosis was wrong for all four: each one
    # ranked its OWN anchor at hop 0 as a perfectly on-topic candidate and then lost
    # the whole batch to the MIN_CITATIONS=2 floor -- one survivor, degraded to none.
    # They were one citation short, not starved of theory. Each gained exactly one
    # extra anchor, picked by the rules this module already follows: keep the 2-hop
    # closure far under the ~40% book-hub ceiling, prefer the least hub-adjacent
    # candidate, and let the REPLAYED gate output (not the closure size) break ties.
    # MIN_CITATIONS itself is untouched -- it is the rule that stops a single
    # cherry-picked-looking citation from shipping, not the bug.

    # whatif: `sensitivity_analysis` alone kept 1 (itself) -> zeroed. `break_even_analysis`
    # is what this tool actually computes (packages.py calls its output "the tornado +
    # break-even study deck"); degree 5, 2-hop closure 206 (6.8%), one book hub
    # (heizer_render). Combined closure 549 (18.2%). Replayed gate: Break-Even Analysis,
    # Break-Even Point, Sensitivity Analysis -- all three on topic.
    # REJECTED, by measurement: `scenario_planning`, despite "What-If Scenario Planning"
    # being its literal label. It sits 1 hop from TWO hubs (book_palamariu_alicke AND
    # supply_chain_resilience), closure 785 (26.0%), and its replayed output leads with
    # "Supply Chain Stress Test" from khan-supply-chain-resilience.txt -- the same
    # resilience-book false friend documented for intake_scan and flow_map below. Adding
    # BOTH is worse still (closure 970, 32.1%): the Khan stress test then displaces the
    # scenario-planning citation that was the only reason to want the anchor.
    "whatif": ("sensitivity_analysis", "break_even_analysis"),
    "financial_kpis": ("vollmann_inventory_turnover", "working_capital_efficiency"),
    "reconciliation": ("inventory_record_accuracy",),
    "returns": ("reverse_logistics", "returnability", "refurbishing"),
    "queuing": ("mm1_queue", "md1_queue", "queuing_analysis", "queue_server_optimization"),
    "scheduling": ("dispatching_rules", "vollmann_spt", "vollmann_edd", "johnsons_rule"),
    # risk_assessment was added when the Khan et al. (2022) resilience book landed as
    # source #27: the original two anchors are narrow nodes chosen when the graph had
    # almost no risk coverage, so the new book's risk concepts sat 3-4 hops away and
    # the tool degraded to zero citations.
    # It is deliberately NOT the broader `supply_chain_risk`, which looks like the
    # obvious canonical parent but is 1 hop from THREE book hubs (Chopra 356 /
    # Christopher 265 / Grant 192) -- the shared-book-hub loophole this module rejects
    # for `promotion_timing` below. Measured 2-hop closures on the 3002-node graph:
    # original anchors 522 (17.4%), +supply_chain_risk 1389 (46.3%, the largest of all
    # 45 anchor sets, admitting reverse_auction/dynamic_pricing/revenue_management),
    # +risk_assessment 586 (19.5%). Both produce the SAME three citations, so the
    # hub-free anchor is strictly better. Keep new anchors off the book hubs.
    "risk": ("atomic_holistic_risk", "collaborative_risk_mitigation", "risk_assessment"),
    # disruption_scan (tool #46, GDELT news screen) grounds on disruption/resilience
    # theory, distinct from the risk tool's mitigation anchors. Deliberately
    # disruption-specific concept nodes, not the broad supply_chain_risk hub.
    "disruption_scan": ("supply_chain_disruption", "supply_chain_resilience", "disruption_forecasting"),
    # flow_map (tool #47, observed-network transit measurement) grounds on lead-time
    # variability and process/value-stream mapping, not on network design: the
    # verdict is the buffer that transit spread forces, not where to put a DC.
    # Measured 2-hop closures on the committed 3002-node graph for every candidate
    # considered, in order of preference: lead_time_variability 608 nodes (20.3%),
    # lead_time_gap 266 nodes (8.9%), value_stream_mapping 195 nodes (6.5%),
    # supply_chain_mapping 340 nodes (11.3%), bullwhip_effect 769 nodes (25.6%).
    # None crosses the ~40% book-hub ceiling, so the anchor was picked on citation
    # quality instead: replaying the real grounding call
    # (kb.ground_citations_detailed(tool.intent_keywords, tool.title, limit=16))
    # against each candidate pair, lead_time_variability + value_stream_mapping
    # is the only combination whose GATED output stays on-topic (End-to-End
    # Lead-Time Variability, Value-Stream Mapping, Activity Map, Bottleneck Time,
    # Variability in Activity Times, Flow Time). lead_time_gap adds nothing over
    # lead_time_variability (identical kept set). supply_chain_mapping and
    # bullwhip_effect both pull in Khan et al. disruption/resilience noise
    # (a "Finding - Measured Resiliency..." case study, a semiconductor shortage
    # case) that has nothing to do with a client's observed lane transit times.
    #
    # IMPORTANT (2026-07-31 fix wave, IMPORTANT 4): these anchors govern what THIS
    # module's filter_citations() would keep -- they are NOT on flow_map's shipped
    # path today. The orchestrator (scm_agent/orchestrator.py:193, `_ground`) calls
    # the UNGATED `knowledge.ground_citations(tool.intent_keywords, brief, limit=5)`
    # directly; filter_citations is only wired into jobs/integrated_plan.py,
    # jobs/price_intelligence.py and jobs/repricing.py. flow_map is not among them.
    # Measured consequence, replaying the real orchestrator call
    # (KnowledgeBase().ground_citations(flow_map_tool().intent_keywords, "", limit=5)):
    # 3 of the 5 shipped citations are Khan et al. resilience-book noise --
    # "Semiconductor and Raw Material Supply Bottleneck", "Realistic Transit Time
    # Calculation", and "Fraunhofer Institute for Material Flow and Logistics (IML)"
    # (all khan-supply-chain-resilience.txt) -- against only 2 of 5 from the anchors
    # above (Christopher ch.9 "End-to-End Lead-Time Variability", Heizer ch.7
    # "Value-Stream Mapping"). Do not read this comment as a guarantee about the
    # shipped deck; wiring filter_citations into flow_map's path is a separate,
    # larger-blast-radius change, not made here.
    "flow_map": ("lead_time_variability", "value_stream_mapping"),
    # org_friction_audit (organizational-friction audit, tool #49; ANCHOR-ISLANDED, see
    # tests/test_packages_citations.py::_ISLANDED_TOOLS) grounds on Hidden Factory alone --
    # the one node in the committed graph that is genuinely on-topic for this tool's four
    # detectors (hidden factory, Goodhart ratchet, accountability vacuum, unclassified
    # workaround). Measured 2026-07-31 by BFS over knowledge/scm-books/graph.json + a real
    # replay of _step_citations: vollmann_hidden_factory has degree 1 (its only edge is
    # vollmann_jit) and a 26-node 2-hop closure containing nothing else on-topic -- a
    # near-isolate, same shape as vehicle_routing's vollmann_traveling_salesman below.
    # Two other candidates were measured and REJECTED, not merely omitted:
    #   - misaligned_incentives: sits 1 hop from book_chopra_meindl (2-hop closure 356
    #     nodes), so admitting it would self-validate almost anything from that entire
    #     book via the shared-book-hub loophole EXCLUDED_CONCEPTS exists for (the same
    #     anti-pattern documented under `risk` above) -- and it still produced zero real
    #     citations when replayed, so there is no recall gain to trade against that risk.
    #   - strategic_inertia: a small (8-node) but topically irrelevant closure, entirely
    #     pandemic/crisis-recovery content (khan-supply-chain-resilience.txt) with nothing
    #     about metrics, targets or accountability.
    # No node for Goodhart's law, target-ratcheting, workaround typologies or metric-gaming
    # exists anywhere in the 3002-node graph (checked by direct keyword search) -- the
    # corpus is quantitative-methods-heavy and simply has no connected content for this
    # tool's political-layer subject. Fix by ingesting organizational-behavior/performance-
    # measurement-dysfunction theory (Goodhart, Ridgway, Merchant & Van der Stede), not by
    # a looser or hub-adjacent anchor.
    "org_friction_audit": ("vollmann_hidden_factory",),
    "forecast": ("forecastability", "crostons_method", "sbc_classification", "syntetos_boylan_approximation", "tsb_method"),
    # No topically-better node exists in the committed graph for SKU-master/
    # GTIN data quality (verified during the 2026-07 adversarial review) - this
    # tool degrades to zero citations on every real run, which is the correct,
    # safe outcome (see the module docstring), not a bug to "fix" with a
    # closer-sounding but wrong anchor.
    "data_quality": ("step_product_data_standard",),
    # intake_scan (tool #50 -- NOT #49: cross_impact and org_friction_audit went in
    # between, as HANDOFF.md already recorded on 2026-08-03 and this comment did not;
    # org_friction_audit above is the real #49, so the file carried two of them.
    # Verified 2026-08-24 against build_default_registry().list(), which is the only
    # count that cannot go stale. Raw-folder data-readiness audit; registered in
    # Task 7 of the 2026-07-31 intake-scan plan, scm_agent/tools.py) is a
    # DELIBERATE, measured absence from this map, same discipline as data_quality
    # right above. Candidates checked, in order of preference, against the
    # committed 3002-node graph (2026-07-31):
    #   master_data_management   -- does not exist (kb.node_exists() False)
    #   data_quality_dimensions  -- does not exist (kb.node_exists() False)
    #   information_sharing      -- EXISTS, 2-hop closure 772 nodes (25.7%, under
    #                                the ~40% ceiling) but REJECTED: it is 1 hop
    #                                from TWO book hubs (book_chopra_meindl,
    #                                book_christopher) plus supply_chain_resilience
    #                                -- the exact shared-book-hub loophole the
    #                                `risk` entry above documents. Replaying the
    #                                real grounding call (kb.ground_citations_detailed
    #                                with title "Data Readiness Scan" and the
    #                                intake/data-audit keyword set now registered on
    #                                the tool, limit=16) and gating on this anchor alone kept
    #                                exactly 5 citations -- ALL FIVE from
    #                                khan-supply-chain-resilience.txt on "Readiness,
    #                                Response and Recovery" / "Post-Crisis Risk
    #                                Readiness" / big-data-analytics chapters: a
    #                                disaster-recovery book riding the "readiness"/
    #                                "data" lexical overlap with this tool's own
    #                                keywords, not a single citation about column
    #                                structure, coverage or data quality. Same
    #                                false-friend class as excess_obsolete/
    #                                excess_capacity_and_inventory and returns/
    #                                reverse_auction above -- confirmed by measurement,
    #                                not assumed from the closure percentage alone.
    #   step_product_data_standard -- EXISTS, 2-hop closure 195 nodes (6.5%), same
    #                                anchor data_quality already uses; alone it kept
    #                                ZERO citations against the same grounding call
    #                                (matches data_quality's own documented zero
    #                                immediately above). Combined with
    #                                information_sharing it just inherits that
    #                                anchor's 5 off-topic Khan citations -- adds
    #                                nothing clean.
    # No candidate both exists and stays on-topic, so intake_scan is intentionally
    # left OUT of this map rather than anchored on a closer-sounding but wrong
    # node. tests/test_citation_gate.py::test_every_registered_tool_has_a_concept_map
    # carries intake_scan in its own documented exemption set
    # (_NO_CONCEPT_MAP_BY_DESIGN); tests/test_packages_citations.py's
    # _CORRECTLY_ZERO_TOOLS also lists intake_scan. Both were added by Task 7 when
    # it registered the tool (see the Task 7 report) -- neither is pending.
    #
    # IMPORTANT (2026-07-31 fix wave, IMPORTANT 4): "omitted from this map" only
    # controls what THIS module's filter_citations() would keep -- it is NOT what
    # the shipped intake_scan deck actually cites (an omitted tool "omits every
    # candidate citation" is true of filter_citations' own "no concept map"
    # branch below, false of the deck a client receives). filter_citations is
    # wired into jobs/integrated_plan.py, jobs/price_intelligence.py and
    # jobs/repricing.py only; intake_scan is not among them, same gap as
    # flow_map's own IMPORTANT-4 note above. The orchestrator
    # (scm_agent/orchestrator.py, `_ground`) calls the UNGATED
    # `knowledge.ground_citations(tool.intent_keywords, brief, limit=5)` directly
    # for every single-tool run, intake_scan included. Measured consequence,
    # replaying the real orchestrator call (KnowledgeBase().ground_citations(
    # intake_scan_tool().intent_keywords, "", limit=5)): 3 of the 5 shipped
    # citations are Khan et al. resilience-book noise -- "Post-Crisis Risk
    # Readiness Regression", "Readiness Phase", and "Barriers to Adopting
    # Sophisticated Risk Methods" (all khan-supply-chain-resilience.txt) -- the
    # exact false-friend class measured and rejected above. Wiring
    # filter_citations into intake_scan's path is a separate, larger-blast-radius
    # change, not made here.
    # dea: the anchor was a degree-1 near-isolate (23-node closure) and the only
    # in-radius candidate was itself -> 1 survivor -> zeroed. `capacity_efficiency`:
    # degree 2, closure 195 (6.5%), one book hub (heizer_render); combined 218 (7.2%).
    # Replayed gate: Data Envelopment Analysis, Competitive Benchmarking, Capacity
    # Efficiency.
    # `benchmarking` produces the IDENTICAL three citations but is 1 hop from TWO book
    # hubs (christopher + heizer_render) and closes 467 nodes (15.5%) instead of 218.
    # Same reasoning as `risk` above: when two candidates give the same kept set, the
    # less hub-adjacent one is strictly better, so `benchmarking` is not used.
    "dea": ("data_envelopment_analysis", "capacity_efficiency"),
    "acceptance_sampling": ("acceptance_sampling", "single_sampling_plan"),
    # earned_value: same shape -- degree-1 anchor, itself the only in-radius candidate.
    # Earned value IS a project-control technique and the graph carries Heizer's whole
    # project-management chapter; it just sat 5-6 hops away. `project_controlling`:
    # degree 2, closure 196 (6.5%), one book hub; combined 219 (7.3%), 8 survivors.
    # Replayed gate: Earned Value Management (SPI/CPI), Project Controlling, Project
    # Crashing. `project_management` (degree 10, closure 204) yields the SAME kept set
    # and is the broader node, so the narrower one is used; adding `project_scheduling`
    # on top changes nothing at all (same 8 survivors, same 3 shown) and is left out.
    "earned_value": ("earned_value_management", "project_controlling"),
    # learning_curve: `experience_curve` is the canonical companion concept (Christopher
    # Ch.1), degree 3, closure 276 (9.1%), one book hub; combined 299 (9.9%). Replayed
    # gate keeps EXACTLY MIN_CITATIONS: Learning Curve (Yx = K x^n) + Experience Curve.
    # Deliberately at the floor -- there is no third on-topic node in the graph, and the
    # alternative was shipping zero. If either node ever leaves the graph this tool goes
    # back to zero, which is the correct behaviour, not a regression to chase.
    "learning_curve": ("learning_curve_model", "experience_curve"),
    "odoo_replenishment": ("continuous_replenishment", "multiple_replenishment_orders"),
    "excel_replenishment": ("continuous_replenishment_program",),
    "newsvendor": ("newsvendor_model", "generalized_newsvendor_problem", "price_setting_newsvendor"),
    "cycle_count": ("abc_classification", "vollmann_abc_analysis"),
    "multi_echelon": ("multiechelon_inventory", "echelon_inventory", "safety_stock"),
    "transportation": ("freight_transport_modes", "intermodal_transport"),
    "fefo": ("perishable_asset", "perishable_assets", "lot_size"),
    # slotting STAYS ZERO, and now by measurement rather than by omission (2026-08-24).
    # Both anchors are degree-1 near-isolates (23- and 17-node closures, 40 combined) and
    # only one candidate lands in radius, so MIN_CITATIONS zeroes the batch. Candidates
    # checked as a second/third anchor:
    #   assignment_hungarian -- slotting IS an assignment problem and this node is already
    #     an in-radius candidate at 2 hops, but it is itself a degree-1 isolate: adding it
    #     leaves the closure at 40 nodes and the survivor count at 1. No change.
    #   warehouse_functions  -- degree 5, closure 210 (7.0%), one book hub (grant). It DOES
    #     produce 6 survivors, and all of them are wrong: Warehouse Location (where to put
    #     the building, not where to put the SKU), Warehouse Automation (AS/RS) and
    #     Warehouse Energy Efficiency, all from Grant's "Sustainable Warehousing" chapter,
    #     riding the bare "warehouse" token. Worse, they DISPLACE the tool's own on-topic
    #     layout anchors out of the top 3. Textbook false friend -- rejected.
    # The graph has no cube-per-order-index / slotting / storage-assignment theory. Fix by
    # ingesting warehouse-design theory (Bartholdi & Hackman, Frazelle), not by a looser
    # anchor. Pinned by tests/test_packages_citations.py::_ISLANDED_TOOLS.
    "slotting": ("load_distance_layout", "facility_layout"),
    "simulation": ("simulation_optimization", "inventory_simulation", "simulation_inventory_analysis"),
    "digital_twin": ("ch16lee_digital_twins", "bullwhip_effect", "multiechelon_inventory"),
    # "excess_capacity_and_inventory" (Chopra Ch.6) was dropped: it describes
    # deliberately CARRYING buffer stock as a hedge, nearly the opposite of
    # this tool's liquidate/write-off intent, and its lexical overlap
    # ("excess", "inventory") with the tool's own keywords meant it could
    # rank itself as a top candidate and then self-validate (hop 0) - a
    # citation-gate loophole (any anchor validates itself), confirmed
    # shipping in every real excess_obsolete run during the 2026-07 review.
    "excess_obsolete": ("obsolescence_cost",),
    "markdown_liquidation": ("markdown_pricing", "obsolescence_cost"),
    "facility_location": ("facility_location", "network_design", "distribution_network_design"),
    "network_design": ("facility_location", "network_design", "distribution_network_design"),
    "drp": ("vollmann_drp", "distribution_network_design"),
    "vehicle_routing": ("route_sheet", "last_mile_delivery"),
    "price_intelligence": ("price_competition", "competition_oriented_pricing", "price_positioning"),
    "price_watch": ("price_competition", "competition_oriented_pricing", "price_positioning"),
    # promotion_timing was rejected as an anchor: it is 1 hop from the Chopra & Meindl
    # book hub, so at MAX_HOPS=2 the whole Chopra book self-validates (2-hop closure
    # 330 -> 650 nodes, pulling in 39 off-topic discount/pricing/capacity magnets - the
    # shared-book-hub loophole EXCLUDED_CONCEPTS exists for). These three anchors are each
    # 3 hops from that hub; their combined closure is 330 nodes with 4 mild magnets, two of
    # which are excluded below. Verified by graph BFS 2026-07-18.
    "launch_readiness": ("new_product_forecasting", "risk_period", "lead_time_gap"),
    # Both hat tools decide the SAME (Q, SL) space as inventory_optimization,
    # so they seed from its anchors (spec 2026-07-20 Sec.10); the settlement adds
    # the working-capital lens (its acta is priced in capital terms).
    "hat_tension": ("safety_stock", "service_level", "cycle_service_level"),
    "hat_settlement": ("safety_stock", "service_level", "working_capital_efficiency"),
    # cross_impact (tool #48) decides the SAME (Q, SL) space as hat_tension, so it
    # inherits those three anchors; cross_functional_integration adds the
    # coordination lens this tool is actually about. Measured by BFS over the
    # 3002-node graph (2026-07-31): the three inherited anchors close 853 nodes
    # (28.4%) at 2 hops; adding cross_functional_integration admits only 16 more
    # (869, 28.9%) and it is hub-free (degree 3; neighbors Supply Chain
    # Orientation / Supply Chain Agility / Tiger Team, all on topic).
    # sales_and_operations_planning (own 2-hop closure 618) and functional_silos
    # (degree 2 but closure 266 - one hop from a hub) were REJECTED as magnets,
    # by measurement, not intuition.
    "cross_impact": ("safety_stock", "service_level", "cycle_service_level",
                     "cross_functional_integration"),
}

# tool_key -> concept ids that must NEVER be cited for this tool, even when
# they resolve within MAX_HOPS of a genuine anchor. Reserved for cases where
# a graph node sits in the same book-chapter neighborhood as the anchor (so
# hop-distance alone can't discriminate - the shared book hub bridges them in
# exactly 2 hops) but is topically contradictory for the tool. Confirmed
# during the 2026-07 adversarial review, not a hypothetical: dropping the
# anchor alone (excess_capacity_and_inventory used to be a second anchor
# here) did not stop the citation, because it is still 2 hops from
# obsolescence_cost via their shared Chopra & Meindl book hub.
EXCLUDED_CONCEPTS: dict[str, tuple[str, ...]] = {
    "excess_obsolete": ("excess_capacity_and_inventory",),
    # "reverse_auction" (a competitive supplier-bidding procurement mechanism)
    # sits exactly 2 hops from the "returns" tool's "reverse_logistics" anchor
    # via a shared Chopra & Meindl book-chapter hub, and rides in on the "reverse"
    # lexical overlap with the tool's own keywords - the same false-friend pattern
    # as excess_obsolete above. It is topically unrelated to product-returns
    # disposition. Surfaced once the candidate pool widened past 3 (see
    # scm_agent/packages.py::_step_citations); excluding it restores the on-topic
    # "Value Recovery" citation in its place.
    "returns": ("reverse_auction",),
    # The two in-radius mild magnets from the launch_readiness anchors' 2-hop closure
    # (facility_location, capacity_planning) - unrelated to a stock-coverage-for-launch
    # check. Both verified to exist in the graph (test_every_excluded_concept_exists).
    "launch_readiness": ("facility_location", "capacity_planning"),
    # BFS over knowledge/scm-books/graph.json (2026-07-20): the tension anchors'
    # 2-hop closure (646 nodes) reaches reverse_auction and procurement_auction
    # through shared book hubs. Both are competitive-bidding procurement
    # mechanisms - topically unrelated to an internal role-tension map - and ride
    # the "compras"/procurement lexical overlap of this tool's keywords: the
    # PR #164 false-friend class. The settlement anchors' closure (386 nodes)
    # contains neither; no exclusions needed there.
    "hat_tension": ("reverse_auction", "procurement_auction"),
    # cross_docking is a flow-through warehousing technique (goods move from
    # inbound to outbound without being stored) - it has nothing to do with the
    # cost of coordination between business areas. It rides the "cross" lexical
    # overlap with this tool's own keywords ("impacto cruzado", "cross-area
    # impact") and clears MAX_HOPS via a shared book hub even though it is NOT
    # reachable from the cross_functional_integration anchor at all. Measured
    # against the committed books graph (2026-07-31):
    #   cross_docking -> safety_stock              = 2 hops
    #   cross_docking -> cycle_service_level        = 2 hops
    #   cross_docking -> service_level              = unreachable
    #   cross_docking -> cross_functional_integration = unreachable
    # Same false-friend class as returns/reverse_auction and
    # excess_obsolete/excess_capacity_and_inventory above.
    "cross_impact": ("cross_docking",),
    # Surfaced 2026-07-24 when internal sources stopped occupying candidate slots
    # (see knowledge._INTERNAL_SOURCES): both ride the bare "quality" token from
    # this tool's own keywords ("data quality") and sit within MAX_HOPS of
    # step_product_data_standard via a shared Heizer & Render book hub - the same
    # false-friend class as PR #164. They are about *product/process* quality
    # (COQ's prevention/appraisal/failure cost categories; QFD's customer-
    # requirement design matrix), not about master-data or GTIN validation, which
    # is all this tool does. The books graph carries no genuine MDM/data-quality
    # source, so degrading to zero citations is the honest outcome here - pinned
    # by test_data_quality_step_degrades_to_no_citations_on_the_demo_intake.
    "data_quality": ("cost_of_quality", "house_of_quality"),
}


@dataclass(frozen=True)
class GateResult:
    """The gate's verdict for one step's candidate citations.

    ``kept`` is empty whenever fewer than MIN_CITATIONS candidates resolved -
    the caller renders that step's methodology without citations rather than
    a single, weakly-grounded one. ``omitted`` explains every drop (one line
    per candidate, human-readable) regardless of whether the final ``kept``
    count also got zeroed by the minimum-count rule.
    """

    kept: tuple[str, ...]
    omitted: tuple[str, ...]


def filter_citations(
    kb: KnowledgeBase, tool_key: str, candidates: list[GroundedCitation],
) -> GateResult:
    """Resolve each candidate against the graph; degrade to empty below MIN_CITATIONS."""
    anchors = TOOL_CONCEPTS.get(tool_key, ())
    excluded = EXCLUDED_CONCEPTS.get(tool_key, ())
    omitted: list[str] = []
    survivors: list[str] = []

    if not anchors:
        for c in candidates:
            reason = f"{tool_key}: no concept map defined for this tool - citation omitted: {c.text}"
            omitted.append(reason)
            _LOG.info(reason)
        return GateResult(kept=(), omitted=tuple(omitted))

    for c in candidates:
        if not kb.node_exists(c.node_id):
            reason = f"{tool_key}: citation node '{c.node_id}' does not exist in the graph - omitted: {c.text}"
            omitted.append(reason)
            _LOG.info(reason)
            continue
        if any(kb.concept_distance(c.node_id, ex, max_hops=0) == 0 for ex in excluded):
            reason = f"{tool_key}: citation node '{c.node_id}' is explicitly excluded for this tool - omitted: {c.text}"
            omitted.append(reason)
            _LOG.info(reason)
            continue
        best_hop = min(
            (d for anchor in anchors if (d := kb.concept_distance(c.node_id, anchor, max_hops=MAX_HOPS)) is not None),
            default=None,
        )
        if best_hop is None:
            reason = (
                f"{tool_key}: citation node '{c.node_id}' is more than {MAX_HOPS} hops from every "
                f"anchor concept ({', '.join(anchors)}) - omitted: {c.text}"
            )
            omitted.append(reason)
            _LOG.info(reason)
            continue
        survivors.append(c.text)

    if len(survivors) < MIN_CITATIONS:
        if survivors:
            reason = (
                f"{tool_key}: only {len(survivors)}/{MIN_CITATIONS} minimum citation(s) resolved - "
                "degrading the whole batch to no citations rather than shipping a single one"
            )
            omitted.append(reason)
            _LOG.info(reason)
        return GateResult(kept=(), omitted=tuple(omitted))

    return GateResult(kept=tuple(survivors), omitted=tuple(omitted))

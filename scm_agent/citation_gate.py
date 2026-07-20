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
    "whatif": ("sensitivity_analysis",),
    "financial_kpis": ("vollmann_inventory_turnover", "working_capital_efficiency"),
    "reconciliation": ("inventory_record_accuracy",),
    "returns": ("reverse_logistics", "returnability", "refurbishing"),
    "queuing": ("mm1_queue", "md1_queue", "queuing_analysis", "queue_server_optimization"),
    "scheduling": ("dispatching_rules", "vollmann_spt", "vollmann_edd", "johnsons_rule"),
    "risk": ("atomic_holistic_risk", "collaborative_risk_mitigation"),
    "forecast": ("forecastability", "crostons_method", "sbc_classification", "syntetos_boylan_approximation", "tsb_method"),
    # No topically-better node exists in the committed graph for SKU-master/
    # GTIN data quality (verified during the 2026-07 adversarial review) - this
    # tool degrades to zero citations on every real run, which is the correct,
    # safe outcome (see the module docstring), not a bug to "fix" with a
    # closer-sounding but wrong anchor.
    "data_quality": ("step_product_data_standard",),
    "dea": ("data_envelopment_analysis",),
    "acceptance_sampling": ("acceptance_sampling", "single_sampling_plan"),
    "earned_value": ("earned_value_management",),
    "learning_curve": ("learning_curve_model",),
    "odoo_replenishment": ("continuous_replenishment", "multiple_replenishment_orders"),
    "excel_replenishment": ("continuous_replenishment_program",),
    "newsvendor": ("newsvendor_model", "generalized_newsvendor_problem", "price_setting_newsvendor"),
    "cycle_count": ("abc_classification", "vollmann_abc_analysis"),
    "multi_echelon": ("multiechelon_inventory", "echelon_inventory", "safety_stock"),
    "transportation": ("freight_transport_modes", "intermodal_transport"),
    "fefo": ("perishable_asset", "perishable_assets", "lot_size"),
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

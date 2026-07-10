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
    "ddmrp": ("demand_driven_supply_chain", "drum_buffer_rope", "vollmann_customer_order_decoupling_point"),
    "landed_cost": ("landed_cost",),
    "warehouse_layout": ("facility_layout", "load_distance_layout"),
    "whatif": ("sensitivity_analysis",),
    "financial_kpis": ("vollmann_inventory_turnover", "working_capital_efficiency"),
    "reconciliation": ("inventory_record_accuracy",),
    "returns": ("reverse_logistics", "returnability", "refurbishing"),
    "queuing": ("mm1_queue", "md1_queue", "queuing_analysis", "queue_server_optimization"),
    "scheduling": ("scheduling", "vollmann_finite_scheduling", "vollmann_back_scheduling", "project_scheduling"),
    "risk": ("aggregation_risk_pooling", "atomic_holistic_risk", "collaborative_risk_mitigation"),
    "forecast": ("aggregate_forecasting", "ai_ml_forecasting", "forecastability", "bullwhip_effect"),
    "data_quality": ("step_product_data_standard",),
    "dea": ("data_envelopment_analysis",),
    "acceptance_sampling": ("acceptance_sampling", "single_sampling_plan"),
    "earned_value": ("earned_value_management",),
    "learning_curve": ("learning_curve_model",),
    "odoo_replenishment": ("continuous_replenishment", "multiple_replenishment_orders"),
    "excel_replenishment": ("continuous_replenishment_program",),
    "newsvendor": ("newsvendor_model", "generalized_newsvendor_problem", "price_setting_newsvendor"),
    "cycle_count": ("inventory_record_accuracy",),
    "multi_echelon": ("multiechelon_inventory", "echelon_inventory", "safety_stock"),
    "transportation": ("freight_transport_modes", "intermodal_transport"),
    "fefo": ("perishable_asset", "perishable_assets", "lot_size"),
    "slotting": ("load_distance_layout", "facility_layout"),
    "simulation": ("simulation_optimization", "inventory_simulation", "simulation_inventory_analysis"),
    "excess_obsolete": ("obsolescence_cost", "excess_capacity_and_inventory"),
    "markdown_liquidation": ("markdown_pricing", "obsolescence_cost"),
    "facility_location": ("facility_location", "network_design", "distribution_network_design"),
    "drp": ("vollmann_drp", "distribution_network_design"),
    "vehicle_routing": ("route_sheet", "last_mile_delivery"),
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

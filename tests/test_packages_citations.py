"""Real-graph tests for the package-step E5 citation grounding
(``scm_agent.packages._step_citations``) -- 3.0-audit finding #7, the
``packages._run_step`` blast radius of the integrated_plan / price_intelligence
citation fixes.

Unlike ``tests/test_packages.py`` (which uses a citation-free stub to avoid
loading the books graph), these exercise the real committed graph so the
recall/precision behavior of the widened candidate pool is actually asserted.
"""

from __future__ import annotations

import pytest

from scm_agent import citation_gate
from scm_agent.citation_gate import MIN_CITATIONS
from scm_agent.knowledge import KnowledgeBase
from scm_agent.packages import _MAX_CITATIONS, _step_citations
from scm_agent.tools import build_default_registry


@pytest.fixture(scope="module")
def _kb() -> KnowledgeBase:
    return KnowledgeBase()


@pytest.fixture(scope="module")
def _reg():
    return build_default_registry()


def _cites(kb, reg, tool_key: str) -> tuple[str, ...]:
    return _step_citations(kb, reg.get(tool_key), tool_key)


# Tools that shipped ZERO citations in every package that runs them under the
# old top-3 pool, and recover with genuinely on-topic citations at pool 8. The
# flagship inventory_optimization was citation-less in every inventory package.
_RECOVERED_TOOLS = (
    "inventory_optimization",
    "pricing",
    "excel_replenishment",
    "odoo_replenishment",
    "risk",
    "reconciliation",
)

# Tools whose ZERO is CORRECT and must be preserved: data_quality's zero is
# intentional (citation_gate.TOOL_CONCEPTS comment); cycle_count / whatif /
# earned_value recover only by surfacing topically-wrong or filler citations
# past the permissive gate, so degrading to zero is the right outcome. A pool
# above 8 would wrongly surface those -- these pin the ceiling.
_CORRECTLY_ZERO_TOOLS = ("data_quality", "cycle_count", "whatif", "earned_value")

# Anchor-islanded tools: zero at ANY pool (a separate anchor problem, not fixable
# by pool sizing) -- pinned so a future anchor fix is a conscious change.
# vehicle_routing joined this set with the Khan et al. (2022) merge. Its two prior
# citations were lexical false friends -- "Logistics as the Vehicle for Change"
# (organisational change, not vehicles) and a "Route Sheet" from a product-design
# chapter -- so shipping zero is the more honest outcome. The graph has no real
# VRP theory to anchor on: vollmann_traveling_salesman is an isolate with 0 edges,
# and the only hub-free routing nodes describe disruption re-routing, not
# Clarke-Wright/savings VRP. Fix by ingesting VRP theory, not by a looser anchor.
_ISLANDED_TOOLS = ("dea", "learning_curve", "slotting", "vehicle_routing")

# Every anchored+registered tool that is allowed to ship zero citations: the four
# whose zero is intentional plus the anchor-islanded ones. price_watch is NOT here
# -- it was a zero before source #27 and the widened pool recovered it, so it is
# now held to the same >= MIN_CITATIONS bar as everything else.
_EXPECTED_ZERO_TOOLS = frozenset(_CORRECTLY_ZERO_TOOLS + _ISLANDED_TOOLS)

_OFF_TOPIC_TERMS = (
    "cost of quality", "house of quality", "cash-to-cash", "quality circle", "tqm",
    "reverse auction",  # procurement false-friend for `returns` (EXCLUDED_CONCEPTS)
)


def _all_package_tool_keys() -> list[str]:
    """Every distinct tool_key actually run across all registered packages."""
    from scm_agent.package_specs import PACKAGES

    seen: dict[str, None] = {}
    for spec in PACKAGES.values():
        for step in spec.steps:
            seen.setdefault(step.tool_key, None)
    return list(seen)


@pytest.mark.parametrize("tool_key", _RECOVERED_TOOLS)
def test_recovered_tool_keeps_at_least_min_citations(_kb, _reg, tool_key):
    """Recall regression: these tools shipped zero citations under the old
    top-3 pool; the widened pool must ground at least MIN_CITATIONS for them."""
    cites = _cites(_kb, _reg, tool_key)
    assert len(cites) >= MIN_CITATIONS, f"{tool_key} degraded to {len(cites)} citation(s)"


@pytest.mark.parametrize("tool_key", _CORRECTLY_ZERO_TOOLS)
def test_intentional_zero_is_preserved(_kb, _reg, tool_key):
    """Precision ceiling: the widened pool must NOT surface citations for tools
    whose zero is correct -- otherwise data_quality ships manufacturing-TQM
    citations and cycle_count ships cash-cycle citations (both wrong)."""
    assert _cites(_kb, _reg, tool_key) == ()


@pytest.mark.parametrize("tool_key", _ISLANDED_TOOLS)
def test_anchor_islanded_tools_stay_zero(_kb, _reg, tool_key):
    assert _cites(_kb, _reg, tool_key) == ()


def test_recovered_citations_are_capped(_kb, _reg):
    for tool_key in _RECOVERED_TOOLS:
        assert len(_cites(_kb, _reg, tool_key)) <= _MAX_CITATIONS


def test_no_off_topic_hub_noise_across_all_package_tools(_kb, _reg):
    """No package tool -- recovered, already-working, or zero -- may ship a
    citation from the known cross-domain hub-noise set (TQM / cost-of-quality /
    cash-to-cash / reverse-auction). Iterates EVERY distinct tool_key actually
    run across all packages, not just the recovered/zero subsets -- an adversarial
    review found `returns` shipping "Reverse Auction" precisely because an earlier
    version of this guard only covered those subsets."""
    for tool_key in _all_package_tool_keys():
        for cite in _cites(_kb, _reg, tool_key):
            low = cite.lower()
            assert not any(t in low for t in _OFF_TOPIC_TERMS), f"{tool_key}: off-topic {cite!r}"


def test_returns_excludes_reverse_auction_and_stays_on_topic(_kb, _reg):
    """The false-friend the wider pool surfaced: `returns` must not cite
    "Reverse Auction" (a procurement concept), and must keep >= MIN_CITATIONS
    genuinely returns/reverse-logistics citations (the exclusion restores the
    on-topic "Value Recovery" in its place)."""
    cites = _cites(_kb, _reg, "returns")
    assert len(cites) >= MIN_CITATIONS
    joined = " ".join(cites).lower()
    assert "reverse auction" not in joined
    assert "reverse logistics" in joined


def test_no_anchored_tool_regressed_to_zero(_kb, _reg):
    """Coverage guard for the blast radius a books-graph merge actually has.

    Every other test here iterates only the tools used by a PACKAGE. Ingesting
    Khan et al. (2022) as source #27 silently dropped digital_twin,
    launch_readiness and vehicle_routing from 2-3 citations to zero precisely
    because none of them is a package step, so nothing failed. This pins the full
    set of anchored+registered tools that must stay grounded: any future source
    that starves one of them fails here instead of shipping a citation-less deck.

    A tool belongs in _EXPECTED_ZERO_TOOLS only as a conscious, documented
    decision (intentional zero, or anchor-islanded).
    """
    for tool_key in sorted(citation_gate.TOOL_CONCEPTS):
        try:
            tool = _reg.get(tool_key)
        except Exception:  # anchored but not registered -- nothing to ground
            continue
        cites = _step_citations(_kb, tool, tool_key)
        if tool_key in _EXPECTED_ZERO_TOOLS:
            assert cites == (), f"{tool_key} was expected to be zero but now cites {cites}"
        else:
            assert len(cites) >= MIN_CITATIONS, (
                f"{tool_key} regressed to {len(cites)} citation(s). Either fix its anchors/"
                f"the candidate pool, or add it to _EXPECTED_ZERO_TOOLS with a reason."
            )


def test_fefo_needs_the_tool_title_not_keyword_only(_kb, _reg):
    """Guards the design choice to ground on tool.title rather than dropping the
    query entirely: fefo's on-topic tokens live in its title, so it must stay
    grounded (>= MIN_CITATIONS), where keyword-only grounding would zero it."""
    assert len(_cites(_kb, _reg, "fefo")) >= MIN_CITATIONS

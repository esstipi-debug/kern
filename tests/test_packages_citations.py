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
# intake_scan is a documented zero, not an unmeasured gap: Task 6 (intake_scan
# design) measured the L3 anchors for it and found no TOOL_CONCEPTS entry that
# both existed and stayed on-topic -- `information_sharing` was the only
# candidate and it pulled in Khan-resilience noise hanging off the word
# "readiness". This could not be added here while intake_scan was still
# unregistered: reg.get(tool_key) below raises KeyError for an unknown key,
# verified twice before Task 7 registered the tool.
# whatif and earned_value LEFT this set on 2026-08-24. The premise recorded above --
# "they recover only by surfacing topically-wrong or filler citations" -- did not survive
# re-measurement: both ranked their own anchor at hop 0 as a perfectly on-topic candidate
# and lost the batch to the MIN_CITATIONS floor with exactly one survivor. One extra
# anchor each (break_even_analysis / project_controlling, both measured against the
# hub-adjacency and closure rules -- see citation_gate.TOOL_CONCEPTS) recovers three
# on-topic citations apiece. See _RECOVERED_BY_ANCHOR below.
# procedure_draft LEFT this set on 2026-09-04. Its zero was never an anchor problem: the
# anchors (checklist/poka_yoke/pdca_cycle) were right, the graph simply held no
# document-control or standard-work theory for them to reach -- so the pool resolved ZERO
# candidates in-radius and there was never a lone survivor for the MIN_CITATIONS floor to
# degrade. The fix was the one packages.py:100-104 prescribes for exactly this shape:
# ingest the missing theory (L3 source #30, twelve nodes -- see
# documentation/SOP_DOCUMENT_CONTROL_SOURCES.md), NOT a looser anchor and NOT a bigger pool.
# Neither TOOL_CONCEPTS nor the pool changed; the new nodes hang off checklist and
# pdca_cycle at hop 1, so the anchors that resolved nothing now resolve three. The
# work_breakdown_structure exclusion STAYS: a real "Job Breakdown Sheet" node now competes
# for the same lexical tokens, and it is the one that wins. See _RECOVERED_BY_INGESTION.
# audit_evidence (tool #55) joined as a measured zero, 2026-09-07: AU-C 530 / MUS
# theory is not in the books graph. Anchors inventory_record_accuracy +
# acceptance_sampling exist and ground *other* tools, but this tool's keywords
# (MUS, workpaper, trial balance) never rank those nodes into the candidate
# pool, so the gate correctly ships zero rather than IRA/AQL false friends.
# Fix by ingesting audit-sampling theory, not by a looser or hub-adjacent anchor.
# import_case (tool #57) joined as a measured zero, 2026-09-09: the wedge unifies
# shipment documents; L3 has TCO/Incoterm theory (the landed_cost anchor) but
# this tool's keywords (DIN pack, packing vs invoice, expediente) never rank
# those nodes into the candidate pool. Shipping zero is honest until a trade-
# documentation source is ingested. Do not hang it off a book hub.
_CORRECTLY_ZERO_TOOLS = (
    "data_quality", "cycle_count", "intake_scan", "audit_evidence", "import_case",
)

# Anchor-islanded tools: zero at ANY pool (a separate anchor problem, not fixable
# by pool sizing) -- pinned so a future anchor fix is a conscious change.
# vehicle_routing joined this set with the Khan et al. (2022) merge. Its two prior
# citations were lexical false friends -- "Logistics as the Vehicle for Change"
# (organisational change, not vehicles) and a "Route Sheet" from a product-design
# chapter -- so shipping zero is the more honest outcome. The graph has no real
# VRP theory to anchor on: vollmann_traveling_salesman is an isolate with 0 edges,
# and the only hub-free routing nodes describe disruption re-routing, not
# Clarke-Wright/savings VRP. Fix by ingesting VRP theory, not by a looser anchor.
# org_friction_audit (tool #49) joined this set at introduction, not via a later merge:
# vollmann_hidden_factory (its sole anchor -- see citation_gate.TOOL_CONCEPTS's comment
# for the full BFS measurement) has degree 1 and a 26-node 2-hop closure with nothing else
# on-topic. Two richer-looking anchors were measured and rejected (misaligned_incentives
# is 1 hop from the Chopra & Meindl book hub -- the self-validation loophole EXCLUDED_
# CONCEPTS exists for; strategic_inertia's 8-node closure is pandemic-crisis content, not
# metrics/accountability). No Goodhart's-law/ratchet/workaround/metric-gaming node exists
# anywhere in the graph. Fix by ingesting performance-measurement-dysfunction theory, not
# by a looser or hub-adjacent anchor.
# dea and learning_curve LEFT this set on 2026-08-24, same re-measurement: neither was
# islanded from theory, both were one citation short of MIN_CITATIONS. slotting stays,
# but now with its own measurement written down in citation_gate.TOOL_CONCEPTS (the
# `warehouse_functions` candidate produces six survivors and every one of them is a
# "warehouse" false friend that also displaces the tool's real layout anchors).
_ISLANDED_TOOLS = ("slotting", "vehicle_routing", "org_friction_audit")

# Recovered on 2026-08-24 by adding ONE anchor each, not by touching MIN_CITATIONS or the
# pool. The substring assertions matter as much as the count: a recovered tool that ships
# three off-topic citations is worse than the zero it replaced.
_RECOVERED_BY_ANCHOR = {
    "whatif": ("break-even", "sensitivity"),
    "dea": ("data envelopment", "benchmarking"),
    "earned_value": ("earned value", "project controlling"),
    "learning_curve": ("learning curve", "experience curve"),
}

# Recovered on 2026-09-04 by INGESTING the missing theory (L3 source #30: document control
# and standard work), with TOOL_CONCEPTS, EXCLUDED_CONCEPTS, MIN_CITATIONS and the pool all
# untouched. A separate registry from _RECOVERED_BY_ANCHOR on purpose: that one records
# tools whose theory existed and whose anchors couldn't reach it; this one records tools
# whose anchors were right and whose theory was absent. Conflating them would lose the only
# fact that tells a future reader which lever to pull.
#
# procedure_audit is here despite never having been zero -- it sat at exactly MIN_CITATIONS
# (2) before the merge, the closest thing to a zero the gate has, and the merge REPLACED
# both of its citations ("Control Chart" and "ISO 9000", generic quality-chapter content)
# with three that name the actual clause. That swap is the whole point of the ingest, and a
# count-only assertion would not have caught it going the other way.
_RECOVERED_BY_INGESTION = {
    "procedure_audit": ("documented information", "iso 10013", "review cadence"),
    "procedure_draft": ("job breakdown sheet", "training timetable", "review cadence"),
}

# Every anchored+registered tool that is allowed to ship zero citations: the four
# whose zero is intentional plus the anchor-islanded ones. price_watch is NOT here
# -- it was a zero before source #27 and the widened pool recovered it, so it is
# now held to the same >= MIN_CITATIONS bar as everything else.
# Tools whose real citations come from a DIFFERENT gate, so the number
# `_step_citations` produces for them is not a measurement of anything a client
# sees. A separate registry from the three above on purpose: those record a tool
# that genuinely ships zero citations. These ship citations -- just not down this
# path -- and conflating the two would lose the fact a future reader needs.
#
# repricing (tool #58) is the first. It is not a package step, has no deck, and
# its citations are resolved inside src.pricing_guardrails.gate_price_changeset,
# which grounds the playbook's own fixed _CITATION_KEYWORDS (brief deliberately
# empty -- the #160/#162/#164 fix) and gates them under tool_key "pricing".
# Measured on this graph 2026-09-12: that path resolves FOUR citations (Markdown
# Pricing, Price Elasticity of Demand, Phillips, the MIT omnichannel paper), the
# same four for every reason line, and a zero there would HARD-BLOCK the whole
# changeset -- so it is the most-guarded citation path in the repo, not the least.
#
# _step_citations returns zero for it because it grounds on
# `tool.intent_keywords` + `tool.title`, and repricing's keywords are action
# phrases ("reprice", "push new prices", "repreciar") on purpose: intent.classify
# requires a STRICT win, so giving it generic pricing vocabulary would tie it with
# `pricing`/`price_intelligence` and break briefs that route correctly today.
# Measured: seven candidate titles, including `pricing`'s own "Price Optimization",
# all return zero under this key; a title stuffed with "markdown / price
# sensitivity" reaches three, which is grounder-pleasing copy in a client-facing
# field. Keeping the honest title and recording the real path is the better trade.
_GATE_CITED_TOOLS = ("repricing",)

_EXPECTED_ZERO_TOOLS = frozenset(_CORRECTLY_ZERO_TOOLS + _ISLANDED_TOOLS + _GATE_CITED_TOOLS)

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


@pytest.mark.parametrize("tool_key,esperados", sorted(_RECOVERED_BY_ANCHOR.items()))
def test_anchor_recovered_tool_cites_its_own_subject(_kb, _reg, tool_key, esperados):
    """EL TEST QUE IMPORTA de esta tanda.

    Cada uno de estos cuatro devolvia cero por el piso de MIN_CITATIONS, no por falta de
    teoria. El conteo solo no alcanza: si el ancla nueva recupera tres citas ajenas al
    tema, es peor que el cero que reemplazo. Por eso se fija tambien QUE cita.
    """
    cites = _cites(_kb, _reg, tool_key)
    assert len(cites) >= MIN_CITATIONS, f"{tool_key} volvio a {len(cites)} cita(s)"
    joined = " ".join(cites).lower()
    faltan = [e for e in esperados if e not in joined]
    assert not faltan, f"{tool_key} ya no cita {faltan}; cita {cites}"


@pytest.mark.parametrize("tool_key,esperados", sorted(_RECOVERED_BY_INGESTION.items()))
def test_ingestion_recovered_tool_cites_the_ingested_theory(_kb, _reg, tool_key, esperados):
    """El test que separa "ingerimos una fuente" de "la fuente sirvio".

    Un merge que sube el conteo citando cualquier cosa es peor que el cero que reemplaza:
    la tool queda "grounded" ante los guardias y le muestra al cliente teoria ajena. Por eso
    se fija el contenido. Los tres substrings de cada tool son los conceptos que la fuente
    #30 aporto -- si un merge futuro los desplaza, esto falla aunque el conteo aguante.
    """
    cites = _cites(_kb, _reg, tool_key)
    assert len(cites) >= MIN_CITATIONS, f"{tool_key} volvio a {len(cites)} cita(s)"
    joined = " ".join(cites).lower()
    faltan = [e for e in esperados if e not in joined]
    assert not faltan, f"{tool_key} ya no cita {faltan}; cita {cites}"


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

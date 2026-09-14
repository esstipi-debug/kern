"""Tests for jobs/repricing_job.py -- the multichannel repricing playbook made
routable as agent tool #58, staging-only.

Gap audit finding #6 (2026-07-13) said jobs/repricing.py had "zero operator
entry point anywhere ... currently unreachable by anyone but a test"; the
2026-09-12 re-verification confirmed it was still unregistered.

The audit ALSO said wiring a live external-write surface as a full agent tool
was "a larger, riskier task than fits one run". These tests are what makes the
smaller, safe version defensible: the safety invariants are asserted, not
asserted-about.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from jobs import repricing_job
from jobs.repricing import (
    ChannelRepricingResult,
    LandedCostsRequired,
    RepricingGuardrailBlocked,
    RepricingVerificationFailed,
    apply_repricing,
    run_channel_repricing,
    stage_repricing,
    verify_applied,
)
from nosilo_core import writeback
from nosilo_core.guided import HANDOFF
from scm_agent.tools import build_default_registry
from src.connectors.meli_prices import InMemoryMeli, MeliPriceStore
from src.connectors.odoo import demo_odoo
from src.connectors.odoo_prices import OdooPriceStore
from src.connectors.shopify_prices import InMemoryShopify, ShopifyPriceStore

#: Motivo de repricing valido, reusado por los tests del gate central. Vive aca
#: porque main reescribio este archivo y se llevo el helper, pero los tests del
#: piso de costo que este PR agrega lo siguen necesitando.
REASON = "Repricing to close a margin gap vs landed cost (elasticity-driven)."


def _price_store() -> writeback.InMemoryStore:
    return writeback.InMemoryStore({"SKU-1": {"price": 20.0}, "SKU-2": {"price": 50.0}})



_REPO_ROOT = Path(__file__).resolve().parents[1]

_GOOD_CSV = """product_id,current_price,proposed_price,landed_cost
R1,100.00,110.00,60.00
R2,250.00,235.00,140.00
R3,80.00,80.00,50.00
"""


def _write(tmp_path: Path, text: str) -> str:
    p = tmp_path / "prices.csv"
    p.write_text(text, encoding="utf-8")
    return str(p)


def _params(**over) -> dict:
    base = {"channel": "shopify", "reason": "apply the reviewed October price list"}
    base.update(over)
    return base


# -- the safety invariants: these are the reason this tool is allowed to exist --

def test_the_module_never_reaches_the_apply_path():
    """AST guard, not a grep: no import of and no call to the functions that
    write to a live channel. If someone later adds one, this fails loudly
    rather than the tool quietly gaining the ability to move real prices."""
    tree = ast.parse((_REPO_ROOT / "jobs" / "repricing_job.py").read_text(encoding="utf-8"))
    forbidden = {"apply_repricing", "run_channel_repricing", "approve"}

    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name:
                called.add(name)

    assert not (imported & forbidden), f"imports a live-write function: {imported & forbidden}"
    assert not (called & forbidden), f"calls a live-write function: {called & forbidden}"


@pytest.mark.parametrize("csv_text", [
    _GOOD_CSV,
    # a move far beyond the gate's 50% band -> blocked
    "product_id,current_price,proposed_price\nR1,100.00,400.00\n",
    # below landed cost -> blocked
    "product_id,current_price,proposed_price,landed_cost\nR1,100.00,55.00,60.00\n",
])
def test_the_outcome_is_always_a_handoff(tmp_path, csv_text):
    """Approved or blocked, this tool can never report that it did something.
    A tool reachable from a plain-language brief must not be able to say it
    changed a client's prices, because it cannot."""
    report = repricing_job.run(repricing_job.prepare(_write(tmp_path, csv_text), _params()))
    outcome = report.outcome
    assert outcome.status == HANDOFF
    assert outcome.handoffs, "a handoff with no packet is a dead end"
    assert outcome.options == []


def test_the_registered_tool_is_not_on_the_mcp_surface():
    """The collection is TOOL_SPECS. The first version of this test read a
    non-existent ``SPECS`` attribute, so it iterated an empty tuple and passed
    for free -- a vacuous assertion that would have stayed green the day
    someone DID expose repricing on the anonymous MCP surface. Caught by
    verifying the live container after deploy, not by the green suite.

    So this asserts against the real surface AND asserts the surface is
    non-empty, which is what makes the negative meaningful."""
    from webapp.mcp_tool_specs import TOOL_SPECS

    exposed = {spec.job_type for spec in TOOL_SPECS}
    assert len(exposed) > 30, "the MCP surface came back suspiciously small -- is this reading the right collection?"
    assert "repricing" not in exposed
    # the exclusion is also documented where the surface is defined
    from webapp import mcp_tool_specs

    assert "`repricing`" in mcp_tool_specs.__doc__


# -- prepare: the two required params, and the column contract ------------------

def test_channel_and_reason_are_required(tmp_path):
    path = _write(tmp_path, _GOOD_CSV)
    with pytest.raises(ValueError, match="channel"):
        repricing_job.prepare(path, {"reason": "x"})
    with pytest.raises(ValueError, match="reason"):
        repricing_job.prepare(path, {"channel": "shopify"})
    with pytest.raises(ValueError, match="reason"):
        repricing_job.prepare(path, _params(reason="   "))


def test_missing_columns_name_what_is_missing(tmp_path):
    path = _write(tmp_path, "product_id,current_price\nR1,100\n")
    with pytest.raises(ValueError, match="proposed_price"):
        repricing_job.prepare(path, _params())


def test_spanish_aliases_resolve(tmp_path):
    path = _write(tmp_path, "codigo,precio_actual,precio_propuesto\nR1,100,110\n")
    plan = repricing_job.prepare(path, _params())
    assert plan.proposed_prices == {"R1": 110.0}


def test_unpriceable_rows_are_counted_not_coerced(tmp_path):
    """A row we cannot price is reported, never silently dropped and never
    turned into a 0.0 that would read as a free giveaway."""
    path = _write(tmp_path, (
        "product_id,current_price,proposed_price\n"
        "R1,100,110\n"
        "R2,,120\n"        # no current price
        "R3,90,\n"         # no proposed price
        "R4,90,0\n"        # a zero proposal is not a price
        ",90,110\n"        # no sku
    ))
    plan = repricing_job.prepare(path, _params())
    assert plan.proposed_prices == {"R1": 110.0}
    assert plan.skipped_rows == 4
    assert 0.0 not in plan.proposed_prices.values()


def test_a_file_with_nothing_priceable_refuses(tmp_path):
    path = _write(tmp_path, "product_id,current_price,proposed_price\nR1,,\n")
    with pytest.raises(ValueError, match="no priceable rows"):
        repricing_job.prepare(path, _params())


# -- run: the gate decides, and a refusal is reported, not raised ---------------

def test_an_approved_proposal_stages_the_real_diff(tmp_path):
    report = repricing_job.run(repricing_job.prepare(_write(tmp_path, _GOOD_CSV), _params()))
    assert report.blocked_reason is None
    assert report.changeset is not None
    moves = [c for c in report.changeset.changes if not c.is_noop]
    assert {c.entity_id for c in moves} == {"R1", "R2"}  # R3 proposes its own price
    assert repricing_job.verify(report) == []


def test_a_move_beyond_the_band_is_reported_not_raised(tmp_path):
    path = _write(tmp_path, "product_id,current_price,proposed_price\nR1,100.00,400.00\n")
    report = repricing_job.run(repricing_job.prepare(path, _params()))
    assert report.changeset is None
    assert report.blocked_reason is not None
    assert "max-move" in report.blocked_reason
    # the operator is told WHY, in the handoff artifact, not handed a traceback
    assert report.outcome.handoffs[0].artifact == report.blocked_reason


def test_below_landed_cost_is_blocked(tmp_path):
    path = _write(tmp_path, "product_id,current_price,proposed_price,landed_cost\nR1,100.00,55.00,60.00\n")
    report = repricing_job.run(repricing_job.prepare(path, _params()))
    assert report.changeset is None
    assert "below landed cost" in report.blocked_reason


def test_an_all_noop_proposal_is_a_qa_issue(tmp_path):
    path = _write(tmp_path, "product_id,current_price,proposed_price\nR1,100,100\n")
    report = repricing_job.run(repricing_job.prepare(path, _params()))
    assert any("nothing to approve" in issue for issue in repricing_job.verify(report))


def test_the_citations_do_not_depend_on_how_the_reason_is_worded(tmp_path):
    """The citation trilogy's property, asserted where a zero would hard-block
    the whole changeset: two very different reason lines must not produce two
    different gate verdicts."""
    path = _write(tmp_path, _GOOD_CSV)
    a = repricing_job.run(repricing_job.prepare(path, _params(reason="Q4 margin recovery")))
    b = repricing_job.run(repricing_job.prepare(path, _params(reason="ajuste por tipo de cambio")))
    assert (a.blocked_reason is None) == (b.blocked_reason is None) is True


def test_the_staged_csv_shows_the_move_per_sku(tmp_path):
    import csv

    report = repricing_job.run(repricing_job.prepare(_write(tmp_path, _GOOD_CSV), _params()))
    out = repricing_job.write_operational(report, tmp_path / "out")
    rows = list(csv.DictReader(out["csv"].read_text(encoding="utf-8").splitlines()))
    by_sku = {r["product_id"]: r for r in rows}
    assert by_sku["R1"]["move_pct"] == "10.0"
    assert by_sku["R2"]["move_pct"] == "-6.0"


# -- registration ---------------------------------------------------------------

def test_the_tool_is_registered_and_declares_its_data_contract():
    tool = build_default_registry().get("repricing")
    assert tool.requires_data is True
    assert tool.required_columns == ("product_id", "current_price", "proposed_price")
    assert tool.deck is None  # operational CSV + handoff; no premium deck in v1


def test_importing_the_playbook_directly_still_works():
    """Regression guard for a circular import this registration introduced and
    then fixed: jobs.repricing imports scm_agent.knowledge, whose package
    __init__ reaches scm_agent.tools, which imports this job module. Importing
    jobs.repricing at module level here closed that cycle and broke a plain
    ``import jobs.repricing`` -- which examples/run_repricing.py and every
    existing playbook test do. Run in a subprocess so the already-imported
    modules in this session cannot mask it."""
    proc = subprocess.run(
        [sys.executable, "-c", "import jobs.repricing; print(jobs.repricing.TIER)"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(_REPO_ROOT)},
    )
    assert proc.returncode == 0, proc.stderr[-2000:]


def test_the_real_citation_path_ships_four_citations():
    """The packages path returns zero for this tool (recorded, with the
    measurement, in tests/test_packages_citations.py::_GATE_CITED_TOOLS) because
    it grounds on intent_keywords, which are action phrases here on purpose.

    THIS is the path a client's changeset actually takes, and a zero here would
    hard-block every price change. Pin it."""
    from jobs.repricing import gated_citations
    from scm_agent.citation_gate import MIN_CITATIONS, filter_citations
    from scm_agent.knowledge import KnowledgeBase

    kb = KnowledgeBase()
    kept = filter_citations(kb, "pricing", list(gated_citations("", kb=kb))).kept
    assert len(kept) >= MIN_CITATIONS
    joined = " ".join(kept).lower()
    assert "markdown pricing" in joined
    assert "elasticity" in joined


# ---- stage_repricing: central gate (PR-17) --------------------------------------


def test_stage_repricing_blocks_a_changeset_with_no_reason():
    store = _price_store()
    with pytest.raises(RepricingGuardrailBlocked):
        stage_repricing(
            store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason="",
            landed_costs={},
        )
    # never reached staging: the store is untouched and nothing was ever applied/claimed
    assert store.read("SKU-1")["price"] == 20.0
    assert store.applied_keys() == set()


def test_stage_repricing_approves_with_a_real_reason_and_real_citations():
    store = _price_store()
    cs = stage_repricing(
        store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON,
        landed_costs={},  # this test is about gate/approval semantics, not costs
    )
    assert cs.reason == REASON
    assert cs.risk_tier == writeback.TIER_REVERSIBLE
    assert cs.changes[0].before == 20.0
    assert cs.changes[0].after == 18.0


def test_stage_repricing_blocks_a_move_beyond_the_gate_sanity_band():
    """A 75% cut (20.0 -> 5.0) trips the central gate's default max-move
    backstop -- no explicit opt-in needed at the repricing layer."""
    store = _price_store()
    with pytest.raises(RepricingGuardrailBlocked):
        stage_repricing(
            store, "shopify:demo", {"SKU-1": 5.0}, idempotency_key="r1", reason=REASON,
            landed_costs={},
        )
    assert store.read("SKU-1")["price"] == 20.0
    assert store.applied_keys() == set()


def test_stage_repricing_blocks_below_cost_when_landed_costs_supplied():
    store = _price_store()
    with pytest.raises(RepricingGuardrailBlocked):
        stage_repricing(
            store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON,
            landed_costs={"SKU-1": 19.0},
        )
    assert store.read("SKU-1")["price"] == 20.0


def test_stage_repricing_passes_when_price_covers_landed_cost():
    store = _price_store()
    cs = stage_repricing(
        store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON,
        landed_costs={"SKU-1": 15.0},
    )
    assert cs.changes[0].after == 18.0


# ---- apply_repricing: approval required, never auto-applies ---------------------


def test_apply_repricing_without_approval_is_refused():
    store = _price_store()
    cs = stage_repricing(
        store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON,
        landed_costs={},  # this test is about gate/approval semantics, not costs
    )
    with pytest.raises(writeback.WritebackRefused):
        apply_repricing(store, cs, None, now=0.0)
    assert store.read("SKU-1")["price"] == 20.0


def test_apply_repricing_with_valid_approval_writes():
    store = _price_store()
    cs = stage_repricing(
        store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON,
        landed_costs={},  # this test is about gate/approval semantics, not costs
    )
    approval = writeback.approve(cs, "operator", now=0.0)
    result = apply_repricing(store, cs, approval, now=10.0)
    assert result.applied is True
    assert store.read("SKU-1")["price"] == 18.0


def test_apply_repricing_never_auto_applies_an_expired_approval():
    store = _price_store()
    cs = stage_repricing(
        store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON,
        landed_costs={},  # this test is about gate/approval semantics, not costs
    )
    approval = writeback.approve(cs, "operator", now=0.0, ttl_seconds=900.0)
    with pytest.raises(writeback.WritebackRefused):
        apply_repricing(store, cs, approval, now=1000.0)  # past the 900s TTL


# ---- verify_applied: post-apply read-back ----------------------------------------


def test_verify_applied_passes_when_live_matches_staged():
    store = _price_store()
    cs = stage_repricing(
        store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON,
        landed_costs={},  # this test is about gate/approval semantics, not costs
    )
    approval = writeback.approve(cs, "operator", now=0.0)
    apply_repricing(store, cs, approval, now=0.0)
    verify_applied(store, cs, "shopify:demo")  # does not raise


class _MismatchStore:
    """Wraps a real store's commit but always reports a stale price for one
    SKU on ``read()`` -- simulates a channel whose apply silently didn't
    take (or an eventual-consistency race), to test ``verify_applied``'s
    incident surfacing (plan section 7: "apply sin verificacion =
    incidente")."""

    def __init__(self, inner: writeback.InMemoryStore, *, lie_about: dict[str, float]) -> None:
        self._inner = inner
        self._lie_about = lie_about

    def read(self, entity_id: str) -> dict:
        live = dict(self._inner.read(entity_id))
        if entity_id in self._lie_about and "price" in live:
            live["price"] = self._lie_about[entity_id]
        return live

    def applied_keys(self) -> set[str]:
        return self._inner.applied_keys()

    def claim(self, idempotency_key: str, *, now: float | None = None) -> bool:
        return self._inner.claim(idempotency_key, now=now)

    def release(self, idempotency_key: str) -> None:
        self._inner.release(idempotency_key)

    def commit(self, changeset: writeback.Changeset, approved_by: str) -> writeback.AuditEntry:
        return self._inner.commit(changeset, approved_by)

    def rollback(self, idempotency_key: str) -> None:
        self._inner.rollback(idempotency_key)


def test_verify_applied_raises_an_incident_on_a_live_mismatch():
    inner = _price_store()
    store = _MismatchStore(inner, lie_about={"SKU-1": 19.99})  # apply "took" 18.0 but channel reports 19.99
    cs = stage_repricing(
        store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON,
        landed_costs={},  # this test is about gate/approval semantics, not costs
    )
    approval = writeback.approve(cs, "operator", now=0.0)
    apply_repricing(store, cs, approval, now=0.0)

    with pytest.raises(RepricingVerificationFailed) as excinfo:
        verify_applied(store, cs, "shopify:demo")
    assert excinfo.value.channel == "shopify:demo"
    assert excinfo.value.mismatches == (("SKU-1", 18.0, 19.99),)


# ---- full cycle against each of the three InMemory connector stand-ins ----------


def test_full_cycle_against_inmemory_shopify():
    rpc = InMemoryShopify({"SKU-1": {"variant_id": "gid://shopify/ProductVariant/1", "price": 20.0}})
    store = ShopifyPriceStore(rpc)
    result = run_channel_repricing(
        store, "shopify:demo-shop", {"SKU-1": 18.0},
        idempotency_key="cycle-1", reason=REASON, approved_by="operator", now=0.0,
        landed_costs={"SKU-1": 10.0},
    )
    assert isinstance(result, ChannelRepricingResult)
    assert result.verified is True
    assert result.apply_result.applied is True
    assert rpc.find_variant_by_sku("SKU-1")["price"] == 18.0


def test_full_cycle_against_inmemory_meli():
    rpc = InMemoryMeli({"SKU-1": {"item_id": "MLA1", "price": 20.0}})
    store = MeliPriceStore(rpc)
    result = run_channel_repricing(
        store, "meli:demo-seller", {"SKU-1": 17.5},
        idempotency_key="cycle-1", reason=REASON, approved_by="operator", now=0.0,
        landed_costs={"SKU-1": 10.0},
    )
    assert result.verified is True
    assert rpc.find_item_by_sku("SKU-1")["price"] == 17.5


def test_full_cycle_against_inmemory_odoo():
    rpc = demo_odoo()
    store = OdooPriceStore(rpc)
    result = run_channel_repricing(
        store, "odoo", {"SKU-1": 19.0},
        idempotency_key="cycle-1", reason=REASON, approved_by="operator", now=0.0,
        landed_costs={"SKU-1": 10.0},
    )
    assert result.verified is True
    assert rpc.records("product.product")[1]["list_price"] == 19.0


def test_full_cycle_blocked_by_guardrails_never_calls_apply():
    rpc = InMemoryShopify({"SKU-1": {"variant_id": "gid://shopify/ProductVariant/1", "price": 20.0}})
    store = ShopifyPriceStore(rpc)
    with pytest.raises(RepricingGuardrailBlocked):
        run_channel_repricing(
            store, "shopify:demo-shop", {"SKU-1": 18.0},
            idempotency_key="cycle-1", reason="", approved_by="operator", now=0.0,
            landed_costs={"SKU-1": 10.0},
        )
    assert rpc.find_variant_by_sku("SKU-1")["price"] == 20.0  # never written


# ---- landed_costs is mandatory: the below-cost gate must never be a no-op -------
#
# Regression guard. ``landed_costs`` used to default to None all the way down, and
# ``run_channel_repricing`` never passed it, so the gate's below-cost check
# (src/pricing_guardrails.py ``_economic_violations``) silently did nothing on the
# production path: every price, however far below cost, cleared the gate.


def test_stage_repricing_rejects_missing_landed_costs():
    store = _price_store()
    with pytest.raises(LandedCostsRequired, match="Landed costs required"):
        stage_repricing(store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON)


def test_stage_repricing_rejects_explicitly_none_landed_costs():
    store = _price_store()
    with pytest.raises(LandedCostsRequired):
        stage_repricing(
            store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON,
            landed_costs=None,
        )


def test_stage_repricing_accepts_empty_landed_costs_as_a_deliberate_choice():
    """An empty mapping is an explicit "no cost data for these SKUs", not the
    accidental None the caller forgot to pass -- it must still be allowed."""
    store = _price_store()
    cs = stage_repricing(
        store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON,
        landed_costs={},
    )
    assert cs.changes


def test_run_channel_repricing_enforces_the_below_cost_gate():
    """The bug this guards: the full production cycle used to let a below-cost
    price through because it never forwarded landed_costs to the gate."""
    rpc = InMemoryShopify({"SKU-1": {"variant_id": "gid://shopify/ProductVariant/1", "price": 20.0}})
    store = ShopifyPriceStore(rpc)
    with pytest.raises(RepricingGuardrailBlocked) as excinfo:
        run_channel_repricing(
            store, "shopify:demo-shop", {"SKU-1": 9.0},
            idempotency_key="cycle-1", reason=REASON, approved_by="operator", now=0.0,
            landed_costs={"SKU-1": 12.0},
        )
    assert "below landed cost" in str(excinfo.value.gate.reason)
    assert rpc.find_variant_by_sku("SKU-1")["price"] == 20.0  # never written


def test_run_channel_repricing_rejects_missing_landed_costs():
    rpc = InMemoryShopify({"SKU-1": {"variant_id": "gid://shopify/ProductVariant/1", "price": 20.0}})
    store = ShopifyPriceStore(rpc)
    with pytest.raises(LandedCostsRequired):
        run_channel_repricing(
            store, "shopify:demo-shop", {"SKU-1": 18.0},
            idempotency_key="cycle-1", reason=REASON, approved_by="operator", now=0.0,
        )
    assert rpc.find_variant_by_sku("SKU-1")["price"] == 20.0  # never written

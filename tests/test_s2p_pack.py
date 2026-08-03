"""The pack as a whole: the tool contract, the entity types, and what it reuses.

The point of these is less "does source-to-pay work" and more "did source-to-pay
need anything the core did not already have". Every assertion here that passes
without a change to ``nosilo_core`` is a piece of evidence for the extraction.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from nosilo_core.access import (
    ALLOW,
    DROP,
    INTERNAL,
    RESTRICTED,
    Capability,
    PolicyEngine,
    Principal,
    Rule,
)
from nosilo_core.entities import EntityTypeRegistry, SqliteEntityStore
from nosilo_core.guided import ESCALATED, HANDOFF, OPTIONS, verify_guided
from nosilo_core.registry import ToolRegistry
from nosilo_core.types import STATUS_NEEDS_DATA, STATUS_OK, JobRequest

from packs.source_to_pay import (
    PURCHASE_ORDER,
    SUPPLIER_INVOICE,
    build_s2p_registry,
    build_s2p_tools,
    invoice_key,
    register,
    s2p_policy,
)
from packs.source_to_pay.tool import THREE_WAY_MATCH, deliver, options, prepare, qa, run

SUPPLIER = "B12345678"

PAYLOAD = {
    "purchase_orders": [
        {"po_number": "PO-1", "line_no": "1", "sku_code": "A1", "ordered_qty": 10, "unit_price": 100},
    ],
    "receipts": [{"receipt_id": "GR-1", "po_number": "PO-1", "po_line_no": "1", "received_qty": 10}],
    "invoices": [
        {
            "invoice_number": "INV-1", "supplier_tax_id": SUPPLIER, "invoice_date": "2026-08-01",
            "currency": "EUR", "net_total": 1000, "tax_total": 0, "gross_total": 1000,
            "lines": [{
                "line_no": "1", "po_number": "PO-1", "po_line_no": "1",
                "invoiced_qty": 10, "unit_price": 100, "line_total": 1000,
            }],
        }
    ],
}


def _payload_file(tmp_path, payload=None):
    path = tmp_path / "s2p.json"
    path.write_text(json.dumps(payload or PAYLOAD), encoding="utf-8")
    return str(path)


def _run(tmp_path, payload=None, params=None):
    prepared = prepare(JobRequest(brief="match my invoices", data_path=_payload_file(tmp_path, payload)), None)
    assert prepared.status == STATUS_OK, prepared.messages
    return run(prepared.payload, params or {})


# ---- the tool contract, unchanged ------------------------------------------


def test_the_pack_registers_with_one_call_and_no_orchestrator_change() -> None:
    registry = ToolRegistry()
    register(registry)
    assert [t.key for t in registry.list()] == ["three_way_match"]
    assert registry.get("three_way_match").requires_data


def test_the_brief_routes_to_the_tool_through_the_stock_matcher() -> None:
    ranked = build_s2p_tools().match("we need to check supplier invoices against purchase orders")
    assert ranked[0][0].key == "three_way_match"
    assert ranked[0][1] > 0


def test_a_spanish_brief_routes_too() -> None:
    ranked = build_s2p_tools().match("necesito una conciliacion de facturas de proveedor")
    assert ranked[0][0].key == "three_way_match"


def test_prepare_asks_for_data_rather_than_failing(tmp_path) -> None:
    assert prepare(JobRequest(brief="match invoices"), None).status == STATUS_NEEDS_DATA


def test_prepare_reports_a_malformed_payload_precisely(tmp_path) -> None:
    broken = {"invoices": [{"invoice_number": "INV-1", "supplier_tax_id": SUPPLIER}]}
    prepared = prepare(JobRequest(brief="x", data_path=_payload_file(tmp_path, broken)), None)
    assert prepared.status == STATUS_NEEDS_DATA
    assert "net_total" in prepared.messages[0]


def test_a_bad_amount_is_rejected_rather_than_coerced(tmp_path) -> None:
    """Silently dropping a line is an invoice that matches when it should not."""
    broken = json.loads(json.dumps(PAYLOAD))
    broken["invoices"][0]["lines"][0]["unit_price"] = "not-a-number"
    prepared = prepare(JobRequest(brief="x", data_path=_payload_file(tmp_path, broken)), None)
    assert prepared.status == STATUS_NEEDS_DATA
    assert "unit_price" in prepared.messages[0]


def test_run_produces_a_report_and_a_human_summary(tmp_path) -> None:
    produced = _run(tmp_path)
    assert "1 invoice(s)" in produced.summary
    assert "100% touchless" in produced.summary


def test_tolerances_come_from_job_params(tmp_path) -> None:
    payload = json.loads(json.dumps(PAYLOAD))
    payload["invoices"][0]["lines"][0]["unit_price"] = 105
    payload["invoices"][0]["lines"][0]["line_total"] = 1050
    payload["invoices"][0]["net_total"] = 1050
    payload["invoices"][0]["gross_total"] = 1050

    assert not _run(tmp_path, payload).report.matched
    assert _run(tmp_path, payload, {"price_tolerance_pct": 10}).report.matched


# ---- the QA veto ------------------------------------------------------------


def test_qa_passes_a_clean_run(tmp_path) -> None:
    assert qa(_run(tmp_path).report) == []


def test_qa_refuses_a_run_where_almost_nothing_matched(tmp_path) -> None:
    """A 90% exception rate is a mapping problem, and shipping it wastes a day."""
    payload = json.loads(json.dumps(PAYLOAD))
    payload["purchase_orders"] = []  # nothing will match
    issues = qa(_run(tmp_path, payload).report)
    assert issues
    assert "mapping problem" in issues[0]


def test_qa_rejects_an_empty_run() -> None:
    from packs.source_to_pay.matching import MatchRun

    assert qa(MatchRun()) == ["no invoices were matched"]


# ---- deliverables -----------------------------------------------------------


def test_deliver_writes_a_report_and_an_exception_csv(tmp_path) -> None:
    payload = json.loads(json.dumps(PAYLOAD))
    payload["invoices"][0]["lines"][0]["unit_price"] = 130
    payload["invoices"][0]["lines"][0]["line_total"] = 1300
    payload["invoices"][0]["net_total"] = 1300
    payload["invoices"][0]["gross_total"] = 1300

    files = deliver(_run(tmp_path, payload).report, tmp_path / "out", "Acme")
    assert set(files) == {"report", "exceptions"}
    report = files["report"].read_text(encoding="utf-8")
    assert "Three-way match - Acme" in report
    assert "price_variance" in files["exceptions"].read_text(encoding="utf-8")


# ---- the never-unprotected contract ----------------------------------------


def test_a_clean_run_still_offers_ranked_options(tmp_path) -> None:
    outcome = options(_run(tmp_path).report)
    assert outcome.status == OPTIONS
    assert verify_guided(outcome) == []
    assert any(o.recommended for o in outcome.options)


def test_a_duplicate_outranks_everything_else_in_the_run(tmp_path) -> None:
    """A payment about to go out twice must not be one row in a list of ninety."""
    payload = json.loads(json.dumps(PAYLOAD))
    payload["invoices"].append(json.loads(json.dumps(payload["invoices"][0])))
    outcome = options(_run(tmp_path, payload).report)
    assert outcome.status == HANDOFF
    assert "duplicate" in outcome.summary.lower()
    assert verify_guided(outcome) == []


def test_every_outcome_this_tool_produces_satisfies_the_contract(tmp_path) -> None:
    payloads = [PAYLOAD]
    variance = json.loads(json.dumps(PAYLOAD))
    variance["invoices"][0]["lines"][0]["unit_price"] = 130
    variance["invoices"][0]["lines"][0]["line_total"] = 1300
    variance["invoices"][0]["net_total"] = 1300
    variance["invoices"][0]["gross_total"] = 1300
    payloads.append(variance)

    for payload in payloads:
        outcome = options(_run(tmp_path, payload).report)
        assert outcome.status in (OPTIONS, HANDOFF, ESCALATED)
        assert verify_guided(outcome) == []


# ---- entity types and policy, reused not rebuilt ----------------------------


def test_the_pack_declares_its_own_entity_types() -> None:
    assert build_s2p_registry().names() == ("goods_receipt", "purchase_order", "supplier_invoice")


def test_the_invoice_key_is_supplier_plus_number() -> None:
    assert invoice_key(" B123 ", " INV-1 ") == "B123|INV-1"
    assert invoice_key("A", "1") != invoice_key("B", "1")


def test_a_re_sent_invoice_resolves_to_the_same_entity(tmp_path) -> None:
    store = SqliteEntityStore(build_s2p_registry(), ":memory:")
    first = store.observe(
        SUPPLIER_INVOICE.name,
        __import__("nosilo_core.entities", fromlist=["SourceRef"]).SourceRef("ocr", "pdf", "scan-1"),
        {"invoice_key": invoice_key(SUPPLIER, "INV-1"), "invoice_number": "INV-1", "net_total": Decimal("1000")},
    )
    second = store.observe(
        SUPPLIER_INVOICE.name,
        __import__("nosilo_core.entities", fromlist=["SourceRef"]).SourceRef("email", "attachment", "mail-9"),
        {"invoice_key": invoice_key(SUPPLIER, "INV-1"), "posting_status": "draft"},
    )
    assert second.nosilo_id == first.nosilo_id
    assert len(store.xrefs(first.nosilo_id)) == 2
    store.close()


def test_invoice_totals_are_guarded_against_an_ocr_pipeline(tmp_path) -> None:
    """Extraction confidence is not authority: a scan must not move the amount."""
    from nosilo_core.entities import SourceRef

    store = SqliteEntityStore(build_s2p_registry(), ":memory:")
    key = {"invoice_key": invoice_key(SUPPLIER, "INV-1")}
    store.observe(SUPPLIER_INVOICE.name, SourceRef("erp", "invoice", "1"),
                  {**key, "gross_total": Decimal("1210")})
    tampered = store.observe(SUPPLIER_INVOICE.name, SourceRef("ocr", "pdf", "scan-2"),
                             {**key, "gross_total": Decimal("12100")}, trust=999.0)
    assert [c.reason for c in tampered.conflicts] == ["guarded"]
    assert store.current(tampered.nosilo_id).attributes["gross_total"] == Decimal("1210")
    store.close()


def test_a_remit_to_account_is_dropped_below_clearance(tmp_path) -> None:
    from nosilo_core.entities import SourceRef

    store = SqliteEntityStore(
        build_s2p_registry(), ":memory:",
        policy=PolicyEngine(s2p_policy("v1", rules=(Rule(effect=ALLOW),))),
    )
    result = store.observe(
        SUPPLIER_INVOICE.name, SourceRef("erp", "invoice", "1"),
        {"invoice_key": invoice_key(SUPPLIER, "INV-1"), "remit_to_account": "ES99 9999"},
    )
    clerk = Principal(id="u1", kind="human", tenant="default", roles=frozenset({"ap_clerk"}))
    read = store.read(
        result.nosilo_id, principal=clerk, purpose="ap",
        capability=Capability(tenant="default", max_sensitivity=INTERNAL),
    )
    assert "remit_to_account" not in read.values
    assert read.withheld["remit_to_account"] == DROP

    treasury = store.read(
        result.nosilo_id, principal=clerk, purpose="ap",
        capability=Capability(tenant="default", max_sensitivity=RESTRICTED),
    )
    assert treasury.values["remit_to_account"] == "ES99 9999"
    store.close()


def test_two_packs_share_one_registry_rather_than_duplicating_supplier() -> None:
    """A shared entity type is the whole point; two ideas of one company is the silo."""
    from src.entities import build_scm_registry

    shared: EntityTypeRegistry = build_s2p_registry(build_scm_registry())
    assert "supplier" in shared
    assert "supplier_invoice" in shared
    assert set(shared.names()) == {
        "goods_receipt", "location", "purchase_order", "sku", "supplier", "supplier_invoice"
    }


def test_registering_the_same_pack_twice_is_refused() -> None:
    registry = ToolRegistry()
    register(registry)
    with pytest.raises(ValueError, match="already registered"):
        register(registry)


def test_the_tool_is_the_pack_s_entire_integration_surface() -> None:
    """Everything the orchestrator needs is on the Tool - nothing else is wired."""
    assert THREE_WAY_MATCH.prepare and THREE_WAY_MATCH.run
    assert THREE_WAY_MATCH.qa and THREE_WAY_MATCH.deliver
    assert THREE_WAY_MATCH.options is not None
    assert PURCHASE_ORDER.name == "purchase_order"

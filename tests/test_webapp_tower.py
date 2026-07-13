"""Tests for the Control Tower webapp surface (Linchpin 3.0 PR-7/PR-9, plan S5):

- GET /api/events returns real emitted scm_agent.events.Event rows (windowed,
  optionally filtered by type), never a fabricated/hardcoded list;
- POST /api/approvals/{id} completes a real pending T2 AutonomyRecord via
  scm_agent.autonomy.acknowledge_pending() -- the SAME function PR-6 built
  for exactly this endpoint -- and the completion is reflected in a
  subsequent GET (here, GET /tower's server-rendered pending section, since
  the plan only asks for GET /api/events + POST /api/approvals/{id}, not a
  third GET /api/autonomy);
- an unknown or already-acknowledged approval id is rejected with a clear,
  loud error (404 / 409), never a silent no-op;
- GET /tower returns 200 HTML containing "Kern" (same brand-presence
  assertion style as test_webapp.py's test_console_route_serves_the_prototype);
- POST /api/promotions/{id}/approve (PR-9) completes a real PENDING
  PromotionRecord via scm_agent.autonomy_promotion.approve_promotion() and
  ACTUALLY mutates the (isolated, throwaway) event_routing.yaml on disk --
  reflected both in a subsequent GET /tower and in a fresh load_routing();
- POST /api/promotions/{id}/reject marks a PromotionRecord rejected without
  ever touching the routing file;
- both promotion endpoints share the approve endpoint's 404/409/401 error
  conventions.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401  (canonical name, python-multipart >= 0.0.26)
except ImportError:
    pytest.importorskip("multipart")  # legacy name; skips the module if also absent
from fastapi.testclient import TestClient  # noqa: E402

import webapp.app as appmod  # noqa: E402
from scm_agent.autonomy import STATUS_PENDING, TIER_T2, AutonomyLedger  # noqa: E402
from scm_agent.autonomy_promotion import (  # noqa: E402
    KIND_PROMOTION,
    PROMOTION_STATUS_PENDING,
    PromotionLedger,
)
from scm_agent.event_intent import load_routing  # noqa: E402
from scm_agent.events import Event, EventLedger  # noqa: E402
from webapp import security  # noqa: E402
from webapp.app import app  # noqa: E402

client = TestClient(app)

_FIXTURE_ROUTING_YAML = """\
version: 1

routes:
  stock_below_rop:
    tool: inventory_optimization
    param_builder: inventory_from_stock_event
    autonomy_tier: T2
"""


@pytest.fixture()
def isolated_ledgers(tmp_path, monkeypatch):
    """Point the app's Control Tower ledgers (and, for PR-9, the routing
    config file promotions actually mutate) at throwaway files so these
    tests never touch the real (gitignored) data/ directory or the real,
    committed config/event_routing.yaml, and never see state left behind by
    another test."""
    events_path = tmp_path / "events.sqlite3"
    autonomy_path = tmp_path / "autonomy.sqlite3"
    routing_path = tmp_path / "event_routing.yaml"
    routing_path.write_text(_FIXTURE_ROUTING_YAML, encoding="utf-8")
    monkeypatch.setattr(appmod, "EVENTS_LEDGER_PATH", str(events_path))
    monkeypatch.setattr(appmod, "AUTONOMY_LEDGER_PATH", str(autonomy_path))
    monkeypatch.setattr(appmod, "EVENT_ROUTING_PATH", str(routing_path))
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 0)
    monkeypatch.setattr(security, "API_KEY", "")
    return events_path, autonomy_path, routing_path


def _record_pending_promotion(autonomy_path, **overrides) -> str:
    kwargs = {
        "kind": KIND_PROMOTION,
        "status": PROMOTION_STATUS_PENDING,
        "event_type": "stock_below_rop",
        "tool": "inventory_optimization",
        "from_tier": TIER_T2,
        "to_tier": "T1",
        "rationale": "4 consecutive cycles at 94% precision",
        "evidence": (),
    }
    kwargs.update(overrides)
    ledger = PromotionLedger(autonomy_path)
    try:
        record = ledger.record(**kwargs)
    finally:
        ledger.close()
    return record.id


def _emit_event(events_path, **overrides) -> Event:
    kwargs = {
        "type": "stock_below_rop",
        "severity": "high",
        "source": "monitors",
        "dedup_key": overrides.get("sku", "SKU-A") + ":stock_below_rop",
        "sku": "SKU-A",
        "payload": {"on_hand": 12.0, "reorder_point": 20.0},
    }
    kwargs.update(overrides)
    event = Event(**kwargs)
    ledger = EventLedger(events_path)
    try:
        ledger.emit(event)
    finally:
        ledger.close()
    return event


def _record_pending(autonomy_path, **overrides) -> str:
    kwargs = {
        "tier": TIER_T2,
        "status": STATUS_PENDING,
        "event_type": "stock_below_rop",
        "event_id": "evt-1",
        "sku": "SKU-A",
        "tool": "inventory_optimization",
        "summary": "stock_below_rop (SKU-A): inventory_optimization -- Reorder plan computed.",
    }
    kwargs.update(overrides)
    ledger = AutonomyLedger(autonomy_path)
    try:
        record = ledger.record(**kwargs)
    finally:
        ledger.close()
    return record.id


# ---- GET /api/events -----------------------------------------------------


def test_events_endpoint_returns_no_fabricated_data_when_ledger_is_empty(isolated_ledgers):
    body = client.get("/api/events").json()
    assert body == {"events": [], "count": 0}


def test_events_endpoint_returns_real_emitted_events(isolated_ledgers):
    events_path, _, _ = isolated_ledgers
    _emit_event(events_path, sku="SKU-A")
    _emit_event(events_path, sku="SKU-B", dedup_key="SKU-B:stock_below_rop")

    body = client.get("/api/events").json()

    assert body["count"] == 2
    skus = {e["sku"] for e in body["events"]}
    assert skus == {"SKU-A", "SKU-B"}
    first = body["events"][0]
    assert first["type"] == "stock_below_rop"
    assert first["severity"] == "high"
    assert first["source"] == "monitors"
    assert first["payload"] == {"on_hand": 12.0, "reorder_point": 20.0}
    assert "ts" in first and "id" in first


def test_events_endpoint_filters_by_event_type(isolated_ledgers):
    events_path, _, _ = isolated_ledgers
    _emit_event(events_path, sku="SKU-A")
    _emit_event(events_path, type="excess_growing", dedup_key="SKU-C:excess_growing", sku="SKU-C")

    body = client.get("/api/events?event_type=excess_growing").json()

    assert body["count"] == 1
    assert body["events"][0]["type"] == "excess_growing"


def test_events_endpoint_windows_to_the_most_recent_rows(isolated_ledgers):
    """The must-have windowing behavior: with limit < total rows, the NEWEST
    events are kept, not the oldest -- a live Tower feed must shrink toward
    what just happened as the table grows."""
    events_path, _, _ = isolated_ledgers
    for i in range(5):
        _emit_event(events_path, sku=f"SKU-{i}", dedup_key=f"SKU-{i}:stock_below_rop")

    body = client.get("/api/events?limit=2").json()

    assert body["count"] == 2
    assert {e["sku"] for e in body["events"]} == {"SKU-3", "SKU-4"}


def test_events_endpoint_rejects_limit_out_of_bounds(isolated_ledgers):
    assert client.get("/api/events?limit=0").status_code == 422
    assert client.get("/api/events?limit=100000").status_code == 422


def test_events_endpoint_rate_limited(isolated_ledgers, monkeypatch):
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 2)
    monkeypatch.setattr(security, "RATE_WINDOW", 60)
    assert client.get("/api/events").status_code == 200
    assert client.get("/api/events").status_code == 200
    blocked = client.get("/api/events")
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


# ---- POST /api/approvals/{id} ---------------------------------------------


def test_approve_pending_completes_the_underlying_t2_item(isolated_ledgers):
    _, autonomy_path, _ = isolated_ledgers
    pending_id = _record_pending(autonomy_path)

    r = client.post(f"/api/approvals/{pending_id}")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "executed"
    assert "Acknowledged by operator" in body["summary"]
    assert body["record"]["status"] == "acknowledged"
    assert body["record"]["acknowledged_by"] == "operator"


def test_approve_pending_honors_approved_by_query_param(isolated_ledgers):
    _, autonomy_path, _ = isolated_ledgers
    pending_id = _record_pending(autonomy_path)

    r = client.post(f"/api/approvals/{pending_id}?approved_by=alice")

    assert r.status_code == 200
    assert r.json()["record"]["acknowledged_by"] == "alice"


def test_approve_pending_is_reflected_in_a_subsequent_get(isolated_ledgers):
    """The approval is not just an in-memory response -- a later request
    (here, GET /tower's server-rendered T2 section) sees the completed
    state: the item is no longer listed as pending."""
    _, autonomy_path, _ = isolated_ledgers
    pending_id = _record_pending(autonomy_path, summary="UNIQUE-PENDING-MARKER")

    before = client.get("/tower").text
    assert "UNIQUE-PENDING-MARKER" in before
    assert pending_id in before

    approve = client.post(f"/api/approvals/{pending_id}")
    assert approve.status_code == 200

    after = client.get("/tower").text
    assert "UNIQUE-PENDING-MARKER" not in after
    assert pending_id not in after

    # White-box confirmation directly against the ledger, independent of how
    # the page happens to render it.
    ledger = AutonomyLedger(autonomy_path)
    try:
        assert ledger.get(pending_id).status == "acknowledged"
        assert ledger.list_pending() == []
    finally:
        ledger.close()


def test_approve_pending_unknown_id_is_a_clear_404_not_a_silent_noop(isolated_ledgers):
    r = client.post("/api/approvals/does-not-exist")

    assert r.status_code == 404
    assert "does-not-exist" in r.json()["detail"]


def test_approve_pending_already_acknowledged_is_a_clear_409_not_a_silent_noop(isolated_ledgers):
    _, autonomy_path, _ = isolated_ledgers
    pending_id = _record_pending(autonomy_path)
    first = client.post(f"/api/approvals/{pending_id}")
    assert first.status_code == 200

    second = client.post(f"/api/approvals/{pending_id}")

    assert second.status_code == 409
    assert "not pending" in second.json()["detail"]


def test_approve_pending_requires_api_key_when_configured(isolated_ledgers, monkeypatch):
    _, autonomy_path, _ = isolated_ledgers
    pending_id = _record_pending(autonomy_path)
    monkeypatch.setattr(security, "API_KEY", "s3cret")

    assert client.post(f"/api/approvals/{pending_id}").status_code == 401
    assert client.post(f"/api/approvals/{pending_id}", headers={"X-API-Key": "nope"}).status_code == 401

    ok = client.post(f"/api/approvals/{pending_id}", headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 200


# ---- GET /tower ------------------------------------------------------------


def test_tower_page_returns_200_with_kern_branding(isolated_ledgers):
    r = client.get("/tower")
    assert r.status_code == 200
    assert "Kern" in r.text


def test_tower_page_lists_a_pending_t2_item_with_an_approve_button(isolated_ledgers):
    _, autonomy_path, _ = isolated_ledgers
    pending_id = _record_pending(autonomy_path, summary="Reorder plan computed for SKU-A.")

    r = client.get("/tower")

    assert "Reorder plan computed for SKU-A." in r.text
    assert f'data-approval-id="{pending_id}"' in r.text
    assert "Aprobar" in r.text


def test_tower_page_empty_state_when_no_pending_items(isolated_ledgers):
    r = client.get("/tower")
    assert "No hay aprobaciones pendientes" in r.text


def test_tower_page_shows_a4_placeholder_not_fabricated_numbers(isolated_ledgers):
    r = client.get("/tower")
    assert "Confiabilidad por tool" in r.text
    assert "PR-8" in r.text


def test_tower_page_lists_auto_executed_t1_records(isolated_ledgers):
    _, autonomy_path, _ = isolated_ledgers
    ledger = AutonomyLedger(autonomy_path)
    try:
        ledger.record(
            tier="T1", status="auto_executed", event_type="stock_below_rop", event_id="evt-2",
            sku="SKU-Z", tool="inventory_optimization", summary="T1-AUTO-MARKER",
        )
    finally:
        ledger.close()

    r = client.get("/tower")

    assert "T1-AUTO-MARKER" in r.text


def test_tower_page_lists_a_pending_promotion_with_evidence(isolated_ledgers):
    _, autonomy_path, _ = isolated_ledgers
    promotion_id = _record_pending_promotion(autonomy_path, rationale="UNIQUE-PROMOTION-RATIONALE")

    r = client.get("/tower")

    assert "UNIQUE-PROMOTION-RATIONALE" in r.text
    assert f'data-promotion-id="{promotion_id}"' in r.text
    assert "Promociones de autonomia pendientes" in r.text


def test_tower_page_empty_state_when_no_pending_promotions(isolated_ledgers):
    r = client.get("/tower")
    assert "No hay promociones de autonomia pendientes" in r.text


# ---- POST /api/promotions/{id}/approve + /reject (PR-9) --------------------


def test_approve_promotion_end_to_end_mutates_the_isolated_routing_file(isolated_ledgers):
    """The real, load-bearing guarantee: approving via HTTP actually flips
    autonomy_tier in the (isolated, throwaway) event_routing.yaml, not just
    the ledger row."""
    _, autonomy_path, routing_path = isolated_ledgers
    promotion_id = _record_pending_promotion(autonomy_path)

    r = client.post(f"/api/promotions/{promotion_id}/approve")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "executed"
    assert body["record"]["status"] == "approved"
    assert body["record"]["resolved_by"] == "operator"
    routes = load_routing(routing_path)
    assert routes["stock_below_rop"].autonomy_tier == "T1"


def test_approve_promotion_honors_approved_by_query_param(isolated_ledgers):
    _, autonomy_path, _ = isolated_ledgers
    promotion_id = _record_pending_promotion(autonomy_path)

    r = client.post(f"/api/promotions/{promotion_id}/approve?approved_by=alice")

    assert r.status_code == 200
    assert r.json()["record"]["resolved_by"] == "alice"


def test_approve_promotion_is_reflected_in_a_subsequent_get(isolated_ledgers):
    _, autonomy_path, _ = isolated_ledgers
    promotion_id = _record_pending_promotion(autonomy_path, rationale="UNIQUE-PROMOTION-MARKER")

    before = client.get("/tower").text
    assert "UNIQUE-PROMOTION-MARKER" in before

    approve = client.post(f"/api/promotions/{promotion_id}/approve")
    assert approve.status_code == 200

    after = client.get("/tower").text
    assert "UNIQUE-PROMOTION-MARKER" not in after
    assert "No hay promociones de autonomia pendientes" in after


def test_approve_promotion_unknown_id_is_a_clear_404_not_a_silent_noop(isolated_ledgers):
    r = client.post("/api/promotions/does-not-exist/approve")

    assert r.status_code == 404
    assert "does-not-exist" in r.json()["detail"]


def test_approve_promotion_already_resolved_is_a_clear_409_not_a_silent_noop(isolated_ledgers):
    _, autonomy_path, _ = isolated_ledgers
    promotion_id = _record_pending_promotion(autonomy_path)
    first = client.post(f"/api/promotions/{promotion_id}/approve")
    assert first.status_code == 200

    second = client.post(f"/api/promotions/{promotion_id}/approve")

    assert second.status_code == 409
    assert "not pending" in second.json()["detail"]


def test_approve_promotion_requires_api_key_when_configured(isolated_ledgers, monkeypatch):
    _, autonomy_path, _ = isolated_ledgers
    promotion_id = _record_pending_promotion(autonomy_path)
    monkeypatch.setattr(security, "API_KEY", "s3cret")

    assert client.post(f"/api/promotions/{promotion_id}/approve").status_code == 401

    ok = client.post(f"/api/promotions/{promotion_id}/approve", headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 200


def test_reject_promotion_marks_rejected_and_never_touches_the_routing_file(isolated_ledgers):
    _, autonomy_path, routing_path = isolated_ledgers
    before = routing_path.read_text(encoding="utf-8")
    promotion_id = _record_pending_promotion(autonomy_path)

    r = client.post(f"/api/promotions/{promotion_id}/reject?rejected_by=bob")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "rejected"
    assert body["record"]["status"] == "rejected"
    assert body["record"]["resolved_by"] == "bob"
    assert routing_path.read_text(encoding="utf-8") == before  # untouched
    routes = load_routing(routing_path)
    assert routes["stock_below_rop"].autonomy_tier == "T2"  # unchanged


def test_reject_promotion_unknown_id_is_a_clear_404(isolated_ledgers):
    r = client.post("/api/promotions/does-not-exist/reject")
    assert r.status_code == 404

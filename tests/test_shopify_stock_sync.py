"""Tests for jobs/shopify_stock_sync.py."""

from __future__ import annotations

from jobs.shopify_stock_sync import (
    TARGET,
    idempotency_key,
    plan_stock_sync,
)
from src.connectors.shopify_inventory import (
    InMemoryShopifyInventory,
    ShopifyInventoryStore,
)

LOC = "gid://shopify/Location/1"


def _store(stock: dict[str, int]) -> ShopifyInventoryStore:
    return ShopifyInventoryStore(InMemoryShopifyInventory(stock={s: {LOC: q} for s, q in stock.items()}),
                                 location_id=LOC)


def test_no_divergence_produces_no_changeset():
    report = plan_stock_sync(_store({"SKU-1": 40}), {"SKU-1": 40})
    assert report.changeset is None
    assert report.missing_skus == ()


def test_divergence_produces_a_changeset_naming_the_sku():
    report = plan_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40})
    assert report.changeset is not None
    assert report.changeset.target == TARGET
    changes = report.changeset.changes
    assert len(changes) == 1
    assert changes[0].entity_id == "SKU-1"
    assert changes[0].field == "quantity"
    assert float(changes[0].before) == 37.0
    assert float(changes[0].after) == 40.0


def test_planning_never_writes_to_shopify():
    rpc = InMemoryShopifyInventory(stock={"SKU-1": {LOC: 37}})
    store = ShopifyInventoryStore(rpc, location_id=LOC)
    plan_stock_sync(store, {"SKU-1": 40})
    # stage() is a dry run: Shopify must be untouched, and no write key recorded.
    assert rpc.get_inventory_level("gid://shopify/InventoryItem/SKU-1", LOC) == 37
    assert rpc.idempotency_keys == []


def test_sku_missing_in_shopify_is_reported_and_skipped():
    report = plan_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40, "SKU-NOPE": 5})
    assert report.missing_skus == ("SKU-NOPE",)
    assert report.changeset is not None
    assert [c.entity_id for c in report.changeset.changes] == ["SKU-1"]


def test_only_divergent_skus_enter_the_changeset():
    report = plan_stock_sync(_store({"SKU-1": 40, "SKU-2": 12}), {"SKU-1": 40, "SKU-2": 20})
    assert [c.entity_id for c in report.changeset.changes] == ["SKU-2"]


def test_empty_desired_is_a_valid_run_with_nothing_to_do():
    report = plan_stock_sync(_store({"SKU-1": 40}), {})
    assert report.changeset is None
    assert report.missing_skus == ()


def test_idempotency_key_is_deterministic_and_content_derived():
    a = idempotency_key({"SKU-1": {"quantity": 40.0}})
    b = idempotency_key({"SKU-1": {"quantity": 40.0}})
    c = idempotency_key({"SKU-1": {"quantity": 41.0}})
    assert a == b
    assert a != c
    assert a.startswith("shopify-stock-sync:")


def test_idempotency_key_ignores_dict_ordering():
    one = idempotency_key({"SKU-1": {"quantity": 1.0}, "SKU-2": {"quantity": 2.0}})
    two = idempotency_key({"SKU-2": {"quantity": 2.0}, "SKU-1": {"quantity": 1.0}})
    assert one == two


from datetime import datetime, timedelta, timezone  # noqa: E402

from jobs.shopify_stock_sync import (  # noqa: E402
    EVENT_TYPE,
    SOURCE,
    run_stock_sync,
)
from scm_agent.events import EventLedger  # noqa: E402


def _ledger() -> EventLedger:
    return EventLedger(path=":memory:")


def test_no_divergence_emits_no_event():
    report, event, emitted = run_stock_sync(_store({"SKU-1": 40}), {"SKU-1": 40}, ledger=_ledger())
    assert report.changeset is None
    assert event is None
    assert emitted is False


def test_empty_desired_emits_no_event():
    report, event, emitted = run_stock_sync(_store({"SKU-1": 40}), {}, ledger=_ledger())
    assert event is None
    assert emitted is False


def test_divergence_emits_an_event_keyed_on_the_changeset_hash():
    store = _store({"SKU-1": 37})
    report, event, emitted = run_stock_sync(store, {"SKU-1": 40}, ledger=_ledger())
    assert emitted is True
    assert event.type == EVENT_TYPE
    assert event.source == SOURCE
    assert event.dedup_key == report.changeset.content_hash


def test_event_payload_carries_what_the_approval_surface_needs():
    _report, event, _emitted = run_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40}, ledger=_ledger())
    assert event.payload["idempotency_key"].startswith("shopify-stock-sync:")
    assert event.payload["n_divergent"] == 1
    assert event.payload["target"] == TARGET
    assert event.payload["missing_skus"] == []


def test_shopify_ahead_of_odoo_is_high_severity():
    # Shopify says 45, Odoo says 40 -> five units that can be oversold.
    _report, event, _emitted = run_stock_sync(_store({"SKU-1": 45}), {"SKU-1": 40}, ledger=_ledger())
    assert event.severity == "high"


def test_shopify_behind_odoo_is_medium_severity():
    _report, event, _emitted = run_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40}, ledger=_ledger())
    assert event.severity == "medium"


def test_the_same_divergence_twice_in_one_window_emits_once():
    # This test passes both with and without honouring the now parameter, because
    # both calls execute milliseconds apart in real time (within the 1h dedup window
    # either way). The 4-hour test below is what truly ensures now is honoured.
    # A test that would discriminate without mocking datetime would require either
    # putting a real sleep between calls or changing the ledger's time source.
    ledger = _ledger()
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    _r1, _e1, first = run_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40}, ledger=ledger, now=ts)
    _r2, _e2, second = run_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40}, ledger=ledger,
                                      now=ts + timedelta(minutes=5))
    assert first is True
    assert second is False


def test_a_different_divergence_emits_again():
    ledger = _ledger()
    ts = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    _r1, _e1, first = run_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40}, ledger=ledger, now=ts)
    _r2, _e2, second = run_stock_sync(_store({"SKU-1": 30}), {"SKU-1": 40}, ledger=ledger,
                                      now=ts + timedelta(minutes=5))
    assert first is True
    assert second is True


def test_a_persisting_divergence_emits_again_after_the_window():
    # The ledger dedups within a 1h window; the cron runs every 4h, so a
    # divergence that persists re-emits. That is a reminder, not noise.
    ledger = _ledger()
    ts = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    _r1, _e1, first = run_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40}, ledger=ledger, now=ts)
    _r2, _e2, second = run_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40}, ledger=ledger,
                                      now=ts + timedelta(hours=4))
    assert first is True
    assert second is True


def test_payload_includes_per_sku_divergence_details():
    _report, event, _emitted = run_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40}, ledger=_ledger())
    assert len(event.payload["divergences"]) == 1
    assert event.payload["divergences"][0] == {"sku": "SKU-1", "before": 37.0, "after": 40.0}


def test_payload_truncates_divergences_over_max_and_sets_flag():
    # Create a store with 60 SKUs, all with divergence
    stock = {f"SKU-{i}": 37 for i in range(60)}
    desired = {f"SKU-{i}": 40 for i in range(60)}
    _report, event, _emitted = run_stock_sync(_store(stock), desired, ledger=_ledger())
    assert len(event.payload["divergences"]) == 50
    assert event.payload["divergences_truncated"] is True
    assert event.payload["n_divergent"] == 60

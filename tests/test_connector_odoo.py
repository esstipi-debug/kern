"""Tests for the Odoo ERP connector (read + safe-staging write), fully offline.

``OdooConnector`` speaks only Odoo's ``execute_kw`` duck type, so an ``InMemoryOdoo``
stand-in (the Odoo analogue of ``SimulatedStore``) drives the whole connector with no
network or API key: products / inventory / sales-orders on the read side, a demand bridge
into the existing engines, lead times, and restock staged as reversible reorder-point
writes through the battle-tested safe-staging plane. ``OdooClient`` (the real XML-RPC
transport) is exercised with injected proxies, so its auth + dispatch are covered too.
"""

import pytest

from src.connectors import InventorySource, OrderLine, Product
from src.connectors.odoo import (
    InMemoryOdoo,
    OdooClient,
    OdooConnector,
    OdooError,
    demo_odoo,
)
from src.connectors.replenish import plan_replenishment
from src.guided import EXECUTED, HANDOFF, passed_guided
from src.sources import DataFrameDemandSource


def _odoo() -> InMemoryOdoo:
    """A focused Odoo fixture: two SKUs, one off-site quant, one unconfirmed order."""
    return InMemoryOdoo(
        {
            "product.product": {
                1: {"default_code": "SKU-1", "name": "Widget", "list_price": 20.0, "standard_price": 12.0},
                2: {"default_code": "SKU-2", "name": "Gadget", "list_price": 50.0, "standard_price": 30.0},
            },
            "stock.location": {10: {"usage": "internal"}, 11: {"usage": "customer"}},
            "stock.quant": {
                100: {"product_id": [1, "Widget"], "location_id": [10, "WH/Stock"], "quantity": 100.0},
                101: {"product_id": [2, "Gadget"], "location_id": [10, "WH/Stock"], "quantity": 40.0},
                # a quant in a customer (non-internal) location must NOT count as on-hand:
                102: {"product_id": [1, "Widget"], "location_id": [11, "Customers"], "quantity": 5.0},
            },
            "sale.order": {
                200: {"name": "S0001", "date_order": "2026-01-05 10:00:00", "state": "sale", "order_line": [300, 301]},
                201: {"name": "S0002", "date_order": "2026-01-06 09:00:00", "state": "sale", "order_line": [302]},
                202: {"name": "S0003", "date_order": "2026-02-10 09:00:00", "state": "done", "order_line": [303]},
                # an unconfirmed (draft) order must NOT count as realized demand:
                203: {"name": "S0099", "date_order": "2026-02-11 09:00:00", "state": "draft", "order_line": [304]},
            },
            "sale.order.line": {
                300: {"product_id": [1, "Widget"], "product_uom_qty": 3.0, "price_unit": 20.0},
                301: {"product_id": [2, "Gadget"], "product_uom_qty": 1.0, "price_unit": 50.0},
                302: {"product_id": [1, "Widget"], "product_uom_qty": 2.0, "price_unit": 20.0},
                303: {"product_id": [2, "Gadget"], "product_uom_qty": 4.0, "price_unit": 50.0},
                304: {"product_id": [1, "Widget"], "product_uom_qty": 99.0, "price_unit": 20.0},
            },
            "product.supplierinfo": {
                400: {"product_id": [1, "Widget"], "partner_id": [70, "Acme Supply"], "sequence": 1, "delay": 7.0},
                401: {"product_id": [2, "Gadget"], "partner_id": [71, "Globex"], "sequence": 1, "delay": 14.0},
            },
            "stock.warehouse.orderpoint": {},
        }
    )


def _connector() -> OdooConnector:
    return OdooConnector(_odoo())


# -- read side ----------------------------------------------------------------


def test_connector_satisfies_the_inventory_source_protocol():
    assert isinstance(_connector(), InventorySource)


def test_lists_products_with_price_and_cost():
    products = {p.sku: p for p in _connector().list_products()}

    assert set(products) == {"SKU-1", "SKU-2"}
    assert products["SKU-1"] == Product("SKU-1", "Widget", 20.0, 12.0)
    assert products["SKU-2"].cost == 30.0


def test_inventory_levels_sum_only_internal_locations():
    levels = {lvl.sku: lvl.available for lvl in _connector().inventory_levels()}

    # SKU-1 has 100 internal + 5 in a customer location -> only the 100 counts.
    assert levels == {"SKU-1": 100.0, "SKU-2": 40.0}


def test_orders_exclude_unconfirmed_and_sort_by_date():
    orders = _connector().orders()

    assert [o.order_id for o in orders] == ["S0001", "S0002", "S0003"]  # draft S0099 excluded
    first = orders[0]
    assert first.created_at == "2026-01-05"  # datetime trimmed to ISO date
    assert OrderLine("SKU-1", 3.0, 20.0) in first.lines


def test_orders_can_be_filtered_since_a_date():
    recent = _connector().orders(since="2026-02-01")

    assert [o.order_id for o in recent] == ["S0003"]


# -- demand + lead-time bridges ----------------------------------------------


def test_demand_frame_feeds_the_demand_source_pipeline():
    src = DataFrameDemandSource(_connector().demand_frame())

    assert set(src.list_products()) == {"SKU-1", "SKU-2"}
    # SKU-1 was sold 3 then 2 across the two January orders.
    assert list(src.demand_series("SKU-1")) == [3.0, 2.0]


def test_lead_times_come_from_supplier_delay():
    assert _connector().lead_times() == {"SKU-1": 7.0, "SKU-2": 14.0}


# -- write side: restock staged as a reversible reorder-point edit ------------


def _orderpoint_min(odoo: InMemoryOdoo, product_id: int) -> float | None:
    rows = [r for r in odoo.records("stock.warehouse.orderpoint").values() if r.get("product_id") == product_id]
    return rows[0]["product_min_qty"] if rows else None


def test_stage_restock_is_a_dry_run_until_applied():
    odoo = _odoo()
    connector = OdooConnector(odoo)

    changeset = connector.stage_restock({"SKU-2": 60.0}, idempotency_key="r1", reason="cover Q1")

    assert changeset.risk_tier == "reversible"
    # staging writes nothing: no reorder rule exists yet
    assert _orderpoint_min(odoo, 2) is None
    # target = on-hand (40) + restock (60) = 100
    assert changeset.changes[0].after == 100.0


def test_apply_restock_writes_reorder_point_and_is_idempotent():
    odoo = _odoo()
    connector = OdooConnector(odoo)
    changeset = connector.stage_restock({"SKU-2": 60.0}, idempotency_key="r1")

    first = connector.apply_restock(changeset)
    assert first.applied is True
    assert _orderpoint_min(odoo, 2) == 100.0  # min qty set to the target cover

    # same idempotency key never lands twice
    second = connector.apply_restock(changeset)
    assert second.applied is False and second.idempotent_skip is True


def test_apply_restock_updates_an_existing_reorder_rule():
    odoo = _odoo()
    odoo.records("stock.warehouse.orderpoint")[500] = {"product_id": 2, "product_min_qty": 5.0, "product_max_qty": 5.0}
    connector = OdooConnector(odoo)

    connector.apply_restock(connector.stage_restock({"SKU-2": 60.0}, idempotency_key="r1"))

    assert _orderpoint_min(odoo, 2) == 100.0  # the existing rule was edited, not duplicated
    assert len(odoo.records("stock.warehouse.orderpoint")) == 1


def test_restock_against_an_existing_rule_can_be_rolled_back():
    odoo = _odoo()
    odoo.records("stock.warehouse.orderpoint")[500] = {"product_id": 2, "product_min_qty": 5.0, "product_max_qty": 5.0}
    connector = OdooConnector(odoo)
    connector.apply_restock(connector.stage_restock({"SKU-2": 60.0}, idempotency_key="r1"))

    connector.rollback("r1")

    assert _orderpoint_min(odoo, 2) == 5.0  # prior min qty restored


# -- end-to-end through the shared replenishment flow -------------------------


def test_plan_replenishment_runs_against_odoo_and_stays_protected():
    odoo = _odoo()
    connector = OdooConnector(odoo)

    plan = plan_replenishment(connector, cover_periods=8.0, store=connector)

    # SKU-1 sells ~2.5/period -> target 20, on-hand 100 -> no restock; SKU-2 sells ~4 on
    # its one period -> target ~32, on-hand 40 -> no restock either. Either way: protected.
    assert passed_guided(plan.outcome)
    assert plan.outcome.status in (EXECUTED, HANDOFF)


def test_plan_replenishment_stages_a_dry_run_for_a_thin_sku():
    odoo = _odoo()
    odoo.records("stock.quant")[101]["quantity"] = 1.0  # starve SKU-2
    connector = OdooConnector(odoo)

    plan = plan_replenishment(connector, cover_periods=8.0, store=connector)

    assert plan.restock.get("SKU-2", 0.0) > 0.0
    assert plan.outcome.status == HANDOFF
    assert passed_guided(plan.outcome)
    assert plan.changeset is not None
    # staged only: nothing written until applied
    assert _orderpoint_min(odoo, 2) is None


# -- demo factory -------------------------------------------------------------


def test_demo_odoo_is_a_consistent_non_empty_backend():
    connector = OdooConnector(demo_odoo())

    assert connector.list_products()
    assert connector.inventory_levels()
    assert not connector.demand_frame().empty


# -- real transport (injected proxies, no network) ----------------------------


class _FakeProxy:
    """Stands in for an xmlrpc ServerProxy: records calls, returns a canned result."""

    def __init__(self, *, uid: int | bool = 7, result=None) -> None:
        self._uid = uid
        self._result = result if result is not None else [{"id": 1}]
        self.calls: list[tuple] = []

    def authenticate(self, db, username, api_key, ctx):
        return self._uid

    def execute_kw(self, db, uid, api_key, model, method, args, kwargs):
        self.calls.append((db, uid, api_key, model, method, args, kwargs))
        return self._result


def test_odoo_client_authenticates_and_delegates_execute_kw():
    common, models = _FakeProxy(uid=7), _FakeProxy(result=[{"id": 1, "name": "X"}])
    client = OdooClient("https://erp.example.com/", "mydb", "admin", "key", common=common, models=models)

    assert client.uid == 7
    out = client.execute_kw("product.product", "search_read", [[]], {"fields": ["name"]})

    assert out == [{"id": 1, "name": "X"}]
    db, uid, key, model, method, _args, _kwargs = models.calls[0]
    assert (db, uid, key, model, method) == ("mydb", 7, "key", "product.product", "search_read")


def test_odoo_client_passes_empty_kwargs_when_omitted():
    models = _FakeProxy()
    client = OdooClient("https://x", "db", "u", "k", common=_FakeProxy(uid=1), models=models)

    client.execute_kw("res.partner", "search", [[]])

    assert models.calls[0][-1] == {}  # kwargs defaulted to {}


def test_odoo_client_raises_on_auth_failure():
    with pytest.raises(OdooError):
        OdooClient("https://x", "db", "u", "bad", common=_FakeProxy(uid=False), models=_FakeProxy())


class _FlakyProxy:
    """Raises a transient transport error the first ``fail_times`` calls, then succeeds."""

    def __init__(self, *, uid: int = 1, fail_times: int = 0, error: Exception | None = None, result=None) -> None:
        self._uid = uid
        self._fail_times = fail_times
        self._error = error or ConnectionError("connection reset")
        self._result = result if result is not None else [{"id": 1}]
        self.calls = 0

    def authenticate(self, db, username, api_key, ctx):
        return self._uid

    def execute_kw(self, db, uid, api_key, model, method, args, kwargs):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._error
        return self._result


def test_odoo_client_retries_a_read_method_on_transient_error_then_succeeds():
    proxy = _FlakyProxy(fail_times=2, result=[{"id": 9}])
    client = OdooClient("https://x", "db", "u", "k", common=proxy, models=proxy, backoff_seconds=0.0)

    out = client.execute_kw("product.product", "search_read", [[]])

    assert out == [{"id": 9}]
    assert proxy.calls == 3  # two failures + the succeeding attempt


def test_odoo_client_gives_up_after_max_attempts_on_a_read_method():
    proxy = _FlakyProxy(fail_times=99)  # never succeeds
    client = OdooClient("https://x", "db", "u", "k", common=proxy, models=proxy, max_attempts=3, backoff_seconds=0.0)

    with pytest.raises(OdooError):
        client.execute_kw("product.product", "search", [[]])
    assert proxy.calls == 3  # exactly max_attempts, no more


def test_odoo_client_never_retries_a_write_method():
    """A create/write/unlink must fail immediately on a transient error, not retry -
    retrying a write whose response was lost could duplicate it server-side."""
    proxy = _FlakyProxy(fail_times=1)
    client = OdooClient("https://x", "db", "u", "k", common=proxy, models=proxy, max_attempts=5, backoff_seconds=0.0)

    with pytest.raises(OdooError):
        client.execute_kw("purchase.order", "create", [{}])
    assert proxy.calls == 1  # no retry attempted


def test_odoo_client_never_retries_an_application_fault_even_on_a_read_method():
    """xmlrpc.client.Fault means Odoo processed the request and rejected it (bad data,
    permission denied, ...) - retrying cannot help and must not be attempted."""
    import xmlrpc.client

    proxy = _FlakyProxy(fail_times=5, error=xmlrpc.client.Fault(1, "access denied"))
    client = OdooClient("https://x", "db", "u", "k", common=proxy, models=proxy, max_attempts=5, backoff_seconds=0.0)

    with pytest.raises(OdooError):
        client.execute_kw("product.product", "search_read", [[]])
    assert proxy.calls == 1  # no retry on an application-level fault


# -- write side: draft purchase orders (RFQs) ---------------------------------


def test_primary_supplier_by_sku_maps_to_partner_ids():
    mapping = _connector().primary_supplier_by_sku(["SKU-1", "SKU-2"])

    assert mapping == {"SKU-1": 70, "SKU-2": 71}


def test_create_draft_purchase_orders_groups_by_supplier():
    odoo = _odoo()
    connector = OdooConnector(odoo)

    result = connector.create_draft_purchase_orders({"SKU-1": 50.0, "SKU-2": 30.0}, prices={"SKU-1": 12.0})

    assert result.n_orders == 2 and result.unsourced == ()  # one PO per distinct supplier
    pos = odoo.records("purchase.order")
    assert {po["partner_id"] for po in pos.values()} == {70, 71}
    # the SKU-1 PO carries one Odoo one2many line command (0, 0, {vals}) with the right fields
    sku1_po = next(po for po in pos.values() if po["partner_id"] == 70)
    cmd = sku1_po["order_line"][0]
    assert cmd[0] == 0 and cmd[2]["product_id"] == 1
    assert cmd[2]["product_qty"] == 50.0 and cmd[2]["price_unit"] == 12.0 and cmd[2]["name"] == "SKU-1"


def test_draft_po_groups_multiple_skus_under_one_supplier():
    odoo = InMemoryOdoo({
        "product.product": {
            1: {"default_code": "SKU-1", "name": "A", "list_price": 10.0, "standard_price": 6.0},
            2: {"default_code": "SKU-2", "name": "B", "list_price": 20.0, "standard_price": 12.0},
        },
        "product.supplierinfo": {
            400: {"product_id": [1, "A"], "partner_id": [70, "OneVendor"], "sequence": 1},
            401: {"product_id": [2, "B"], "partner_id": [70, "OneVendor"], "sequence": 1},
        },
        "purchase.order": {},
    })

    result = OdooConnector(odoo).create_draft_purchase_orders({"SKU-1": 5.0, "SKU-2": 7.0})

    assert result.n_orders == 1  # both SKUs share a supplier -> a single PO
    po = next(iter(odoo.records("purchase.order").values()))
    assert po["partner_id"] == 70 and len(po["order_line"]) == 2


def test_draft_po_reports_unsourced_skus_instead_of_dropping_them():
    odoo = InMemoryOdoo({
        "product.product": {
            1: {"default_code": "SKU-1", "name": "A", "list_price": 10.0, "standard_price": 6.0},
            2: {"default_code": "SKU-2", "name": "B", "list_price": 20.0, "standard_price": 12.0},
        },
        "product.supplierinfo": {400: {"product_id": [1, "A"], "partner_id": [70, "V"], "sequence": 1}},
        "purchase.order": {},
    })

    result = OdooConnector(odoo).create_draft_purchase_orders({"SKU-1": 5.0, "SKU-2": 7.0})

    assert result.n_orders == 1 and result.unsourced == ("SKU-2",)
    po = next(iter(odoo.records("purchase.order").values()))
    assert po["partner_id"] == 70 and len(po["order_line"]) == 1


# -- write side: draft POs now route through the writeback safety plane ------


def test_draft_po_creation_is_idempotent_on_content_not_just_a_caller_key():
    """Repro of the audit finding: draft-PO creation used to bypass the writeback
    plane entirely, so a retry raised a duplicate RFQ. Retrying the SAME restock
    (same content) must now idempotent-skip instead of creating a second PO."""
    odoo = _odoo()
    connector = OdooConnector(odoo)

    first = connector.create_draft_purchase_orders({"SKU-1": 50.0})
    second = connector.create_draft_purchase_orders({"SKU-1": 50.0})  # a client retry

    assert first.n_orders == 1
    assert second.purchase_orders == first.purchase_orders  # same PO, not a new one
    assert len(odoo.records("purchase.order")) == 1  # exactly one PO exists in Odoo


def test_draft_po_with_different_content_creates_a_different_po():
    odoo = _odoo()
    connector = OdooConnector(odoo)

    first = connector.create_draft_purchase_orders({"SKU-1": 50.0})
    second = connector.create_draft_purchase_orders({"SKU-1": 999.0})  # different quantity

    assert set(first.purchase_orders) != set(second.purchase_orders)
    assert len(odoo.records("purchase.order")) == 2


def test_draft_po_is_recorded_in_the_writeback_audit_and_can_be_rolled_back():
    odoo = _odoo()
    connector = OdooConnector(odoo)

    result = connector.create_draft_purchase_orders({"SKU-1": 50.0})
    po_id = next(iter(result.purchase_orders))

    connector.rollback(next(iter(connector._po_store.applied_keys())))

    assert po_id not in odoo.records("purchase.order")  # the draft PO was deleted


def test_stage_draft_purchase_orders_is_a_dry_run_until_applied():
    odoo = _odoo()
    connector = OdooConnector(odoo)

    changeset, unsourced = connector.stage_draft_purchase_orders({"SKU-1": 50.0})

    assert unsourced == ()
    assert not odoo.records("purchase.order")  # nothing written yet
    connector.apply_draft_purchase_orders(changeset)
    assert len(odoo.records("purchase.order")) == 1


def test_apply_draft_purchase_orders_without_approval_can_be_required():
    """auto_apply_reversible=False makes draft-PO creation require a human approval,
    exactly like reorder-point writes already do."""
    from src.writeback import WritebackRefused

    odoo = _odoo()
    connector = OdooConnector(odoo)
    changeset, _ = connector.stage_draft_purchase_orders({"SKU-1": 50.0})

    with pytest.raises(WritebackRefused):
        connector.apply_draft_purchase_orders(changeset, auto_apply_reversible=False)
    assert not odoo.records("purchase.order")


# -- write side: persistent ledger shared by both write paths -----------------


def test_connector_with_a_sqlite_ledger_persists_both_write_paths(tmp_path):
    from src.writeback_store import SqliteAuditLedger

    ledger_path = tmp_path / "odoo_writeback.sqlite3"
    odoo = _odoo()
    connector = OdooConnector(odoo, ledger=SqliteAuditLedger(ledger_path))

    connector.apply_restock(connector.stage_restock({"SKU-2": 60.0}, idempotency_key="r1"))
    connector.create_draft_purchase_orders({"SKU-1": 50.0})

    # A brand-new connector + ledger pointed at the same file "sees" both prior applies.
    reopened = OdooConnector(odoo, ledger=SqliteAuditLedger(ledger_path))
    retried_restock = reopened.apply_restock(reopened.stage_restock({"SKU-2": 60.0}, idempotency_key="r1"))
    assert retried_restock.idempotent_skip

    reopened.create_draft_purchase_orders({"SKU-1": 50.0})
    assert len(odoo.records("purchase.order")) == 1  # not duplicated after the "restart"

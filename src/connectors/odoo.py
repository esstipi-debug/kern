"""Odoo ERP connector — read a live Odoo backend, write back safely (Gap #5).

Implements the ``InventorySource`` read side (products, inventory levels, sales orders)
straight from Odoo's standard models, plus a write side that routes restock decisions
through the safe-staging ``src.writeback`` plane (dry-run -> approval -> idempotent apply
-> audit/rollback) as Odoo *reorder rules* -- Linchpin never mutates the system of record
blindly.

Two layers, mirroring ``http_client.StoreApiClient`` (which takes any httpx-style object):

- ``OdooClient`` is the real transport: ``xmlrpc.client`` (Python stdlib, no new deps)
  against ``/xmlrpc/2/common`` (authenticate) and ``/xmlrpc/2/object`` (``execute_kw``).
- ``OdooConnector`` holds all the model<->DTO mapping and speaks only an ``execute_kw``
  duck type, so the whole connector runs and is tested offline against ``InMemoryOdoo`` --
  a real Odoo instance (URL + db + API key) is needed only at deploy time.

Field mapping (Odoo standard models -> canonical connector DTOs):

===================== =========================================================
Odoo model            mapped to
===================== =========================================================
product.product       default_code -> sku, name, list_price -> price,
                      standard_price -> cost
stock.quant           internal-location quantity summed per product -> level
sale.order(.line)     date_order + product_uom_qty + price_unit -> Order/line
product.supplierinfo  delay (days) -> lead time (feeds canonical lead_time_days)
stock.warehouse.      product_min_qty (reorder point) <- restock target write
  orderpoint          (reversible)
===================== =========================================================
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import pandas as pd

from src import writeback
from src.connectors import InventoryLevel, Order, OrderLine, Product

# Odoo standard model names.
_M_PRODUCT = "product.product"
_M_LOCATION = "stock.location"
_M_QUANT = "stock.quant"
_M_SALE = "sale.order"
_M_SALE_LINE = "sale.order.line"
_M_SUPPLIERINFO = "product.supplierinfo"
_M_ORDERPOINT = "stock.warehouse.orderpoint"

_SALE_STATES = ("sale", "done")  # confirmed/locked sales count as realized demand
_WB_TARGET = "odoo"


class OdooError(RuntimeError):
    """Odoo transport, authentication, or mapping failure."""


@runtime_checkable
class OdooRPC(Protocol):
    """The single method the connector needs: Odoo's ``execute_kw`` dispatch."""

    def execute_kw(self, model: str, method: str, args: list, kwargs: dict | None = None) -> Any: ...


# -- real transport -----------------------------------------------------------


class OdooClient:
    """Real XML-RPC transport to an Odoo server (stdlib ``xmlrpc.client``).

    Pass ``common``/``models`` proxies to inject a transport in tests; otherwise they are
    built lazily from ``url`` so importing this module never needs the network.
    """

    def __init__(
        self,
        url: str,
        db: str,
        username: str,
        api_key: str,
        *,
        common: Any = None,
        models: Any = None,
    ) -> None:
        if common is None or models is None:
            import xmlrpc.client  # stdlib; lazy so offline use needs no network

            base = url.rstrip("/")
            common = common or xmlrpc.client.ServerProxy(f"{base}/xmlrpc/2/common")
            models = models or xmlrpc.client.ServerProxy(f"{base}/xmlrpc/2/object")
        uid = common.authenticate(db, username, api_key, {})
        if not uid:
            raise OdooError(f"Odoo authentication failed for user {username!r} on db {db!r}")
        self._db = db
        self._uid = int(uid)
        self._api_key = api_key
        self._models = models

    @property
    def uid(self) -> int:
        return self._uid

    def execute_kw(self, model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
        return self._models.execute_kw(self._db, self._uid, self._api_key, model, method, args, kwargs or {})


# -- connector ----------------------------------------------------------------


def _as_date(value: Any) -> str:
    """Odoo datetimes arrive as 'YYYY-MM-DD HH:MM:SS'; keep the ISO date so it sorts."""
    return str(value)[:10] if value else ""


def _m2o_id(value: Any) -> int | None:
    """Odoo many2one fields read back as ``[id, display_name]`` (or ``False`` when unset)."""
    if isinstance(value, (list, tuple)) and value:
        return int(value[0])
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


class OdooConnector:
    """``InventorySource`` over Odoo, with safe-staging restock as reorder-point writes."""

    def __init__(self, rpc: OdooRPC) -> None:
        self._rpc = rpc
        self._sku_by_id: dict[int, str] = {}
        self._id_by_sku: dict[str, int] = {}
        self._rules = _ReorderRuleStore(rpc, self._id_by_sku)

    # -- read side (InventorySource) ------------------------------------------

    def list_products(self) -> list[Product]:
        rows = self._rpc.execute_kw(
            _M_PRODUCT,
            "search_read",
            [[["default_code", "!=", False]]],
            {"fields": ["default_code", "name", "list_price", "standard_price"]},
        )
        products: list[Product] = []
        for r in rows:
            sku = str(r["default_code"])
            self._sku_by_id[r["id"]] = sku
            self._id_by_sku[sku] = r["id"]
            products.append(
                Product(
                    sku,
                    str(r.get("name") or sku),
                    float(r.get("list_price") or 0.0),
                    float(r.get("standard_price") or 0.0),
                )
            )
        return products

    def inventory_levels(self) -> list[InventoryLevel]:
        internal = self._rpc.execute_kw(_M_LOCATION, "search", [[["usage", "=", "internal"]]])
        domain = [["location_id", "in", internal]] if internal else []
        rows = self._rpc.execute_kw(
            _M_QUANT, "search_read", [domain], {"fields": ["product_id", "quantity"]}
        )
        totals: dict[int, float] = {}
        for r in rows:
            pid = _m2o_id(r.get("product_id"))
            if pid is not None:
                totals[pid] = totals.get(pid, 0.0) + float(r.get("quantity") or 0.0)
        levels: list[InventoryLevel] = []
        for pid, qty in totals.items():
            sku = self._sku(pid)
            if sku is not None:
                levels.append(InventoryLevel(sku, qty))
        return levels

    def orders(self, *, since: str | None = None) -> list[Order]:
        domain: list = [["state", "in", list(_SALE_STATES)]]
        if since is not None:
            domain.append(["date_order", ">=", since])
        heads = self._rpc.execute_kw(
            _M_SALE, "search_read", [domain], {"fields": ["name", "date_order", "order_line"]}
        )
        line_ids = [lid for h in heads for lid in h.get("order_line", [])]
        lines_by_id = self._read_sale_lines(line_ids)
        out: list[Order] = []
        for h in heads:
            lines = tuple(lines_by_id[lid] for lid in h.get("order_line", []) if lid in lines_by_id)
            out.append(Order(str(h["name"]), _as_date(h["date_order"]), lines))
        return sorted(out, key=lambda o: o.created_at)

    # -- demand + lead-time bridges into the existing engines ------------------

    def demand_frame(self) -> pd.DataFrame:
        """Sales lines as a ``(date, product_id, quantity, unit_cost)`` demand history.

        Same shape ``src.sources.DataFrameDemandSource`` consumes, so an Odoo backend
        drops straight into the forecasting / inventory engines.
        """
        costs = {p.sku: p.cost for p in self.list_products()}
        rows = [
            {"date": o.created_at, "product_id": ln.sku, "quantity": ln.quantity, "unit_cost": costs.get(ln.sku, 0.0)}
            for o in self.orders()
            for ln in o.lines
        ]
        frame = pd.DataFrame(rows, columns=["date", "product_id", "quantity", "unit_cost"])
        if frame.empty:
            return frame
        return frame.groupby(["date", "product_id"], as_index=False).agg(
            quantity=("quantity", "sum"), unit_cost=("unit_cost", "first")
        )

    def lead_times(self) -> dict[str, float]:
        """Per-SKU purchasing lead time (days) from ``product.supplierinfo.delay``.

        Feeds the canonical ``lead_time_days`` the inventory engines and the risk-period
        differentiation already consume. Only variant-scoped supplier lines are mapped.
        """
        rows = self._rpc.execute_kw(
            _M_SUPPLIERINFO, "search_read", [[]], {"fields": ["product_id", "delay"]}
        )
        out: dict[str, float] = {}
        for r in rows:
            pid = _m2o_id(r.get("product_id"))
            if pid is None:
                continue
            sku = self._sku(pid)
            if sku is not None and sku not in out:
                out[sku] = float(r.get("delay") or 0.0)
        return out

    # -- write side (safe-staging restock -> reorder point) -------------------

    def stage_restock(self, restock: dict[str, float], *, idempotency_key: str, reason: str = "") -> writeback.Changeset:
        """Stage a dry-run reorder-point update (min qty = on-hand + restock). Does NOT write.

        The restock delta from the engines is interpreted as the *target* cover level, so
        Odoo's own replenishment generates the POs. Reversible: applying only edits a field.
        """
        self._ensure_catalog()
        on_hand = {lvl.sku: lvl.available for lvl in self.inventory_levels()}
        edits = {
            sku: {"product_min_qty": round(on_hand.get(sku, 0.0) + float(qty), 4)}
            for sku, qty in restock.items()
        }
        return writeback.stage(
            self._rules,
            _WB_TARGET,
            edits,
            risk_tier=writeback.TIER_REVERSIBLE,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    def apply_restock(
        self,
        changeset: writeback.Changeset,
        *,
        approval: writeback.Approval | None = None,
        now: float = 0.0,
        auto_apply_reversible: bool = True,
    ) -> writeback.ApplyResult:
        """Apply a staged reorder-point change. Reversible edits auto-apply by default; pass an
        ``approval`` (and ``auto_apply_reversible=False``) to require a human in the loop."""
        return writeback.apply(
            self._rules, changeset, approval=approval, now=now, auto_apply_reversible=auto_apply_reversible
        )

    def rollback(self, idempotency_key: str) -> None:
        """Undo an applied reorder-point change, restoring the prior min quantity."""
        self._rules.rollback(idempotency_key)

    # -- internals ------------------------------------------------------------

    def _ensure_catalog(self) -> None:
        if not self._id_by_sku:
            self.list_products()

    def _sku(self, product_id: int) -> str | None:
        if product_id not in self._sku_by_id:
            rows = self._rpc.execute_kw(_M_PRODUCT, "read", [[product_id]], {"fields": ["default_code"]})
            code = rows[0]["default_code"] if rows else None
            if not code:
                return None
            self._sku_by_id[product_id] = str(code)
            self._id_by_sku[str(code)] = product_id
        return self._sku_by_id[product_id]

    def _read_sale_lines(self, line_ids: list[int]) -> dict[int, OrderLine]:
        if not line_ids:
            return {}
        rows = self._rpc.execute_kw(
            _M_SALE_LINE, "read", [line_ids], {"fields": ["product_id", "product_uom_qty", "price_unit"]}
        )
        out: dict[int, OrderLine] = {}
        for r in rows:
            pid = _m2o_id(r.get("product_id"))
            if pid is None:
                continue
            sku = self._sku(pid)
            if sku is not None:
                out[r["id"]] = OrderLine(sku, float(r.get("product_uom_qty") or 0.0), float(r.get("price_unit") or 0.0))
        return out


_ABSENT = object()  # sentinel: the reorder rule's field did not exist before the change


class _ReorderRuleStore:
    """writeback system-of-record surface (read/applied_keys/commit/rollback) over Odoo
    reorder rules (``stock.warehouse.orderpoint``). Lets the connector reuse the entire
    safe-staging policy (tiers, approval, idempotency, audit) unchanged, against Odoo."""

    def __init__(self, rpc: OdooRPC, id_by_sku: dict[str, int]) -> None:
        self._rpc = rpc
        self._id_by_sku = id_by_sku  # shared with the connector; filled by list_products()
        self._applied: dict[str, writeback.AuditEntry] = {}

    def read(self, entity_id: str) -> dict:
        pid = self._id_by_sku.get(entity_id)
        if pid is None:
            return {}
        rows = self._rpc.execute_kw(
            _M_ORDERPOINT,
            "search_read",
            [[["product_id", "=", pid]]],
            {"fields": ["product_min_qty", "product_max_qty"], "limit": 1},
        )
        if not rows:
            return {}
        return {
            "product_min_qty": float(rows[0].get("product_min_qty") or 0.0),
            "product_max_qty": float(rows[0].get("product_max_qty") or 0.0),
        }

    def applied_keys(self) -> set[str]:
        return set(self._applied)

    def commit(self, changeset: writeback.Changeset, approved_by: str) -> writeback.AuditEntry:
        restore: list[tuple[str, str, object]] = []
        for c in changeset.changes:
            current = self.read(c.entity_id)
            restore.append((c.entity_id, c.field, current.get(c.field, _ABSENT)))
            self._write_field(c.entity_id, c.field, c.after)
        entry = writeback.AuditEntry(changeset.idempotency_key, changeset.target, approved_by, tuple(restore))
        self._applied[changeset.idempotency_key] = entry
        return entry

    def rollback(self, idempotency_key: str) -> None:
        entry = self._applied.get(idempotency_key)
        if entry is None:
            raise KeyError(idempotency_key)
        for entity_id, fld, original in entry.restore:
            if original is not _ABSENT:  # a freshly-created rule is left in place (still reversible to edit)
                self._write_field(entity_id, fld, original)
        del self._applied[idempotency_key]

    def _write_field(self, sku: str, field: str, value: object) -> None:
        pid = self._id_by_sku.get(sku)
        if pid is None:
            raise OdooError(f"unknown SKU {sku!r}; call list_products() before staging a restock")
        existing = self._rpc.execute_kw(_M_ORDERPOINT, "search", [[["product_id", "=", pid]]], {"limit": 1})
        if existing:
            self._rpc.execute_kw(_M_ORDERPOINT, "write", [existing, {field: value}])
        else:
            self._rpc.execute_kw(_M_ORDERPOINT, "create", [{"product_id": pid, field: value}])


# -- offline stand-in (the Odoo analogue of SimulatedStore / emulator) --------


class InMemoryOdoo:
    """Offline stand-in for an Odoo server: the slice of ``execute_kw`` the connector uses.

    Holds records per model as ``{id: {field: value}}`` and supports the handful of methods
    the connector calls (search / search_read / read / write / create) over simple domain
    leaves (``=``, ``!=``, ``in``, ``>=``). Lets the whole Odoo connector run and be tested
    end-to-end with no network or API key. Domains use flat fields only (the connector is
    written to avoid dotted relational leaves so this stand-in stays small).
    """

    def __init__(self, data: dict[str, dict[int, dict]] | None = None) -> None:
        self._data = {m: {i: dict(r) for i, r in recs.items()} for m, recs in (data or {}).items()}
        self._next = max([i for recs in self._data.values() for i in recs] + [0]) + 1

    def execute_kw(self, model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
        handler = getattr(self, f"_op_{method}", None)
        if handler is None:
            raise OdooError(f"InMemoryOdoo does not implement execute_kw method {method!r}")
        return handler(model, list(args), dict(kwargs or {}))

    def records(self, model: str) -> dict[int, dict]:
        """Direct access to a model's records (read-only inspection in tests)."""
        return self._data.setdefault(model, {})

    def _match(self, rec: dict, domain: list) -> bool:
        for field, op, val in domain:
            cur = rec.get(field)
            if field.endswith("_id") and isinstance(cur, (list, tuple)) and cur:
                cur = cur[0]  # many2one stored as [id, name]
            if op == "=" and cur != val:
                return False
            if op == "!=" and cur == val:
                return False
            if op == "in" and cur not in val:
                return False
            if op == ">=" and not (cur is not None and cur >= val):
                return False
        return True

    def _project(self, rec_id: int, rec: dict, fields: list | None) -> dict:
        keys = fields if fields else list(rec.keys())
        row: dict = {"id": rec_id}
        for k in keys:
            row[k] = rec.get(k, False)
        return row

    def _op_search(self, model: str, args: list, kwargs: dict) -> list[int]:
        domain = args[0] if args else []
        ids = [i for i, r in self.records(model).items() if self._match(r, domain)]
        limit = kwargs.get("limit")
        return ids[:limit] if limit else ids

    def _op_search_read(self, model: str, args: list, kwargs: dict) -> list[dict]:
        domain = args[0] if args else []
        fields = kwargs.get("fields")
        rows = [self._project(i, r, fields) for i, r in self.records(model).items() if self._match(r, domain)]
        limit = kwargs.get("limit")
        return rows[:limit] if limit else rows

    def _op_read(self, model: str, args: list, kwargs: dict) -> list[dict]:
        ids = args[0] if args else []
        fields = kwargs.get("fields")
        recs = self.records(model)
        return [self._project(i, recs[i], fields) for i in ids if i in recs]

    def _op_write(self, model: str, args: list, kwargs: dict) -> bool:
        ids, vals = args[0], args[1]
        recs = self.records(model)
        for i in ids:
            if i in recs:
                recs[i].update(vals)
        return True

    def _op_create(self, model: str, args: list, kwargs: dict) -> int:
        vals = args[0]
        new_id = self._next
        self._next += 1
        self.records(model)[new_id] = dict(vals)
        return new_id


def demo_odoo() -> InMemoryOdoo:
    """A small, deterministic in-memory Odoo for demos and tests (no randomness)."""
    return InMemoryOdoo(
        {
            _M_PRODUCT: {
                1: {"default_code": "SKU-1", "name": "Widget", "list_price": 20.0, "standard_price": 12.0},
                2: {"default_code": "SKU-2", "name": "Gadget", "list_price": 50.0, "standard_price": 30.0},
                3: {"default_code": "SKU-3", "name": "Gizmo", "list_price": 8.0, "standard_price": 5.0},
            },
            _M_LOCATION: {10: {"usage": "internal"}, 11: {"usage": "customer"}},
            _M_QUANT: {
                100: {"product_id": [1, "Widget"], "location_id": [10, "WH/Stock"], "quantity": 12.0},
                101: {"product_id": [2, "Gadget"], "location_id": [10, "WH/Stock"], "quantity": 300.0},
                102: {"product_id": [3, "Gizmo"], "location_id": [10, "WH/Stock"], "quantity": 20.0},
            },
            _M_SALE: {
                200: {"name": "S0001", "date_order": "2026-01-05 10:00:00", "state": "sale", "order_line": [300, 301]},
                201: {"name": "S0002", "date_order": "2026-01-12 10:00:00", "state": "sale", "order_line": [302]},
                202: {"name": "S0003", "date_order": "2026-01-19 10:00:00", "state": "done", "order_line": [303, 304]},
            },
            _M_SALE_LINE: {
                300: {"product_id": [1, "Widget"], "product_uom_qty": 10.0, "price_unit": 20.0},
                301: {"product_id": [3, "Gizmo"], "product_uom_qty": 5.0, "price_unit": 8.0},
                302: {"product_id": [1, "Widget"], "product_uom_qty": 10.0, "price_unit": 20.0},
                303: {"product_id": [1, "Widget"], "product_uom_qty": 10.0, "price_unit": 20.0},
                304: {"product_id": [3, "Gizmo"], "product_uom_qty": 5.0, "price_unit": 8.0},
            },
            _M_SUPPLIERINFO: {
                400: {"product_id": [1, "Widget"], "delay": 7.0},
                401: {"product_id": [2, "Gadget"], "delay": 14.0},
                402: {"product_id": [3, "Gizmo"], "delay": 21.0},
            },
            _M_ORDERPOINT: {},
        }
    )

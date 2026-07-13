"""Odoo ERP connector — read a live Odoo backend, write back safely (Gap #5).

Implements the ``InventorySource`` read side (products, inventory levels, sales orders)
straight from Odoo's standard models, plus a write side that routes restock decisions
through the safe-staging ``src.writeback`` plane (dry-run -> approval -> idempotent apply
-> audit/rollback) as Odoo *reorder rules* -- Kern never mutates the system of record
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

import time
import xmlrpc.client
from dataclasses import dataclass
from dataclasses import replace as _dc_replace
from typing import Any, Protocol, runtime_checkable

import pandas as pd

from src import writeback
from src.connectors import InventoryLevel, Order, OrderLine, Product

# Odoo ORM methods that only read: safe to retry on a transient transport error,
# since retrying can never duplicate a write. `create`/`write`/`unlink` are never
# retried automatically here - a lost response after the request reached the
# server would otherwise risk a duplicate PO/record on retry.
_READ_ONLY_METHODS = frozenset({"search", "search_read", "read", "search_count", "fields_get"})
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_BACKOFF_SECONDS = 0.5

# Odoo standard model names.
_M_PRODUCT = "product.product"
_M_LOCATION = "stock.location"
_M_QUANT = "stock.quant"
_M_SALE = "sale.order"
_M_SALE_LINE = "sale.order.line"
_M_SUPPLIERINFO = "product.supplierinfo"
_M_ORDERPOINT = "stock.warehouse.orderpoint"
_M_PO = "purchase.order"

_SALE_STATES = ("sale", "done")  # confirmed/locked sales count as realized demand
_WB_TARGET = "odoo"


class OdooError(RuntimeError):
    """Odoo transport, authentication, or mapping failure."""


@runtime_checkable
class OdooRPC(Protocol):
    """The single method the connector needs: Odoo's ``execute_kw`` dispatch."""

    def execute_kw(self, model: str, method: str, args: list, kwargs: dict | None = None) -> Any: ...


# -- real transport -----------------------------------------------------------


class _TimeoutTransport(xmlrpc.client.Transport):
    """xmlrpc Transport that bounds the socket timeout on every connection."""

    def __init__(self, timeout: float, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._timeout = timeout

    def make_connection(self, host: Any) -> Any:
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class _TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    """HTTPS variant of ``_TimeoutTransport`` (Odoo Online is always https)."""

    def __init__(self, timeout: float, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._timeout = timeout

    def make_connection(self, host: Any) -> Any:
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class OdooClient:
    """Real XML-RPC transport to an Odoo server (stdlib ``xmlrpc.client``).

    Pass ``common``/``models`` proxies to inject a transport in tests; otherwise they are
    built from ``url`` with a bounded socket ``timeout`` (a hung Odoo instance must never
    hang a job indefinitely).

    Read-only ORM methods (``search`` / ``search_read`` / ``read`` / ...) are retried up
    to ``max_attempts`` times with exponential backoff on a transient transport error
    (a dropped connection, DNS hiccup, timeout). Writes (``create`` / ``write`` /
    ``unlink``) are never auto-retried: if the response is lost after the request
    reached the server, blindly retrying a ``create`` could duplicate a record (e.g. a
    purchase order) - that ambiguity has to surface as an error, not a silent retry.
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
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = _DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        if common is None or models is None:
            base = url.rstrip("/")
            transport_cls = _TimeoutSafeTransport if base.startswith("https") else _TimeoutTransport
            common = common or xmlrpc.client.ServerProxy(f"{base}/xmlrpc/2/common", transport=transport_cls(timeout))
            models = models or xmlrpc.client.ServerProxy(f"{base}/xmlrpc/2/object", transport=transport_cls(timeout))
        uid = common.authenticate(db, username, api_key, {})
        if not uid:
            raise OdooError(f"Odoo authentication failed for user {username!r} on db {db!r}")
        self._db = db
        self._uid = int(uid)
        self._api_key = api_key
        self._models = models
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_seconds = backoff_seconds

    @property
    def uid(self) -> int:
        return self._uid

    def execute_kw(self, model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
        attempts = self._max_attempts if method in _READ_ONLY_METHODS else 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return self._models.execute_kw(self._db, self._uid, self._api_key, model, method, args, kwargs or {})
            except xmlrpc.client.Fault as exc:
                # An application-level error response from Odoo (bad data, permission
                # denied, ...): the request was processed. Retrying will not help.
                raise OdooError(f"Odoo rejected {model}.{method}: {exc.faultString}") from exc
            except (OSError, TimeoutError) as exc:  # transport-level: connection never confirmed
                last_exc = exc
                if attempt + 1 >= attempts:
                    break
                time.sleep(self._backoff_seconds * (2**attempt))
        raise OdooError(
            f"Odoo transport failed for {model}.{method} after {attempts} attempt(s): {last_exc}"
        ) from last_exc


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


@dataclass(frozen=True)
class DraftPOResult:
    """Outcome of staging draft purchase orders: what was created and what couldn't be sourced."""

    purchase_orders: dict[int, tuple[str, ...]]   # new PO id -> the SKUs on it
    unsourced: tuple[str, ...]                     # SKUs with no supplier in Odoo (skipped, not dropped)

    @property
    def n_orders(self) -> int:
        return len(self.purchase_orders)


class OdooConnector:
    """``InventorySource`` over Odoo, with safe-staging restock as reorder-point writes.

    ``ledger``, when given (a ``src.writeback_store.SqliteAuditLedger``), persists the
    audit/idempotency bookkeeping for BOTH write paths (reorder points and draft POs)
    so a process restart cannot re-apply an idempotency key or lose rollback data.
    Without one, bookkeeping lives in process memory - unchanged from before.
    """

    def __init__(self, rpc: OdooRPC, *, ledger: object | None = None) -> None:
        self._rpc = rpc
        self._sku_by_id: dict[int, str] = {}
        self._id_by_sku: dict[str, int] = {}
        self._rules = _ReorderRuleStore(rpc, self._id_by_sku, ledger=ledger)
        self._po_store = _DraftPoStore(rpc, self._id_by_sku, ledger=ledger)

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
        now: float | None = None,
        auto_apply_reversible: bool = True,
    ) -> writeback.ApplyResult:
        """Apply a staged reorder-point change. Reversible edits auto-apply by default; pass an
        ``approval`` (and ``auto_apply_reversible=False``) to require a human in the loop.
        ``now`` defaults to the real clock; pass an explicit value only in tests."""
        return writeback.apply(
            self._rules, changeset, approval=approval, now=now, auto_apply_reversible=auto_apply_reversible
        )

    def rollback(self, idempotency_key: str) -> None:
        """Undo an applied reorder-point OR draft-PO change (whichever holds the key)."""
        if idempotency_key in self._rules.applied_keys():
            self._rules.rollback(idempotency_key)
        elif idempotency_key in self._po_store.applied_keys():
            self._po_store.rollback(idempotency_key)
        else:
            raise KeyError(idempotency_key)

    # -- write side (draft purchase orders) -----------------------------------

    def primary_supplier_by_sku(self, skus: list[str]) -> dict[str, int]:
        """Map each SKU to its primary supplier's partner id (lowest-sequence product.supplierinfo)."""
        self._ensure_catalog()
        out: dict[str, int] = {}
        for sku in skus:
            pid = self._id_by_sku.get(sku)
            if pid is None:
                continue
            rows = self._rpc.execute_kw(
                _M_SUPPLIERINFO, "search_read", [[["product_id", "=", pid]]],
                {"fields": ["partner_id", "sequence"]},
            )
            if not rows:
                continue
            best = min(rows, key=lambda r: r.get("sequence") or 0)
            partner = _m2o_id(best.get("partner_id"))
            if partner is not None:
                out[sku] = partner
        return out

    def stage_draft_purchase_orders(
        self, restock: dict[str, float], *, prices: dict[str, float] | None = None, reason: str = ""
    ) -> tuple[writeback.Changeset, tuple[str, ...]]:
        """Stage (dry-run) draft POs for the restock, grouped by each SKU's primary supplier.
        Does NOT write. Returns the changeset plus SKUs with no supplier (unsourced, not dropped).

        The idempotency key is derived from the changeset's own content hash, so staging
        the SAME restock+prices twice (e.g. a client retry) always resolves to the same
        key - a retry can idempotent-skip instead of raising a duplicate RFQ.
        """
        self._ensure_catalog()
        prices = prices or {}
        suppliers = self.primary_supplier_by_sku(list(restock))
        by_supplier: dict[int, list[str]] = {}
        unsourced: list[str] = []
        for sku in restock:
            partner = suppliers.get(sku)
            if partner is None:
                unsourced.append(sku)
            else:
                by_supplier.setdefault(partner, []).append(sku)

        changes = tuple(
            writeback.Change(
                f"supplier:{partner}",
                "draft_po_lines",
                None,  # nothing existed before (a create, not an edit) - matches stage()'s convention
                tuple(sorted((sku, float(restock[sku]), float(prices.get(sku, 0.0))) for sku in skus)),
            )
            for partner, skus in sorted(by_supplier.items())
        )
        cs = writeback.Changeset(_WB_TARGET, changes, writeback.TIER_REVERSIBLE, idempotency_key="pending", reason=reason)
        cs = _dc_replace(cs, idempotency_key=cs.content_hash[:32])
        return cs, tuple(unsourced)

    def apply_draft_purchase_orders(
        self,
        changeset: writeback.Changeset,
        *,
        approval: writeback.Approval | None = None,
        now: float | None = None,
        auto_apply_reversible: bool = True,
    ) -> writeback.ApplyResult:
        """Apply staged draft POs. Reversible (a draft PO can be deleted) so this auto-applies
        by default; pass an ``approval`` (and ``auto_apply_reversible=False``) to require a
        human in the loop. ``now`` defaults to the real clock; pass an explicit value only in tests."""
        return writeback.apply(
            self._po_store, changeset, approval=approval, now=now, auto_apply_reversible=auto_apply_reversible
        )

    def create_draft_purchase_orders(
        self, restock: dict[str, float], *, prices: dict[str, float] | None = None
    ) -> DraftPOResult:
        """Create DRAFT (RFQ) purchase orders for the restock, grouped by each SKU's primary supplier.

        Convenience one-shot: stage + auto-apply through the safe-staging plane (idempotent
        on content, audited, rollback-able via ``rollback()``). Each PO is left in Odoo's
        'draft' state - the unconfirmed draft IS the safety boundary; a buyer reviews and
        confirms it in Odoo. SKUs with no supplier are skipped and reported in ``unsourced``
        rather than silently dropped. For explicit approval-gated control, or to inspect the
        changeset before it is applied, use ``stage_draft_purchase_orders`` +
        ``apply_draft_purchase_orders`` directly.
        """
        changeset, unsourced = self.stage_draft_purchase_orders(restock, prices=prices)
        if changeset.changes:
            self.apply_draft_purchase_orders(changeset)
        created_by_entity = self._po_store.created_pos(changeset.idempotency_key)
        purchase_orders: dict[int, tuple[str, ...]] = {}
        for c in changeset.changes:
            po_id = created_by_entity.get(c.entity_id)
            if po_id is not None:
                purchase_orders[po_id] = tuple(sku for sku, _qty, _price in c.after)
        return DraftPOResult(purchase_orders=purchase_orders, unsourced=unsourced)

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


class _ReorderRuleStore:
    """writeback system-of-record surface (read/applied_keys/claim/release/commit/
    rollback) over Odoo reorder rules (``stock.warehouse.orderpoint``). Lets the
    connector reuse the entire safe-staging policy (tiers, approval, idempotency,
    audit) unchanged, against Odoo."""

    def __init__(self, rpc: OdooRPC, id_by_sku: dict[str, int], *, ledger: object | None = None) -> None:
        self._rpc = rpc
        self._id_by_sku = id_by_sku  # shared with the connector; filled by list_products()
        self._audit = writeback.AuditBookkeeping(ledger)

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
        return self._audit.applied_keys()

    def claim(self, idempotency_key: str, *, now: float | None = None) -> bool:
        return self._audit.claim(idempotency_key, now=now)

    def release(self, idempotency_key: str) -> None:
        self._audit.release(idempotency_key)

    def commit(self, changeset: writeback.Changeset, approved_by: str) -> writeback.AuditEntry:
        restore: list[tuple[str, str, object]] = []
        try:
            for c in changeset.changes:
                current = self.read(c.entity_id)
                restore.append((c.entity_id, c.field, current.get(c.field, writeback.ABSENT)))
                self._write_field(c.entity_id, c.field, c.after)
        except Exception:
            # A write partway through raised: everything written so far in THIS call is
            # already live in Odoo but not yet audited. Undo it with the same restore
            # values `rollback()` would use, before letting the failure propagate - a
            # local compensating transaction, since Odoo has no cross-call transaction
            # to lean on here. If a compensating write itself raises (e.g. Odoo drops
            # mid-undo), that's a compounding failure outside what a local compensation
            # can recover from - it surfaces (chained via __context__, nothing swallowed)
            # but leaves no audit entry, since record() below is unreached either way;
            # that residual needs manual reconciliation, same as a bare `rollback()`
            # failing partway today.
            self._apply_restore(restore)
            raise
        entry = writeback.AuditEntry(changeset.idempotency_key, changeset.target, approved_by, tuple(restore))
        self._audit.record(entry)
        return entry

    def rollback(self, idempotency_key: str) -> None:
        entry = self._audit.get(idempotency_key)
        if entry is None:
            raise KeyError(idempotency_key)
        self._apply_restore(entry.restore)
        self._audit.forget(idempotency_key)

    def _apply_restore(self, restore: list[tuple[str, str, object]] | tuple[tuple[str, str, object], ...]) -> None:
        for entity_id, fld, original in restore:
            if original is not writeback.ABSENT:  # a freshly-created rule is left in place (still reversible)
                self._write_field(entity_id, fld, original)

    def _write_field(self, sku: str, field: str, value: object) -> None:
        pid = self._id_by_sku.get(sku)
        if pid is None:
            raise OdooError(f"unknown SKU {sku!r}; call list_products() before staging a restock")
        existing = self._rpc.execute_kw(_M_ORDERPOINT, "search", [[["product_id", "=", pid]]], {"limit": 1})
        if existing:
            self._rpc.execute_kw(_M_ORDERPOINT, "write", [existing, {field: value}])
        else:
            self._rpc.execute_kw(_M_ORDERPOINT, "create", [{"product_id": pid, field: value}])


class _DraftPoStore:
    """writeback system-of-record surface over Odoo draft purchase orders.

    Lets draft-PO creation reuse the same safe-staging policy (idempotency, audit,
    rollback) as reorder-point writes, instead of calling ``execute_kw(... "create")``
    directly: previously a retry after a lost response could raise a duplicate RFQ,
    with no record of what was created and no way to undo it.

    One ``Change`` per supplier group: ``entity_id`` is a synthetic
    ``"supplier:<partner_id>"`` key (there is no pre-existing entity to diff against -
    a PO is created, not edited), ``before`` is always ``writeback.ABSENT``, and
    ``after`` is the sorted tuple of ``(sku, qty, price)`` lines for that supplier.
    """

    def __init__(self, rpc: OdooRPC, id_by_sku: dict[str, int], *, ledger: object | None = None) -> None:
        self._rpc = rpc
        self._id_by_sku = id_by_sku
        self._audit = writeback.AuditBookkeeping(ledger)
        # idempotency_key -> {entity_id: created po_id}, for the caller to map lines
        # back to the PO id and for rollback to know what to unlink.
        self._created: dict[str, dict[str, int]] = {}

    def read(self, entity_id: str) -> dict:
        return {}  # nothing to diff against: a draft PO is created, never edited here

    def applied_keys(self) -> set[str]:
        return self._audit.applied_keys()

    def claim(self, idempotency_key: str, *, now: float | None = None) -> bool:
        return self._audit.claim(idempotency_key, now=now)

    def release(self, idempotency_key: str) -> None:
        self._audit.release(idempotency_key)

    def created_pos(self, idempotency_key: str) -> dict[str, int]:
        """``{entity_id: po_id}`` created under ``idempotency_key`` (empty if unknown/rolled back)."""
        return dict(self._created.get(idempotency_key, {}))

    def commit(self, changeset: writeback.Changeset, approved_by: str) -> writeback.AuditEntry:
        restore: list[tuple[str, str, object]] = []
        created: dict[str, int] = {}
        try:
            for c in changeset.changes:
                if c.is_noop:
                    continue
                partner = int(c.entity_id.split(":", 1)[1])
                order_lines = [
                    (0, 0, {"product_id": self._id_by_sku[sku], "product_qty": qty, "price_unit": price, "name": sku})
                    for sku, qty, price in c.after
                ]
                po_id = self._rpc.execute_kw(_M_PO, "create", [{"partner_id": partner, "order_line": order_lines}])
                created[c.entity_id] = int(po_id)
                restore.append((c.entity_id, c.field, writeback.ABSENT))  # nothing existed before
        except Exception:
            # A create partway through raised: POs created so far in THIS call are
            # already live in Odoo but not yet audited, and `self._created` (the only
            # record of their ids) is still a local variable that would be discarded on
            # re-raise. Delete them before propagating - a local compensating
            # transaction, since Odoo has no cross-call transaction to lean on here. If
            # an unlink itself raises (compounding failure), that surfaces too (chained
            # via __context__, nothing swallowed) but leaves no audit entry describing
            # which POs are still orphaned - that residual needs manual reconciliation,
            # same as a bare `rollback()` failing partway today.
            self._unlink_all(created.values())
            raise
        entry = writeback.AuditEntry(changeset.idempotency_key, changeset.target, approved_by, tuple(restore))
        self._audit.record(entry)
        self._created[changeset.idempotency_key] = created
        return entry

    def rollback(self, idempotency_key: str) -> None:
        entry = self._audit.get(idempotency_key)
        if entry is None:
            raise KeyError(idempotency_key)
        self._unlink_all(self._created.get(idempotency_key, {}).values())
        self._audit.forget(idempotency_key)
        self._created.pop(idempotency_key, None)

    def _unlink_all(self, po_ids) -> None:
        for po_id in po_ids:
            self._rpc.execute_kw(_M_PO, "unlink", [[po_id]])  # a draft PO can be deleted outright


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

    def _op_unlink(self, model: str, args: list, kwargs: dict) -> bool:
        ids = args[0] if args else []
        recs = self.records(model)
        for i in ids:
            recs.pop(i, None)
        return True


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
                400: {"product_id": [1, "Widget"], "partner_id": [70, "Acme Supply"], "sequence": 1, "delay": 7.0},
                401: {"product_id": [2, "Gadget"], "partner_id": [71, "Globex"], "sequence": 1, "delay": 14.0},
                402: {"product_id": [3, "Gizmo"], "partner_id": [70, "Acme Supply"], "sequence": 1, "delay": 21.0},
            },
            _M_ORDERPOINT: {},
        }
    )

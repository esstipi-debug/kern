"""Odoo price writeback connector (Linchpin 3.0 PR-18, plan section 7 P3
``repricing_multichannel``).

Sibling to ``src.connectors.odoo.OdooConnector`` -- that module's write side
is reorder-point/purchase-order restocking; this one is a focused
``product.product.list_price`` writer, kept as its own small store rather
than growing ``OdooConnector`` with an unrelated concern (repo file-org
convention: many small, cohesive files over one large one). It reuses
``OdooRPC``/``InMemoryOdoo``/``OdooError``/``demo_odoo`` from
``src.connectors.odoo`` verbatim -- no second Odoo transport or stand-in is
built here (DRY): a live Odoo needs no new extra (stdlib
``xmlrpc.client``, same as ``OdooClient``), and the offline stand-in is the
SAME ``InMemoryOdoo`` used everywhere else in the repo.

``OdooPriceStore`` holds the writeback ``store`` surface
(read/applied_keys/claim/release/commit/rollback) over ``product.product``,
addressed by SKU (``default_code``) -- the same
connector-independence pattern ``_ReorderRuleStore`` established: the
safety plane (risk tiers, signed Approval, idempotency, audit, rollback) is
100% reused from ``src.writeback``, unchanged.
"""

from __future__ import annotations

from src import writeback
from src.connectors.odoo import InMemoryOdoo, OdooError, OdooRPC, demo_odoo  # noqa: F401  (re-exported)

_M_PRODUCT = "product.product"
_WB_TARGET = "odoo"


class OdooPriceStore:
    """writeback system-of-record surface over Odoo ``product.product.list_price``.

    ``ledger``, when given (a ``src.writeback_store.SqliteAuditLedger``),
    persists the audit/idempotency bookkeeping across a process restart --
    matching every other store in this repo.
    """

    def __init__(self, rpc: OdooRPC, *, ledger: object | None = None) -> None:
        self._rpc = rpc
        self._audit = writeback.AuditBookkeeping(ledger)
        self._id_by_sku: dict[str, int] = {}

    def _resolve(self, sku: str) -> int | None:
        if sku in self._id_by_sku:
            return self._id_by_sku[sku]
        ids = self._rpc.execute_kw(_M_PRODUCT, "search", [[["default_code", "=", sku]]], {"limit": 1})
        if not ids:
            return None
        self._id_by_sku[sku] = int(ids[0])
        return self._id_by_sku[sku]

    def read(self, entity_id: str) -> dict:
        pid = self._resolve(entity_id)
        if pid is None:
            return {}
        rows = self._rpc.execute_kw(_M_PRODUCT, "read", [[pid]], {"fields": ["list_price"]})
        if not rows:
            return {}
        return {"price": float(rows[0].get("list_price") or 0.0)}

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
                if c.is_noop:
                    continue
                pid = self._resolve(c.entity_id)
                if pid is None:
                    raise OdooError(f"unknown SKU {c.entity_id!r} in Odoo (no product.product.default_code match)")
                current = self.read(c.entity_id)
                restore.append((c.entity_id, c.field, current.get(c.field, writeback.ABSENT)))
                self._rpc.execute_kw(_M_PRODUCT, "write", [[pid], {"list_price": float(c.after)}])
        except Exception:
            # Compensating undo of whatever landed in THIS call, same pattern as
            # OdooConnector's own _ReorderRuleStore -- Odoo has no cross-call
            # transaction to lean on here either.
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
        for entity_id, _field, original in restore:
            if original is writeback.ABSENT:
                continue  # nothing existed before (mirrors _ReorderRuleStore's identical convention)
            pid = self._resolve(entity_id)
            if pid is None:
                raise OdooError(f"cannot restore: SKU {entity_id!r} no longer exists in Odoo")
            self._rpc.execute_kw(_M_PRODUCT, "write", [[pid], {"list_price": float(original)}])

"""Stock sync Odoo -> Shopify: detect divergence, propose, never write.

Odoo is the system of record for stock: purchases, receipts, adjustments and
warehouses all live there, so Shopify follows and never leads. This job reads
the desired quantities (computed by its caller -- see ``plan_stock_sync``),
compares them against what Shopify currently reports, and stages a Changeset
for a human to approve.

It deliberately does NOT write. ``writeback.stage`` is a dry run by contract;
applying the changeset is the T2 approval's job, and that path already carries
signed approval, idempotency and rollback. A call to ``commit`` or to the
connector's ``set_inventory`` from this module would be a defect.

Nothing here schedules itself either -- an external cron already POSTs to
``/api/jobs/run-scheduled`` on a cadence (a persistent in-process scheduler was
rejected for this deploy; see that endpoint's docstring).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from nosilo_core import writeback

#: writeback target for the Shopify inventory store.
TARGET = "shopify_inventory"

_KEY_PREFIX = "shopify-stock-sync"
_FIELD = "quantity"


@dataclass(frozen=True)
class SyncReport:
    """What one planning pass found.

    ``changeset`` is None when there is nothing to propose -- either every SKU
    already agrees, or ``desired`` was empty. ``missing_skus`` are SKUs present
    in ``desired`` but unknown to Shopify; a partially aligned catalogue is
    normal, so they are reported rather than raised.
    """

    changeset: writeback.Changeset | None
    missing_skus: tuple[str, ...]


def idempotency_key(edits: dict[str, dict]) -> str:
    """Derive a stable key from the edits themselves.

    Deterministic on purpose: if the same divergence is re-planned on the next
    tick, the key is identical and writeback's own ``claim()`` rejects it as a
    duplicate. Idempotency therefore holds at the writeback layer too, not only
    at the event ledger's.
    """
    canonical = repr(sorted((sku, sorted(fields.items())) for sku, fields in edits.items()))
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return f"{_KEY_PREFIX}:{digest}"


def plan_stock_sync(store: Any, desired: dict[str, float]) -> SyncReport:
    """Compare ``desired`` against Shopify and stage the difference.

    ``desired`` maps SKU to quantity and is computed by the CALLER -- for stock
    it comes from ``OdooConnector.inventory_levels()``. Passing it in rather
    than reading Odoo here keeps this function pure enough to test without an
    ERP, and lets the price sync reuse it with a computed source instead of a
    read one.
    """
    edits: dict[str, dict] = {}
    missing: list[str] = []

    for sku in sorted(desired):
        current = store.read(sku)
        if not current:
            missing.append(sku)
            continue
        if float(current[_FIELD]) != float(desired[sku]):
            edits[sku] = {_FIELD: float(desired[sku])}

    if not edits:
        return SyncReport(changeset=None, missing_skus=tuple(missing))

    changeset = writeback.stage(
        store,
        TARGET,
        edits,
        risk_tier=writeback.TIER_REVERSIBLE,
        idempotency_key=idempotency_key(edits),
        reason="stock sync Odoo -> Shopify",
    )
    return SyncReport(changeset=changeset, missing_skus=tuple(missing))

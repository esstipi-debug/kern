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
from datetime import datetime
from typing import Any

from nosilo_core import writeback

from scm_agent.events import Event, EventLedger

from .scheduler import ScheduledJob

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


#: Event type routed to tier T2 in config/event_routing.yaml.
EVENT_TYPE = "shopify_stock_divergence"
SOURCE = "shopify_stock_sync"
_MAX_PAYLOAD_ROWS = 50


def _severity(changeset: writeback.Changeset) -> str:
    """Rate the divergence: high when Shopify is ahead of Odoo.

    Shopify holding more than Odoo means units that can be oversold.

    Shopify lagging behind is a lost sale: annoying, recoverable, and it does
    not put the shop in the position of selling stock it does not have.
    """
    for change in changeset.changes:
        if float(change.before) > float(change.after):
            return "high"
    return "medium"


def run_stock_sync(
    store: Any,
    desired: dict[str, float],
    *,
    ledger: EventLedger,
    now: datetime | None = None,
) -> tuple[SyncReport, Event | None, bool]:
    """Plan the sync and emit one event describing what needs approving.

    Returns the report, the event (None when there is nothing to propose) and
    whether the ledger accepted it -- False means the same divergence was
    already recorded within the dedup window.

    ``now`` sets the event timestamp, which is what the dedup window is
    measured against; passing it explicitly makes the dedup behaviour
    deterministic in tests and correct in a replay.
    """
    report = plan_stock_sync(store, desired)
    if report.changeset is None:
        return report, None, False

    divergences = [
        {"sku": c.entity_id, "before": float(c.before), "after": float(c.after)}
        for c in report.changeset.changes
    ]

    event = Event(
        type=EVENT_TYPE,
        severity=_severity(report.changeset),
        source=SOURCE,
        dedup_key=report.changeset.content_hash,
        payload={
            "idempotency_key": report.changeset.idempotency_key,
            "target": TARGET,
            "n_divergent": len(report.changeset.changes),
            "missing_skus": list(report.missing_skus),
            "divergences": divergences[:_MAX_PAYLOAD_ROWS],
            "divergences_truncated": len(divergences) > _MAX_PAYLOAD_ROWS,
        },
        **({"ts": now} if now is not None else {}),
    )
    return report, event, ledger.emit(event)


#: Matches the external cron's cadence for the other scheduled jobs.
DEFAULT_CADENCE_HOURS = 4


def desired_from_odoo(odoo: Any) -> dict[str, float]:
    """Map ``OdooConnector.inventory_levels()`` to the SKU -> quantity shape.

    This is the ONLY thing tying the stock sync to Odoo, which is why it lives
    apart from the job itself: the price sync will feed the same job from the
    repricing engine instead, with nothing else changing.

    A SKU stocked in several Odoo locations is SUMMED -- Shopify holds one
    level per SKU at the configured location, so the shop's sellable quantity
    is the total on hand, not whichever warehouse happened to be read last.
    """
    totals: dict[str, float] = {}
    for level in odoo.inventory_levels():
        totals[level.sku] = totals.get(level.sku, 0.0) + float(level.available)
    return totals


def _not_wired() -> None:
    """Placeholder callable: this job is registrable but not yet wired to credentials.

    Building a live OdooConnector and ShopifyInventoryStore needs ODOO_URL,
    ODOO_DB, ODOO_USERNAME, ODOO_API_KEY, SHOPIFY_SHOP_URL and
    SHOPIFY_ACCESS_TOKEN. That is deploy-time wiring, it cannot run offline,
    and it is deliberately NOT part of this plan -- see the plan's closing
    section. Raising rather than silently doing nothing means a deploy that
    schedules this job before wiring it finds out immediately.
    """
    raise NotImplementedError(
        "shopify_stock_sync is not wired to credentials yet -- see "
        "docs/superpowers/plans/2026-08-03-shopify-stock-sync.md"
    )


STOCK_SYNC_JOB = ScheduledJob(
    id="shopify_stock_sync",
    func=_not_wired,
    trigger="interval",
    trigger_args={"hours": DEFAULT_CADENCE_HOURS},
)

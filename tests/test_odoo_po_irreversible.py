"""Creating purchase orders in a client's ERP requires a human. Always.

Two defects sat on top of each other here.

1. The changeset was labelled ``TIER_REVERSIBLE`` ("a draft PO can be deleted"),
   and ``apply_draft_purchase_orders`` defaulted to ``auto_apply_reversible=True``.
   Together those made ``writeback.requires_approval`` return False, so a caller
   could put purchase orders into a live Odoo with ``approval=None`` and no human
   anywhere in the path. ``nosilo_core.writeback``'s own tier docstring names "send a
   PO" as THE example of an irreversible write.

2. The reversibility the label promised was not actually delivered.
   ``_DraftPoStore`` keeps the created PO ids in ``self._created`` -- process
   memory -- while ``jobs/odoo_job.py:61`` builds the connector with no persistent
   ledger. So a rollback from a later process unlinked nothing, forgot the audit
   entry, and returned normally: a "successful" rollback that reversed nothing and
   erased the only record of what was still live in the client's ERP.

Note what is NOT changed here: the reorder-point path (``stage_restock``) stays
reversible. That one sets a field, captures the prior value in the changeset, and
restores it exactly. The distinction is the point -- "reversible" is a promise that
undo works, and it should only be claimed where it does.
"""

from __future__ import annotations

import pytest

from nosilo_core import writeback
from src.connectors.odoo import OdooConnector, OdooError, demo_odoo


def _staged():
    connector = OdooConnector(demo_odoo())
    changeset, _unsourced = connector.stage_draft_purchase_orders({"SKU-1": 40.0})
    return connector, changeset


def _po_count(connector) -> int:
    return len(connector._rpc.records("purchase.order"))


# ---------------------------------------------------------------------------
# the tier
# ---------------------------------------------------------------------------


def test_a_purchase_order_changeset_is_irreversible():
    _connector, changeset = _staged()
    assert changeset.risk_tier == writeback.TIER_IRREVERSIBLE


def test_the_reorder_point_path_stays_reversible():
    """The control case. Blanket-labelling every write irreversible would be its own
    failure: it would push routine, genuinely undoable parameter edits through a
    human gate for no reason, and the gate would stop being taken seriously."""
    connector = OdooConnector(demo_odoo())
    changeset = connector.stage_restock({"SKU-1": 40.0}, idempotency_key="rop-1")
    assert changeset.risk_tier == writeback.TIER_REVERSIBLE


def test_applying_without_an_approval_is_refused_and_writes_nothing():
    connector, changeset = _staged()
    before = _po_count(connector)

    with pytest.raises(writeback.WritebackRefused):
        connector.apply_draft_purchase_orders(changeset)

    assert _po_count(connector) == before, "a refused apply must not touch the ERP"


def test_auto_apply_reversible_cannot_be_used_to_bypass_the_gate():
    """The flag survives for symmetry with the reversible write paths, but
    ``writeback.requires_approval`` returns True for the irreversible tier no matter
    what it is set to. A caller that flips it back to True must still be refused --
    otherwise this whole fix is one keyword argument away from being undone."""
    connector, changeset = _staged()

    with pytest.raises(writeback.WritebackRefused):
        connector.apply_draft_purchase_orders(changeset, auto_apply_reversible=True)

    assert _po_count(connector) == 0


def test_a_signed_approval_lets_it_through():
    """The gate must not be a wall: with a human's signed approval the POs land."""
    connector, changeset = _staged()
    approval = writeback.approve(changeset, "compras@cliente.com", now=1000.0)

    result = connector.apply_draft_purchase_orders(changeset, approval=approval, now=1000.0)

    assert result.applied
    assert _po_count(connector) > 0


def test_an_approval_for_a_different_changeset_is_refused():
    """Approvals bind to content, not just to a key -- so an operator cannot sign off
    on a 40-unit order and have a 4,000-unit one applied under it."""
    connector, small = _staged()
    approval = writeback.approve(small, "compras@cliente.com", now=1000.0)
    big, _ = connector.stage_draft_purchase_orders({"SKU-1": 4000.0})

    with pytest.raises(writeback.WritebackRefused):
        connector.apply_draft_purchase_orders(big, approval=approval, now=1000.0)


def test_an_expired_approval_is_refused():
    connector, changeset = _staged()
    approval = writeback.approve(changeset, "compras@cliente.com", now=1000.0, ttl_seconds=900.0)

    with pytest.raises(writeback.WritebackRefused):
        connector.apply_draft_purchase_orders(changeset, approval=approval, now=1000.0 + 901.0)


# ---------------------------------------------------------------------------
# the rollback promise
# ---------------------------------------------------------------------------


def test_rollback_removes_the_purchase_orders_it_created():
    connector, changeset = _staged()
    approval = writeback.approve(changeset, "compras@cliente.com", now=1000.0)
    connector.apply_draft_purchase_orders(changeset, approval=approval, now=1000.0)
    assert _po_count(connector) > 0

    connector._po_store.rollback(changeset.idempotency_key)

    assert _po_count(connector) == 0


def test_rollback_refuses_loudly_when_it_cannot_know_what_to_undo():
    """The silent-success bug. Simulates the cross-process case: the audit ledger
    remembers the key, this process does not remember the PO ids.

    Before, this returned None having unlinked nothing AND forgotten the audit entry.
    The caller saw a clean rollback; the purchase orders were still sitting in the
    client's ERP; and the record of which ones they were had just been deleted.
    """
    connector, changeset = _staged()
    approval = writeback.approve(changeset, "compras@cliente.com", now=1000.0)
    connector.apply_draft_purchase_orders(changeset, approval=approval, now=1000.0)
    live = _po_count(connector)

    connector._po_store._created.clear()          # what a process restart looks like

    with pytest.raises(OdooError, match="no record of the purchase orders"):
        connector._po_store.rollback(changeset.idempotency_key)

    assert _po_count(connector) == live, "nothing was removed, and it said so"
    # the audit entry survives, so the key cannot be silently reused
    assert changeset.idempotency_key in connector._po_store.applied_keys()

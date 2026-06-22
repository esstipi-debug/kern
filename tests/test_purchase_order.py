"""Tests for the purchase-order dataclass + state machine (capability M8)."""

import pytest

from src.purchase_order import (
    APPROVED,
    CANCELLED,
    DRAFT,
    ISSUED,
    RECEIVED,
    POError,
    POLine,
    draft_po,
    transition,
)


def _po():
    return draft_po(
        po_number="PO-1001",
        supplier="Acme",
        lines=[POLine("SKU-A", 100, 5.0), POLine("SKU-B", 50, 2.0)],
    )


def test_draft_po_totals_lines():
    po = _po()
    assert po.status == DRAFT
    assert po.total == pytest.approx(100 * 5.0 + 50 * 2.0)  # 600


def test_happy_path_transitions():
    po = _po()
    po = transition(po, "approve")
    assert po.status == APPROVED
    po = transition(po, "issue")
    assert po.status == ISSUED
    po = transition(po, "receive")
    assert po.status == RECEIVED


def test_cannot_issue_a_draft_directly():
    with pytest.raises(POError):
        transition(_po(), "issue")


def test_cannot_receive_a_draft():
    with pytest.raises(POError):
        transition(_po(), "receive")


def test_can_cancel_a_draft():
    po = transition(_po(), "cancel")
    assert po.status == CANCELLED


def test_cannot_transition_out_of_terminal_state():
    received = transition(transition(transition(_po(), "approve"), "issue"), "receive")
    with pytest.raises(POError):
        transition(received, "cancel")


def test_transition_returns_a_new_object():
    po = _po()
    approved = transition(po, "approve")
    assert po.status == DRAFT          # original unchanged (immutable)
    assert approved is not po

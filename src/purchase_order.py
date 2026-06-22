"""Purchase order: immutable dataclass + state machine (capability M8).

Turns an approved replenishment/award decision into a PO that moves through a guarded
lifecycle. Transitions are validated (invalid moves raise ``POError``) and return a
new object (immutable, matching the repo's no-mutation style). Issuing/receiving are
the consequential steps the safe-staging writeback plane gates in production.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

DRAFT = "draft"
APPROVED = "approved"
ISSUED = "issued"
RECEIVED = "received"
CANCELLED = "cancelled"

# action -> (from_state, to_state)
_TRANSITIONS = {
    "approve": (DRAFT, APPROVED),
    "issue": (APPROVED, ISSUED),
    "receive": (ISSUED, RECEIVED),
    "cancel": ({DRAFT, APPROVED}, CANCELLED),
}


class POError(Exception):
    """Raised on an invalid PO state transition."""


@dataclass(frozen=True)
class POLine:
    product_id: str
    qty: float
    unit_price: float

    @property
    def line_total(self) -> float:
        return self.qty * self.unit_price


@dataclass(frozen=True)
class PurchaseOrder:
    po_number: str
    supplier: str
    lines: tuple[POLine, ...]
    status: str

    @property
    def total(self) -> float:
        return sum(line.line_total for line in self.lines)


def draft_po(po_number: str, supplier: str, lines: list[POLine]) -> PurchaseOrder:
    """Create a PO in DRAFT status."""
    return PurchaseOrder(po_number=po_number, supplier=supplier, lines=tuple(lines), status=DRAFT)


def transition(po: PurchaseOrder, action: str) -> PurchaseOrder:
    """Apply a lifecycle action, returning a new PO. Raises POError if invalid."""
    if action not in _TRANSITIONS:
        raise POError(f"unknown action: {action}")
    from_state, to_state = _TRANSITIONS[action]
    allowed = from_state if isinstance(from_state, set) else {from_state}
    if po.status not in allowed:
        raise POError(f"cannot '{action}' a PO in status '{po.status}'")
    return replace(po, status=to_state)

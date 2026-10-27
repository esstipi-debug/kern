"""Double-Entry Commercial Override Ledger & Margin Conservation Engine.

Implements:
1. Double-entry operational ledger recording commercial overrides (expedited freight,
   warehouse overtime, supplier rush fees).
2. Cost transfer debiting requesting commercial cost centers (Marketing, Sales)
   and crediting operational cost recovery centers.
3. Strict margin conservation auditing reconciling with activity-based cost-to-serve
   (src/cost_to_serve.py).

Pure, deterministic module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from src.cost_to_serve import SegmentActivity, ServiceCostRates, cost_to_serve


@dataclass(frozen=True)
class OverrideTransaction:
    """A commercial override authorizing an expedite or capacity violation."""
    transaction_id: str
    timestamp_iso: str
    sku: str
    units: float
    requester_cost_center: str      # e.g., "MKTG_CAMPAIGN_CYBER" or "SALES_ENTERPRISE_B2B"
    operational_credit_center: str  # e.g., "LOGISTICS_FREIGHT_EXPEDITE"
    standard_freight_cost: float
    expedited_freight_cost: float
    overtime_labor_cost: float = 0.0
    supplier_rush_fee: float = 0.0
    justification: str = ""

    @property
    def net_surcharge(self) -> float:
        """The marginal surcharge incurred above standard operational delivery."""
        freight_delta = max(0.0, self.expedited_freight_cost - self.standard_freight_cost)
        return freight_delta + self.overtime_labor_cost + self.supplier_rush_fee


@dataclass(frozen=True)
class LedgerEntry:
    """A single double-entry accounting line."""
    transaction_id: str
    account: str
    debit: float
    credit: float
    description: str


@dataclass(frozen=True)
class ReconciledSegmentMargin:
    """Audit outcome verifying margin conservation after commercial override transfer."""
    segment: str
    revenue: float
    standard_net_to_serve: float
    total_commercial_surcharges: float
    audited_net_to_serve: float
    audited_net_margin_pct: float
    is_margin_conserved: bool
    reconciliation_error: float


class CommercialLedger:
    """In-memory double-entry operational cost ledger."""

    def __init__(self) -> None:
        self._transactions: List[OverrideTransaction] = []
        self._journal: List[LedgerEntry] = []

    def record_override(self, tx: OverrideTransaction) -> None:
        """Record an override transaction and post balancing journal entries."""
        if tx.units <= 0:
            raise ValueError("Override transaction units must be positive.")
        if tx.expedited_freight_cost < tx.standard_freight_cost:
            raise ValueError("Expedited freight cost cannot be less than standard freight cost.")

        surcharge = tx.net_surcharge
        self._transactions.append(tx)

        # Double-entry posting:
        # Debit: Commercial cost center absorbs the surcharge as an operational penalty
        self._journal.append(
            LedgerEntry(
                transaction_id=tx.transaction_id,
                account=tx.requester_cost_center,
                debit=surcharge,
                credit=0.0,
                description=f"Override surcharge for {tx.units:.0f} units of {tx.sku}: {tx.justification}",
            )
        )
        # Credit: Operations cost center recovers the extraordinary expense
        self._journal.append(
            LedgerEntry(
                transaction_id=tx.transaction_id,
                account=tx.operational_credit_center,
                debit=0.0,
                credit=surcharge,
                description=f"Recovery from {tx.requester_cost_center} for expedite of {tx.sku}",
            )
        )

    def total_debits(self) -> float:
        """Sum of all debits across the ledger."""
        return sum(entry.debit for entry in self._journal)

    def total_credits(self) -> float:
        """Sum of all credits across the ledger."""
        return sum(entry.credit for entry in self._journal)

    def is_balanced(self) -> bool:
        """Verify the fundamental accounting equation: Total Debits == Total Credits."""
        return abs(self.total_debits() - self.total_credits()) < 1e-6

    def get_cost_center_balance(self, cost_center: str) -> float:
        """Compute net balance (Debits - Credits) for a specific cost center."""
        debits = sum(e.debit for e in self._journal if e.account == cost_center)
        credits = sum(e.credit for e in self._journal if e.account == cost_center)
        return debits - credits

    def get_transactions_for_center(self, cost_center: str) -> List[OverrideTransaction]:
        """Retrieve all override transactions initiated by a specific commercial cost center."""
        return [tx for tx in self._transactions if tx.requester_cost_center == cost_center]


def reconcile_with_cost_to_serve(
    activity: SegmentActivity,
    rates: ServiceCostRates,
    ledger: CommercialLedger,
    commercial_cost_center: Optional[str] = None,
) -> ReconciledSegmentMargin:
    """Audit and reconcile segment cost-to-serve with commercial override deductions.

    Guarantees strict margin conservation:
    Audited Net = Standard Net - Override Surcharges.
    """
    cost_center = commercial_cost_center or activity.segment
    standard_cts = cost_to_serve(activity, rates)

    # Sum surcharges charged to this commercial unit
    surcharges = sum(
        tx.net_surcharge
        for tx in ledger.get_transactions_for_center(cost_center)
    )

    audited_net = standard_cts.net_to_serve - surcharges
    audited_margin_pct = (audited_net / activity.revenue) if activity.revenue > 0 else 0.0

    # Theoretical conservation check:
    # (Revenue - Product - Fulfillment - Returns - Overhead) - Surcharges == Audited Net
    expected_net = activity.revenue - (
        standard_cts.product_cost
        + standard_cts.fulfillment_cost
        + standard_cts.returns_cost
        + standard_cts.overhead_cost
        + surcharges
    )
    error = abs(audited_net - expected_net)
    is_conserved = error < 1e-6

    return ReconciledSegmentMargin(
        segment=activity.segment,
        revenue=activity.revenue,
        standard_net_to_serve=standard_cts.net_to_serve,
        total_commercial_surcharges=surcharges,
        audited_net_to_serve=audited_net,
        audited_net_margin_pct=audited_margin_pct,
        is_margin_conserved=is_conserved,
        reconciliation_error=error,
    )

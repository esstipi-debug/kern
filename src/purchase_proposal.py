"""Replenishment needs -> a draft purchase order per supplier (capability M5b).

WHY this module exists. A replenishment run answers "how many units", and a
quantity is not a document anybody can act on. The buy only becomes work a buyer
can execute once it is split by supplier and each line carries three things the
quantity does not: when the order is released, when the stock is actually needed,
and why the line is on the order at all.

  * Release date / due date. A planned order becomes a commitment when it is
    released with dates, not when its quantity is computed -- planned-order
    release in Vollmann, Berry, Whybark & Jacobs, *Manufacturing Planning and
    Control for Supply Chain Management*. Until then it is a plan, and a plan
    with no date cannot be expedited, chased or measured.
  * Split by supplier. Purchasing executes against a supplier, not against a
    catalog: the order is placed with the party that holds the contract and the
    lead time (APICS/ASCM CSCP, Module 3 Sec. D, "Supplier Selection,
    Contracting, and Use"). Two SKUs from two suppliers are two orders.
  * Reason per line. The buyer has to be able to overrule the system, and cannot
    overrule a number with no argument behind it. In a demand-driven run the
    argument is the buffer zone the net-flow position penetrated (Ptak & Smith,
    *DDMRP v3*); in a reorder-point run it is the ROP breach. The caller supplies
    the sentence; this module only carries it to the line.

A SKU with no known supplier is never dropped: it comes back under
:data:`UNSOURCED` so the residual is visible instead of silently missing. Same
convention as ``src/connectors/odoo.py::stage_draft_purchase_orders``
("unsourced, not dropped").

**The lead time is an EXTERNAL INPUT and says whose it is.** A SKU nobody mapped
a lead time for falls back to ``default_lead_time_days`` and the line is stamped
:data:`LEAD_TIME_ASSUMED` -- exactly the contract
``src/sop_engine/cash_plan.py::resolve_payment_terms`` keeps for a payment plazo
nobody stated. On such a line ``arrives_late`` is ``None``, not ``False``: with
the default at 0.0 the arrival landed on the order date, so every unmapped SKU
came back "arrives on time" -- a promise made out of an absence, and the one
answer the buyer had no way to argue with. ``None`` is the repo's word for a
verdict nobody can issue (``src/deliverable.py::not_measured``), and the SKUs it
applies to come back named in
:attr:`PurchaseProposal.lead_time_assumed_for` so an absent lead time is a
residual to close, not a line that quietly reads as healthy.

Pure: stdlib only, deterministic, frozen dataclasses, no I/O. ``today`` is
injectable exactly like ``as_of`` in ``src/risk.py::rank_mitigations`` so a run
can be reproduced. The lifecycle that carries a draft from here to issued and
received lives in ``src/purchase_order.py``; this module only proposes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

#: Supplier label for a line whose SKU has no supplier mapped. Kept in the
#: proposal on purpose: a missing supplier is a residual to hand back, not a
#: reason to lose the demand.
UNSOURCED = "UNSOURCED"

#: The caller mapped this SKU's supplier lead time, so the expected arrival is a
#: reading. Also the default of a hand-built :class:`ProposedLine`, whose caller
#: passes ``expected_arrival`` outright: that date IS what they stated.
LEAD_TIME_STATED = "stated"
#: Nobody mapped this SKU: the arrival rests on ``default_lead_time_days``. Same
#: spelling as ``cash_plan.TERMS_ASSUMED`` on purpose -- one word for one idea.
LEAD_TIME_ASSUMED = "assumed_default"


@dataclass(frozen=True)
class ReplenishmentNeed:
    """One SKU that needs buying, engine-neutral: DDMRP, ROP or a manual list.

    ``days_to_stockout`` is how long the current cover lasts; it is what turns
    into the required (need-by) date. ``reason`` is the sentence the engine can
    defend the line with.
    """

    sku: str
    order_qty: float
    reason: str = ""
    days_to_stockout: float = 0.0


@dataclass(frozen=True)
class ProposedLine:
    """One line of a draft PO: what to buy, from whom, by when, and why."""

    supplier: str
    sku: str
    order_qty: float
    order_date: date
    required_date: date
    expected_arrival: date
    reason: str
    #: :data:`LEAD_TIME_STATED` or :data:`LEAD_TIME_ASSUMED` -- where the lead
    #: time behind ``expected_arrival`` came from. Without it a fabricated
    #: arrival date is indistinguishable from a contracted one.
    lead_time_source: str = LEAD_TIME_STATED

    @property
    def sourced(self) -> bool:
        return self.supplier != UNSOURCED

    @property
    def lead_time_stated(self) -> bool:
        return self.lead_time_source == LEAD_TIME_STATED

    @property
    def arrives_late(self) -> bool | None:
        """The supplier's lead time does not make the need-by date.

        ``None`` when the lead time was assumed: comparing a need-by date
        against an arrival nobody supplied answers a question about the default,
        not about this supplier. See the module docstring.
        """
        if not self.lead_time_stated:
            return None
        return self.expected_arrival > self.required_date

    @property
    def days_late(self) -> int | None:
        """How many days late, or ``None`` when there is no verdict to size."""
        if not self.lead_time_stated:
            return None
        return max(0, (self.expected_arrival - self.required_date).days)


@dataclass(frozen=True)
class SupplierOrder:
    """The lines going to one supplier on one release date: the draft PO."""

    supplier: str
    order_date: date
    lines: tuple[ProposedLine, ...] = ()

    @property
    def n_lines(self) -> int:
        return len(self.lines)

    @property
    def total_qty(self) -> float:
        return sum(line.order_qty for line in self.lines)

    @property
    def late_lines(self) -> tuple[ProposedLine, ...]:
        """Only the lines KNOWN to arrive late: ``is True``, never truthiness, so
        a line whose verdict is ``None`` is not counted as on time either."""
        return tuple(line for line in self.lines if line.arrives_late is True)


@dataclass(frozen=True)
class PurchaseProposal:
    """Every draft PO of one run, plus the lines nobody can be asked to fill."""

    orders: tuple[SupplierOrder, ...] = ()
    unsourced: tuple[ProposedLine, ...] = ()

    @property
    def n_orders(self) -> int:
        return len(self.orders)

    @property
    def n_lines(self) -> int:
        return sum(o.n_lines for o in self.orders) + len(self.unsourced)

    @property
    def total_qty(self) -> float:
        return sum(line.order_qty for line in self.all_lines)

    @property
    def all_lines(self) -> tuple[ProposedLine, ...]:
        """Sourced lines first (grouped), then the unsourced residual."""
        sourced = tuple(line for o in self.orders for line in o.lines)
        return sourced + self.unsourced

    @property
    def late_lines(self) -> tuple[ProposedLine, ...]:
        """The lines KNOWN to arrive late. A line with no lead time is absent from
        both this list and any "arrives on time" reading of it."""
        return tuple(line for line in self.all_lines if line.arrives_late is True)

    @property
    def lead_time_assumed_for(self) -> tuple[str, ...]:
        """SKUs whose expected arrival rests on ``default_lead_time_days``.

        The counterpart of ``cash_plan.CashCommitmentSchedule.terms_assumed_for``,
        and read the same way: these lines carry a date, and the date is this
        module's assumption rather than a supplier's commitment. Naming them is
        what keeps ``late_lines`` from being read as the whole exposure.
        """
        return tuple(line.sku for line in self.all_lines if not line.lead_time_stated)

    def restock_by_sku(self) -> dict[str, float]:
        """``{sku: qty}`` over every line, unsourced included.

        The adapter to the write side: this is the exact argument
        ``OdooConnector.stage_draft_purchase_orders(restock, ...)`` takes, so the
        proposal can be staged without either module knowing about the other.
        """
        out: dict[str, float] = {}
        for line in self.all_lines:
            out[line.sku] = out.get(line.sku, 0.0) + line.order_qty
        return out


#: A run that proposed nothing. Frozen and shareable, so it is safe as the
#: default of a field on another dataclass.
EMPTY_PROPOSAL = PurchaseProposal()


def _supplier_of(sku: str, supplier_by_sku: dict[str, object]) -> str:
    """Whoever holds the line, or UNSOURCED. Ids are stringified: Odoo maps to int."""
    raw = supplier_by_sku.get(sku)
    if raw is None:
        return UNSOURCED
    name = str(raw).strip()
    return name or UNSOURCED


def _days(value: float) -> int:
    """Whole days, never negative: a calendar offset cannot run backwards."""
    return max(0, int(round(float(value))))


def _lead_time_of(
    sku: str, lead_time_days: dict[str, float], default_days: float
) -> tuple[float, str]:
    """``(days, source)``. Absent from the map is a SUPPOSITION, whatever the
    default's value: the caller choosing 5 instead of 0 makes the number theirs,
    not this supplier's. Same rule as ``cash_plan.resolve_payment_terms``, which
    stamps TERMS_ASSUMED even when the caller passes an explicit default."""
    if sku in lead_time_days:
        return float(lead_time_days[sku]), LEAD_TIME_STATED
    return float(default_days), LEAD_TIME_ASSUMED


def _line(
    need: ReplenishmentNeed,
    *,
    supplier: str,
    order_date: date,
    lead_time_days: float,
    lead_time_source: str,
) -> ProposedLine:
    """Date one need against its supplier's lead time and its own cover."""
    return ProposedLine(
        supplier=supplier,
        sku=need.sku,
        order_qty=float(need.order_qty),
        order_date=order_date,
        required_date=order_date + timedelta(days=_days(need.days_to_stockout)),
        expected_arrival=order_date + timedelta(days=_days(lead_time_days)),
        reason=need.reason,
        lead_time_source=lead_time_source,
    )


def _orders_of(
    by_supplier: dict[str, list[ProposedLine]], order_date: date
) -> tuple[SupplierOrder, ...]:
    """One draft PO per supplier, deterministic: orders by supplier, lines by SKU."""
    return tuple(
        SupplierOrder(
            supplier=supplier,
            order_date=order_date,
            lines=tuple(sorted(lines, key=lambda ln: ln.sku)),
        )
        for supplier, lines in sorted(by_supplier.items())
    )


def build_proposal(
    needs: list[ReplenishmentNeed],
    *,
    supplier_by_sku: dict[str, object] | None = None,
    lead_time_days: dict[str, float] | None = None,
    today: date | None = None,
    default_lead_time_days: float = 0.0,
) -> PurchaseProposal:
    """Group replenishment needs into one dated draft PO per supplier.

    Both maps are keyed by SKU because that is the shape the ERP already hands
    back: ``OdooConnector.primary_supplier_by_sku()`` and
    ``OdooConnector.lead_times()``. Needs with a non-positive quantity are
    dropped -- a zero-unit PO line is not an order. Output is deterministic:
    orders sorted by supplier, lines by SKU.

    ``default_lead_time_days`` covers a SKU the ``lead_time_days`` map does not
    mention, and is never silent about it: see :func:`_lead_time_of`.
    """
    supplier_by_sku = supplier_by_sku or {}
    lead_time_days = lead_time_days or {}
    order_date = today or date.today()

    by_supplier: dict[str, list[ProposedLine]] = {}
    unsourced: list[ProposedLine] = []
    for need in needs:
        if float(need.order_qty) <= 0.0:
            continue
        days, source = _lead_time_of(need.sku, lead_time_days, default_lead_time_days)
        line = _line(
            need,
            supplier=_supplier_of(need.sku, supplier_by_sku),
            order_date=order_date,
            lead_time_days=days,
            lead_time_source=source,
        )
        if line.sourced:
            by_supplier.setdefault(line.supplier, []).append(line)
        else:
            unsourced.append(line)

    return PurchaseProposal(
        orders=_orders_of(by_supplier, order_date),
        unsourced=tuple(sorted(unsourced, key=lambda ln: ln.sku)),
    )


def proposal_rows(proposal: PurchaseProposal) -> list[dict[str, object]]:
    """Flat, export-ready rows of the proposal (one per line), grouped by supplier.

    ``arrives_late``/``days_late`` are ``None`` -- an EMPTY cell, never ``False``
    or ``0`` -- on a line whose lead time was assumed, and ``lead_time_source``
    beside them says why the cell is empty. A blank a reader can explain is the
    whole point: an exported ``False`` there is a claim the run never made.
    """
    return [
        {
            "supplier": line.supplier,
            "sku": line.sku,
            "order_qty": round(line.order_qty, 2),
            "order_date": line.order_date.isoformat(),
            "required_date": line.required_date.isoformat(),
            "expected_arrival": line.expected_arrival.isoformat(),
            "arrives_late": line.arrives_late,
            "days_late": line.days_late,
            "lead_time_source": line.lead_time_source,
            "reason": line.reason,
        }
        for line in proposal.all_lines
    ]

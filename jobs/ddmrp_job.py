"""DDMRP agent job: a parts/buffer CSV -> red/yellow/green zones + net-flow signals.

The data-prep + deck half of the DDMRP tool. Reads a parts table (ADU, decoupled lead
time, buffer-profile factors, and the current on-hand / on-order / qualified demand) with
pandas directly (deliberately *not* via jobs/intake.py, which the parallel loop owns),
sizes each buffer and computes the net-flow planning signal, then composes the buffer
plan deck inline. Column names are sniffed and overridable via params; LTF/VF default to
0.5 when absent.

The signal says how many units; it does not say who to buy them from or when. The run
therefore also drafts the purchase orders (``src/purchase_proposal.py``): the recommended
parts are grouped into one dated draft PO per supplier, each line carrying the zone
penetration as its reason. Parts with no supplier column come back unsourced rather than
dropped, so a parts table without one still runs.

Two rules about dirty input, and they are deliberately different. A value that cannot
be what its column claims - a negative ADU or lead time - is refused at the border
(:func:`_reject_impossible_quantities`), because nothing sized from it is a buffer and
no date drafted from it is a date. A repeated ``part_id`` is NOT refused: every row
still gets its own honest buffer, and only the one artifact the repeat makes ambiguous
- the purchase order - is withheld, with the ids named on the report and in the deck
(:func:`_duplicate_part_ids`). Nothing is ever merged, and nothing is ever dropped.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from src.ddmrp import BufferZones, PlanningSignal, planning_signal, size_buffer
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.purchase_proposal import (
    EMPTY_PROPOSAL,
    ProposedLine,
    PurchaseProposal,
    ReplenishmentNeed,
    build_proposal,
    proposal_rows,
)

_PART_COLS = ("part_id", "Part", "part", "sku", "SKU", "product_id", "item")
_ADU_COLS = ("adu", "ADU", "avg_daily_usage", "average_daily_usage", "daily_usage")
_DLT_COLS = ("dlt", "DLT", "decoupled_lead_time", "lead_time", "lead_time_days", "lead")
_LTF_COLS = ("ltf", "LTF", "lead_time_factor")
_VF_COLS = ("vf", "VF", "variability_factor")
_MOQ_COLS = ("moq", "MOQ", "min_order_qty")
_OCD_COLS = ("order_cycle_days", "order_cycle", "order_cycle_period")
_ONHAND_COLS = ("on_hand", "On Hand", "onhand", "stock", "inventory")
_ONORDER_COLS = ("on_order", "On Order", "onorder", "open_po", "open_orders")
_QD_COLS = ("qualified_demand", "qualified", "open_demand", "sales_orders", "demand")
_SUPPLIER_COLS = ("supplier", "vendor", "proveedor", "supplier_name", "supplier_id")


@dataclass(frozen=True)
class DdmrpPart:
    """One buffered part: its zones, its net-flow signal, and the PO line it drives.

    ``po_line`` is nested rather than flattened into ``supplier`` / ``order_date``
    fields on purpose. A part that is above Top of Yellow has no line at all, so a
    top-level supplier field would be empty for it and would claim a capability the
    row does not have; the nesting keeps "there is a PO line" and "the PO line names
    a supplier" as two separate, checkable facts.
    """

    part_id: str
    zones: BufferZones
    signal: PlanningSignal
    po_line: ProposedLine | None = None   # None when no order is recommended


@dataclass(frozen=True)
class DdmrpReport:
    parts: tuple[DdmrpPart, ...]   # sorted most-urgent first
    n_parts: int
    n_red: int
    n_yellow: int
    n_order: int
    total_order_qty: float
    proposal: PurchaseProposal = EMPTY_PROPOSAL   # the draft POs, grouped by supplier
    #: ``part_id`` values carried by more than one row. They ARE buffered (each
    #: row from its own ADU/DLT) and they are deliberately NOT ordered: see
    #: :func:`_duplicate_part_ids`. Named here so "no PO line" is a fact a caller
    #: can read off the report instead of an absence it has to infer.
    ambiguous_part_ids: tuple[str, ...] = ()


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _text(row, col: str | None) -> str:
    """A label off the row, or "" when the column is absent / blank / NaN."""
    if not col:
        return ""
    value = row[col]
    if value is None or value != value:   # NaN never equals itself
        return ""
    return str(value).strip()


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[dict]:
    """Sniff the DDMRP columns and build one input record per part."""
    params = params or {}
    part = _pick_column(df, params.get("part_col"), _PART_COLS)
    adu = _pick_column(df, params.get("adu_col"), _ADU_COLS)
    dlt = _pick_column(df, params.get("dlt_col"), _DLT_COLS)
    missing = [n for n, c in (("part_id", part), ("adu", adu), ("dlt", dlt)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    ltf = _pick_column(df, params.get("ltf_col"), _LTF_COLS)
    vf = _pick_column(df, params.get("vf_col"), _VF_COLS)
    moq = _pick_column(df, params.get("moq_col"), _MOQ_COLS)
    ocd = _pick_column(df, params.get("order_cycle_col"), _OCD_COLS)
    onhand = _pick_column(df, params.get("on_hand_col"), _ONHAND_COLS)
    onorder = _pick_column(df, params.get("on_order_col"), _ONORDER_COLS)
    qd = _pick_column(df, params.get("qualified_demand_col"), _QD_COLS)
    supplier = _pick_column(df, params.get("supplier_col"), _SUPPLIER_COLS)

    def _num(row, col, default):
        return float(row[col]) if col else float(default)

    return [
        {
            "part_id": str(row[part]),
            "adu": float(row[adu]),
            "dlt": float(row[dlt]),
            "ltf": _num(row, ltf, params.get("ltf", 0.5)),
            "vf": _num(row, vf, params.get("vf", 0.5)),
            "moq": _num(row, moq, 0.0),
            "order_cycle_days": _num(row, ocd, 0.0),
            "on_hand": _num(row, onhand, 0.0),
            "on_order": _num(row, onorder, 0.0),
            "qualified_demand": _num(row, qd, 0.0),
            "supplier": _text(row, supplier),
        }
        for _, row in df.iterrows()
    ]


def prepare(data_path: str, params: dict | None = None) -> list[dict]:
    """Read a parts CSV and build the DDMRP input records."""
    return prepare_records(pd.read_csv(data_path), params)


#: Quantities whose sign nothing downstream re-checks. ADU is units per day and
#: DLT is a number of days: a negative value in either is a broken cell, not a
#: small buffer. Paired with the wording the error message uses.
_NON_NEGATIVE = (("adu", "average daily usage"), ("dlt", "decoupled lead time"))


def _reject_impossible_quantities(records: list[dict]) -> None:
    """Refuse a part whose ADU or DLT is negative, naming the part and the value.

    WHY here, at the border, and not in ``verify()``. The DLT is handed to
    ``build_proposal`` as this part's supplier lead time, and
    ``purchase_proposal._days`` clamps a negative offset to zero -- correct for
    what that clamp guards ("a calendar offset cannot run backwards") and the
    wrong place to notice this: a -99 day lead time comes out as an expected
    arrival ON the order date, a date nobody supplied, printed like a commitment.

    ``verify()`` cannot catch it either, which is what made this worth writing.
    Its zone check (tog >= toy >= tor >= 0) only fires while a positive ADU drags
    the zones negative; at ADU 0 with an MOQ the zones collapse to -0.0/-0.0/MOQ,
    ``-0.0 >= 0`` is True in Python, and the run ships a clean QA verdict over a
    fabricated arrival date. Same stance as
    ``src.order_fulfillment._check_reported``: a value that cannot be what the
    column claims is refused, because coercing it yields a confident wrong answer.

    Refused rather than dropped on purpose. A part missing from a buffer plan is
    invisible -- nobody notices the row that is not there -- so a silent skip
    would hand back a plan the planner reads as complete. Contrast
    :func:`_duplicate_part_ids`, where every row still has an honest buffer and
    only the purchase order is withheld.
    """
    for r in records:
        for key, label in _NON_NEGATIVE:
            if float(r[key]) < 0:
                raise ValueError(
                    f"part {r['part_id']!r} has a negative {label} ({key}={r[key]!r}). "
                    f"A {label} cannot run backwards; the buffer sized from it is not a "
                    "buffer and the arrival date drafted from it is not a date. Fix the "
                    "row rather than letting it through."
                )


def _duplicate_part_ids(records: list[dict]) -> tuple[str, ...]:
    """The ``part_id`` values that appear on more than one row, sorted.

    ``prepare_records`` builds one record per CSV line with no groupby (the same
    contract ``src.liquidation.plan_liquidation`` documents), but ``run`` resolves
    three things back to a part THROUGH that id: the supplier
    (``supplier_by_sku``), the lead time (``lead_time_days``) and the PO line
    (``by_sku``). A repeat breaks all three at once and none of them says so: the
    last row's supplier and DLT win for every duplicate, ``build_proposal`` dates
    one part's arrival off another's lead time, and both parts end up holding the
    same ``ProposedLine`` while the other is orphaned. What comes out is not an
    odd number a planner squints at -- it is an executable purchase order with the
    wrong supplier, the wrong dates and a doubled quantity.

    So the ids come back NAMED, and :func:`run` drafts no purchase order for them.

    Why not merge, and why not refuse the file. Merging is impossible: in DDMRP a
    part has ONE buffer, and two rows with a 7-day and a 60-day lead time have no
    mean -- the duplicate means ``part_id`` is not the key the caller thinks it
    is (two plants, two warehouses, a dirty row). Refusing the whole run was the
    first answer here and it was too wide: the BUFFER of each row is honest, sized
    from that row's own ADU and DLT and from nothing else, exactly as
    ``plan_liquidation`` keeps each row on its own cost and demand. Only the
    purchase order is ambiguous. Killing a whole self-service run over a residual
    is the same mistake ``verify()`` refuses to make for an unsourced line, and
    nobody is standing next to the client to explain why their file produced
    nothing. So: nothing is merged, nothing is dropped, the one artifact that
    cannot be resolved is withheld, and the deck says which parts and why.
    """
    seen: set[str] = set()
    repeated: set[str] = set()
    for r in records:
        part_id = r["part_id"]
        if part_id in seen:
            repeated.add(part_id)
        else:
            seen.add(part_id)
    return tuple(sorted(repeated))


def _days_of_cover(part: DdmrpPart) -> float:
    """How long the current net-flow position lasts at the average daily usage."""
    adu = part.zones.adu
    return max(0.0, part.signal.nfp / adu) if adu > 0 else 0.0


def _need(part: DdmrpPart) -> ReplenishmentNeed:
    """The buy this part needs, with the zone penetration written out as its reason."""
    cover = _days_of_cover(part)
    return ReplenishmentNeed(
        sku=part.part_id,
        order_qty=part.signal.order_qty,
        reason=(
            f"net flow {part.signal.nfp:,.1f} in the {part.signal.zone} zone "
            f"(TOR {part.zones.tor:,.1f}, TOG {part.zones.tog:,.1f}); "
            f"cover runs out in {cover:,.1f} day(s)"
        ),
        days_to_stockout=cover,
    )


def _buffer_records(
    records: list[dict], blocked: set[str]
) -> tuple[list[DdmrpPart], dict[str, str], dict[str, float]]:
    """One buffered part per record (most-urgent first) + the two id-keyed maps.

    A part whose id is in ``blocked`` is still buffered - from its own row's ADU
    and DLT and from nothing else - and is deliberately absent from BOTH maps.
    That absence is the whole guard: it is what stops the last row's supplier and
    lead time from being applied to the first row's. See :func:`_duplicate_part_ids`.
    """
    parts: list[DdmrpPart] = []
    supplier_by_sku: dict[str, str] = {}
    lead_time_days: dict[str, float] = {}
    for r in records:
        zones = size_buffer(r["adu"], r["dlt"], ltf=r["ltf"], vf=r["vf"],
                            moq=r["moq"], order_cycle_days=r["order_cycle_days"])
        signal = planning_signal(zones, r["on_hand"], r["on_order"], r["qualified_demand"])
        parts.append(DdmrpPart(r["part_id"], zones, signal))
        if r["part_id"] in blocked:
            continue
        lead_time_days[r["part_id"]] = r["dlt"]
        if str(r.get("supplier", "")).strip():
            supplier_by_sku[r["part_id"]] = str(r["supplier"]).strip()
    parts.sort(key=lambda p: p.signal.priority)
    return parts, supplier_by_sku, lead_time_days


def run(records: list[dict], *, today: date | None = None) -> DdmrpReport:
    """Size every buffer, signal the net flow, and draft the POs, most-urgent first.

    ``today`` anchors the order / required / arrival dates and defaults to the real
    date; it is injectable so a run can be replayed. The decoupled lead time doubles
    as the supplier's lead time for the expected arrival: in DDMRP that is what DLT
    means for a purchased part.

    Raises ``ValueError`` on a negative ADU or DLT -- see
    :func:`_reject_impossible_quantities`. A repeated ``part_id`` does NOT raise:
    those parts are buffered and left out of the purchase proposal, and come back
    named in :attr:`DdmrpReport.ambiguous_part_ids` (:func:`_duplicate_part_ids`).
    """
    _reject_impossible_quantities(records)
    ambiguous = _duplicate_part_ids(records)
    blocked = set(ambiguous)
    parts, supplier_by_sku, lead_time_days = _buffer_records(records, blocked)

    proposal = build_proposal(
        [_need(p) for p in parts
         if p.signal.order_recommended and p.part_id not in blocked],
        supplier_by_sku=supplier_by_sku,
        lead_time_days=lead_time_days,
        today=today,
    )
    # Rebuilt through the constructor, NOT through dataclasses.replace(): replace()
    # re-reads __dataclass_fields__ and forwards every name it finds to __init__,
    # so any field injected onto the class from outside would blow the call up.
    by_sku = {line.sku: line for line in proposal.all_lines}
    parts = [DdmrpPart(p.part_id, p.zones, p.signal, by_sku.get(p.part_id)) for p in parts]
    return DdmrpReport(
        parts=tuple(parts),
        n_parts=len(parts),
        n_red=sum(1 for p in parts if p.signal.zone == "red"),
        n_yellow=sum(1 for p in parts if p.signal.zone == "yellow"),
        n_order=sum(1 for p in parts if p.signal.order_recommended),
        total_order_qty=sum(p.signal.order_qty for p in parts),
        proposal=proposal,
        ambiguous_part_ids=ambiguous,
    )


def verify(report: DdmrpReport) -> list[str]:
    """QA gate: parts present and each buffer's zones are well-ordered.

    Two things are deliberately NOT QA failures. A missing supplier: a parts table
    with no supplier column is a normal input, the lines come back unsourced and
    the deck says so. And a repeated ``part_id``: those parts are buffered, no
    purchase order is drafted for them, and the deck names them. Failing here
    would suppress the whole deliverable over a residual.

    There is no date check. One lived here -- ``expected_arrival < order_date`` --
    and it was unreachable: ``purchase_proposal._days`` is ``max(0, ...)``, so an
    arrival is never before its order date and the branch could not run for any
    input. What it was reaching for (a lead time that is not a lead time) is
    caught where the value enters, in :func:`_reject_impossible_quantities`; a QA
    gate that can only pass is not a gate, it is a sentence about one.
    """
    issues: list[str] = []
    if not report.parts:
        issues.append("no parts to buffer")
    for p in report.parts:
        z = p.zones
        if not (z.tog >= z.toy >= z.tor >= 0):
            issues.append(f"invalid buffer zones for {p.part_id}")
    return issues


#: Why a buffered part has no purchase order beside it. Spelled out in the row
#: because five empty cells look identical whether the part needs nothing or its
#: order was withheld, and those are not the same news for a buyer.
NO_PO_AMBIGUOUS_ID = (
    "no purchase order drafted: this part_id is on more than one row, so its "
    "supplier and lead time are ambiguous"
)


def _po_columns(line: ProposedLine | None, *, no_po_reason: str = "") -> dict[str, object]:
    """The PO half of a buffer row; blank for a part that does not need an order.

    ``no_po_reason`` fills ``order_reason`` when there is no line to describe, so
    a withheld order reads differently from an unnecessary one.
    """
    if line is None:
        return {"supplier": "", "order_date": "", "required_date": "",
                "expected_arrival": "", "order_reason": no_po_reason}
    return {
        "supplier": line.supplier,
        "order_date": line.order_date.isoformat(),
        "required_date": line.required_date.isoformat(),
        "expected_arrival": line.expected_arrival.isoformat(),
        "order_reason": line.reason,
    }


def write_operational(report: DdmrpReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """Two machine-readable files: the buffer plan per part, and the draft POs.

    The buffer file answers "how is this part buffered and where is it now"; the PO
    file answers "what do I send to which supplier today". They are separate because
    they are read by different people at different moments.
    """
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    ambiguous = set(report.ambiguous_part_ids)
    rows = [
        {
            "part_id": p.part_id,
            "adu": round(p.zones.adu, 2),
            "dlt": round(p.zones.dlt, 2),
            "red": round(p.zones.red, 1),
            "yellow": round(p.zones.yellow, 1),
            "green": round(p.zones.green, 1),
            "tor": round(p.zones.tor, 1),
            "toy": round(p.zones.toy, 1),
            "tog": round(p.zones.tog, 1),
            "nfp": round(p.signal.nfp, 1),
            "zone": p.signal.zone,
            "order_qty": round(p.signal.order_qty, 1),
            "priority": round(p.signal.priority, 3),
            **_po_columns(
                p.po_line,
                no_po_reason=NO_PO_AMBIGUOUS_ID if p.part_id in ambiguous else "",
            ),
        }
        for p in report.parts
    ]
    return {
        "csv": write_summary_csv(rows, d / "ddmrp_buffers.csv"),
        "purchase_orders_csv": write_summary_csv(
            proposal_rows(report.proposal), d / "ddmrp_purchase_orders.csv"
        ),
    }


def _po_finding(proposal: PurchaseProposal) -> Finding:
    """What the draft POs look like: who gets what, and what is still open."""
    names = ", ".join(
        f"{o.supplier} ({o.n_lines} line(s), {o.total_qty:,.0f} u)" for o in proposal.orders[:5]
    )
    unsourced = len(proposal.unsourced)
    return Finding(
        f"{proposal.n_orders} draft purchase order(s) ready to send",
        "The buy is split by supplier with a release date, a need-by date and an "
        f"expected arrival per line: {names or 'no supplier could be resolved'}."
        + (f" {unsourced} line(s) have no supplier mapped and are listed unsourced."
           if unsourced else "")
        + (f" {len(proposal.late_lines)} line(s) arrive after they are needed."
           if proposal.late_lines else ""),
        impact="a buyer can act on this without rebuilding it by hand",
    )


def _ambiguous_finding(part_ids: tuple[str, ...]) -> Finding:
    """The parts whose purchase order was withheld, and the word for why.

    Written to be read as news, not as a footnote: a client who does not notice
    this line will read the buffer plan as complete and wonder, later, why two
    parts never got ordered.
    """
    names = ", ".join(part_ids[:5])
    more = f" (and {len(part_ids) - 5} more)" if len(part_ids) > 5 else ""
    return Finding(
        f"{len(part_ids)} part_id(s) appear on more than one row - NOT ordered",
        f"Rows share these ids: {names}{more}. Every row is still buffered from ITS "
        "OWN average daily usage and lead time - nothing was merged and no row was "
        "dropped - but no purchase order was drafted for them: a supplier, a lead "
        "time and an arrival date cannot be resolved through an id that names two "
        "parts, and a part has one buffer, so a 7-day and a 60-day lead time have "
        "no average. De-duplicate the rows, or key the parts by plant / warehouse "
        "as well, and re-run.",
        impact="these parts are buffered but NOT on any draft PO - order them by hand "
               "or fix the id and re-run",
    )


def _deck_findings(report: DdmrpReport, over: list[DdmrpPart]) -> tuple[Finding, ...]:
    """What the buffer run found, worst first, then how to read it."""
    findings: list[Finding] = []
    if report.ambiguous_part_ids:
        findings.append(_ambiguous_finding(report.ambiguous_part_ids))
    red = [p for p in report.parts if p.signal.zone == "red"]
    if red:
        names = ", ".join(f"{p.part_id} (+{p.signal.order_qty:,.0f})" for p in red[:5])
        findings.append(Finding(
            f"{len(red)} part(s) in the red - order now",
            f"Net-flow position has dropped into the red zone for: {names}. Order back up to "
            "Top of Green.",
            impact="prevent imminent stockouts",
        ))
    if report.proposal.n_lines:
        findings.append(_po_finding(report.proposal))
    if over:
        findings.append(Finding(
            f"{len(over)} part(s) above Top of Green",
            "Net-flow position exceeds the full buffer - excess stock or open orders tied up here.",
            impact="defer / cancel open supply; free working capital",
        ))
    findings.append(Finding(
        "Buffers driven by net flow, not a forecast-only reorder point",
        "Each zone = ADU x DLT scaled by the lead-time and variability factors; execution "
        "follows the net-flow position (on-hand + on-order - qualified demand).",
        impact="decoupled, demand-driven replenishment",
    ))
    return tuple(findings)


def _deck_recommendations(report: DdmrpReport, over: list[DdmrpPart]) -> tuple[str, ...]:
    """The planner's next moves, in the order they have to happen."""
    proposal = report.proposal
    out = ["Issue replenishment orders for the red parts up to Top of Green."]
    if report.ambiguous_part_ids:
        out.append(
            f"De-duplicate (or key by plant/warehouse) the {len(report.ambiguous_part_ids)} "
            "repeated part_id(s) and re-run: they are buffered but no purchase order "
            "could be drafted for them."
        )
    if proposal.n_orders:
        out.append(
            f"Send the {proposal.n_orders} draft purchase order(s) to their suppliers; each line "
            "carries its need-by date and the reason it was raised."
        )
    if proposal.unsourced:
        out.append(
            f"Map a supplier for the {len(proposal.unsourced)} unsourced line(s) before releasing."
        )
    if over:
        out.append("Review parts above Top of Green for deferral / cancellation of open supply.")
    out.append("Re-profile buffers (LTF/VF) as lead times and demand variability shift.")
    return tuple(out)


def _summary(report: DdmrpReport) -> str:
    """The one line everybody reads, and it has to survive being read alone.

    ``n_order`` counts the parts the SIGNAL says to buy; the proposal can be
    smaller, because a part under a repeated id is buffered and deliberately not
    ordered. Left implicit, the summary promises N orders and the PO file delivers
    fewer -- so the gap is named in the same sentence that opens it, not only in
    the finding further down.
    """
    line = (
        f"Sized DDMRP buffers for {report.n_parts} parts: {report.n_red} in the red, "
        f"{report.n_order} need an order totaling {report.total_order_qty:,.0f} units."
    )
    blocked = set(report.ambiguous_part_ids)
    withheld = sum(1 for p in report.parts
                   if p.signal.order_recommended and p.part_id in blocked)
    if withheld:
        line += (
            f" {withheld} of those carry NO draft purchase order: their part_id is on "
            "more than one row, so no supplier or lead time resolves - see the findings."
        )
    return line


def build_deck(
    report: DdmrpReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the DDMRP buffer plan: what to order now and how the buffers are sized."""
    summary = _summary(report)
    over = [p for p in report.parts if p.signal.zone == "over_green"]

    kpis = (
        Kpi("Parts buffered", str(report.n_parts), rationale="Catalog coverage of the buffer plan"),
        Kpi("In the red", str(report.n_red), target="0", rationale="Parts needing an immediate order"),
        Kpi("Orders recommended", str(report.n_order), rationale="Parts at/below Top of Yellow"),
        Kpi("Total order quantity", f"{report.total_order_qty:,.0f}",
            rationale="Units to bring net flow back to Top of Green"),
        Kpi("Draft POs by supplier", str(report.proposal.n_orders),
            rationale="One dated draft order per supplier, ready for the buyer"),
    )

    data_sources = (
        DataSource("Part ADU / decoupled lead time / on-hand / on-order / qualified demand", "ERP / MRP data", "daily"),
        DataSource("Buffer-profile factors (LTF / VF / MOQ)", "engagement parameters", "per run"),
        DataSource("Primary supplier per part", "ERP purchasing master data", "per run"),
    )

    return Deliverable(
        title="DDMRP Buffer Plan",
        client=client,
        summary=summary,
        findings=_deck_findings(report, over),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=_deck_recommendations(report, over),
        citations=tuple(citations),
        confidence=confidence,
        residual="Issuing the replenishment orders (and adjusting buffer profiles) is the "
                 "planner's call; this sizes the buffers and ranks the execution priority.",
        prepared=prepared,
    )

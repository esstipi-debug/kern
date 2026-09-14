"""Dwell between canonical fulfillment hops. No pandas, no I/O.

A hop is the pair of adjacent ids in ``MILESTONE_IDS``. Missing an endpoint
makes that hop ``None`` -- never a skip to the next present clock. Skipping
would name a tramo nobody measured.

Sequence inversions are listed, not clamped to zero. The job's QA gate refuses
the deliverable when the list is not empty.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import median

from nosilo_core.guided import (
    HandoffPacket,
    Residual,
    as_executed,
    as_handoff,
)
from src.connectors.milestones import (
    MILESTONE_IDS,
    MilestoneCoverage,
    MilestoneEvent,
    parse_occurred_at,
    unavailable_coverage,
)

HOPS: tuple[tuple[str, str], ...] = tuple(zip(MILESTONE_IDS, MILESTONE_IDS[1:]))

_CUT_LEFT = "pack_end"
_CUT_RIGHT = "carrier_pickup"


@dataclass(frozen=True)
class HopDwell:
    """Aggregate dwell for one canonical hop across the orders that have both ends."""

    from_id: str
    to_id: str
    #: Median of the observed dwells, in seconds. ``None`` when nobody had both ends.
    p50_seconds: float | None
    unit: str  # "hours" | "days"
    n_orders: int
    n_missing: int
    mixed_clock: bool

    @property
    def p50(self) -> float | None:
        """Median in ``unit``. Empty hop stays ``None``, never 0."""
        if self.p50_seconds is None:
            return None
        if self.unit == "hours":
            return self.p50_seconds / 3600.0
        return self.p50_seconds / 86400.0


@dataclass(frozen=True)
class SequenceViolation:
    order_id: str
    earlier_id: str
    later_id: str
    earlier_at: str
    later_at: str


@dataclass(frozen=True)
class ChainScan:
    n_orders: int
    n_events: int
    complete_chain_n: int
    warehouse_transport_cut_n: int
    hops: tuple[HopDwell, ...]
    sequence_violations: tuple[SequenceViolation, ...]
    uninstrumented_late: int | None
    uninstrumented_late_scored: int
    mixed_clock_caveat: str
    coverage: MilestoneCoverage
    summary: str
    outcome: object
    measurable_hops: int


def _collapse(events: Sequence[MilestoneEvent]) -> dict[str, dict[str, MilestoneEvent]]:
    """Last (latest) event per order and milestone: the order is not done until the last line is."""
    by_order: dict[str, dict[str, MilestoneEvent]] = {}
    for event in events:
        slots = by_order.setdefault(event.order_id, {})
        current = slots.get(event.milestone_id)
        if current is None or event.instant > current.instant:
            slots[event.milestone_id] = event
    return by_order


def _seconds(left: datetime, right: datetime) -> float:
    return (right - left).total_seconds()


def scan_chain(
    events: Sequence[MilestoneEvent],
    *,
    promised: Mapping[str, str] | None = None,
    coverage: MilestoneCoverage | None = None,
) -> ChainScan:
    """Measure dwell per canonical hop. ``promised`` is ISO dates keyed by order_id."""
    promised = dict(promised or {})
    declared = coverage if coverage is not None else unavailable_coverage("unspecified")
    by_order = _collapse(events)
    order_ids = tuple(sorted(by_order))
    violations: list[SequenceViolation] = []
    hop_samples: dict[tuple[str, str], list[tuple[float, bool]]] = {hop: [] for hop in HOPS}
    hop_missing = {hop: 0 for hop in HOPS}
    mixed_any = False
    complete_n = 0
    cut_n = 0
    late_uninstrumented = 0
    late_scored = 0

    for oid in order_ids:
        slots = by_order[oid]
        if all(mid in slots for mid in MILESTONE_IDS):
            complete_n += 1
        if _CUT_LEFT in slots and _CUT_RIGHT in slots:
            cut_n += 1
        present = [mid for mid in MILESTONE_IDS if mid in slots]
        for earlier, later in zip(present, present[1:], strict=False):
            left, right = slots[earlier], slots[later]
            if right.instant < left.instant:
                violations.append(SequenceViolation(
                    oid, earlier, later, left.occurred_at, right.occurred_at,
                ))
        for hop in HOPS:
            a, b = hop
            if a in slots and b in slots:
                left, right = slots[a], slots[b]
                both_time = left.has_time and right.has_time
                if left.has_time != right.has_time:
                    mixed_any = True
                    both_time = False
                hop_samples[hop].append((_seconds(left.instant, right.instant), both_time))
            elif a in slots or b in slots:
                hop_missing[hop] += 1
        if oid in promised and "arrived" in slots:
            late_scored += 1
            try:
                due, _ = parse_occurred_at(promised[oid])
                got = slots["arrived"].instant
            except ValueError:
                continue
            if got.date() > due.date():
                if any(mid not in slots for mid in MILESTONE_IDS):
                    late_uninstrumented += 1

    hops: list[HopDwell] = []
    for hop in HOPS:
        samples = hop_samples[hop]
        if not samples:
            hops.append(HopDwell(
                hop[0], hop[1], None, "days", 0, hop_missing[hop], False,
            ))
            continue
        all_hours = all(has_time for _, has_time in samples)
        mixed = not all_hours and any(has_time for _, has_time in samples)
        if mixed:
            mixed_any = True
        hops.append(HopDwell(
            from_id=hop[0],
            to_id=hop[1],
            p50_seconds=float(median(sec for sec, _ in samples)),
            unit="hours" if all_hours else "days",
            n_orders=len(samples),
            n_missing=hop_missing[hop],
            mixed_clock=mixed,
        ))

    measurable = sum(1 for h in hops if h.p50_seconds is not None)
    caveat = ""
    if mixed_any:
        caveat = (
            "at least one hop mixed a date-only timestamp with a clock time; "
            "that hop is reported in days, not hours"
        )
    uninstrumented: int | None
    if not promised:
        uninstrumented = None
    else:
        uninstrumented = late_uninstrumented

    n_events = sum(len(v) for v in by_order.values())
    summary = _summary(
        n_orders=len(order_ids),
        measurable=measurable,
        complete_n=complete_n,
        uninstrumented=uninstrumented,
        late_scored=late_scored,
        violations=len(violations),
    )
    outcome = _outcome(summary, measurable=measurable, n_events=n_events, n_orders=len(order_ids))
    return ChainScan(
        n_orders=len(order_ids),
        n_events=n_events,
        complete_chain_n=complete_n,
        warehouse_transport_cut_n=cut_n,
        hops=tuple(hops),
        sequence_violations=tuple(violations),
        uninstrumented_late=uninstrumented,
        uninstrumented_late_scored=late_scored,
        mixed_clock_caveat=caveat,
        coverage=declared,
        summary=summary,
        outcome=outcome,
        measurable_hops=measurable,
    )


def _summary(
    *,
    n_orders: int,
    measurable: int,
    complete_n: int,
    uninstrumented: int | None,
    late_scored: int,
    violations: int,
) -> str:
    if n_orders == 0:
        return "No fulfillment events to scan."
    bits = [
        f"{n_orders} order(s), {measurable} hop(s) with both ends present, "
        f"{complete_n} with a complete six-milestone chain"
    ]
    if uninstrumented is None:
        bits.append("no promised date, so lateness is not scored")
    elif late_scored:
        bits.append(
            f"{uninstrumented} of {late_scored} late arrival(s) are missing at least "
            "one hop (uninstrumented, not attributed)"
        )
    else:
        bits.append("promised dates were present but no arrival to score lateness against")
    if violations:
        bits.append(f"{violations} sequence inversion(s)")
    return ". ".join(bits) + "."


def _outcome(summary: str, *, measurable: int, n_events: int, n_orders: int):
    if measurable > 0:
        residual = Residual(
            description=(
                "Dwell is a clock, not a cause. Warehouse vs carrier blame needs a "
                "signed definition pack."
            ),
            risk_if_skipped="A complete chain still has no owner for a late load.",
        )
        return as_executed(summary, confidence=0.7, residuals=[residual])
    if n_events == 0:
        return as_handoff(
            summary,
            [HandoffPacket(
                title="Supply a fulfillment-event file",
                steps=[
                    "Export order_id + milestone timestamps from WMS/TMS (long or wide).",
                    "Re-run fulfillment_chain on that file.",
                ],
                risk_if_skipped="OTIF stays a single clock with no hop to explain a miss.",
            )],
            confidence=0.2,
        )
    return as_handoff(
        summary,
        [HandoffPacket(
            title="Instrument the missing hops",
            steps=[
                "The file had events but no adjacent pair in the canonical chain.",
                "Add pack_end and load_end from the WMS, carrier_pickup from the TMS.",
            ],
            risk_if_skipped=(
                f"{n_orders} order(s) still have no measurable dwell; late deliveries "
                "stay unexplained by instrumentation."
            ),
        )],
        confidence=0.35,
    )

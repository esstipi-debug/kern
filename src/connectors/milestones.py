"""Hitos de cumplimiento: el reloj entre crear el pedido y que llegue.

``OrderLine`` solo sabe el extremo (prometido vs entregado). Este modulo nombra
los eventos del medio con el mismo contrato que ``SERVICE_COVERAGE``: cada hito
es measured, derived o unavailable, y ``means`` dice que reloj es. Un
``MilestoneSource`` no es un ``InventorySource``: un WMS no tiene precio ni
promesa, igual que un transportista no tiene catalogo.

El vocabulario es cerrado. Un campo que no mapea a uno de los seis ids no se
cuela como "hito extra"; o entra a este set o queda fuera del engine.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from src.connectors import (
    COVERAGE_MEASURED,
    COVERAGE_UNAVAILABLE,
    FieldCoverage,
)

MILESTONE_IDS: tuple[str, ...] = (
    "order_created",
    "released_to_warehouse",
    "pack_end",
    "load_end",
    "carrier_pickup",
    "arrived",
)

_MILESTONE_SET = frozenset(MILESTONE_IDS)

_DEFAULT_MEANS: dict[str, str] = {
    "order_created": "the instant the order exists for the process",
    "released_to_warehouse": "the warehouse can start preparing the order",
    "pack_end": "the goods are packed and ready for the dock",
    "load_end": "loading onto the vehicle finished",
    "carrier_pickup": "the carrier took the goods",
    "arrived": "the shipment arrived at the store or customer",
}


def parse_occurred_at(raw: str) -> tuple[datetime, bool]:
    """ISO date or datetime. The bool is whether the string carried a clock time.

    A date-only value becomes midnight, but ``has_time`` stays False so dwell
    never invents hours from a calendar day. ``Z`` is accepted as UTC and then
    dropped: dwell compares naive instants, not zones.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("occurred_at is empty: a milestone without a time is not an event")
    has_time = "T" in text or (len(text) > 10 and " " in text[10:])
    normalised = text.replace("Z", "+00:00")
    try:
        if has_time:
            moment = datetime.fromisoformat(normalised)
        else:
            moment = datetime.combine(date.fromisoformat(normalised[:10]), datetime.min.time())
    except ValueError as exc:
        raise ValueError(
            f"occurred_at {raw!r} is not ISO-8601 date or datetime"
        ) from exc
    if moment.tzinfo is not None:
        moment = moment.replace(tzinfo=None)
    return moment, has_time


@dataclass(frozen=True)
class MilestoneEvent:
    """One event that happened to one order, at one canonical milestone."""

    order_id: str
    milestone_id: str
    occurred_at: str
    source_system: str

    def __post_init__(self) -> None:
        if not str(self.order_id).strip():
            raise ValueError("order_id is empty")
        if self.milestone_id not in _MILESTONE_SET:
            raise ValueError(
                f"milestone_id {self.milestone_id!r} is not one of {MILESTONE_IDS}: "
                "the vocabulary is closed so a dwell hop has a name"
            )
        if not str(self.source_system).strip():
            raise ValueError(
                "source_system must name the clock (wms, tms, erp, or the file). "
                "An unnamed timestamp cannot be checked."
            )
        parse_occurred_at(self.occurred_at)

    @property
    def instant(self) -> datetime:
        return parse_occurred_at(self.occurred_at)[0]

    @property
    def has_time(self) -> bool:
        return parse_occurred_at(self.occurred_at)[1]


@dataclass(frozen=True)
class MilestoneCoverage:
    """Field-by-field answer to 'which fulfillment clocks can you report?'.

    All six ids are required. Omitting one is the claim this type exists to
    forbid: a backend that 'just doesn't mention' pack_end would look like a
    complete chain.
    """

    backend: str
    order_created: FieldCoverage
    released_to_warehouse: FieldCoverage
    pack_end: FieldCoverage
    load_end: FieldCoverage
    carrier_pickup: FieldCoverage
    arrived: FieldCoverage

    def fields(self) -> dict[str, FieldCoverage]:
        return {
            "order_created": self.order_created,
            "released_to_warehouse": self.released_to_warehouse,
            "pack_end": self.pack_end,
            "load_end": self.load_end,
            "carrier_pickup": self.carrier_pickup,
            "arrived": self.arrived,
        }


def _absent(backend: str, mid: str) -> FieldCoverage:
    return FieldCoverage(
        COVERAGE_UNAVAILABLE, "", f"{backend} does not record {_DEFAULT_MEANS[mid]}"
    )


def unavailable_coverage(backend: str) -> MilestoneCoverage:
    """Every hito absent. The honest declaration of an ERP that only has orders."""
    return MilestoneCoverage(
        backend=backend,
        order_created=_absent(backend, "order_created"),
        released_to_warehouse=_absent(backend, "released_to_warehouse"),
        pack_end=_absent(backend, "pack_end"),
        load_end=_absent(backend, "load_end"),
        carrier_pickup=_absent(backend, "carrier_pickup"),
        arrived=_absent(backend, "arrived"),
    )


def coverage_of_milestones(
    warehouse: MilestoneCoverage,
    carrier: MilestoneCoverage,
) -> MilestoneCoverage:
    """Compose two declarations. The same id from both is a conflict, not a min().

    Warehouse wins when it reports that id; otherwise the carrier. ``arrived``
    is usually the carrier. If both report the same id, merging would pick a
    clock in silence -- the caller must choose.
    """
    merged: dict[str, FieldCoverage] = {}
    conflicts: list[str] = []
    for mid in MILESTONE_IDS:
        left = warehouse.fields()[mid]
        right = carrier.fields()[mid]
        if left.reports and right.reports:
            conflicts.append(mid)
            continue
        merged[mid] = left if left.reports else right
    if conflicts:
        raise ValueError(
            "both sources report milestone(s) "
            + ", ".join(conflicts)
            + ": choose one clock per id, do not take the earlier timestamp"
        )
    return MilestoneCoverage(backend=f"{warehouse.backend} + {carrier.backend}", **merged)


@runtime_checkable
class MilestoneSource(Protocol):
    """A backend that knows when fulfillment events happened. And nothing else."""

    def milestones(self, *, since: str | None = None) -> list[MilestoneEvent]: ...


#: Fixture the contract tests run: one order, every hop measured. A module-level
#: declaration that the stand-in does not actually emit fails those tests.
MILESTONE_COVERAGE = MilestoneCoverage(
    backend="memory",
    order_created=FieldCoverage(
        COVERAGE_MEASURED, "memory.order_created", _DEFAULT_MEANS["order_created"]
    ),
    released_to_warehouse=FieldCoverage(
        COVERAGE_MEASURED, "memory.released_to_warehouse",
        _DEFAULT_MEANS["released_to_warehouse"],
    ),
    pack_end=FieldCoverage(COVERAGE_MEASURED, "memory.pack_end", _DEFAULT_MEANS["pack_end"]),
    load_end=FieldCoverage(COVERAGE_MEASURED, "memory.load_end", _DEFAULT_MEANS["load_end"]),
    carrier_pickup=FieldCoverage(
        COVERAGE_MEASURED, "memory.carrier_pickup", _DEFAULT_MEANS["carrier_pickup"]
    ),
    arrived=FieldCoverage(COVERAGE_MEASURED, "memory.arrived", _DEFAULT_MEANS["arrived"]),
)


def _demo_events() -> tuple[MilestoneEvent, ...]:
    return (
        MilestoneEvent("SO-1", "order_created", "2024-04-01T08:00:00", "memory"),
        MilestoneEvent("SO-1", "released_to_warehouse", "2024-04-01T10:00:00", "memory"),
        MilestoneEvent("SO-1", "pack_end", "2024-04-01T16:00:00", "memory"),
        MilestoneEvent("SO-1", "load_end", "2024-04-01T18:00:00", "memory"),
        MilestoneEvent("SO-1", "carrier_pickup", "2024-04-01T19:00:00", "memory"),
        MilestoneEvent("SO-1", "arrived", "2024-04-03T11:00:00", "memory"),
    )


class InMemoryMilestones:
    """Stand-in source. Used by tests the same way ``InMemoryOdoo`` is."""

    def __init__(
        self,
        events: Sequence[MilestoneEvent] | None = None,
        coverage: MilestoneCoverage | None = None,
    ) -> None:
        self._events = tuple(events) if events is not None else _demo_events()
        self.coverage = coverage if coverage is not None else MILESTONE_COVERAGE

    def milestones(self, *, since: str | None = None) -> list[MilestoneEvent]:
        if since is None:
            return list(self._events)
        floor = since[:10]
        return [e for e in self._events if e.occurred_at[:10] >= floor]

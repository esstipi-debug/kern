"""Slotting agent job: an order-lines CSV -> COI zone slot map + affinity co-location.

The data-prep + deck half of the slotting tool. Reads order lines (one row per order x SKU)
with pandas directly (deliberately *not* via jobs/intake.py, which the parallel loop owns)
and wires two engines:

- ``src.space``            : Cube-per-Order-Index slotting - assign each SKU to zone A/B/C by
                             storage cube per pick (fast / dense movers go to the closest zone).
- ``src.slotting_affinity``: co-location - SKUs frequently ordered together cluster so they
                             sit near each other to cut pick travel.

Pick frequency is the number of distinct orders containing the SKU. Storage cube is the SKU's
TOTAL stored volume: a ``required_space`` column taken as-is, or a per-unit ``unit_volume``
times the units on hand (``on_hand``) through ``src.space.required_space``. A per-unit volume
alone is not a stored cube: COI still ranks by it (a proxy), but warehouse utilization is only
reported when every SKU's stored cube is known -- summing one unit of each SKU against the
building's volume produced a number like 0.017% that looked like a measurement and was not.
With no volume column at all, the cube is a uniform 1.0 (so COI ranks by pick frequency).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.slotting_affinity import AffinityPair, affinity_pairs, co_location_groups
from src.space import SkuSlot, required_space, slot_skus, warehouse_utilization

_ORDER_COLS = ("order_id", "order", "invoice", "basket", "Order")
_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "Product", "product")
# Per-unit volume and total stored cube are different quantities; one alias list for both
# is how a WMS unit volume ended up summed as if it were the SKU's whole stored cube.
_UNIT_VOLUME_COLS = ("unit_volume", "volume", "cube", "Volume")
_TOTAL_CUBE_COLS = ("required_space",)
# Units stored, not units ordered: an order line's `quantity` is the line's pick quantity,
# so it is deliberately absent here.
_ONHAND_COLS = ("on_hand", "On Hand", "onhand", "units_on_hand", "stock_on_hand", "stock")

_TOP_PAIRS = 5


@dataclass(frozen=True)
class SlottingReport:
    n_skus: int
    n_orders: int
    slots: tuple[SkuSlot, ...]              # COI-ranked, zone assigned
    n_a: int
    n_b: int
    n_c: int
    co_location_groups: tuple[tuple[str, ...], ...]
    top_pairs: tuple[AffinityPair, ...]
    total_required_space: float
    utilization: float | None
    summary: str
    # Why utilization is None although a warehouse volume was given ("" otherwise).
    utilization_note: str = ""


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Build baskets + per-SKU pick frequency and cube from the order lines."""
    params = params or {}
    order = _pick_column(df, params.get("order_col"), _ORDER_COLS)
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    missing = [n for n, c in (("order_id", order), ("product_id", product)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    total_col = _pick_column(df, params.get("space_col"), _TOTAL_CUBE_COLS)
    unit_col = _pick_column(df, params.get("volume_col"), _UNIT_VOLUME_COLS)
    onhand_col = _pick_column(df, params.get("on_hand_col"), _ONHAND_COLS)
    baskets: dict[str, set[str]] = {}
    total_by_sku: dict[str, float] = {}
    unit_by_sku: dict[str, float] = {}
    onhand_by_sku: dict[str, float] = {}
    for _, row in df.iterrows():
        oid, sku = str(row[order]), str(row[product])
        baskets.setdefault(oid, set()).add(sku)
        for col, by_sku in ((total_col, total_by_sku), (unit_col, unit_by_sku), (onhand_col, onhand_by_sku)):
            if col and pd.notna(row[col]) and sku not in by_sku:
                by_sku[sku] = float(row[col])

    pick_freq: Counter[str] = Counter()
    for skus in baskets.values():
        pick_freq.update(skus)

    sku_records = []
    n_without_cube = 0
    for sku, freq in pick_freq.items():
        if sku in total_by_sku:
            cube = total_by_sku[sku]
        elif sku in unit_by_sku and sku in onhand_by_sku:
            # A negative on-hand is a book error, not negative shelf space.
            cube = required_space(unit_by_sku[sku], max(0.0, onhand_by_sku[sku]))
        else:
            # No stored cube: the unit volume (else 1.0) still ranks COI, but it is not a
            # measurement of space, so it must not feed utilization.
            cube = unit_by_sku.get(sku, 1.0)
            n_without_cube += 1
        sku_records.append({"product_id": sku, "required_space": cube, "pick_frequency": float(freq)})

    return {
        "skus": sku_records,
        "baskets": [sorted(s) for s in baskets.values()],
        "zone_cuts": tuple(params.get("zone_cuts", (0.2, 0.5))),
        "min_lift": float(params.get("min_lift", 1.0)),
        "warehouse_volume": params.get("warehouse_volume"),
        "cube_note": _cube_note(n_without_cube, len(sku_records), unit_col, onhand_col),
    }


def _cube_note(n_without: int, n_skus: int, unit_col: str | None, onhand_col: str | None) -> str:
    """Why the stored cube is unknown ("" when every SKU has one)."""
    if n_without == 0:
        return ""
    if unit_col and not onhand_col:
        return (f"'{unit_col}' is a per-unit volume and the file has no on-hand units column "
                f"(e.g. on_hand), so the stored cube of each SKU is unknown")
    if not unit_col and n_without == n_skus:
        return "the file has no volume column (unit_volume + on_hand, or required_space)"
    return f"{n_without} of {n_skus} SKU(s) lack a volume or on-hand value, so the stored cube is incomplete"


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read an order-lines CSV and build the slotting payload."""
    return prepare_records(pd.read_csv(data_path), params)


def run(payload: dict) -> SlottingReport:
    """Assign COI zones and find the affinity co-location clusters."""
    slots = slot_skus(payload["skus"], zone_cuts=payload["zone_cuts"])
    groups = co_location_groups(payload["baskets"], min_lift=payload["min_lift"])
    pairs = affinity_pairs(payload["baskets"])[:_TOP_PAIRS]
    zones = Counter(s.zone for s in slots)
    total_space = sum(s.required_space for s in slots)
    warehouse_volume = payload["warehouse_volume"]
    utilization = None
    utilization_note = ""
    if warehouse_volume:
        if payload["cube_note"]:
            utilization_note = f"Warehouse utilization not computed: {payload['cube_note']}."
        else:
            utilization = warehouse_utilization(total_space, float(warehouse_volume))
    summary = (
        f"Slotting over {len(slots)} SKU(s) / {len(payload['baskets'])} order(s): "
        f"{zones.get('A', 0)} A / {zones.get('B', 0)} B / {zones.get('C', 0)} C; "
        f"{len(groups)} co-location group(s)."
    )
    if utilization_note:
        summary = f"{summary} {utilization_note}"
    return SlottingReport(
        n_skus=len(slots), n_orders=len(payload["baskets"]), slots=tuple(slots),
        n_a=zones.get("A", 0), n_b=zones.get("B", 0), n_c=zones.get("C", 0),
        co_location_groups=tuple(tuple(g) for g in groups), top_pairs=tuple(pairs),
        total_required_space=total_space, utilization=utilization, summary=summary,
        utilization_note=utilization_note,
    )


def verify(report: SlottingReport) -> list[str]:
    """QA gate: SKUs present, every SKU zoned, utilization (if any) is a valid fraction."""
    import math

    issues: list[str] = []
    if report.n_skus <= 0:
        issues.append("no SKUs to slot")
    for s in report.slots:
        if s.zone not in ("A", "B", "C"):
            issues.append(f"{s.product_id}: invalid zone {s.zone}")
        if not math.isfinite(s.coi):
            issues.append(f"{s.product_id}: non-finite COI")
    if report.utilization is not None and report.utilization < 0:
        issues.append("negative utilization")
    return issues


def write_operational(report: SlottingReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the per-SKU slot map (COI order, zone)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "product_id": s.product_id,
            "zone": s.zone,
            "coi": round(s.coi, 4),
            "pick_frequency": round(s.pick_frequency, 1),
            "required_space": round(s.required_space, 3),
        }
        for s in report.slots
    ]
    return {"csv": write_summary_csv(rows, d / "slotting.csv")}


def build_deck(
    report: SlottingReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the slotting study: where each SKU goes and which SKUs to keep together."""
    util_txt = f", warehouse {report.utilization * 100:.0f}% utilized" if report.utilization is not None else ""
    summary = (
        f"Slot map over {report.n_skus} SKU(s) from {report.n_orders} order(s): "
        f"{report.n_a} in zone A (closest), {report.n_b} in B, {report.n_c} in C; "
        f"{len(report.co_location_groups)} co-location group(s){util_txt}."
    )

    findings = [
        Finding(
            "COI zone assignment",
            f"By cube-per-order index: {report.n_a} fast/dense SKU(s) to the closest zone A, "
            f"{report.n_b} to B, {report.n_c} to C.",
            impact="placing the lowest-COI SKUs forward cuts the most pick travel",
        ),
    ]
    if report.co_location_groups:
        sample = report.co_location_groups[0]
        findings.append(Finding(
            "Affinity co-location",
            f"{len(report.co_location_groups)} cluster(s) of SKUs ordered together "
            f"(e.g. {', '.join(sample[:4])}) should sit adjacent.",
            impact="co-locating frequently co-ordered SKUs cuts multi-line pick travel",
        ))
    if report.utilization is not None:
        findings.append(Finding(
            "Space utilization",
            f"Stored cube is {report.utilization * 100:.0f}% of the usable warehouse volume.",
            impact="headroom for growth or a flag to densify / expand",
        ))
    elif report.utilization_note:
        findings.append(Finding(
            "Space utilization not measured",
            report.utilization_note,
            impact="send units on hand per SKU (or its total stored cube) to measure how full the warehouse is",
        ))

    kpis = [
        Kpi("SKUs", f"{report.n_skus}", rationale="SKUs slotted"),
        Kpi("Orders analyzed", f"{report.n_orders}", rationale="Order history behind the pick frequencies"),
        Kpi("Zone A SKUs", f"{report.n_a}", target="-", rationale="Fast/dense movers in the closest zone"),
        Kpi("Co-location groups", f"{len(report.co_location_groups)}", target="-",
            rationale="Affinity clusters to keep adjacent"),
    ]
    if report.utilization is not None:
        kpis.append(Kpi("Warehouse utilization", f"{report.utilization * 100:.0f}%", target="balance",
                        rationale="Stored cube vs usable volume"))

    data_sources = (
        DataSource("Order lines (order id + SKU, optional unit volume + units on hand)", "WMS / order history",
                   "rolling window"),
    )

    recommendations = (
        "Move the zone-A SKUs to the closest, most accessible pick faces first.",
        "Co-locate the affinity clusters so multi-line orders are picked in fewer steps.",
        "Re-slot on a cadence - pick frequency and the order mix drift over time.",
    )

    return Deliverable(
        title="Warehouse Slotting (COI + Affinity)",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=tuple(kpis),
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual="Physical re-slotting is a human act: the agent delivers the slot map and the "
                 "co-location move list to approve and schedule - it does not move stock. Confirm "
                 "slot dimensions and equipment reach before relocating.",
        prepared=prepared,
    )

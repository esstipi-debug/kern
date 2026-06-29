"""Slotting agent job: an order-lines CSV -> COI zone slot map + affinity co-location.

The data-prep + deck half of the slotting tool. Reads order lines (one row per order x SKU)
with pandas directly (deliberately *not* via jobs/intake.py, which the parallel loop owns)
and wires two engines:

- ``src.space``            : Cube-per-Order-Index slotting - assign each SKU to zone A/B/C by
                             storage cube per pick (fast / dense movers go to the closest zone).
- ``src.slotting_affinity``: co-location - SKUs frequently ordered together cluster so they
                             sit near each other to cut pick travel.

Pick frequency is the number of distinct orders containing the SKU; storage cube comes from a
``unit_volume`` column when present, else a uniform 1.0 (so COI ranks by pick frequency).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.slotting_affinity import AffinityPair, affinity_pairs, co_location_groups
from src.space import SkuSlot, slot_skus, warehouse_utilization

_ORDER_COLS = ("order_id", "order", "invoice", "basket", "Order")
_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "Product", "product")
_VOLUME_COLS = ("unit_volume", "volume", "required_space", "cube", "Volume")

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

    volume = _pick_column(df, params.get("volume_col"), _VOLUME_COLS)
    baskets: dict[str, set[str]] = {}
    volume_by_sku: dict[str, float] = {}
    for _, row in df.iterrows():
        oid, sku = str(row[order]), str(row[product])
        baskets.setdefault(oid, set()).add(sku)
        if volume and pd.notna(row[volume]) and sku not in volume_by_sku:
            volume_by_sku[sku] = float(row[volume])

    pick_freq: Counter[str] = Counter()
    for skus in baskets.values():
        pick_freq.update(skus)

    sku_records = [
        {"product_id": sku, "required_space": volume_by_sku.get(sku, 1.0), "pick_frequency": float(freq)}
        for sku, freq in pick_freq.items()
    ]
    return {
        "skus": sku_records,
        "baskets": [sorted(s) for s in baskets.values()],
        "zone_cuts": tuple(params.get("zone_cuts", (0.2, 0.5))),
        "min_lift": float(params.get("min_lift", 1.0)),
        "warehouse_volume": params.get("warehouse_volume"),
    }


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
    utilization = (
        warehouse_utilization(total_space, float(warehouse_volume)) if warehouse_volume else None
    )
    summary = (
        f"Slotting over {len(slots)} SKU(s) / {len(payload['baskets'])} order(s): "
        f"{zones.get('A', 0)} A / {zones.get('B', 0)} B / {zones.get('C', 0)} C; "
        f"{len(groups)} co-location group(s)."
    )
    return SlottingReport(
        n_skus=len(slots), n_orders=len(payload["baskets"]), slots=tuple(slots),
        n_a=zones.get("A", 0), n_b=zones.get("B", 0), n_c=zones.get("C", 0),
        co_location_groups=tuple(tuple(g) for g in groups), top_pairs=tuple(pairs),
        total_required_space=total_space, utilization=utilization, summary=summary,
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
        DataSource("Order lines (order id + SKU, optional unit volume)", "WMS / order history", "rolling window"),
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

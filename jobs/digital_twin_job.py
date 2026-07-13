"""Digital twin agent job: params -> simulated network -> datasets that feed the suite.

The scenario factory for the whole engine. ``prepare()`` turns plain params
(store/DC counts, demand shape, an optional disruption) into a network spec;
``run()`` simulates every product through ``src.digital_twin``; the deliverable
is the DATA ITSELF - demand history, inventory and order traces shaped exactly
like a client CSV export - plus a KPI readout of how the simulated network
performed. Feed the demand CSV straight into forecasting / safety stock /
policy tools to exercise them on a scenario whose ground truth is known.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.digital_twin import DemandProfile, Disruption, NodeSpec, simulate_network
from src.export import write_summary_csv

_DEFAULT_PERIODS = 364
_MIN_PERIODS, _MAX_PERIODS = 30, 5_000
_MAX_CELLS = 2_000_000  # n_products * n_stores * periods guardrail
_DISRUPTION_KINDS = ("supplier_outage", "lead_time_spike", "demand_surge")
_START_DATE = "2025-01-06"


@dataclass(frozen=True)
class NodeKpi:
    name: str
    kind: str
    fill_rate: float
    stockout_periods: int
    mean_on_hand: float


@dataclass(frozen=True)
class TwinReport:
    """One simulated scenario across all products, with exportable traces."""

    n_products: int
    n_stores: int
    n_dcs: int
    periods: int
    network_fill_rate: float
    weakest_store: str
    weakest_store_fill: float
    total_mean_on_hand: float
    disruption: str
    node_kpis: tuple[NodeKpi, ...]
    demand_rows: tuple[tuple[int, str, str, float], ...]   # period, product, location, units
    inventory_rows: tuple[tuple[int, str, str, float], ...]
    order_rows: tuple[tuple[int, str, str, float], ...]
    summary: str


def _int_param(params: dict, key: str, default: int, lo: int, hi: int) -> int:
    value = int(params.get(key, default))
    if not lo <= value <= hi:
        raise ValueError(f"{key} must be in [{lo}, {hi}], got {value}")
    return value


def prepare(params: dict | None = None) -> dict:
    """Build the network + demand + disruption payload from plain params."""
    params = params or {}
    n_stores = _int_param(params, "n_stores", 3, 1, 20)
    n_dcs = _int_param(params, "n_dcs", 1, 1, 5)
    n_products = _int_param(params, "n_products", 5, 1, 50)
    periods = _int_param(params, "periods", _DEFAULT_PERIODS, _MIN_PERIODS, _MAX_PERIODS)
    if n_products * n_stores * periods > _MAX_CELLS:
        raise ValueError(
            f"scenario too large: n_products * n_stores * periods must stay under "
            f"{_MAX_CELLS:,} (got {n_products * n_stores * periods:,})"
        )
    seed = int(params.get("seed", 42))
    base = float(params.get("base_demand", 100.0))
    if base <= 0:
        raise ValueError("base_demand must be > 0")

    store_lead = _int_param(params, "store_lead_time", 2, 0, 60)
    dc_lead = _int_param(params, "dc_lead_time", 7, 0, 120)
    review = _int_param(params, "review_period", 1, 1, 30)

    nodes: list[dict] = [{"name": "SUPPLIER-1", "kind": "supplier", "supplier": None,
                          "lead_time": 0, "review_period": 1}]
    for d in range(n_dcs):
        nodes.append({
            "name": f"DC-{d + 1}", "kind": "dc", "supplier": "SUPPLIER-1",
            "lead_time": dc_lead, "review_period": review,
            "capacity": params.get("dc_capacity"),
        })
    for s in range(n_stores):
        nodes.append({
            "name": f"STORE-{s + 1}", "kind": "store",
            "supplier": f"DC-{(s % n_dcs) + 1}",
            "lead_time": store_lead, "review_period": review,
            "capacity": params.get("store_capacity"),
        })

    # ABC-like spread of demand across products, deterministic from the seed
    import numpy as np

    rng = np.random.default_rng(seed)
    factors = np.sort(rng.lognormal(mean=0.0, sigma=0.7, size=n_products))[::-1]
    products = [
        {"product_id": f"SKU-{i + 1:03d}", "base": round(base * float(f), 2)}
        for i, f in enumerate(factors)
    ]

    demand = {
        "trend": float(params.get("trend", 0.0)),
        "season_amplitude": float(params.get("season_amplitude", 0.25)),
        "season_period": _int_param(params, "season_period", 52, 2, 730),
        "promo_every": _int_param(params, "promo_every", 0, 0, 365),
        "promo_length": _int_param(params, "promo_length", 1, 1, 60),
        "promo_uplift": float(params.get("promo_uplift", 0.5)),
        "noise_cv": float(params.get("noise_cv", 0.25)),
        "zero_prob": float(params.get("zero_prob", 0.0)),
    }

    disruptions: list[dict] = []
    kind = params.get("disruption")
    if kind is not None:
        if kind not in _DISRUPTION_KINDS:
            raise ValueError(f"disruption must be one of {_DISRUPTION_KINDS}, got {kind!r}")
        default_target = "STORE-1" if kind == "demand_surge" else (
            "SUPPLIER-1" if kind == "supplier_outage" else "DC-1"
        )
        disruptions.append({
            "kind": kind,
            "target": str(params.get("disruption_target", default_target)),
            "start": _int_param(params, "disruption_start", max(periods // 3, 1), 0, periods - 1),
            "duration": _int_param(params, "disruption_duration",
                                   max(periods // 10, 1), 1, periods),
            "magnitude": float(params.get("disruption_magnitude", 2.0)),
        })

    return {
        "nodes": nodes, "products": products, "periods": periods, "demand": demand,
        "disruptions": disruptions, "seed": seed,
        "auto_size_factor": float(params.get("auto_size_factor", 1.5)),
    }


def run(payload: dict) -> TwinReport:
    """Simulate every product through the network and collect exportable traces."""
    specs = [NodeSpec(
        name=n["name"], kind=n["kind"], supplier=n["supplier"],
        lead_time=int(n["lead_time"]), review_period=int(n["review_period"]),
        capacity=(float(n["capacity"]) if n.get("capacity") is not None else None),
    ) for n in payload["nodes"]]
    d = payload["demand"]
    disruptions = tuple(
        Disruption(kind=x["kind"], target=x["target"], start=int(x["start"]),
                   duration=int(x["duration"]), magnitude=float(x["magnitude"]))
        for x in payload["disruptions"]
    )
    periods = payload["periods"]

    demand_rows: list[tuple[int, str, str, float]] = []
    inventory_rows: list[tuple[int, str, str, float]] = []
    order_rows: list[tuple[int, str, str, float]] = []
    total_demand = total_served = 0.0
    store_totals: dict[str, list[float]] = {}
    node_agg: dict[str, list[float]] = {}
    kinds: dict[str, str] = {}

    for idx, product in enumerate(payload["products"]):
        profile = DemandProfile(
            base=product["base"], trend=d["trend"],
            season_amplitude=d["season_amplitude"], season_period=d["season_period"],
            promo_every=d["promo_every"], promo_length=d["promo_length"],
            promo_uplift=d["promo_uplift"],
            noise_std=product["base"] * d["noise_cv"], zero_prob=d["zero_prob"],
        )
        result = simulate_network(
            specs, profile, periods=periods, disruptions=disruptions,
            seed=payload["seed"] + idx, auto_size_factor=payload["auto_size_factor"],
        )
        pid = product["product_id"]
        for store, trace in result.demand.items():
            served = result.served[store]
            demand_rows.extend(
                (t, pid, store, round(float(trace[t]), 2)) for t in range(periods)
            )
            agg = store_totals.setdefault(store, [0.0, 0.0])
            agg[0] += float(trace.sum())
            agg[1] += float(served.sum())
            total_demand += float(trace.sum())
            total_served += float(served.sum())
        for node, trace in result.on_hand.items():
            inventory_rows.extend(
                (t, pid, node, round(float(trace[t]), 2)) for t in range(periods)
            )
        for node, orders in result.orders.items():
            order_rows.extend((t, pid, node, round(qty, 2)) for t, qty in orders)
        for stats in result.nodes:
            kinds[stats.name] = stats.kind
            agg = node_agg.setdefault(stats.name, [0.0, 0.0, 0.0, 0.0])
            agg[0] += stats.fill_rate
            agg[1] += stats.stockout_periods
            agg[2] += stats.mean_on_hand
            agg[3] += 1

    n_products = len(payload["products"])
    node_kpis = tuple(
        NodeKpi(name=name, kind=kinds[name],
                fill_rate=round(agg[0] / agg[3], 4),
                stockout_periods=int(agg[1]),
                mean_on_hand=round(agg[2], 2))
        for name, agg in node_agg.items()
    )
    store_fills = {
        s: (v[1] / v[0] if v[0] > 0 else 1.0) for s, v in store_totals.items()
    }
    weakest = min(store_fills, key=store_fills.get)
    network_fill = (total_served / total_demand) if total_demand > 0 else 1.0
    disruption_label = (
        payload["disruptions"][0]["kind"] if payload["disruptions"] else "none"
    )
    n_stores = sum(1 for k in kinds.values() if k == "store")
    n_dcs = sum(1 for k in kinds.values() if k == "dc")
    summary = (
        f"Digital twin simulated {n_products} product(s) across "
        f"{n_stores} store(s) / {n_dcs} DC(s) for {periods} periods "
        f"(disruption: {disruption_label}): network fill {network_fill * 100:.1f}%, "
        f"weakest store {weakest} at {store_fills[weakest] * 100:.1f}%."
    )
    return TwinReport(
        n_products=n_products, n_stores=n_stores, n_dcs=n_dcs, periods=periods,
        network_fill_rate=network_fill, weakest_store=weakest,
        weakest_store_fill=store_fills[weakest],
        total_mean_on_hand=round(sum(k.mean_on_hand for k in node_kpis), 2),
        disruption=disruption_label, node_kpis=node_kpis,
        demand_rows=tuple(demand_rows), inventory_rows=tuple(inventory_rows),
        order_rows=tuple(order_rows), summary=summary,
    )


def verify(report: TwinReport) -> list[str]:
    """QA gate: rates are valid fractions, traces exist and stay non-negative."""
    import math

    issues: list[str] = []
    if not report.demand_rows:
        issues.append("no demand rows generated")
    if not 0.0 <= report.network_fill_rate <= 1.0:
        issues.append(f"network fill rate out of [0,1]: {report.network_fill_rate}")
    if not math.isfinite(report.total_mean_on_hand) or report.total_mean_on_hand < 0:
        issues.append("invalid total mean on-hand")
    for kpi in report.node_kpis:
        if not 0.0 <= kpi.fill_rate <= 1.0:
            issues.append(f"{kpi.name}: fill rate out of [0,1]: {kpi.fill_rate}")
        if kpi.mean_on_hand < 0:
            issues.append(f"{kpi.name}: negative mean on-hand")
    if any(units < 0 for _, _, _, units in report.demand_rows):
        issues.append("negative demand units in trace")
    return issues


def _dates(periods: pd.Series) -> pd.Series:
    start = pd.Timestamp(_START_DATE)
    return periods.map(lambda t: (start + pd.Timedelta(weeks=int(t))).date().isoformat())


def write_operational(report: TwinReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the generated datasets themselves."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    frames = {
        "demand_history": ("units", report.demand_rows),
        "inventory": ("on_hand", report.inventory_rows),
        "orders": ("order_qty", report.order_rows),
    }
    for key, (value_col, rows) in frames.items():
        df = pd.DataFrame(rows, columns=["period", "product_id", "location", value_col])
        df.insert(0, "date", _dates(df.pop("period")))
        path = d / f"twin_{key}.csv"
        df.to_csv(path, index=False)
        paths[key] = path

    kpi_rows = [
        {"location": k.name, "kind": k.kind, "fill_rate": k.fill_rate,
         "stockout_periods": k.stockout_periods, "mean_on_hand": k.mean_on_hand}
        for k in report.node_kpis
    ]
    paths["node_kpis"] = write_summary_csv(kpi_rows, d / "twin_node_kpis.csv")
    return paths


def build_deck(
    report: TwinReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.8,
) -> Deliverable:
    """Compose the scenario study: how the simulated network performed and what to feed next."""
    summary = (
        f"Digital twin of a {report.n_stores}-store / {report.n_dcs}-DC network over "
        f"{report.periods} periods and {report.n_products} product(s) "
        f"(disruption: {report.disruption}): network fill {report.network_fill_rate * 100:.1f}%, "
        f"weakest store {report.weakest_store} at {report.weakest_store_fill * 100:.1f}%. "
        f"The generated demand/inventory/order datasets are ready to feed the analysis suite."
    )

    findings = [
        Finding(
            "Network service level",
            f"End-customer fill rate {report.network_fill_rate * 100:.1f}% across "
            f"{report.n_stores} store(s) under the simulated scenario.",
            impact="the baseline any policy change must beat",
        ),
        Finding(
            "Weakest link",
            f"{report.weakest_store} serves only {report.weakest_store_fill * 100:.1f}% "
            f"of its demand - the network's service floor.",
            impact="where safety stock or lead-time work pays off first",
        ),
    ]
    if report.disruption != "none":
        findings.append(Finding(
            "Disruption scenario",
            f"A {report.disruption} shock was injected mid-horizon; the KPIs above "
            f"already include its ripple through the echelons.",
            impact="quantifies resilience before it happens in the real network",
        ))

    kpis = (
        Kpi("Products", f"{report.n_products}", rationale="Products simulated through the network"),
        Kpi("Horizon", f"{report.periods} periods", rationale="Simulated planning horizon"),
        Kpi("Network fill rate", f"{report.network_fill_rate * 100:.1f}%", target="maximize",
            rationale="Units served / demanded at the shelf, lost-sales basis"),
        Kpi("Weakest store fill", f"{report.weakest_store_fill * 100:.1f}%", target="maximize",
            rationale=f"Service floor ({report.weakest_store})"),
        Kpi("Mean network inventory", f"{report.total_mean_on_hand:,.0f}", target="minimize",
            rationale="Average units held across DCs and stores"),
    )

    data_sources = (
        DataSource("Network topology + policies", "Twin parameters (or defaults)", "per scenario"),
        DataSource("Demand pattern + disruption", "Twin parameters", "per scenario"),
    )

    recommendations = (
        "Feed twin_demand_history.csv to the forecasting tool to benchmark methods on known ground truth.",
        "Run safety stock / policy tools on the weakest store's series and re-simulate to verify the fix.",
        "Re-run the twin with harsher disruptions to find the network's breaking point before reality does.",
    )

    return Deliverable(
        title="Digital Twin - Network Scenario Study",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual="Synthetic scenario: the twin reproduces structure (echelons, policies, "
                 "disruptions), not any specific client's history. Calibrate base demand, "
                 "lead times and review periods to the client's numbers before treating "
                 "service levels as predictions.",
        prepared=prepared,
    )

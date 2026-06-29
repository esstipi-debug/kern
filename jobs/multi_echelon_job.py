"""Multi-echelon (serial GSM) agent job: a stage CSV -> safety-stock placement.

The data-prep + deck half of the multi-echelon tool. Reads a serial supply chain (one row
per stage, ordered upstream -> downstream, the end customer at the last stage) with pandas
directly (deliberately *not* via jobs/intake.py, which the parallel loop owns) and finds the
cost-minimizing placement of safety stock across the echelons via the Guaranteed-Service
Model (``src.multi_echelon``, Vandeput ch.10): per-stage safety stock, local + echelon
order-up-to levels, and total holding cost. Optionally simulates the achieved fill rate.

End-customer demand (mean + std) is taken from columns when present, else from params; the
cycle service level and review period come from params (defaults 0.95 / 1.0).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.multi_echelon import optimize_serial_gsm, simulate_serial_gsm

_STAGE_COLS = ("stage", "echelon", "node", "name", "location", "Stage")
_ORDER_COLS = ("order", "sequence", "stage_order", "index", "position")
_LEAD_COLS = ("lead_time", "lead_time_days", "lead", "lt", "Lead Time")
_HOLDING_COLS = ("holding_cost", "holding", "holding_cost_per_unit", "h", "carry_cost")
_MEAN_COLS = ("mean_demand", "demand_mean", "mean", "avg_demand", "demand")
_STD_COLS = ("demand_std", "std_demand", "std", "sigma", "demand_sigma")

_DEFAULT_SERVICE_LEVEL = 0.95
_DEFAULT_REVIEW_PERIOD = 1.0
_SIM_PERIODS = 2000


@dataclass(frozen=True)
class EchelonLine:
    name: str
    lead_time: float
    holding_cost: float
    risk_period: float
    safety_stock: float
    order_up_to: float
    echelon_order_up_to: float
    holds_safety_stock: bool


@dataclass(frozen=True)
class MultiEchelonReport:
    n_stages: int
    stages: tuple[EchelonLine, ...]        # upstream -> downstream
    total_holding_cost: float
    service_level: float
    mean_demand: float
    demand_std: float
    stocking_stage_names: tuple[str, ...]  # stages that hold safety stock
    n_stocking: int
    achieved_fill_rate: float | None       # from the optional simulation
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _first_value(df: pd.DataFrame, col: str | None) -> float | None:
    if col is None:
        return None
    series = df[col].dropna()
    return float(series.iloc[0]) if not series.empty else None


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Build the ordered serial chain + the chain-level demand/service parameters."""
    params = params or {}
    stage = _pick_column(df, params.get("stage_col"), _STAGE_COLS)
    lead = _pick_column(df, params.get("lead_col"), _LEAD_COLS)
    holding = _pick_column(df, params.get("holding_col"), _HOLDING_COLS)
    missing = [n for n, c in (("stage", stage), ("lead_time", lead), ("holding_cost", holding)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    ordered = df
    order_col = _pick_column(df, params.get("order_col"), _ORDER_COLS)
    if order_col is not None:
        ordered = df.sort_values(order_col, kind="stable")

    stages = [
        {"name": str(row[stage]), "lead_time": float(row[lead]), "holding_cost": float(row[holding])}
        for _, row in ordered.iterrows()
    ]

    mean_demand = _first_value(df, _pick_column(df, params.get("mean_col"), _MEAN_COLS))
    demand_std = _first_value(df, _pick_column(df, params.get("std_col"), _STD_COLS))
    if mean_demand is None:
        mean_demand = params.get("mean_demand")
    if demand_std is None:
        demand_std = params.get("demand_std")
    if mean_demand is None or demand_std is None:
        raise ValueError(
            "end-customer demand is required: add 'mean_demand' + 'demand_std' columns "
            "or pass them in params"
        )

    return {
        "stages": stages,
        "mean_demand": float(mean_demand),
        "demand_std": float(demand_std),
        "service_level": float(params.get("service_level", _DEFAULT_SERVICE_LEVEL)),
        "review_period": float(params.get("review_period", _DEFAULT_REVIEW_PERIOD)),
    }


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a serial-chain CSV and build the multi-echelon payload."""
    return prepare_records(pd.read_csv(data_path), params)


def _simulate_fill_rate(allocation, lead_times: list[float], review_period: float,
                        mean_demand: float, demand_std: float) -> float | None:
    """Best-effort achieved-fill-rate check; integer lead times only, never fatal."""
    try:
        int_lead = [max(1, int(round(lt))) for lt in lead_times]
        result = simulate_serial_gsm(
            allocation, int_lead, review_period=max(1, int(round(review_period))),
            periods=_SIM_PERIODS, mean_demand=mean_demand, std_demand=demand_std, seed=42,
        )
        return result.fill_rate
    except Exception:
        return None


def run(payload: dict) -> MultiEchelonReport:
    """Optimize the safety-stock placement across the serial chain and roll up the plan."""
    stages = payload["stages"]
    lead_times = [s["lead_time"] for s in stages]
    holding = [s["holding_cost"] for s in stages]
    # Keep the service level inside the open interval the normal inverse-CDF needs.
    service_level = min(max(payload["service_level"], 0.5), 0.999)
    mean_demand = payload["mean_demand"]
    demand_std = payload["demand_std"]
    review_period = payload["review_period"]

    allocation = optimize_serial_gsm(
        lead_times, mean_demand, demand_std, holding, service_level, review_period,
    )

    lines: list[EchelonLine] = []
    for stage, node, echelon in zip(stages, allocation.nodes, allocation.echelon_order_up_to):
        lines.append(EchelonLine(
            name=stage["name"], lead_time=node.lead_time, holding_cost=node.holding_cost,
            risk_period=node.risk_period, safety_stock=node.safety_stock,
            order_up_to=node.order_up_to, echelon_order_up_to=echelon,
            holds_safety_stock=node.risk_period > 0,
        ))

    stocking = tuple(ln.name for ln in lines if ln.holds_safety_stock)
    fill = _simulate_fill_rate(allocation, lead_times, review_period, mean_demand, demand_std)
    summary = (
        f"Serial multi-echelon over {len(lines)} stage(s): hold safety stock at "
        f"{len(stocking)} stage(s) for {allocation.total_holding_cost:,.0f} holding cost "
        f"at {service_level * 100:.0f}% service."
    )
    return MultiEchelonReport(
        n_stages=len(lines), stages=tuple(lines),
        total_holding_cost=allocation.total_holding_cost, service_level=service_level,
        mean_demand=mean_demand, demand_std=demand_std,
        stocking_stage_names=stocking, n_stocking=len(stocking),
        achieved_fill_rate=fill, summary=summary,
    )


def verify(report: MultiEchelonReport) -> list[str]:
    """QA gate: stages present, costs/levels finite and non-negative, service a valid fraction."""
    import math

    issues: list[str] = []
    if report.n_stages <= 0:
        issues.append("no stages to optimize")
    if not 0.0 <= report.service_level <= 1.0:
        issues.append(f"service level out of [0,1]: {report.service_level}")
    if not math.isfinite(report.total_holding_cost) or report.total_holding_cost < 0:
        issues.append("total holding cost is negative or non-finite")
    for ln in report.stages:
        if ln.safety_stock < 0 or not math.isfinite(ln.safety_stock):
            issues.append(f"{ln.name}: invalid safety stock")
    return issues


def write_operational(report: MultiEchelonReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the per-stage base-stock plan (upstream -> downstream)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "stage": ln.name,
            "lead_time": round(ln.lead_time, 2),
            "holding_cost": round(ln.holding_cost, 4),
            "safety_stock": round(ln.safety_stock, 1),
            "order_up_to": round(ln.order_up_to, 1),
            "echelon_order_up_to": round(ln.echelon_order_up_to, 1),
            "holds_safety_stock": "yes" if ln.holds_safety_stock else "",
        }
        for ln in report.stages
    ]
    return {"csv": write_summary_csv(rows, d / "multi_echelon.csv")}


def build_deck(
    report: MultiEchelonReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the multi-echelon study: where to hold safety stock across the chain and why."""
    placement = ", ".join(report.stocking_stage_names) if report.stocking_stage_names else "none"
    summary = (
        f"Serial multi-echelon network over {report.n_stages} stage(s): the cost-minimizing "
        f"placement holds safety stock at {report.n_stocking} stage(s) ({placement}) for "
        f"{report.total_holding_cost:,.0f} holding cost at {report.service_level * 100:.0f}% service."
    )

    findings = [
        Finding(
            "Safety-stock placement (GSM)",
            f"Hold safety stock at: {placement}. Total holding cost {report.total_holding_cost:,.0f}.",
            impact="placing stock at the cheapest effective stage minimizes network holding cost",
        ),
        Finding(
            "Base-stock levels per stage",
            "Each stage gets a local and an echelon order-up-to level - see the plan CSV.",
            impact="run each stage to its echelon target to hold the chain-wide policy",
        ),
    ]
    if report.achieved_fill_rate is not None:
        findings.append(Finding(
            "Simulated service achieved",
            f"A {report.achieved_fill_rate * 100:.1f}% fill rate at the recommended base stocks "
            f"(target {report.service_level * 100:.0f}%).",
            impact="validates the analytic placement against simulated demand",
        ))

    kpis = [
        Kpi("Stages", f"{report.n_stages}", rationale="Echelons in the serial chain"),
        Kpi("Stocking stages", f"{report.n_stocking}", target="minimize",
            rationale="Stages that hold safety stock (fewer = more pooled)"),
        Kpi("Total holding cost", f"{report.total_holding_cost:,.0f}", target="minimize",
            rationale="Network safety-stock holding cost at the chosen placement"),
        Kpi("Service level", f"{report.service_level * 100:.0f}%", target="balance",
            rationale="Cycle service level the placement is sized to"),
    ]
    if report.achieved_fill_rate is not None:
        kpis.append(Kpi("Simulated fill rate", f"{report.achieved_fill_rate * 100:.1f}%", target="maximize",
                        rationale="Fill rate from the base-stock simulation"))

    data_sources = (
        DataSource("Serial chain (stage, lead time, holding cost)", "Network / cost master", "per network change"),
        DataSource("End-customer demand mean/std", "Demand history / S&OP forecast", "per planning cycle"),
    )

    recommendations = (
        "Run each stage to its echelon order-up-to level to hold the network-wide policy.",
        "Re-optimize when a stage's lead time or holding cost changes - placement is sensitive to both.",
        "Pool safety stock at the cheapest effective stage where lead times allow.",
    )

    return Deliverable(
        title="Multi-Echelon Safety-Stock Placement",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=tuple(kpis),
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual="Serial-chain model: confirm the stage order (upstream -> downstream), each "
                 "stage's holding cost, and the end-customer demand mean/std before committing "
                 "base-stock levels; the model assumes a single serial path, not a divergent network.",
        prepared=prepared,
    )

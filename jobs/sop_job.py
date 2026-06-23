"""S&OP agent job: demand history -> monthly horizon -> S&OP cadence review.

The data-prep half of the S&OP tool. Periodizes a demand/order history into a monthly
demand horizon using pandas directly (deliberately *not* via jobs/intake.py, which the
parallel loop owns), then runs the S&OP cadence. The deck stays in
``jobs/sop_deliverable`` and ``write_operational`` writes the per-period plan CSV.

Note: in a full engagement the demand horizon is the *forward* consensus forecast; here
the history is periodized and used as the plan, which is the right shape for the cadence
and keeps the tool usable on the data a client actually has.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.export import write_summary_csv
from src.guided import verify_guided
from src.sop import CostModel, SopReview, run_sop_cycle

_DATE_COLS = ("Order Date", "order_date", "Date", "date", "ds", "period", "Period")
_DEMAND_COLS = ("Quantity", "quantity", "demand", "Demand", "Sales", "sales", "units", "qty", "y")


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a demand/order CSV and roll it into a periodic demand horizon.

    Raises ``FileNotFoundError`` if the file is missing and ``ValueError`` (naming the
    params to set, or that more history is needed) on a malformed input.
    """
    params = params or {}
    df = pd.read_csv(data_path)

    date_col = _pick_column(df, params.get("date_col"), _DATE_COLS)
    demand_col = _pick_column(df, params.get("demand_col"), _DEMAND_COLS)
    missing = [name for name, col in (("date_col", date_col), ("demand_col", demand_col)) if col is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col])
    freq = str(params.get("freq", "MS"))  # month-start buckets by default
    series = df.set_index(date_col)[demand_col].resample(freq).sum().fillna(0.0)

    demand = [float(x) for x in series.to_numpy()]
    labels = [d.strftime("%Y-%m") for d in series.index]
    if len(demand) < 2:
        raise ValueError("need at least two periods of demand history for an S&OP horizon")
    return {"demand": demand, "labels": labels}


def run(
    payload: dict,
    *,
    opening_inventory: float = 0.0,
    target: float = 0.0,
    cost: CostModel | None = None,
    confidence: float = 0.8,
) -> SopReview:
    """Run the S&OP cadence over the periodized demand horizon."""
    return run_sop_cycle(
        payload["demand"],
        opening_inventory=opening_inventory,
        target=target,
        cost=cost,
        period_labels=payload["labels"],
        confidence=confidence,
    )


def verify(review: SopReview) -> list[str]:
    """QA gate: the cadence must honour the never-unprotected contract and have plans."""
    issues = list(verify_guided(review.outcome))
    if not review.evaluations:
        issues.append("no supply plans were evaluated")
    return issues


def write_operational(review: SopReview, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the recommended plan's per-period balance."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rec = review.recommended
    rows = [
        {
            "period": p.period,
            "demand": round(p.demand, 2),
            "production": round(p.production, 2),
            "opening_inventory": round(p.opening_inventory, 2),
            "closing_inventory": round(p.closing_inventory, 2),
            "on_hand": round(p.on_hand, 2),
            "shortfall": round(p.shortfall, 2),
        }
        for p in rec.periods
    ]
    return {"csv": write_summary_csv(rows, d / f"sop_plan_{rec.name.lower()}.csv")}

"""Price-optimization playbook.

Given client price/quantity history (any schema), estimates demand elasticity per
SKU and recommends a margin-maximizing price, with the expected profit impact —
a sellable "price optimization analysis" deliverable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.pricing import recommend_price

from .intake import ALIASES, load_table

PERIODS_PER_YEAR = 52.0

PRICE_ALIASES: dict[str, list[str]] = {
    "date": ALIASES["date"],
    "product_id": ALIASES["product_id"],
    "quantity": ALIASES["quantity"],
    "price": ["price", "unit_price", "unitprice", "sell_price", "sellprice", "selling_price", "retail_price", "list_price"],
    "cost": ["cost", "unit_cost", "unitcost", "cogs", "buy_price", "purchase_price"],
}
_REQUIRED = ("date", "product_id", "quantity", "price")


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def prepare_pricing(path: str | Path, *, period: str = "W") -> pd.DataFrame:
    """Load + normalize client price/quantity data to date, product_id, quantity, price[, cost]."""
    raw = load_table(path)
    by_norm = {_norm(c): c for c in raw.columns}
    mapping: dict[str, str] = {}
    for field, aliases in PRICE_ALIASES.items():
        for alias in aliases:
            hit = by_norm.get(_norm(alias))
            if hit is not None:
                mapping[field] = hit
                break
    missing = [c for c in _REQUIRED if c not in mapping]
    if missing:
        raise ValueError(f"could not detect required columns for pricing: {missing}")

    out = pd.DataFrame()
    out["date"] = pd.to_datetime(raw[mapping["date"]], errors="coerce")
    out["product_id"] = raw[mapping["product_id"]].astype(str).str.strip()
    out["quantity"] = pd.to_numeric(raw[mapping["quantity"]], errors="coerce")
    out["price"] = pd.to_numeric(raw[mapping["price"]], errors="coerce")
    if "cost" in mapping:
        out["cost"] = pd.to_numeric(raw[mapping["cost"]], errors="coerce")

    out = out.dropna(subset=["date", "quantity", "price"])
    out = out[(out["quantity"] >= 0) & (out["price"] > 0)]
    if out.empty:
        raise ValueError("no usable rows after cleaning (check price/quantity columns)")

    out["bucket"] = out["date"].dt.to_period(period).dt.start_time
    agg: dict[str, str] = {"quantity": "sum", "price": "mean"}
    if "cost" in out.columns:
        agg["cost"] = "mean"
    grouped = out.groupby(["product_id", "bucket"], as_index=False).agg(agg).rename(columns={"bucket": "date"})
    cols = ["date", "product_id", "quantity", "price"] + (["cost"] if "cost" in grouped.columns else [])
    return grouped.sort_values(["product_id", "date"]).reset_index(drop=True)[cols]


@dataclass(frozen=True)
class PricingRec:
    product_id: str
    current_price: float
    optimal_price: float | None
    unit_cost: float
    cost_is_imputed: bool  # True when unit_cost is a guess (cost_ratio), not a real cost column
    elasticity: float
    r_squared: float
    n_points: int
    demand_change_pct: float | None
    profit_uplift_pct: float | None
    action: str
    confident: bool


@dataclass(frozen=True)
class PricingReport:
    recommendations: list[PricingRec]
    params: dict
    n_skus: int
    n_actionable: int  # confident raise/lower
    n_inelastic: int
    n_insufficient: int


def run(demand: pd.DataFrame, *, cost_ratio: float = 0.6) -> PricingReport:
    """Run price optimization over canonical price/quantity data."""
    if not 0 < cost_ratio < 1:
        raise ValueError("cost_ratio must be in (0, 1)")
    has_cost = "cost" in demand.columns
    recs: list[PricingRec] = []

    for pid, grp in demand.groupby("product_id"):
        prices = grp["price"].to_numpy(dtype=float)
        quantities = grp["quantity"].to_numpy(dtype=float)
        current = float(pd.Series(prices[prices > 0]).median()) if (prices > 0).any() else 0.0
        if has_cost and grp["cost"].notna().any() and float(grp["cost"].mean()) > 0:
            unit_cost = float(grp["cost"].mean())
            cost_is_imputed = False
        else:
            unit_cost = max(current * cost_ratio, 1e-6)
            cost_is_imputed = True

        r = recommend_price(prices, quantities, unit_cost)
        recs.append(
            PricingRec(
                product_id=str(pid), current_price=r.current_price, optimal_price=r.optimal_price,
                unit_cost=unit_cost, cost_is_imputed=cost_is_imputed,
                elasticity=r.elasticity, r_squared=r.r_squared, n_points=r.n_points,
                demand_change_pct=r.demand_change_pct, profit_uplift_pct=r.profit_uplift_pct,
                action=r.action,
                # A recommendation cannot be "confident" when the cost it was
                # optimized against is itself a guess (cost_ratio * price, not a
                # real cost column): previously an imputed cost could still be
                # reported at full confidence, e.g. "raise the price 18%, +22%
                # profit" built on a fabricated cost.
                confident=r.confident and not cost_is_imputed,
            )
        )

    actionable = sum(1 for r in recs if r.confident and r.action in {"raise", "lower"})
    return PricingReport(
        recommendations=recs,
        params={"cost_ratio": cost_ratio, "has_cost_column": has_cost},
        n_skus=len(recs),
        n_actionable=actionable,
        n_inelastic=sum(1 for r in recs if r.action == "inelastic"),
        n_insufficient=sum(1 for r in recs if r.action == "insufficient_data"),
    )

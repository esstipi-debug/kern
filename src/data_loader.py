"""Load demand history and product metadata from CSV."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ProductMetadata:
    product_id: str
    mean_demand_per_period: float
    demand_std_per_period: float
    periods: int
    mean_unit_cost: float
    lead_time_periods: float
    lead_time_std_periods: float = 0.0
    # Supplier terms, in units. 0.0 = none declared (the file had no such column,
    # or the SKU's cells were blank). Read by jobs/inventory_optimization.py's
    # lot choice (src.constraints.cheapest_feasible_lot).
    minimum_order_quantity: float = 0.0
    order_multiple: float = 0.0


def load_demand_frame(path: str | Path) -> pd.DataFrame:
    """Load full demand CSV sorted by date."""
    df = pd.read_csv(path, parse_dates=["date"])
    required = {"date", "product_id", "quantity"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"missing columns: {missing}")
    return df.sort_values(["product_id", "date"])


def list_products(path: str | Path) -> list[str]:
    return sorted(load_demand_frame(path)["product_id"].unique().tolist())


def load_demand_csv(
    path: str | Path,
    *,
    product_id: str | None = None,
    quantity_col: str = "quantity",
    product_col: str = "product_id",
) -> pd.Series:
    """Return a time-ordered demand series for one SKU."""
    df = load_demand_frame(path)
    if product_id is not None:
        df = df[df[product_col] == product_id]
    if df.empty:
        raise ValueError(f"no rows for product_id={product_id!r}")
    return df[quantity_col].astype(float)


def product_metadata(
    path: str | Path,
    product_id: str,
    *,
    periods_per_year: float = 52.0,
    default_lead_time_periods: float = 2.0,
) -> ProductMetadata:
    """
    Demand stats plus unit_cost and lead_time from CSV when present.

    lead_time_days is converted to periods via periods_per_year / 365.
    """
    df = load_demand_frame(path)
    sku = df[df["product_id"] == product_id]
    if sku.empty:
        raise ValueError(f"no rows for product_id={product_id!r}")

    values = sku["quantity"].astype(float).to_numpy()
    mu = float(np.mean(values))
    sigma = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0

    if "unit_cost" in sku.columns:
        unit_cost = float(sku["unit_cost"].astype(float).mean())
    else:
        unit_cost = 1.0

    if "lead_time_days" in sku.columns:
        lt_days = float(sku["lead_time_days"].astype(float).median())
        lt_periods = lead_time_periods_from_days(lt_days, periods_per_year)
    else:
        lt_periods = default_lead_time_periods

    return ProductMetadata(
        product_id=product_id,
        mean_demand_per_period=mu,
        demand_std_per_period=sigma,
        periods=len(values),
        mean_unit_cost=unit_cost,
        lead_time_periods=lt_periods,
    )


def lead_time_periods_from_days(days: float, periods_per_year: float = 52.0) -> float:
    """Dias -> periodos, la conversion que el motor de politicas usa en todos lados.

    Nombrada y no repetida porque se cruza una frontera de unidades: ``lead_times`` en
    ``jobs/inventory_optimization.py::run`` es un override de ``lead_time_periods``, y
    quien traiga un plazo en DIAS (un conector, un evento de ``lead_time_drift``) y lo
    pase tal cual multiplica el periodo de riesgo por siete sin que nada avise -- punto
    de reorden y stock de seguridad inflados sobre una lectura correcta.
    """
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be > 0")
    return float(days) * float(periods_per_year) / 365.0


def demand_stats(series: pd.Series) -> dict[str, float]:
    """Mean and sample std (ddof=1) per period."""
    values = series.to_numpy(dtype=float)
    return {
        "mean_demand_per_period": float(np.mean(values)),
        "demand_std_per_period": float(np.std(values, ddof=1)) if len(values) > 1 else 0.0,
        "periods": float(len(values)),
    }


def annualize_demand(mean_per_period: float, periods_per_year: float) -> float:
    return mean_per_period * periods_per_year

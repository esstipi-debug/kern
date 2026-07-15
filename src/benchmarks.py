"""Old-method vs Linchpin comparison benchmarks -- pure functions, no I/O.

Data loading and scripting for real-dataset backtests lives in scripts/, not
here (see scripts/benchmark_forecast_m5.py) -- this module only computes
comparison metrics from already-loaded arrays.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.forecast_metrics import mae, wape


@dataclass(frozen=True)
class ForecastComparison:
    """Naive vs smart forecast accuracy, scored against the same actuals."""

    naive_mae: float
    naive_wape: float
    smart_mae: float
    smart_wape: float
    improvement_pct: float


def compare_forecast_methods(
    actuals: list[float],
    naive_forecast: list[float],
    smart_forecast: list[float],
) -> ForecastComparison:
    """improvement_pct is the WAPE reduction of smart_forecast over naive_forecast.

    Edge case: if naive_forecast is already a perfect match (naive_wape == 0),
    improvement_pct is reported as 0.0 regardless of smart_forecast's own
    accuracy (there's no meaningful percentage relative to a zero baseline) --
    check naive_wape == 0 before treating improvement_pct == 0.0 as "no change."
    """
    naive_mae_v = mae(actuals, naive_forecast)
    naive_wape_v = wape(actuals, naive_forecast)
    smart_mae_v = mae(actuals, smart_forecast)
    smart_wape_v = wape(actuals, smart_forecast)
    improvement = 0.0
    if naive_wape_v > 0:
        improvement = (naive_wape_v - smart_wape_v) / naive_wape_v * 100
    return ForecastComparison(
        naive_mae=naive_mae_v,
        naive_wape=naive_wape_v,
        smart_mae=smart_mae_v,
        smart_wape=smart_wape_v,
        improvement_pct=improvement,
    )

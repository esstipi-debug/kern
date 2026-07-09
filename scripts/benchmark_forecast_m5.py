"""Backtest: naive persistence vs Linchpin's classification-routed forecast,
scored against REAL M5 competition held-out actuals (d_1914-d_1941).

Requires local M5 data (not committed -- see case-studies/CASE_STUDIES.md
Exercise 10 for how to obtain it):
    data/kaggle/m5/m5/datasets/sales_train_evaluation.csv
    data/kaggle/m5/m5/datasets/calendar.csv

Reports both a per-SKU-average comparison and a demand-weighted (pooled)
comparison, since unweighted per-SKU WAPE averaging is skewed by intermittent
low-volume SKUs.

Usage:
    python scripts/benchmark_forecast_m5.py [--sample-size 100] [--seed 42]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.benchmarks import compare_forecast_methods
from src.forecasting import forecast_demand, moving_average

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "kaggle" / "m5" / "m5" / "datasets"
TRAIN_END_DAY = 1913
TEST_DAYS = [f"d_{d}" for d in range(1914, 1942)]


def load_sample(sample_size: int, seed: int) -> pd.DataFrame:
    path = DATA_DIR / "sales_train_evaluation.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy sales_train_evaluation.csv and calendar.csv "
            "from the main checkout's data/kaggle/m5/m5/datasets/ into this worktree "
            "before running this script (see case-studies/CASE_STUDIES.md Exercise 10)."
        )
    df = pd.read_csv(path)
    return df.sample(n=min(sample_size, len(df)), random_state=seed)


def run_benchmark(sample_size: int = 100, seed: int = 42) -> None:
    df = load_sample(sample_size, seed)
    day_cols = [f"d_{d}" for d in range(1, TRAIN_END_DAY + 1)]

    naive_maes: list[float] = []
    naive_wapes: list[float] = []
    smart_maes: list[float] = []
    smart_wapes: list[float] = []
    all_actuals: list[float] = []
    all_naive_forecasts: list[float] = []
    all_smart_forecasts: list[float] = []
    skipped = 0

    for _, row in df.iterrows():
        history = row[day_cols].astype(float).to_numpy()
        actuals = row[TEST_DAYS].astype(float).to_numpy()
        if history.sum() == 0 or actuals.sum() == 0:
            # actuals.sum() == 0 matters here, not just history.sum(): wape()'s
            # denominator is sum(|actual|), so an all-zero 28-day evaluation
            # window (plausible for a slow-moving real SKU) makes wape() return
            # inf, which then makes compare_forecast_methods()'s improvement_pct
            # come out as nan and silently corrupt the aggregate average below.
            skipped += 1
            continue

        naive_daily_rate = moving_average(history, window=1).forecast
        naive_forecast = [naive_daily_rate] * len(actuals)

        smart_result = forecast_demand(history, method="auto")
        smart_forecast = [smart_result.forecast] * len(actuals)

        comparison = compare_forecast_methods(list(actuals), naive_forecast, smart_forecast)
        naive_maes.append(comparison.naive_mae)
        naive_wapes.append(comparison.naive_wape)
        smart_maes.append(comparison.smart_mae)
        smart_wapes.append(comparison.smart_wape)
        all_actuals.extend(actuals.tolist())
        all_naive_forecasts.extend(naive_forecast)
        all_smart_forecasts.extend(smart_forecast)

    n = len(naive_maes)
    if n == 0:
        print("No scorable SKUs in this sample (all-zero history). Try a different --seed.")
        return

    avg_naive_mae = sum(naive_maes) / n
    avg_naive_wape = sum(naive_wapes) / n
    avg_smart_mae = sum(smart_maes) / n
    avg_smart_wape = sum(smart_wapes) / n
    improvement = 0.0
    if avg_naive_wape > 0:
        improvement = (avg_naive_wape - avg_smart_wape) / avg_naive_wape * 100

    pooled = compare_forecast_methods(all_actuals, all_naive_forecasts, all_smart_forecasts)

    print(f"SKUs scored: {n} (skipped {skipped} all-zero-history/actuals SKUs)")
    print(f"Per-SKU average -- Naive:     MAE={avg_naive_mae:.3f}  WAPE={avg_naive_wape:.3f}")
    print(f"Per-SKU average -- Linchpin:  MAE={avg_smart_mae:.3f}  WAPE={avg_smart_wape:.3f}")
    print(f"Per-SKU average WAPE improvement: {improvement:.1f} percent")
    print(f"Demand-weighted (pooled) -- Naive:     MAE={pooled.naive_mae:.3f}  WAPE={pooled.naive_wape:.3f}")
    print(f"Demand-weighted (pooled) -- Linchpin:  MAE={pooled.smart_mae:.3f}  WAPE={pooled.smart_wape:.3f}")
    print(f"Demand-weighted WAPE improvement: {pooled.improvement_pct:.1f} percent")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    run_benchmark(args.sample_size, args.seed)

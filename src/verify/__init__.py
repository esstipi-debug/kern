"""The A4 "verify" layer (Linchpin 3.0 Control Tower, plan section 5).

Predicted-vs-real backtesting per SKU (``backtest.py``), aggregated into a
per-tool reliability report (``reliability.py``). Pure -- no I/O beyond
reading already-persisted ``src.state`` snapshots; nothing here writes back
to config or state. PR-9's T2->T1 autonomy promotion consumes
``ToolReliabilityReport.headline_precision`` as its evidence.
"""

from __future__ import annotations

from .backtest import (
    ACTUAL_QTY_METRIC,
    MatchedObservation,
    RecalibrationSuggestion,
    SkuBacktestResult,
    match_decision_actuals,
    match_forecast_actuals,
    per_sku_backtest,
    run_forecast_backtest,
    suggest_sigma_recalibration,
)
from .reliability import (
    ToolReliabilityReport,
    build_all_reliability_reports,
    build_reliability_report,
)

__all__ = [
    "ACTUAL_QTY_METRIC",
    "MatchedObservation",
    "RecalibrationSuggestion",
    "SkuBacktestResult",
    "match_decision_actuals",
    "match_forecast_actuals",
    "per_sku_backtest",
    "run_forecast_backtest",
    "suggest_sigma_recalibration",
    "ToolReliabilityReport",
    "build_all_reliability_reports",
    "build_reliability_report",
]

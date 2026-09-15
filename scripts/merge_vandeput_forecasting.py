"""Merge Vandeput, *Data Science for Supply Chain Forecasting* (2021, 2ed) into the books graph.

This is the L3 source behind the forecasting corrections in PR #232 (seasonal naive
benchmark, the MASE/RMSSE double gate, forecast-error-based outlier detection). The
graph already carried Vandeput's *Inventory Optimization* (2020) -- a different book.

**Why this one carries its extraction inline instead of reading chunk JSONs.**
The other per-book scripts (see ``merge_khan_resilience.py``) read
``.graphify_chunk_*.json`` produced by an LLM extraction pass over the raw corpus,
which lives under the gitignored ``knowledge/scm-books-rebuild/``. That makes the
committed graph reproducible only on a machine that still has the working tree, and
only with a live API key. This extraction was done by reading the book directly, so
its output is a fixed artifact -- and a fixed artifact belongs in the repo. Keeping it
here means ``graph.json`` is reproducible from a clean clone with no key and no raw
corpus. Only concept labels and one-line descriptions are stored; none of the book's
text is reproduced.

Every other transformation matches the established pattern exactly: `knowledge::`
namespacing, rubric snapping and low-confidence pruning for INFERRED edges,
parallel-edge collapsing, and the id-set / parallel-edge / idempotency guards.

Run order (see knowledge/scm-books/README.md):
  1. this script
  2. scripts/recluster_books_graph.py
  3. scripts/regen_books_report.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMITTED = REPO / "knowledge" / "scm-books" / "graph.json"
BACKUP = REPO / "knowledge" / "scm-books" / "graph.json.bak"

NS = "knowledge"
BOOK_SLUG = "vandeput-data-science-supply-chain-forecasting.pdf"
BOOK_ID = "book_vandeput_ds_forecasting"
MIN_INFERRED = 0.75
RUBRIC = (0.55, 0.65, 0.75, 0.85, 0.95)

# --- extraction ---------------------------------------------------------------
# (local_id, label, source_location, rationale)
# Ids that already exist in the graph bridge onto the canonical node: the existing
# node is kept and this book's edges become the cross-book connection. Ids that do
# not exist are added carrying this book's citation.
CONCEPTS: tuple[tuple[str, str, str, str], ...] = (
    # -- Part I: statistical forecasting --------------------------------------
    ("moving_average", "Moving Average", "Ch.1",
     "Forecast as the mean of the last n demand observations; flat into the future, blind to trend and season."),
    ("naive_forecast", "Naive Forecast", "Ch.1",
     "Projects the last observation forward. The reference every other method must beat."),
    ("white_noise", "Noise", "Ch.1",
     "Unexplained random variation in the demand; what a model must not learn."),
    ("forecast_accuracy", "Forecast Accuracy", "Ch.2",
     "How far the forecasts sit from the actuals - magnitude of error, saying nothing about direction."),
    ("mean_error_bias", "Forecast Bias", "Ch.2",
     "Average signed error: whether the forecast systematically over- or under-shoots. Low bias does not imply accuracy."),
    ("mean_absolute_percentage_error", "MAPE", "Ch.2",
     "Averages each period's percentage error, so low-demand periods dominate; optimising it produces a forecast that undershoots."),
    ("mean_absolute_error", "MAE", "Ch.2",
     "Mean of the absolute errors; robust to outliers, and the basis of MASE."),
    ("root_mean_square_error", "RMSE", "Ch.2",
     "Square root of the mean squared error; weights the largest misses most, which is why one bad period moves it."),
    ("mae_optimum_is_the_median", "MAE Optimises Toward the Demand Median", "Ch.2",
     "Setting MAE as the target drives the forecast to the demand median. Supply-chain demand is right-skewed, so that "
     "biases low - and for intermittent demand, where more than half the periods are zero, the MAE-optimal forecast is zero."),
    ("rmse_optimum_is_the_mean", "RMSE Optimises Toward the Demand Mean", "Ch.2",
     "Setting RMSE as the target drives the forecast to the demand mean, so it is unbiased - at the cost of sensitivity to outliers."),
    ("report_one_kpi_optimise_another", "Report One KPI, Optimise Another", "Ch.2",
     "Accuracy reported to management (MAE and bias) need not be the criterion a model is fitted on (RMSE). Conflating "
     "the two lets a median-seeking metric silently choose the model."),
    ("single_exponential_smoothing", "Simple Exponential Smoothing", "Ch.3",
     "Learns only the level, weighting past observations exponentially through the smoothing rate alpha."),
    ("level_component", "Level", "Ch.3",
     "The smoothed average the demand varies around; estimated, never directly observed."),
    ("smoothing_rate_tradeoff", "Reactivity vs Stability Trade-off", "Ch.3",
     "A high learning rate reacts fast to a level change and overreacts to noise; a low one is stable and slow. "
     "Above ~0.6 the model has effectively stopped using demand history - a sign something is wrong."),
    ("data_leakage", "Data Leakage", "Ch.3",
     "Giving a model information about the future - e.g. initialising from an average that spans periods it will be scored on. "
     "Produces accuracy that cannot be reproduced on new data."),
    ("underfitting", "Underfitting", "Ch.4",
     "The model is too simple to represent the pattern in the training data; it will not perform on new data either. "
     "Fixed by a richer model or by explanatory variables the model was missing."),
    ("double_exponential_smoothing", "Double Exponential Smoothing", "Ch.5",
     "Adds a trend component learned at its own rate (beta), so the forecast can extrapolate direction, not just level."),
    ("trend", "Trend", "Ch.5",
     "Average change in the level between two consecutive periods."),
    ("model_parameter_optimisation", "Smoothing-Parameter Optimisation", "Ch.6",
     "Search alpha/beta/phi/gamma against a chosen KPI instead of guessing. The KPI chosen decides which model wins, "
     "and an unconstrained range invites overfitting."),
    ("damped_trend", "Damped Trend", "Ch.7",
     "A damping factor phi (typically 0.7-1) shrinks the trend each period, so a mid-term forecast stops extrapolating "
     "a slope forever - the vanilla double-smoothing failure that eventually predicts negative demand."),
    ("overfitting", "Overfitting", "Ch.8",
     "The model learned patterns from the noise: excellent on the training set, worse on unseen data. "
     "The number-one failure mode, and the reason a striking accuracy deserves suspicion rather than celebration."),
    ("triple_exponential_smoothing", "Triple Exponential Smoothing", "Ch.9",
     "Adds seasonal factors learned at their own rate (gamma), applied from the previous cycle."),
    ("multiplicative_seasonality", "Multiplicative Seasonality", "Ch.9",
     "Seasonal factors as percentages of the level, scaled to sum to the season length. Breaks down at low volumes, "
     "where a small absolute change is a huge relative one, and fails outright on a zero factor."),
    ("holt_winters", "Holt-Winters Models", "Ch.9",
     "The family of level/trend/season exponential-smoothing models still at the core of most statistical forecast tools."),
    ("outlier_detection", "Outlier Detection in Demand History", "Ch.10",
     "Exceptional periods - mistakes, promotions, bulk orders, clearances - inflate the forecast and the safety stock built "
     "on its error spread. Detection is worth automating; the book reports accuracy gains from cleaning alone."),
    ("winsorization", "Winsorization", "Ch.10",
     "Capping the top and bottom x% of the demand. Rejected: it invents outliers on a clean series and under-corrects real ones."),
    ("demand_std_outlier_threshold", "Demand-Deviation Threshold", "Ch.10",
     "Thresholding on the demand's own standard deviation around its mean. Better than winsorization, but flags the peaks "
     "as outliers as soon as the series has a trend or a season."),
    ("forecast_error_outlier_threshold", "Forecast-Error Threshold", "Ch.10",
     "The method that works: an outlier is a value far from what the model expected *there*, so the threshold goes on the "
     "spread of the forecast error, which survives trend and seasonality."),
    ("iterative_outlier_threshold_refinement", "Refining the Threshold Without the Outliers", "Ch.10",
     "The outlier inflates the very error spread that judges it, so the threshold is recomputed excluding the points just "
     "flagged, and the correction applied against the tighter band."),
    ("additive_seasonality", "Additive Seasonality", "Ch.11",
     "Seasonal factors as absolute amounts summing to zero over a cycle. Suited to stable or low demand, where multiplicative "
     "factors misbehave; weaker when the level trends, since absolute factors do not scale with it."),
    # -- Part II: machine learning ---------------------------------------------
    ("machine_learning_forecasting", "Machine Learning for Demand Forecasting", "Ch.12",
     "Learns relationships from the whole dataset rather than applying an assumed model per series."),
    ("training_set", "Training Set", "Ch.12",
     "The data a model fits its parameters on."),
    ("test_set", "Test Set", "Ch.12",
     "Data held out to estimate accuracy on unseen demand. Using it to pick a model or a parameter destroys its purpose."),
    ("regression_tree", "Regression Tree", "Ch.13",
     "Splits the data with yes/no questions on the features until a leaf predicts a number; unbounded growth overfits."),
    ("k_fold_cross_validation", "k-Fold Cross-Validation", "Ch.14",
     "Rotates a validation fold through the training set so many parameter sets can be tried without touching the test set."),
    ("validation_set", "Validation Set", "Ch.14",
     "A subset of the training data kept aside to compare candidate models."),
    ("random_search", "Random Search", "Ch.14",
     "Samples the parameter space at random rather than on a grid, covering more distinct values per parameter for the same budget."),
    ("random_forest", "Random Forest", "Ch.15",
     "Bagged ensemble of trees, each on a random subset of samples and features; the crowd beats the individual tree."),
    ("feature_importance", "Feature Importance", "Ch.16",
     "How much each input contributed to the model's splits - the handle for removing inputs that only add noise."),
    ("extremely_randomized_trees", "Extremely Randomized Trees", "Ch.17",
     "Picks split points at random rather than optimally; faster, and flattens the feature-importance profile."),
    ("feature_optimisation", "Feature Optimisation", "Ch.18",
     "Choosing *how many* historical periods and which features to feed the model. More features fit the training set better "
     "while leaving less data to learn from - both push overfitting up."),
    ("holdout_set", "Holdout Set", "Ch.18",
     "A second test set carved from the tail of the training data. Needed because k-fold folds drawn at random from a time "
     "series contain periods the model already knows, so it overfits the validation set too."),
    ("adaptive_boosting", "AdaBoost", "Ch.19",
     "Boosting ensemble that reweights the samples each round toward the ones still predicted badly."),
    ("demand_driver", "Demand Driver", "Ch.20",
     "An internal (price, promotion, marketing) or external factor that moves demand beyond its own history."),
    ("leading_indicator", "Leading Indicator", "Ch.20",
     "An external driver observable before the demand it predicts. Its *forecast* is rarely as good as its actual value, "
     "and reliability decays with the horizon - the weather is useless at a one-month lead time."),
    ("exogenous_variable", "Exogenous Variable", "Ch.20",
     "A factor outside the demand history. Exponential-smoothing models cannot use one; this is where ML earns its place."),
    ("extreme_gradient_boosting", "XGBoost", "Ch.21",
     "Regularised gradient boosting with early stopping; the family behind the top M5 entries."),
    ("early_stopping", "Early Stopping", "Ch.21",
     "Stop adding trees once a validation set stops improving - fights overfitting and cuts training time at once."),
    ("one_hot_encoding", "One-Hot Encoding", "Ch.22",
     "One boolean column per category, for categories with no natural order (markets, families, channels)."),
    ("integer_encoding", "Integer Encoding", "Ch.22",
     "One number per category; only meaningful when the categories are genuinely ordered."),
    ("k_means_clustering", "K-Means Clustering", "Ch.23",
     "Groups products by a shared profile (e.g. seasonal shape) so the model can use the group as a feature."),
    ("backward_elimination", "Backward Elimination", "Ch.24",
     "Drop features below an importance threshold until accuracy stops improving; the mirror of forward selection."),
    ("neural_network", "Neural Network", "Ch.25",
     "Layers of weighted units with activation functions, trained by gradient descent - no decomposition of the series to inspect."),
    ("gradient_descent", "Gradient Descent", "Ch.25",
     "Walks the cost function downhill by its local slope; the optimiser under most ML training."),
    ("backpropagation", "Backpropagation", "Ch.25",
     "Propagates local derivatives back through the network so every weight learns from the last prediction error."),
    ("statistical_models_are_inspectable", "Statistical Models Are Inspectable, ML Is Not", "Ch.5",
     "Exponential smoothing decomposes a series into level, trend and season, so a forecast can be explained by inspecting "
     "its components. Machine learning returns the answer without the why - which is why the two are complements, not rivals."),
    # -- Part III: process -----------------------------------------------------
    ("judgmental_forecast", "Judgmental Forecast", "Ch.26",
     "A human adjustment on top of the statistical baseline. Adds value when the human holds information the model cannot see, "
     "and destroys it when the adjustment is small, frequent and unaccountable."),
    ("intentional_judgmental_bias", "Intentional Judgmental Bias", "Ch.26",
     "Bias from misaligned incentives (a bonus for beating the forecast) or from power - the loudest or most senior voice "
     "moving the number. Oliva & Watson's first category."),
    ("unintentional_judgmental_bias", "Unintentional Judgmental Bias", "Ch.26",
     "Bias from process blind spots and cognitive bias rather than intent. Oliva & Watson's second category."),
    ("sales_versus_demand_blind_spot", "Forecasting Sales Instead of Demand", "Ch.26",
     "The most common informational blind spot: history records what was sold, not what was wanted. Without lost sales, "
     "a stockout teaches the model to forecast lower, which causes the next stockout."),
    ("confirmation_bias", "Confirmation Bias", "Ch.26",
     "Seeking and weighting information that supports what one already believes - or wants - about next month."),
    ("anchoring_bias", "Anchoring Bias", "Ch.26",
     "The first number spoken shapes the final estimate, even when it is unrelated. The reason forecasts should be written "
     "down independently before a meeting discusses them."),
    ("group_forecasting", "Group Forecasting", "Ch.26",
     "Independent estimates from several people, averaged - the wisdom of the crowd, and the human analogue of an ensemble model."),
    ("discuss_assumptions_not_numbers", "Discuss Assumptions, Not Numbers", "Ch.26",
     "A debate about the final figure is sterile and invites bias; a debate about market share, growth and launches is auditable."),
    ("weighted_forecast_kpi", "Weighted Forecast KPI", "Ch.27",
     "Weight each SKU's error by its unit profit or cost so a planner's scarce attention goes to the item that matters, not the "
     "one with the largest raw error. Never weight over- and under-forecast asymmetrically: that buys a biased forecast and "
     "costs the organisation's trust - set service levels and safety stock instead."),
    ("forecast_granularity_and_horizon", "Granularity and Horizon That Matter", "Ch.27",
     "Score the buckets and lags the supply chain can actually act on, set by its review period and lead times, not every lag at once."),
    ("seasonal_naive_benchmark", "Seasonal Naive Benchmark", "Ch.27",
     "On seasonal demand the benchmark must be the same period last cycle. Scoring a seasonal series against a lag-1 naive "
     "flatters every model, because lag-1 swings with the season and is trivially beaten."),
    ("fva_process_analysis", "FVA Across the Forecasting Process", "Ch.27",
     "Gilliland's framework applied step by step: measure each team's accuracy delta *and the hours it spent*. Steps that consume "
     "time and add error become visible and arguable - which is what makes senior management act on them."),
    ("human_machine_team", "Human-Machine Team", "Ch.27",
     "The planner adds value precisely where the model is blind, so understanding the model's shortcomings is what makes the "
     "human useful. A black-box baseline forfeits that."),
)

# (source, relation, target, confidence, score)
EDGES: tuple[tuple[str, str, str, str, float], ...] = (
    # -- KPI choice is a modelling decision, not a reporting one ---------------
    ("mean_absolute_error", "conceptually_related_to", "mae_optimum_is_the_median", "EXTRACTED", 1.0),
    ("root_mean_square_error", "conceptually_related_to", "rmse_optimum_is_the_mean", "EXTRACTED", 1.0),
    ("mae_optimum_is_the_median", "conceptually_related_to", "intermittent_demand", "EXTRACTED", 1.0),
    ("mae_optimum_is_the_median", "conceptually_related_to", "mean_error_bias", "EXTRACTED", 1.0),
    ("rmse_optimum_is_the_mean", "conceptually_related_to", "mean_error_bias", "EXTRACTED", 1.0),
    ("rmse_optimum_is_the_mean", "conceptually_related_to", "outlier_detection", "EXTRACTED", 1.0),
    ("report_one_kpi_optimise_another", "conceptually_related_to", "mae_optimum_is_the_median", "EXTRACTED", 1.0),
    ("report_one_kpi_optimise_another", "conceptually_related_to", "rmse_optimum_is_the_mean", "EXTRACTED", 1.0),
    ("report_one_kpi_optimise_another", "conceptually_related_to", "forecast_accuracy", "EXTRACTED", 1.0),
    ("mean_absolute_percentage_error", "conceptually_related_to", "forecast_accuracy", "EXTRACTED", 1.0),
    ("mean_absolute_percentage_error", "conceptually_related_to", "mae_optimum_is_the_median", "INFERRED", 0.75),
    ("mean_absolute_error", "conceptually_related_to", "mean_absolute_scaled_error", "EXTRACTED", 1.0),
    ("root_mean_square_error", "conceptually_related_to", "mean_square_error", "EXTRACTED", 1.0),
    ("forecast_error", "conceptually_related_to", "mean_error_bias", "EXTRACTED", 1.0),
    ("forecast_error", "conceptually_related_to", "forecast_accuracy", "EXTRACTED", 1.0),
    ("mean_absolute_error", "conceptually_related_to", "aggregate_forecasting", "EXTRACTED", 1.0),
    # -- the benchmark ---------------------------------------------------------
    ("seasonal_naive_benchmark", "conceptually_related_to", "naive_forecast", "EXTRACTED", 1.0),
    ("seasonal_naive_benchmark", "conceptually_related_to", "seasonality", "EXTRACTED", 1.0),
    ("seasonal_naive_benchmark", "conceptually_related_to", "forecasting_benchmarking", "EXTRACTED", 1.0),
    ("seasonal_naive_benchmark", "conceptually_related_to", "mean_absolute_scaled_error", "EXTRACTED", 1.0),
    ("seasonal_naive_benchmark", "conceptually_related_to", "forecast_value_added", "EXTRACTED", 1.0),
    ("seasonal_naive_benchmark", "conceptually_related_to", "moving_average", "EXTRACTED", 1.0),
    ("forecast_value_added", "conceptually_related_to", "forecasting_benchmarking", "EXTRACTED", 1.0),
    ("naive_forecast", "conceptually_related_to", "moving_average", "EXTRACTED", 1.0),
    ("naive_forecast", "conceptually_related_to", "white_noise", "EXTRACTED", 1.0),
    # -- smoothing family ------------------------------------------------------
    ("single_exponential_smoothing", "conceptually_related_to", "level_component", "EXTRACTED", 1.0),
    ("single_exponential_smoothing", "conceptually_related_to", "moving_average", "EXTRACTED", 1.0),
    ("single_exponential_smoothing", "conceptually_related_to", "smoothing_rate_tradeoff", "EXTRACTED", 1.0),
    ("double_exponential_smoothing", "conceptually_related_to", "single_exponential_smoothing", "EXTRACTED", 1.0),
    ("double_exponential_smoothing", "conceptually_related_to", "trend", "EXTRACTED", 1.0),
    ("damped_trend", "conceptually_related_to", "double_exponential_smoothing", "EXTRACTED", 1.0),
    ("damped_trend", "conceptually_related_to", "trend", "EXTRACTED", 1.0),
    ("damped_trend", "conceptually_related_to", "forecasting_time_horizons", "INFERRED", 0.85),
    ("triple_exponential_smoothing", "conceptually_related_to", "double_exponential_smoothing", "EXTRACTED", 1.0),
    ("triple_exponential_smoothing", "conceptually_related_to", "seasonality", "EXTRACTED", 1.0),
    ("triple_exponential_smoothing", "conceptually_related_to", "multiplicative_seasonality", "EXTRACTED", 1.0),
    ("triple_exponential_smoothing", "conceptually_related_to", "additive_seasonality", "EXTRACTED", 1.0),
    ("multiplicative_seasonality", "conceptually_related_to", "seasonal_component", "EXTRACTED", 1.0),
    ("additive_seasonality", "conceptually_related_to", "seasonal_component", "EXTRACTED", 1.0),
    ("additive_seasonality", "conceptually_related_to", "multiplicative_seasonality", "EXTRACTED", 1.0),
    ("holt_winters", "conceptually_related_to", "triple_exponential_smoothing", "EXTRACTED", 1.0),
    ("holt_winters", "conceptually_related_to", "single_exponential_smoothing", "EXTRACTED", 1.0),
    ("smoothing_rate_tradeoff", "conceptually_related_to", "white_noise", "EXTRACTED", 1.0),
    ("smoothing_rate_tradeoff", "conceptually_related_to", "overfitting", "EXTRACTED", 1.0),
    ("model_parameter_optimisation", "conceptually_related_to", "single_exponential_smoothing", "EXTRACTED", 1.0),
    ("model_parameter_optimisation", "conceptually_related_to", "overfitting", "EXTRACTED", 1.0),
    ("model_parameter_optimisation", "conceptually_related_to", "report_one_kpi_optimise_another", "EXTRACTED", 1.0),
    ("statistical_models_are_inspectable", "conceptually_related_to", "double_exponential_smoothing", "EXTRACTED", 1.0),
    ("statistical_models_are_inspectable", "conceptually_related_to", "machine_learning_forecasting", "EXTRACTED", 1.0),
    ("statistical_models_are_inspectable", "conceptually_related_to", "human_machine_team", "EXTRACTED", 1.0),
    # -- fit failures ----------------------------------------------------------
    ("underfitting", "conceptually_related_to", "overfitting", "EXTRACTED", 1.0),
    ("underfitting", "conceptually_related_to", "exogenous_variable", "EXTRACTED", 1.0),
    ("overfitting", "conceptually_related_to", "test_set", "EXTRACTED", 1.0),
    ("overfitting", "conceptually_related_to", "training_set", "EXTRACTED", 1.0),
    ("overfitting", "conceptually_related_to", "data_leakage", "EXTRACTED", 1.0),
    ("overfitting", "conceptually_related_to", "feature_optimisation", "EXTRACTED", 1.0),
    ("data_leakage", "conceptually_related_to", "training_set", "EXTRACTED", 1.0),
    ("data_leakage", "conceptually_related_to", "single_exponential_smoothing", "EXTRACTED", 1.0),
    # -- outliers --------------------------------------------------------------
    ("outlier_detection", "conceptually_related_to", "winsorization", "EXTRACTED", 1.0),
    ("outlier_detection", "conceptually_related_to", "demand_std_outlier_threshold", "EXTRACTED", 1.0),
    ("outlier_detection", "conceptually_related_to", "forecast_error_outlier_threshold", "EXTRACTED", 1.0),
    ("forecast_error_outlier_threshold", "conceptually_related_to", "demand_std_outlier_threshold", "EXTRACTED", 1.0),
    ("forecast_error_outlier_threshold", "conceptually_related_to", "seasonality", "EXTRACTED", 1.0),
    ("forecast_error_outlier_threshold", "conceptually_related_to", "trend", "EXTRACTED", 1.0),
    ("forecast_error_outlier_threshold", "conceptually_related_to", "forecast_error", "EXTRACTED", 1.0),
    ("iterative_outlier_threshold_refinement", "conceptually_related_to", "forecast_error_outlier_threshold", "EXTRACTED", 1.0),
    ("outlier_detection", "conceptually_related_to", "safety_stock", "EXTRACTED", 1.0),
    ("outlier_detection", "conceptually_related_to", "price_promotions", "INFERRED", 0.85),
    ("winsorization", "conceptually_related_to", "demand_std_outlier_threshold", "EXTRACTED", 1.0),
    # -- forecastability -------------------------------------------------------
    ("intermittent_demand", "conceptually_related_to", "crostons_method", "EXTRACTED", 1.0),
    ("intermittent_demand", "conceptually_related_to", "demand_aggregation", "EXTRACTED", 1.0),
    ("mae_optimum_is_the_median", "conceptually_related_to", "demand_aggregation", "EXTRACTED", 1.0),
    ("forecastability", "conceptually_related_to", "intermittent_demand", "INFERRED", 0.85),
    ("forecastability", "conceptually_related_to", "forecast_accuracy", "INFERRED", 0.85),
    # -- machine learning ------------------------------------------------------
    ("machine_learning_forecasting", "conceptually_related_to", "training_set", "EXTRACTED", 1.0),
    ("machine_learning_forecasting", "conceptually_related_to", "test_set", "EXTRACTED", 1.0),
    ("machine_learning_forecasting", "conceptually_related_to", "exogenous_variable", "EXTRACTED", 1.0),
    ("machine_learning_forecasting", "conceptually_related_to", "regression_tree", "EXTRACTED", 1.0),
    ("machine_learning_forecasting", "conceptually_related_to", "machine_learning_demand", "INFERRED", 0.85),
    ("regression_tree", "conceptually_related_to", "overfitting", "EXTRACTED", 1.0),
    ("regression_tree", "conceptually_related_to", "random_forest", "EXTRACTED", 1.0),
    ("random_forest", "conceptually_related_to", "extremely_randomized_trees", "EXTRACTED", 1.0),
    ("random_forest", "conceptually_related_to", "feature_importance", "EXTRACTED", 1.0),
    ("random_forest", "conceptually_related_to", "group_forecasting", "EXTRACTED", 1.0),
    ("extremely_randomized_trees", "conceptually_related_to", "feature_importance", "EXTRACTED", 1.0),
    ("adaptive_boosting", "conceptually_related_to", "random_forest", "EXTRACTED", 1.0),
    ("extreme_gradient_boosting", "conceptually_related_to", "adaptive_boosting", "EXTRACTED", 1.0),
    ("extreme_gradient_boosting", "conceptually_related_to", "early_stopping", "EXTRACTED", 1.0),
    ("early_stopping", "conceptually_related_to", "validation_set", "EXTRACTED", 1.0),
    ("early_stopping", "conceptually_related_to", "overfitting", "EXTRACTED", 1.0),
    ("k_fold_cross_validation", "conceptually_related_to", "validation_set", "EXTRACTED", 1.0),
    ("k_fold_cross_validation", "conceptually_related_to", "test_set", "EXTRACTED", 1.0),
    ("k_fold_cross_validation", "conceptually_related_to", "holdout_set", "EXTRACTED", 1.0),
    ("k_fold_cross_validation", "conceptually_related_to", "random_search", "EXTRACTED", 1.0),
    ("random_search", "conceptually_related_to", "model_parameter_optimisation", "EXTRACTED", 1.0),
    ("holdout_set", "conceptually_related_to", "test_set", "EXTRACTED", 1.0),
    ("holdout_set", "conceptually_related_to", "overfitting", "EXTRACTED", 1.0),
    ("feature_optimisation", "conceptually_related_to", "feature_importance", "EXTRACTED", 1.0),
    ("feature_optimisation", "conceptually_related_to", "backward_elimination", "EXTRACTED", 1.0),
    ("feature_optimisation", "conceptually_related_to", "holdout_set", "EXTRACTED", 1.0),
    ("backward_elimination", "conceptually_related_to", "feature_importance", "EXTRACTED", 1.0),
    ("demand_driver", "conceptually_related_to", "leading_indicator", "EXTRACTED", 1.0),
    ("demand_driver", "conceptually_related_to", "exogenous_variable", "EXTRACTED", 1.0),
    ("demand_driver", "conceptually_related_to", "price_promotions", "EXTRACTED", 1.0),
    ("leading_indicator", "conceptually_related_to", "forecasting_time_horizons", "EXTRACTED", 1.0),
    ("leading_indicator", "conceptually_related_to", "exogenous_variable", "EXTRACTED", 1.0),
    ("exogenous_variable", "conceptually_related_to", "single_exponential_smoothing", "EXTRACTED", 1.0),
    ("one_hot_encoding", "conceptually_related_to", "integer_encoding", "EXTRACTED", 1.0),
    ("one_hot_encoding", "conceptually_related_to", "machine_learning_forecasting", "EXTRACTED", 1.0),
    ("k_means_clustering", "conceptually_related_to", "seasonal_component", "EXTRACTED", 1.0),
    ("k_means_clustering", "conceptually_related_to", "feature_optimisation", "EXTRACTED", 1.0),
    ("k_means_clustering", "conceptually_related_to", "demand_classification", "INFERRED", 0.85),
    ("neural_network", "conceptually_related_to", "gradient_descent", "EXTRACTED", 1.0),
    ("neural_network", "conceptually_related_to", "backpropagation", "EXTRACTED", 1.0),
    ("neural_network", "conceptually_related_to", "early_stopping", "EXTRACTED", 1.0),
    ("neural_network", "conceptually_related_to", "machine_learning_forecasting", "EXTRACTED", 1.0),
    ("gradient_descent", "conceptually_related_to", "backpropagation", "EXTRACTED", 1.0),
    ("gradient_descent", "conceptually_related_to", "mean_square_error", "EXTRACTED", 1.0),
    # -- process ---------------------------------------------------------------
    ("judgmental_forecast", "conceptually_related_to", "judgmental_adjustment", "EXTRACTED", 1.0),
    ("judgmental_forecast", "conceptually_related_to", "intentional_judgmental_bias", "EXTRACTED", 1.0),
    ("judgmental_forecast", "conceptually_related_to", "unintentional_judgmental_bias", "EXTRACTED", 1.0),
    ("judgmental_forecast", "conceptually_related_to", "forecasting_process", "EXTRACTED", 1.0),
    ("judgmental_forecast", "conceptually_related_to", "consensus_forecast", "INFERRED", 0.85),
    ("intentional_judgmental_bias", "conceptually_related_to", "forecasting_politics", "EXTRACTED", 1.0),
    ("intentional_judgmental_bias", "conceptually_related_to", "sales_and_operations_planning", "INFERRED", 0.85),
    ("unintentional_judgmental_bias", "conceptually_related_to", "confirmation_bias", "EXTRACTED", 1.0),
    ("unintentional_judgmental_bias", "conceptually_related_to", "anchoring_bias", "EXTRACTED", 1.0),
    ("unintentional_judgmental_bias", "conceptually_related_to", "sales_versus_demand_blind_spot", "EXTRACTED", 1.0),
    ("sales_versus_demand_blind_spot", "conceptually_related_to", "censored_demand_estimation", "EXTRACTED", 1.0),
    ("sales_versus_demand_blind_spot", "conceptually_related_to", "mean_error_bias", "EXTRACTED", 1.0),
    ("anchoring_bias", "conceptually_related_to", "group_forecasting", "EXTRACTED", 1.0),
    ("group_forecasting", "conceptually_related_to", "consensus_forecast", "EXTRACTED", 1.0),
    ("group_forecasting", "conceptually_related_to", "collaborative_forecasting", "INFERRED", 0.85),
    ("discuss_assumptions_not_numbers", "conceptually_related_to", "group_forecasting", "EXTRACTED", 1.0),
    ("discuss_assumptions_not_numbers", "conceptually_related_to", "leading_indicator", "EXTRACTED", 1.0),
    ("discuss_assumptions_not_numbers", "conceptually_related_to", "anchoring_bias", "EXTRACTED", 1.0),
    ("fva_process_analysis", "conceptually_related_to", "forecast_value_added", "EXTRACTED", 1.0),
    ("fva_process_analysis", "conceptually_related_to", "forecasting_process", "EXTRACTED", 1.0),
    ("fva_process_analysis", "conceptually_related_to", "judgmental_forecast", "EXTRACTED", 1.0),
    ("fva_process_analysis", "conceptually_related_to", "intentional_judgmental_bias", "EXTRACTED", 1.0),
    ("fva_process_analysis", "conceptually_related_to", "weighted_forecast_kpi", "EXTRACTED", 1.0),
    ("fva_process_analysis", "conceptually_related_to", "seasonal_naive_benchmark", "EXTRACTED", 1.0),
    ("fva_process_analysis", "conceptually_related_to", "key_performance_indicators", "INFERRED", 0.85),
    ("weighted_forecast_kpi", "conceptually_related_to", "forecast_accuracy", "EXTRACTED", 1.0),
    ("weighted_forecast_kpi", "conceptually_related_to", "service_level", "EXTRACTED", 1.0),
    ("weighted_forecast_kpi", "conceptually_related_to", "safety_stock", "EXTRACTED", 1.0),
    ("weighted_forecast_kpi", "conceptually_related_to", "mean_error_bias", "EXTRACTED", 1.0),
    ("forecast_granularity_and_horizon", "conceptually_related_to", "forecasting_time_horizons", "EXTRACTED", 1.0),
    ("forecast_granularity_and_horizon", "conceptually_related_to", "demand_aggregation", "EXTRACTED", 1.0),
    ("forecast_granularity_and_horizon", "conceptually_related_to", "sales_and_operations_planning", "INFERRED", 0.85),
    ("human_machine_team", "conceptually_related_to", "judgmental_forecast", "EXTRACTED", 1.0),
    ("human_machine_team", "conceptually_related_to", "ai_ml_forecasting", "EXTRACTED", 1.0),
    ("human_machine_team", "conceptually_related_to", "demand_driver", "EXTRACTED", 1.0),
    ("human_machine_team", "conceptually_related_to", "group_forecasting", "EXTRACTED", 1.0),
    ("damped_trend", "conceptually_related_to", "bullwhip_effect", "INFERRED", 0.85),
    ("smoothing_rate_tradeoff", "conceptually_related_to", "bullwhip_effect", "EXTRACTED", 1.0),
)

#: Canonical ids this book's edges attach to but does not itself define. They are
#: listed rather than inferred so a typo (``intermitent_demand``) fails loudly instead
#: of silently dropping the edge as dangling - which is how a bridge quietly turns into
#: an isolated island. ``main`` asserts every one of these is really in the graph.
BRIDGES: tuple[str, ...] = (
    "aggregate_forecasting", "ai_ml_forecasting", "bullwhip_effect", "censored_demand_estimation",
    "collaborative_forecasting", "consensus_forecast", "crostons_method", "demand_aggregation",
    "demand_classification", "forecast_error", "forecast_value_added", "forecastability",
    "forecasting_benchmarking", "forecasting_politics", "forecasting_process",
    "forecasting_time_horizons", "intermittent_demand", "judgmental_adjustment",
    "key_performance_indicators", "machine_learning_demand", "mean_absolute_scaled_error",
    "mean_square_error", "price_promotions", "safety_stock", "sales_and_operations_planning",
    "seasonal_component", "seasonality", "service_level",
)

BOOK_NODE = {
    "label": "Vandeput: Data Science for Supply Chain Forecasting (2nd ed.)",
    "file_type": "paper",
    "source_location": None,
    "source_url": None,
    "captured_at": None,
    "author": "Nicolas Vandeput",
    "contributor": None,
    "rationale": (
        "2021 De Gruyter text (2nd ed., foreword by Spyros Makridakis) covering statistical forecasting "
        "(exponential smoothing family, outlier handling, forecast KPIs), machine-learning demand models "
        "(trees through XGBoost and neural networks), and the management of a forecasting process "
        "(judgmental bias, forecast value added)."
    ),
    "norm_label": "vandeput: data science for supply chain forecasting (2nd ed.)",
    "repo": NS,
    "local_id": BOOK_ID,
}


def snap(score: float | None) -> float | None:
    """Snap an off-rubric INFERRED score to the nearest allowed value (ties go LOWER)."""
    if score is None or score in RUBRIC:
        return score
    return min(RUBRIC, key=lambda r: (round(abs(r - score), 6), r))


def edge_key(source: str, target: str, relation: str | None) -> tuple:
    """Identity of an undirected edge; the graph declares multigraph=false."""
    return (frozenset((source, target)), relation)


def _rank(edge: dict) -> tuple:
    return (1 if edge.get("confidence") == "EXTRACTED" else 0, edge.get("confidence_score") or 0)


def build_extraction() -> tuple[dict[str, dict], list[dict]]:
    nodes: dict[str, dict] = {
        BOOK_ID: {**BOOK_NODE, "id": BOOK_ID, "source_file": BOOK_SLUG},
    }
    for local_id, label, loc, rationale in CONCEPTS:
        if local_id in nodes:
            raise SystemExit(f"duplicate concept id in extraction: {local_id}")
        nodes[local_id] = {
            "id": local_id,
            "label": label,
            "file_type": "concept",
            "source_file": BOOK_SLUG,
            "source_location": loc,
            "source_url": None,
            "captured_at": None,
            "author": None,
            "contributor": None,
            "rationale": rationale,
            "norm_label": label.lower(),
            "repo": NS,
            "local_id": local_id,
        }

    edges: list[dict] = []
    # The book node references every concept it introduces: that is what makes each
    # concept citable back to this source.
    for local_id, _label, loc, _r in CONCEPTS:
        edges.append({
            "relation": "references", "confidence": "EXTRACTED", "confidence_score": 1.0,
            "source_file": BOOK_SLUG, "source_location": loc, "weight": 1.0,
            "source": BOOK_ID, "target": local_id,
        })
    declared = set(nodes) | set(BRIDGES)
    for s, rel, t, conf, score in EDGES:
        for end in (s, t):
            if end not in declared:
                raise SystemExit(f"edge endpoint declared in neither CONCEPTS nor BRIDGES: {end}")
        edges.append({
            "relation": rel, "confidence": conf,
            "confidence_score": snap(score) if conf == "INFERRED" else score,
            "source_file": BOOK_SLUG, "source_location": None, "weight": 1.0,
            "source": s, "target": t,
        })
    return nodes, edges


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-apply even if already merged")
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = ap.parse_args()

    nodes, edges = build_extraction()
    print(f"extraction: {len(nodes)} nodes, {len(edges)} edges")

    base = json.loads(COMMITTED.read_text(encoding="utf-8"))
    base_links = base.get("links", base.get("edges", []))
    committed_ids = {n["id"] for n in base["nodes"] if "id" in n}

    def qualify(bare: str) -> str:
        return f"{NS}::{bare}"

    missing_bridges = [b for b in BRIDGES if qualify(b) not in committed_ids]
    if missing_bridges:
        print(
            "ABORT: BRIDGES ids absent from the committed graph (typo, or the concept was "
            f"renamed upstream): {', '.join(sorted(missing_bridges))}\n"
            "Left alone these would be added as new nodes carrying only this book's citation, "
            "quietly forking a concept that already exists under another id.",
            file=sys.stderr,
        )
        return 1

    if qualify(BOOK_ID) in committed_ids and not args.force:
        print(
            f"ABORT: {qualify(BOOK_ID)} is already in {COMMITTED.name} -- this book looks merged.\n"
            "Re-running would double every edge while the id-set guard still reported 'purely "
            "additive'. Restore the pre-merge graph (git checkout HEAD -- "
            "knowledge/scm-books/graph.json) and re-run, or pass --force.",
            file=sys.stderr,
        )
        return 1

    bridged: list[str] = []
    added: list[dict] = []
    for bare, n in nodes.items():
        qid = qualify(bare)
        if qid in committed_ids:
            bridged.append(bare)  # shared concept: keep the canonical node, edges form the bridge
            continue
        added.append({**n, "id": qid})

    merged_nodes = base["nodes"] + added
    merged_ids = {n["id"] for n in merged_nodes if "id" in n}

    seen: dict[tuple, dict] = {}
    for e in base_links:
        s, t = e.get("source"), e.get("target")
        if s is not None and t is not None:
            seen.setdefault(edge_key(s, t, e.get("relation")), e)

    new_links: list[dict] = []
    dropped_low_conf = dropped_dangling = collapsed = 0
    for e in edges:
        ne = {**e, "source": qualify(e["source"]), "target": qualify(e["target"])}
        if ne.get("confidence") == "INFERRED" and (ne.get("confidence_score") or 0) < MIN_INFERRED:
            dropped_low_conf += 1
            continue
        if ne["source"] not in merged_ids or ne["target"] not in merged_ids or ne["source"] == ne["target"]:
            dropped_dangling += 1
            continue
        key = edge_key(ne["source"], ne["target"], ne.get("relation"))
        prior = seen.get(key)
        if prior is not None:
            collapsed += 1
            if _rank(ne) > _rank(prior) and prior in new_links:
                new_links[new_links.index(prior)] = ne
                seen[key] = ne
            continue
        seen[key] = ne
        new_links.append(ne)

    merged_links = base_links + new_links
    out = {
        **{k: v for k, v in base.items() if k not in ("nodes", "links", "edges")},
        "nodes": merged_nodes,
        "links": merged_links,
    }

    # A "bridge" is any pre-existing canonical node this book's edges land on - whether it
    # came through CONCEPTS (the book defines it too) or BRIDGES (it only attaches to it).
    # Counting only the first understates the cross-book connection, which is the point of
    # merging into a shared graph rather than keeping a per-book island.
    new_ids = {n["id"] for n in added}
    touched_existing = sorted({
        end for e in new_links for end in (e["source"], e["target"]) if end not in new_ids
    })
    print()
    print(f"nodes  {len(base['nodes'])} -> {len(merged_nodes)}  (+{len(added)} new)")
    print(f"bridges  {len(touched_existing)} pre-existing canonical nodes gained an edge from this book "
          f"({len(bridged)} of them are concepts this book also defines)")
    print(f"edges  {len(base_links)} -> {len(merged_links)}  (+{len(new_links)}; dropped "
          f"{dropped_low_conf} low-confidence INFERRED, {dropped_dangling} dangling/self-loop, "
          f"collapsed {collapsed} duplicate parallel)")

    lost = committed_ids - merged_ids
    print()
    print(f"ID-SET GUARD: pre-existing ids lost in merge: {len(lost)}")
    if lost:
        print("  !! " + ", ".join(sorted(lost)[:20]))
        return 1
    print("  OK - every pre-existing node id survived (purely additive)")

    keys = [edge_key(e["source"], e["target"], e.get("relation")) for e in merged_links]
    dupes = len(keys) - len(set(keys))
    print(f"PARALLEL-EDGE GUARD: duplicate (endpoints, relation) links: {dupes}")
    if dupes:
        print("  !! graph declares multigraph=false; these will silently collapse on load")
        return 1
    print("  OK - no parallel links")

    orphans = [n["id"] for n in added
               if not any(n["id"] in (e["source"], e["target"]) for e in new_links)]
    print(f"ORPHAN GUARD: new nodes with no edge: {len(orphans)}")
    if orphans:
        print("  !! " + ", ".join(sorted(orphans)[:20]))
        return 1
    print("  OK - every new node is connected")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    if BACKUP.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        rotated = BACKUP.with_suffix(f".{stamp}.bak")
        shutil.move(str(BACKUP), str(rotated))
        print(f"rotated previous backup -> {rotated.name}")
    shutil.copy2(COMMITTED, BACKUP)
    COMMITTED.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"backup -> {BACKUP}")
    print(f"sample bridges: {[b.split('::')[-1] for b in touched_existing[:15]]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

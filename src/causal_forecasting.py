"""Causal multivariate demand forecasting with information criterion gating.

Combines a clean deterministic baseline with exogenous covariates (e.g. price
promotions, marketing uplift, calendar interventions), strictly gated by
Akaike's Information Criterion (AIC):
    AIC = 2*k + n * ln(SSE / n)

If AIC_causal <= AIC_univariate - aic_tolerance, the causal model is adopted
because the exogenous variables provide statistically significant explanatory
power beyond the penalized complexity. Otherwise, the engine refuses the
covariates to prevent overfitting and falls back to the robust univariate baseline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CausalForecastResult:
    """The result of causal multivariate forecasting."""

    forecast: float
    verdict: str  # "causal_accepted" | "univariate_fallback"
    method: str
    coefficients: dict[str, float]
    aic_univariate: float
    aic_causal: float
    delta_aic: float
    r_squared: float
    fva_pct: float
    covariates_retained: tuple[str, ...] = ()


def _compute_aic(sse: float, n: int, k: int) -> float:
    """Compute Akaike Information Criterion for least squares."""
    safe_sse = max(sse, 1e-12)
    return 2.0 * k + n * math.log(safe_sse / n)


def fit_causal_forecast(
    y: np.ndarray | list[float],
    covariates: dict[str, np.ndarray | list[float]],
    future_covariates: dict[str, float],
    *,
    aic_tolerance: float = 2.0,
    ridge_lambda: float = 1e-4,
) -> CausalForecastResult:
    """Fit a causal multivariate regression model against a univariate baseline with AIC gating.

    Parameters:
      y: Historical demand array (at least 6 periods).
      covariates: Named historical feature series of equal length as y.
      future_covariates: Values of each covariate for the upcoming target period.
      aic_tolerance: Minimum AIC reduction required to accept the causal model (default 2.0).
      ridge_lambda: L2 regularization parameter for numerical stability.
    """
    y_arr = np.asarray(y, dtype=float)
    n = len(y_arr)
    if n < 6:
        raise ValueError(f"at least 6 demand periods are required, got {n}")

    # 1. Fit univariate baseline: linear trend + intercept
    t_idx = np.arange(n, dtype=float)
    t_next = float(n)

    X_uni = np.column_stack([np.ones(n), t_idx])
    beta_uni, _, _, _ = np.linalg.lstsq(X_uni, y_arr, rcond=None)
    fitted_uni = X_uni @ beta_uni
    next_uni = float(np.dot([1.0, t_next], beta_uni))

    residuals_uni = y_arr - fitted_uni
    sse_uni = float(np.sum(residuals_uni ** 2))
    k_uni = 2  # intercept + trend
    aic_uni = _compute_aic(sse_uni, n=n, k=k_uni)

    if not covariates:
        return CausalForecastResult(
            forecast=max(0.0, next_uni),
            verdict="univariate_fallback",
            method="univariate_trend_baseline",
            coefficients={"intercept": float(beta_uni[0]), "trend": float(beta_uni[1])},
            aic_univariate=aic_uni,
            aic_causal=aic_uni,
            delta_aic=0.0,
            r_squared=0.0,
            fva_pct=0.0,
            covariates_retained=(),
        )

    # 2. Build multivariate design matrix: [1, trend, covar_1, covar_2, ...]
    covar_names = sorted(covariates.keys())
    x_cols = [np.ones(n), t_idx]
    x_future = [1.0, t_next]

    for name in covar_names:
        c_series = np.asarray(covariates[name], dtype=float)
        if len(c_series) != n:
            raise ValueError(f"covariate {name!r} has length {len(c_series)}, expected {n}")
        if name not in future_covariates:
            raise ValueError(f"missing future_covariates value for {name!r}")
        x_cols.append(c_series)
        x_future.append(float(future_covariates[name]))

    X = np.column_stack(x_cols)
    p = X.shape[1]

    # 3. Solve Ridge OLS: (X^T X + lambda*I)^(-1) X^T y
    XtX = X.T @ X
    ridge_eye = ridge_lambda * np.eye(p)
    ridge_eye[0, 0] = 0.0  # Do not penalize intercept
    ridge_eye[1, 1] = 0.0  # Do not penalize trend

    try:
        beta = np.linalg.solve(XtX + ridge_eye, X.T @ y_arr)
    except np.linalg.LinAlgError:
        # Collinear matrix: fallback to univariate
        return CausalForecastResult(
            forecast=max(0.0, next_uni),
            verdict="univariate_fallback",
            method="univariate_trend_baseline",
            coefficients={"intercept": float(beta_uni[0]), "trend": float(beta_uni[1])},
            aic_univariate=aic_uni,
            aic_causal=aic_uni,
            delta_aic=0.0,
            r_squared=0.0,
            fva_pct=0.0,
            covariates_retained=(),
        )

    # 4. Predict and evaluate causal model
    fitted_causal = X @ beta
    residuals_causal = y_arr - fitted_causal
    sse_causal = float(np.sum(residuals_causal ** 2))
    k_causal = p
    aic_causal = _compute_aic(sse_causal, n=n, k=k_causal)
    delta_aic = aic_causal - aic_uni

    # Total Sum of Squares for R^2
    y_mean = float(np.mean(y_arr))
    sst = float(np.sum((y_arr - y_mean) ** 2))
    r_squared = max(0.0, 1.0 - (sse_causal / sst)) if sst > 1e-12 else 0.0
    fva_pct = (sse_uni - sse_causal) / sse_uni if sse_uni > 1e-12 else 0.0

    coef_dict = {
        "intercept": float(beta[0]),
        "trend": float(beta[1]),
    }
    for i, name in enumerate(covar_names):
        coef_dict[name] = float(beta[i + 2])

    # 5. Information criterion gate:
    # Causal model is accepted ONLY if AIC improves by at least aic_tolerance (default 2.0)
    if delta_aic <= -aic_tolerance and sse_causal < sse_uni:
        future_pred = float(np.dot(x_future, beta))
        return CausalForecastResult(
            forecast=max(0.0, future_pred),
            verdict="causal_accepted",
            method="causal_multivariate_aic",
            coefficients=coef_dict,
            aic_univariate=aic_uni,
            aic_causal=aic_causal,
            delta_aic=delta_aic,
            r_squared=r_squared,
            fva_pct=fva_pct,
            covariates_retained=tuple(covar_names),
        )

    # Failed the gate: reject covariates to protect against overfitting
    return CausalForecastResult(
        forecast=max(0.0, next_uni),
        verdict="univariate_fallback",
        method="univariate_trend_baseline",
        coefficients=coef_dict,
        aic_univariate=aic_uni,
        aic_causal=aic_causal,
        delta_aic=delta_aic,
        r_squared=r_squared,
        fva_pct=max(0.0, fva_pct),
        covariates_retained=(),
    )

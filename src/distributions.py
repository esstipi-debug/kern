"""Gamma demand and distribution selection — Vandeput (2020), Chapter 9."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
from scipy import optimize
from scipy.stats import gamma, skew


class DemandDistribution(str, Enum):
    NORMAL = "normal"
    GAMMA = "gamma"


@dataclass(frozen=True)
class GammaParams:
    shape: float
    scale: float
    loc: float = 0.0
    mean: float = 0.0
    std: float = 0.0


@dataclass(frozen=True)
class DistributionFit:
    recommended: DemandDistribution
    observed_skewness: float
    normal_skewness: float
    gamma_skewness: float
    gamma_params: GammaParams | None


def fit_gamma(
    mean: float,
    std: float,
    minimum: float = 0.0,
) -> GammaParams:
    """Fit Gamma(k, theta) with optional offset d_min (Section 9.3.2)."""
    if std <= 0 or mean <= minimum:
        raise ValueError("invalid mean/std for gamma fit")
    mu_p = mean - minimum
    shape = mu_p**2 / std**2
    scale = std**2 / mu_p
    return GammaParams(shape=shape, scale=scale, loc=minimum, mean=mean, std=std)


def gamma_skewness(mean: float, std: float, minimum: float = 0.0) -> float:
    """gamma skewness = 2*sigma/mu (Section 9.3.1)."""
    mu_eff = mean - minimum
    if mu_eff <= 0:
        return 0.0
    return 2 * std / mu_eff


def select_distribution(
    data: np.ndarray,
    minimum: float | None = None,
) -> DistributionFit:
    """
    Skewness rule (Section 9.4.1): gamma if gamma1 > sigma/mu.
    """
    values = np.asarray(data, dtype=float)
    mu = float(np.mean(values))
    sigma = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
    gamma1 = float(skew(values)) if len(values) > 2 else 0.0
    d_min = float(minimum if minimum is not None else np.min(values))
    g_skew = gamma_skewness(mu, sigma, d_min)
    threshold = sigma / mu if mu > 0 else 0.0
    recommended = DemandDistribution.GAMMA if gamma1 > threshold else DemandDistribution.NORMAL
    params = fit_gamma(mu, sigma, d_min) if sigma > 0 else None
    return DistributionFit(
        recommended=recommended,
        observed_skewness=gamma1,
        normal_skewness=0.0,
        gamma_skewness=g_skew,
        gamma_params=params,
    )


def gamma_risk_period_params(
    mean_per_period: float,
    std_per_period: float,
    risk_periods: float,
    minimum: float = 0.0,
) -> GammaParams:
    """d_tau ~ Gamma(tau*k_d, theta_d) or offset variant (Table 9.2)."""
    mu_x = mean_per_period * risk_periods
    sigma_x = std_per_period * (risk_periods**0.5)
    x_min = minimum * risk_periods
    return fit_gamma(mu_x, sigma_x, x_min)


def inventory_for_cycle_service_gamma(
    cycle_service_level: float,
    gamma_params: GammaParams,
) -> float:
    """iota = F_Gamma^{-1}(alpha) (Section 9.5.2)."""
    return float(
        gamma.ppf(
            cycle_service_level,
            gamma_params.shape,
            loc=gamma_params.loc,
            scale=gamma_params.scale,
        )
    )


def gamma_loss(inventory: float, mean: float, std: float, loc: float = 0.0) -> float:
    """Expected units short under gamma demand (Section 9.5.3)."""
    inv_adj = inventory - loc
    mu = mean - loc
    if std <= 0 or mu <= 0:
        return max(mean - inventory, 0.0)
    shape = mu**2 / std**2
    scale = std**2 / mu
    return float(
        shape * scale * (1 - gamma.cdf(inv_adj, shape + 1, scale=scale))
        - inv_adj * (1 - gamma.cdf(inv_adj, shape, scale=scale))
    )


def gamma_loss_inverse(
    mean: float,
    std: float,
    cycle_demand: float,
    target_fill_rate: float,
    loc: float = 0.0,
) -> float:
    """Solve for iota targeting fill rate beta (Section 9.5.3)."""
    target = cycle_demand * (1 - target_fill_rate)

    def objective(inv: float) -> float:
        return abs(gamma_loss(inv, mean, std, loc) - target)

    lower = loc
    upper = loc + mean + 5 * std
    result = optimize.minimize_scalar(objective, bounds=(lower, upper), method="bounded")
    return float(result.x)


def safety_stock_gamma(
    mean_risk: float,
    std_risk: float,
    cycle_service_level: float,
    minimum: float = 0.0,
) -> tuple[float, float]:
    """Returns (order_up_to iota, safety stock Ss)."""
    params = fit_gamma(mean_risk, std_risk, minimum)
    iota = inventory_for_cycle_service_gamma(cycle_service_level, params)
    ss = iota - mean_risk
    return iota, ss

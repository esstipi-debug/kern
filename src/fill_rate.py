"""Fill rate and normal loss function — Vandeput (2020), Chapter 7."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import optimize
from scipy.stats import norm

# Andrade & Sikorski (2016) polynomial for inverse standard normal loss (Section 7.3.2)
_INVERSE_LOSS_COEFFICIENTS = np.array(
    [
        4.41738119e-09,
        1.79200966e-07,
        3.01634229e-06,
        2.63537452e-05,
        1.12381749e-04,
        5.71289020e-06,
        -2.64198510e-03,
        -1.59986142e-02,
        -5.60399292e-02,
        -1.48968884e-01,
        -3.68776346e-01,
        -1.22551895e00,
        -8.99375602e-01,
    ]
)


@dataclass(frozen=True)
class FillRateResult:
    """Fill rate metrics for one policy cycle."""

    fill_rate: float
    expected_units_short: float
    cycle_service_level: float
    service_level_factor: float
    safety_stock: float


def normal_loss_standard(x: float | np.ndarray) -> float | np.ndarray:
    """Standard normal loss L_N(x) = phi(x) - x*(1 - Phi(x)) (Section 7.2.2)."""
    x_arr = np.asarray(x, dtype=float)
    result = norm.pdf(x_arr) - x_arr * (1.0 - norm.cdf(x_arr))
    if np.ndim(x_arr) == 0:
        return float(result)
    return result


def normal_loss(inventory: float, mean_demand: float, demand_std: float) -> float:
    """Normal loss L_N(inv; mu, sigma) = sigma * L_N((inv-mu)/sigma) (eq. 7.1)."""
    if demand_std <= 0:
        return max(mean_demand - inventory, 0.0)
    z = (inventory - mean_demand) / demand_std
    return demand_std * float(normal_loss_standard(z))


def inverse_standard_loss(target: float, *, use_solver: bool = False) -> float:
    """
    z_beta such that L_N(z) ~= target (Section 7.3.2).

    Uses Andrade & Sikorski polynomial by default; optional scipy solver.
    """
    if target <= 0:
        raise ValueError("target must be > 0")

    if use_solver:

        def objective(x: float) -> float:
            return abs(float(normal_loss_standard(x)) - target)

        result = optimize.minimize_scalar(objective, bounds=(-10, 10), method="bounded")
        return float(result.x)

    log_target = np.log(target)
    return float(np.polyval(_INVERSE_LOSS_COEFFICIENTS, log_target))


def fill_rate_from_inventory(
    inventory: float,
    cycle_demand: float,
    mean_demand_risk: float,
    demand_std_risk: float,
) -> FillRateResult:
    """
    beta = 1 - Us / dc (Section 7.3.1).

    inventory: on-hand at start of risk-period (iota).
    cycle_demand: expected demand over order cycle (Q or d*R).
    """
    if cycle_demand <= 0:
        raise ValueError("cycle_demand must be > 0")

    us = normal_loss(inventory, mean_demand_risk, demand_std_risk)
    beta = 1.0 - us / cycle_demand
    beta = max(0.0, min(1.0, beta))

    if demand_std_risk > 0:
        z = (inventory - mean_demand_risk) / demand_std_risk
        ss = inventory - mean_demand_risk
    else:
        z = 0.0
        ss = max(inventory - mean_demand_risk, 0.0)

    alpha = float(norm.cdf(z)) if demand_std_risk > 0 else (1.0 if inventory >= mean_demand_risk else 0.0)

    return FillRateResult(
        fill_rate=beta,
        expected_units_short=us,
        cycle_service_level=alpha,
        service_level_factor=z,
        safety_stock=max(ss, 0.0),
    )


def safety_stock_for_fill_rate(
    cycle_demand: float,
    demand_std_risk: float,
    target_fill_rate: float,
) -> FillRateResult:
    """
    Ss = z_beta * sigma_x where z_beta = L^{-1}(dc*(1-beta)/sigma_x) (Section 7.3.2).
    """
    if not 0 < target_fill_rate < 1:
        raise ValueError("target_fill_rate must be between 0 and 1")
    if demand_std_risk <= 0:
        raise ValueError("demand_std_risk must be > 0")

    target = cycle_demand * (1.0 - target_fill_rate) / demand_std_risk
    z_beta = inverse_standard_loss(target)
    ss = z_beta * demand_std_risk  # relative to mu_x=0 for Ss-only; caller adds mu_x

    return FillRateResult(
        fill_rate=target_fill_rate,
        expected_units_short=cycle_demand * (1.0 - target_fill_rate),
        cycle_service_level=float(norm.cdf(z_beta)),
        service_level_factor=z_beta,
        safety_stock=ss,
    )


def fill_rate_from_safety_stock(
    safety_stock: float,
    cycle_demand: float,
    demand_std_risk: float,
) -> float:
    """beta = 1 - (sigma_x/dc) * L_N(Ss/sigma_x) (Section 7.3.1)."""
    if cycle_demand <= 0 or demand_std_risk <= 0:
        raise ValueError("cycle_demand and demand_std_risk must be > 0")
    z = safety_stock / demand_std_risk
    us = demand_std_risk * float(normal_loss_standard(z))
    return max(0.0, min(1.0, 1.0 - us / cycle_demand))

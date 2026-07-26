"""Safety stock under normal or gamma demand."""

from __future__ import annotations

from typing import Literal

from src.distributions import fit_gamma, inventory_for_cycle_service_gamma
from src.safety_stock import SafetyStockResult, service_level_factor

DistributionKind = Literal["normal", "gamma", "auto"]


def safety_stock_risk_period(
    mean_risk: float,
    std_risk: float,
    cycle_service_level: float,
    risk_periods: float,
    *,
    distribution: DistributionKind = "normal",
    observed_skewness: float | None = None,
    minimum: float = 0.0,
) -> SafetyStockResult:
    """
    Ss for a risk-period demand distribution (Ch. 4 normal, Ch. 9 gamma).

    distribution='auto' uses gamma when observed skewness > std/mean.
    """
    z = service_level_factor(cycle_service_level)
    use_gamma = distribution == "gamma"
    if distribution == "auto" and mean_risk > 0 and observed_skewness is not None:
        use_gamma = observed_skewness > std_risk / mean_risk

    fitted_gamma = use_gamma and std_risk > 0
    if fitted_gamma:
        params = fit_gamma(mean_risk, std_risk, minimum)
        iota = inventory_for_cycle_service_gamma(cycle_service_level, params)
        ss = iota - mean_risk
    else:
        ss = z * std_risk

    return SafetyStockResult(
        safety_stock=ss,
        service_level_factor=z,
        cycle_service_level=cycle_service_level,
        risk_periods=risk_periods,
        # What was *fitted*, not what was *requested*: 'gamma' with std_risk == 0
        # silently runs the normal branch, and the report must say so.
        distribution="gamma" if fitted_gamma else "normal",
    )

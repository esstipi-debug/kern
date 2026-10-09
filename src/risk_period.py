"""Demand over risk-period — Vandeput (2020), Chapters 5–6."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskPeriodStats:
    """Demand statistics aggregated over the inventory risk period."""

    mean_demand: float
    demand_std: float
    risk_periods: float
    mean_lead_time: float
    review_period: float


def provisional_cover(
    mean_demand_per_period: float,
    mean_lead_time: float,
    review_period: float = 0.0,
) -> float:
    """Cobertura SIN buffer: la demanda media sobre el periodo de riesgo.

    Es ``mu_x`` de la ecuacion 6.4 -- el MISMO primer termino que
    ``demand_over_risk_period`` calcula y que ``src/policies.py`` usa como base del
    punto de reorden -- con el termino de seguridad OMITIDO, no puesto en cero.

    Existe porque hay un caso en el que el motor tiene con que cubrir y no tiene con
    que dimensionar: un SKU con menos de ``MIN_PERIODS_FOR_POLICY`` periodos
    observados. Ahi ``sigma_d`` no se puede estimar honestamente -- y sin sigma no
    hay ``z * sigma_x`` que valga --, pero la media de lo que SI se vendio y el plazo
    del proveedor son dos hechos medidos, y con ellos alcanza para decir cuanto hace
    falta para llegar a la proxima reposicion.

    Lo que devuelve NO es una politica y no se puede presentar como una: no tiene
    nivel de servicio, no tiene buffer, y cubre la media -- o sea que se espera que
    se quede corta la mitad de las veces. Quien la consuma tiene que decir eso. El
    campo ``safety_stock`` en ``None`` es como el registro lo declara.
    """
    if mean_demand_per_period < 0:
        raise ValueError("mean_demand_per_period must be >= 0")
    if mean_lead_time < 0 or review_period < 0:
        raise ValueError("lead time / review parameters must be >= 0")
    return mean_demand_per_period * (mean_lead_time + review_period)


def demand_over_risk_period(
    mean_demand_per_period: float,
    demand_std_per_period: float,
    mean_lead_time: float,
    lead_time_std: float = 0.0,
    review_period: float = 0.0,
) -> RiskPeriodStats:
    """
    Combined stochastic demand and lead time (eq. 6.4–6.5).

    sigma_x = sqrt(tau * sigma_d^2 + sigma_L^2 * mu_d^2)
    where tau = L for (s,Q) or R+L for (R,S).
    """
    if mean_demand_per_period < 0 or demand_std_per_period < 0:
        raise ValueError("demand parameters must be >= 0")
    if mean_lead_time < 0 or lead_time_std < 0 or review_period < 0:
        raise ValueError("lead time / review parameters must be >= 0")

    tau = mean_lead_time + review_period
    mu_x = mean_demand_per_period * tau
    variance = tau * (demand_std_per_period**2) + (lead_time_std**2) * (mean_demand_per_period**2)
    sigma_x = math.sqrt(max(variance, 0.0))

    return RiskPeriodStats(
        mean_demand=mu_x,
        demand_std=sigma_x,
        risk_periods=tau,
        mean_lead_time=mean_lead_time,
        review_period=review_period,
    )

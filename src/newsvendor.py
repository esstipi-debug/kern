"""Newsvendor model — Vandeput (2020), Chapter 11."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from src.discrete_demand import DiscretePMF
from src.fill_rate import normal_loss


@dataclass(frozen=True)
class NewsvendorResult:
    optimal_quantity: float
    critical_ratio: float
    expected_profit: float
    expected_cost: float


def critical_ratio(unit_underage_cost: float, unit_overage_cost: float) -> float:
    """cu / (cu + co) (Section 11.3.5)."""
    if unit_underage_cost < 0 or unit_overage_cost < 0:
        raise ValueError("costs must be >= 0")
    total = unit_underage_cost + unit_overage_cost
    if total == 0:
        raise ValueError("sum of costs must be > 0")
    return unit_underage_cost / total


def expected_profit_discrete(
    order_quantity: float,
    pmf: DiscretePMF,
    price: float,
    unit_cost: float,
    salvage_value: float = 0.0,
) -> float:
    """P(Q) = p*E[S] - c*Q + v*E[(Q-D)+] (Section 11.3)."""
    q = order_quantity
    sales = sum(min(q, d) * p for d, p in zip(pmf.values, pmf.probabilities))
    excess = sum(max(0, q - d) * p for d, p in zip(pmf.values, pmf.probabilities))
    return price * sales - unit_cost * q + salvage_value * excess


def expected_cost_discrete(
    order_quantity: float,
    pmf: DiscretePMF,
    unit_overage_cost: float,
    unit_underage_cost_with_profit: float,
) -> float:
    """C(Q) from eq. 11.2 — cu includes lost profit."""
    q = order_quantity
    excess = sum(max(0, q - d) * p for d, p in zip(pmf.values, pmf.probabilities))
    short = sum(max(0, d - q) * p for d, p in zip(pmf.values, pmf.probabilities))
    return unit_overage_cost * excess + unit_underage_cost_with_profit * short


def optimal_newsvendor_discrete(
    pmf: DiscretePMF,
    price: float,
    unit_cost: float,
    salvage_value: float = 0.0,
    goodwill: float = 0.0,
) -> NewsvendorResult:
    """
    Optimize Q over PMF support (Section 11.3).

    co = c - sv; cu = p - c + g for cost minimization with profit in cu.
    """
    co = unit_cost - salvage_value
    cu = price - unit_cost + goodwill
    cr = critical_ratio(cu, co)

    # Critical-ratio rule: smallest Q with F(Q) >= cr (Section 11.3.4).
    # Candidates are the PMF's own support values, unmodified: truncating them
    # through int() previously collapsed any non-integer support (e.g. {0, 2.5,
    # 5}) onto the wrong candidate ({0, 2, 5}), so the optimizer could never
    # even consider ordering the true support value.
    cdf_q = sorted(set(float(v) for v in pmf.values) | {0.0})
    cr_q = cdf_q[-1]
    for q in cdf_q:
        if pmf.cdf(q) >= cr:
            cr_q = q
            break

    candidates = cdf_q
    best_q = cr_q
    best_profit = expected_profit_discrete(cr_q, pmf, price, unit_cost, salvage_value)
    best_cost = expected_cost_discrete(cr_q, pmf, co, cu)
    for q in candidates:
        profit = expected_profit_discrete(q, pmf, price, unit_cost, salvage_value)
        cost = expected_cost_discrete(q, pmf, co, cu)
        if profit > best_profit + 1e-9 or (
            abs(profit - best_profit) <= 1e-9 and q < best_q
        ):
            best_profit = profit
            best_q = q
            best_cost = cost

    return NewsvendorResult(
        optimal_quantity=float(best_q),
        critical_ratio=cr,
        expected_profit=best_profit,
        expected_cost=best_cost,
    )


def optimal_newsvendor_continuous_normal(
    mean_demand: float,
    std_demand: float,
    price: float,
    unit_cost: float,
    salvage_value: float = 0.0,
) -> NewsvendorResult:
    """Q* = F^{-1}(cu/(cu+co)) for normal demand (Section 11.3.5).

    Demand with zero (or negative, treated as zero) variability is deterministic:
    the optimal quantity is exactly ``mean_demand`` regardless of the critical
    ratio, since there is no uncertainty to hedge against. ``norm.ppf`` with
    ``scale=0`` is undefined (NaN); that NaN previously flowed through
    ``max(0, q_star)`` into a silent ``Q*=0`` - understocking by the full mean
    demand - paired with a hardcoded, never-computed ``expected_profit=0.0``.
    """
    co = unit_cost - salvage_value
    cu = price - unit_cost
    cr = critical_ratio(cu, co)
    if std_demand <= 0:
        q_star = max(0.0, float(mean_demand))
    else:
        q_star = max(0.0, float(norm.ppf(cr, loc=mean_demand, scale=std_demand)))

    expected_shortage = normal_loss(q_star, mean_demand, std_demand)  # E[max(D-Q,0)]
    expected_excess = expected_shortage + (q_star - mean_demand)  # E[max(Q-D,0)]
    expected_sales = mean_demand - expected_shortage  # E[min(Q,D)]
    expected_profit = price * expected_sales - unit_cost * q_star + salvage_value * expected_excess
    expected_cost = co * expected_excess + cu * expected_shortage

    return NewsvendorResult(
        optimal_quantity=q_star,
        critical_ratio=cr,
        expected_profit=expected_profit,
        expected_cost=expected_cost,
    )


def muffin_pmf() -> DiscretePMF:
    """Table 11.1 — chocolate muffin example."""
    values = np.array([0, 2, 4, 6, 8, 10], dtype=int)
    probs = np.array([0.40, 0.20, 0.20, 0.10, 0.05, 0.05])
    return DiscretePMF(values=values, probabilities=probs)

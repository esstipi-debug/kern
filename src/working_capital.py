"""Working-capital / cash-release engine (capability gap #3, the CFO lens).

Turns the cash-to-cash cycle into dollars: how much working capital each cycle day
ties up, and how much cash is freed by improving each lever. Inventory and payables
scale on COGS (they are carried at cost); receivables scale on revenue (billed at
sales value). Reducing DIO or DSO, or extending DPO, releases cash one-for-one with
the drop in net working capital.

Composes ``financial_kpis.cash_to_cash`` for the cycle length so the two modules can
never disagree. Pure / deterministic. Reference: SCOR cash-to-cash (AM.1.1); Chopra &
Meindl, working-capital levers; retail-math DIO/DSO/DPO.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.financial_kpis import cash_to_cash
from src.finite import require_finite

_DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class WorkingCapital:
    """The cash cycle decomposed into the dollars tied up in each pool."""

    dio: float
    dso: float
    dpo: float
    cash_conversion_cycle: float    # dio + dso - dpo (days)
    inventory_investment: float     # cogs / days * dio
    receivables: float              # revenue / days * dso
    payables: float                 # cogs / days * dpo
    net_working_capital: float      # inventory + receivables - payables


@dataclass(frozen=True)
class CashRelease:
    """Cash freed by improving one lever by ``days_improved`` days."""

    lever: str
    days_improved: float
    cash_released: float


@dataclass(frozen=True)
class CashReleasePlan:
    """The set of lever improvements and the total cash they free."""

    levers: tuple[CashRelease, ...]
    total_cash_released: float


def working_capital(
    *, revenue: float, cogs: float, dio: float, dso: float, dpo: float, days: int = _DAYS_PER_YEAR
) -> WorkingCapital:
    """Decompose the cash-to-cash cycle into the dollars it ties up.

    Every leg must be a real number: ``cash_to_cash`` returns ``None`` for a
    non-finite input, and every formatter of ``cash_conversion_cycle`` takes a float.
    Refusing here keeps that contract instead of shipping a ``None`` downstream.
    """
    for name, value in (("revenue", revenue), ("cogs", cogs),
                        ("dio", dio), ("dso", dso), ("dpo", dpo)):
        require_finite(value, name)
    inventory = cogs / days * dio
    receivables = revenue / days * dso
    payables = cogs / days * dpo
    return WorkingCapital(
        dio=dio,
        dso=dso,
        dpo=dpo,
        cash_conversion_cycle=cash_to_cash(dio, dso, dpo),
        inventory_investment=inventory,
        receivables=receivables,
        payables=payables,
        net_working_capital=inventory + receivables - payables,
    )


def cash_release_plan(
    *,
    revenue: float,
    cogs: float,
    dio_days: float = 0.0,
    dso_days: float = 0.0,
    dpo_days: float = 0.0,
    days: int = _DAYS_PER_YEAR,
) -> CashReleasePlan:
    """Cash freed by trimming DIO / DSO days or extending DPO days. Only the levers that
    move are listed."""
    candidates = (
        ("inventory (DIO)", dio_days, cogs / days * dio_days),
        ("receivables (DSO)", dso_days, revenue / days * dso_days),
        ("payables (DPO)", dpo_days, cogs / days * dpo_days),
    )
    levers = tuple(
        CashRelease(lever=name, days_improved=d, cash_released=cash)
        for name, d, cash in candidates
        if d
    )
    return CashReleasePlan(
        levers=levers,
        total_cash_released=sum(r.cash_released for r in levers),
    )

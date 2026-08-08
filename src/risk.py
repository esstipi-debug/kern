"""Supply-chain risk engine (Kern capability M-risk).

Scores a risk register on the standard SCM dimensions (likelihood x impact), ranks by
expected loss (EMV) and FMEA RPN, buckets into a 5x5 heatmap, flags TTR>TTS exposure
(Simchi-Levi), and ranks mitigation options by net benefit. Pure stdlib + deterministic.

Grounded in L3: Likelihood-Impact Risk Assessment, Five Sources of Supply Chain Risk,
Risk Mitigation (Redundancy vs Flexibility), TTR/TTS exposure (Simchi-Levi), FMEA RPN.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

CATEGORIES = (
    "demand", "supply", "operational", "logistics", "financial",
    "geopolitical", "cyber", "environmental", "reputational", "concentration",
)
DEFAULT_SEVERITY_THRESHOLDS = (10_000.0, 50_000.0, 250_000.0, 1_000_000.0)


@dataclass(frozen=True)
class MitigationOption:
    """A candidate control: its cost and how much it cuts likelihood and/or impact."""

    name: str
    kind: str                       # redundancy | flexibility | control | transfer | accept
    cost: float
    likelihood_reduction: float = 0.0
    impact_reduction: float = 0.0


@dataclass(frozen=True)
class RiskFactor:
    """One row of the risk register: a hazard with its likelihood, impact and timing."""

    name: str
    category: str
    likelihood: float               # annual probability 0..1
    impact_value: float             # $ loss if it occurs
    exposure: float = 0.0
    velocity_days: float = 30.0
    detectability_days: float = 7.0
    time_to_recover: float = 0.0    # TTR
    time_to_survive: float = 0.0    # TTS
    owner: str = ""
    mitigations: tuple[MitigationOption, ...] = ()


@dataclass(frozen=True)
class RankedMitigation:
    """A mitigation scored against the bare risk: residual EMV, net benefit and ROI."""

    name: str
    kind: str
    cost: float
    residual_emv: float
    risk_reduction: float
    net_benefit: float
    roi: float
    recommended: bool = False
    #: Days between noticing the hazard and being hit by it -- the window in which the
    #: control still has time to matter. Zero when TTR > TTS (already exposed) or when
    #: detection is slower than the hazard.
    days_available: float = 0.0
    #: The date that window closes. A mitigation nobody committed to a date is a
    #: suggestion, not an action -- and the risk register is read as a plan.
    by_when: date | None = None


@dataclass(frozen=True)
class RiskAssessment:
    """A fully-scored risk: 5x5 ratings, EMV, FMEA RPN, exposure gap and ranked mitigations."""

    name: str
    category: str
    occurrence: int
    severity: int
    detection: int
    score: int
    zone: str
    emv: float
    rpn: int
    exposure: float
    exposure_gap_days: float
    mitigations: tuple[RankedMitigation, ...]
    recommended: str


@dataclass(frozen=True)
class RiskReport:
    """Portfolio roll-up: assessments ranked by EMV, totals, heatmap and the worst gap."""

    assessments: tuple[RiskAssessment, ...]   # ranked by EMV desc
    total_emv: float
    residual_emv: float
    heatmap: dict
    top_exposure_gap: tuple[str, float]


def _rate_occurrence(p: float) -> int:
    if p < 0.05:
        return 1
    if p < 0.15:
        return 2
    if p < 0.30:
        return 3
    if p < 0.50:
        return 4
    return 5


def _rate_severity(v: float, thresholds: tuple[float, ...]) -> int:
    for i, thr in enumerate(thresholds):
        if v < thr:
            return i + 1
    return 5


def _rate_detection(days: float) -> int:
    if days < 1:
        return 1
    if days < 7:
        return 2
    if days < 30:
        return 3
    if days < 90:
        return 4
    return 5


def _zone(score: int) -> str:
    if score <= 4:
        return "low"
    if score <= 9:
        return "medium"
    if score <= 15:
        return "high"
    return "critical"


def warning_window_days(risk: RiskFactor) -> float:
    """Days of usable warning: how long the hazard takes to bite, minus how long it
    takes to notice it. Never negative -- detection slower than the hazard means you
    find out afterwards, which is no window at all.

    Zero when TTR > TTS: the Simchi-Levi exposure gap says the hole is already there,
    so nothing about this risk can be scheduled for later.
    """
    if risk.time_to_recover > risk.time_to_survive:
        return 0.0
    return max(0.0, risk.velocity_days - risk.detectability_days)


def rank_mitigations(
    risk: RiskFactor, *, as_of: date | None = None
) -> tuple[RankedMitigation, ...]:
    """Score every mitigation (plus an always-present "Accept / monitor") by net benefit.

    ``as_of`` anchors the deadline and defaults to today; it is injectable so the
    deliverable is reproducible and tests are deterministic.
    """
    dias = warning_window_days(risk)
    vence = (as_of or date.today()) + timedelta(days=dias)
    base = risk.likelihood * risk.impact_value
    options = list(risk.mitigations) + [MitigationOption("Accept / monitor", "accept", 0.0)]
    ranked = []
    for m in options:
        resid = risk.likelihood * (1 - m.likelihood_reduction) * risk.impact_value * (1 - m.impact_reduction)
        reduction = base - resid
        net = reduction - m.cost
        roi = reduction / m.cost if m.cost > 0 else float("inf")
        ranked.append(RankedMitigation(
            m.name, m.kind, m.cost, resid, reduction, net, roi,
            days_available=dias, by_when=vence,
        ))
    ranked.sort(key=lambda r: r.net_benefit, reverse=True)
    best = ranked[0]
    return tuple(
        RankedMitigation(
            r.name, r.kind, r.cost, r.residual_emv, r.risk_reduction, r.net_benefit, r.roi,
            recommended=(r is best), days_available=r.days_available, by_when=r.by_when,
        )
        for r in ranked
    )


def assess(
    risk: RiskFactor,
    *,
    severity_thresholds: tuple[float, ...] = DEFAULT_SEVERITY_THRESHOLDS,
    as_of: date | None = None,
) -> RiskAssessment:
    """Rate one risk on occurrence/severity/detection and rank its mitigations."""
    o = _rate_occurrence(risk.likelihood)
    s = _rate_severity(risk.impact_value, severity_thresholds)
    d = _rate_detection(risk.detectability_days)
    mits = rank_mitigations(risk, as_of=as_of)
    rec = next((m.name for m in mits if m.recommended), "Accept / monitor")
    return RiskAssessment(
        risk.name, risk.category, o, s, d, o * s, _zone(o * s),
        risk.likelihood * risk.impact_value, s * o * d, risk.exposure,
        max(0.0, risk.time_to_recover - risk.time_to_survive), mits, rec,
    )


def assess_portfolio(
    risks,
    *,
    severity_thresholds: tuple[float, ...] = DEFAULT_SEVERITY_THRESHOLDS,
    as_of: date | None = None,
) -> RiskReport:
    """Assess every risk, rank by EMV, and roll up totals, the heatmap and the worst gap.

    ``as_of`` is resolved once here, not per risk: a portfolio whose deadlines drifted
    because the clock ticked mid-run would not be reproducible.
    """
    hoy = as_of or date.today()
    a = sorted(
        (assess(r, severity_thresholds=severity_thresholds, as_of=hoy) for r in risks),
        key=lambda x: x.emv, reverse=True,
    )
    total = sum(x.emv for x in a)
    residual = sum(next(m.residual_emv for m in x.mitigations if m.recommended) for x in a)
    heat = {z: sum(1 for x in a if x.zone == z) for z in ("critical", "high", "medium", "low")}
    gap = max(((x.name, x.exposure_gap_days) for x in a), key=lambda t: t[1], default=("n/a", 0.0))
    return RiskReport(tuple(a), total, residual, heat, gap)

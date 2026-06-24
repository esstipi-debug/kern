"""Supply-chain risk engine (Linchpin capability M-risk).

Scores a risk register on the standard SCM dimensions (likelihood x impact), ranks by
expected loss (EMV) and FMEA RPN, buckets into a 5x5 heatmap, flags TTR>TTS exposure
(Simchi-Levi), and ranks mitigation options by net benefit. Pure stdlib + deterministic.

Grounded in L3: Likelihood-Impact Risk Assessment, Five Sources of Supply Chain Risk,
Risk Mitigation (Redundancy vs Flexibility), TTR/TTS exposure (Simchi-Levi), FMEA RPN.
"""

from __future__ import annotations

from dataclasses import dataclass

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


def rank_mitigations(risk: RiskFactor) -> tuple[RankedMitigation, ...]:
    """Score every mitigation (plus an always-present "Accept / monitor") by net benefit."""
    base = risk.likelihood * risk.impact_value
    options = list(risk.mitigations) + [MitigationOption("Accept / monitor", "accept", 0.0)]
    ranked = []
    for m in options:
        resid = risk.likelihood * (1 - m.likelihood_reduction) * risk.impact_value * (1 - m.impact_reduction)
        reduction = base - resid
        net = reduction - m.cost
        roi = reduction / m.cost if m.cost > 0 else float("inf")
        ranked.append(RankedMitigation(m.name, m.kind, m.cost, resid, reduction, net, roi))
    ranked.sort(key=lambda r: r.net_benefit, reverse=True)
    best = ranked[0]
    return tuple(
        RankedMitigation(
            r.name, r.kind, r.cost, r.residual_emv, r.risk_reduction, r.net_benefit, r.roi,
            recommended=(r is best),
        )
        for r in ranked
    )


def assess(
    risk: RiskFactor, *, severity_thresholds: tuple[float, ...] = DEFAULT_SEVERITY_THRESHOLDS
) -> RiskAssessment:
    """Rate one risk on occurrence/severity/detection and rank its mitigations."""
    o = _rate_occurrence(risk.likelihood)
    s = _rate_severity(risk.impact_value, severity_thresholds)
    d = _rate_detection(risk.detectability_days)
    mits = rank_mitigations(risk)
    rec = next((m.name for m in mits if m.recommended), "Accept / monitor")
    return RiskAssessment(
        risk.name, risk.category, o, s, d, o * s, _zone(o * s),
        risk.likelihood * risk.impact_value, s * o * d, risk.exposure,
        max(0.0, risk.time_to_recover - risk.time_to_survive), mits, rec,
    )


def assess_portfolio(
    risks, *, severity_thresholds: tuple[float, ...] = DEFAULT_SEVERITY_THRESHOLDS
) -> RiskReport:
    """Assess every risk, rank by EMV, and roll up totals, the heatmap and the worst gap."""
    a = sorted(
        (assess(r, severity_thresholds=severity_thresholds) for r in risks),
        key=lambda x: x.emv, reverse=True,
    )
    total = sum(x.emv for x in a)
    residual = sum(next(m.residual_emv for m in x.mitigations if m.recommended) for x in a)
    heat = {z: sum(1 for x in a if x.zone == z) for z in ("critical", "high", "medium", "low")}
    gap = max(((x.name, x.exposure_gap_days) for x in a), key=lambda t: t[1], default=("n/a", 0.0))
    return RiskReport(tuple(a), total, residual, heat, gap)

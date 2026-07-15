"""Audit-grade evidence engine: statistical sampling, GL tie-out, and calculation lineage.

Turns existing Linchpin inventory/COGS outputs into the raw material for audit-defensible
workpapers. Pure, clock-free functions (timestamps and identities enter as arguments at the
job boundary, mirroring ``Deliverable.prepared`` / ``ClientProfile.updated_at``). See
``documentation/AUDIT_EVIDENCE_MEMO.md`` and ``AUDIT_EVIDENCE_DESIGN.md``.

Standards anchors:
  - AU-C 530 / PCAOB AS 2315: audit sampling (attribute + monetary unit sampling).
  - AS 1105: sufficiency & appropriateness of evidence; .10 information produced by the entity.
  - AS 1215: re-performability and who/when documentation (drives the lineage record).

Statistical method: everything is computed in closed form, NOT looked up from a table. The
reliability factors are the Poisson/gamma quantiles the AICPA MUS tables tabulate (RF_0 at 5%
risk = 3.00, RF_1 = 4.75, RF_2 = 6.30), and attribute sizes are exact binomial (95% / TDR 10% /
0 expected -> n = 29). The AICPA guide tables therefore serve as *test fixtures* the math must
reproduce, not as hard-coded constants.

PROVISIONAL (see memo Q1): the MUS *planning* sample size with a non-zero expected misstatement
uses the expansion-factor method with the factor map ``_EXPANSION_FACTORS`` below. Those factors,
and the choice between the expansion-factor and confidence-factor planning methods, must be
confirmed against a specific AICPA Audit Sampling guide edition before this is relied on in a
real engagement. The zero-expected-error planning size, all evaluation math, and all attribute
math are exact and edition-independent.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from scipy.stats import beta, gamma

# Bump whenever any formula in this module changes; stamped onto every EvidenceRecord so a
# reviewer can tie an output back to the exact calculation that produced it (AS 1215).
FORMULA_VERSION = "1"


# --------------------------------------------------------------------------- #
# Reliability factors (shared by MUS planning and evaluation)
# --------------------------------------------------------------------------- #

def reliability_factor(errors: int, risk_of_incorrect_acceptance: float) -> float:
    """Poisson reliability factor for ``errors`` misstatements at the given risk.

    RF_k is the mean of a Poisson whose probability of <= k events equals the risk of incorrect
    acceptance -- i.e. the ``(1 - risk)`` quantile of a Gamma(shape=k+1, scale=1). This exactly
    reproduces the AICPA MUS confidence-factor tables (RF_0 @ 5% = 3.00, RF_1 = 4.75, RF_2 =
    6.30) without a lookup.
    """
    if errors < 0:
        raise ValueError("errors must be >= 0")
    if not 0 < risk_of_incorrect_acceptance < 1:
        raise ValueError("risk_of_incorrect_acceptance must be in (0, 1)")
    return float(gamma.ppf(1 - risk_of_incorrect_acceptance, errors + 1))


# --------------------------------------------------------------------------- #
# Attribute sampling (tests of controls) -- exact binomial
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class AttributePlan:
    sample_size: int
    acceptable_deviations: int
    confidence_level: float
    tolerable_deviation_rate: float
    expected_deviation_rate: float
    population_size: int | None
    formula_ref: str


@dataclass(frozen=True)
class AttributeEvaluation:
    sample_size: int
    deviations_found: int
    sample_deviation_rate: float
    achieved_upper_deviation_limit: float
    tolerable_deviation_rate: float
    conclusion: str  # "supports_reliance" | "does_not_support_reliance"
    formula_ref: str


def _upper_deviation_limit(sample_size: int, deviations: int, risk: float) -> float:
    """Clopper-Pearson one-sided upper bound on the deviation rate at confidence ``1 - risk``."""
    if deviations >= sample_size:
        return 1.0
    return float(beta.ppf(1 - risk, deviations + 1, sample_size - deviations))


def attribute_plan(
    *,
    confidence_level: float,
    tolerable_deviation_rate: float,
    expected_deviation_rate: float = 0.0,
    population_size: int | None = None,
    max_n: int = 5000,
) -> AttributePlan:
    """Smallest attribute sample whose upper deviation limit would still fall at or below the
    tolerable rate if the expected number of deviations is observed (AU-C 530 / AS 2315).

    Exact binomial via the Clopper-Pearson bound; with zero expected deviations this returns the
    classic AICPA attribute-table size (95% / TDR 10% / 0 -> 29). A finite-population correction
    is applied when ``population_size`` is given.
    """
    if not 0 < confidence_level < 1:
        raise ValueError("confidence_level must be in (0, 1)")
    if not 0 < tolerable_deviation_rate < 1:
        raise ValueError("tolerable_deviation_rate must be in (0, 1)")
    if not 0 <= expected_deviation_rate < tolerable_deviation_rate:
        raise ValueError("require 0 <= expected_deviation_rate < tolerable_deviation_rate")
    risk = 1 - confidence_level

    for n in range(1, max_n + 1):
        c = math.floor(expected_deviation_rate * n)
        if _upper_deviation_limit(n, c, risk) <= tolerable_deviation_rate:
            if population_size is not None and population_size > 0:
                # Finite-population correction: n' = n / (1 + n/N), rounded up.
                n = math.ceil(n / (1 + n / population_size))
                c = math.floor(expected_deviation_rate * n)
            return AttributePlan(
                sample_size=n,
                acceptable_deviations=c,
                confidence_level=confidence_level,
                tolerable_deviation_rate=tolerable_deviation_rate,
                expected_deviation_rate=expected_deviation_rate,
                population_size=population_size,
                formula_ref="AU-C 530 / AS 2315 attribute sampling; exact binomial (Clopper-Pearson)",
            )
    raise ValueError(f"no attribute sample <= max_n={max_n}; loosen the rates or raise max_n")


def attribute_evaluate(
    plan_or_size: AttributePlan | int,
    deviations_found: int,
    *,
    confidence_level: float | None = None,
    tolerable_deviation_rate: float | None = None,
) -> AttributeEvaluation:
    """Evaluate observed deviations: does the achieved upper deviation limit support reliance?"""
    if isinstance(plan_or_size, AttributePlan):
        n = plan_or_size.sample_size
        cl = plan_or_size.confidence_level
        tdr = plan_or_size.tolerable_deviation_rate
    else:
        n = plan_or_size
        if confidence_level is None or tolerable_deviation_rate is None:
            raise ValueError("confidence_level and tolerable_deviation_rate required with a bare size")
        cl = confidence_level
        tdr = tolerable_deviation_rate
    if n <= 0:
        raise ValueError("sample_size must be > 0")
    if not 0 <= deviations_found <= n:
        raise ValueError("deviations_found must be in [0, sample_size]")

    udl = _upper_deviation_limit(n, deviations_found, 1 - cl)
    return AttributeEvaluation(
        sample_size=n,
        deviations_found=deviations_found,
        sample_deviation_rate=deviations_found / n,
        achieved_upper_deviation_limit=udl,
        tolerable_deviation_rate=tdr,
        conclusion="supports_reliance" if udl <= tdr else "does_not_support_reliance",
        formula_ref="AU-C 530 / AS 2315; Clopper-Pearson upper deviation limit",
    )


# --------------------------------------------------------------------------- #
# Monetary unit sampling (substantive tests of details)
# --------------------------------------------------------------------------- #

# PROVISIONAL (memo Q1): expansion factors for planning with a non-zero expected misstatement.
# Keyed by risk of incorrect acceptance. Must be verified against the AICPA guide edition before
# reliance. The zero-expected-error path does not use these.
_EXPANSION_FACTORS: dict[float, float] = {
    0.01: 1.90, 0.05: 1.60, 0.10: 1.50, 0.15: 1.40,
    0.20: 1.30, 0.25: 1.25, 0.30: 1.20, 0.37: 1.15, 0.50: 1.00,
}


def _expansion_factor(risk: float) -> float:
    """Nearest tabulated expansion factor at or below ``risk`` (conservative)."""
    eligible = [r for r in _EXPANSION_FACTORS if r <= risk + 1e-9]
    key = max(eligible) if eligible else min(_EXPANSION_FACTORS)
    return _EXPANSION_FACTORS[key]


@dataclass(frozen=True)
class MusPlan:
    population_value: float
    tolerable_misstatement: float
    expected_misstatement: float
    risk_of_incorrect_acceptance: float
    reliability_factor: float
    sample_size: int
    sampling_interval: float
    top_stratum_threshold: float
    exclusions: str
    formula_ref: str


@dataclass(frozen=True)
class MusItem:
    unit_id: str
    book_value: float
    is_top_stratum: bool


@dataclass(frozen=True)
class MusSelection:
    selected: tuple[MusItem, ...]
    sampling_interval: float
    random_start: float
    excluded_zero_or_negative: int


@dataclass(frozen=True)
class MusMisstatement:
    unit_id: str
    book_value: float
    audited_value: float
    tainting: float
    projected: float
    is_top_stratum: bool


@dataclass(frozen=True)
class MusEvaluation:
    basic_precision: float
    projected_misstatement: float
    incremental_allowance: float
    upper_misstatement_limit: float
    tolerable_misstatement: float
    conclusion: str  # "accept" | "do_not_accept"
    per_item: tuple[MusMisstatement, ...]
    formula_ref: str


def mus_plan(
    *,
    population_value: float,
    tolerable_misstatement: float,
    expected_misstatement: float = 0.0,
    risk_of_incorrect_acceptance: float = 0.05,
) -> MusPlan:
    """Design a monetary-unit sampling plan (AU-C 530 / AS 2315, substantive test of details).

    Sample size n = ceil(PV * RF_0 / (TM - EM * EF)); with EM = 0 this collapses to the exact
    PV * RF_0 / TM. RF_0 is the Poisson zero-error reliability factor (not a table lookup). The
    interval is PV / n; any logical unit >= the interval is certainty-selected (top stratum).
    """
    if population_value <= 0:
        raise ValueError("population_value must be > 0")
    if tolerable_misstatement <= 0:
        raise ValueError("tolerable_misstatement must be > 0")
    if expected_misstatement < 0:
        raise ValueError("expected_misstatement must be >= 0")
    if not 0 < risk_of_incorrect_acceptance < 1:
        raise ValueError("risk_of_incorrect_acceptance must be in (0, 1)")

    rf0 = reliability_factor(0, risk_of_incorrect_acceptance)
    if expected_misstatement == 0:
        denom = tolerable_misstatement
        ref = "AU-C 530 / AS 2315 MUS; n = ceil(PV*RF0/TM), RF0 = Poisson zero-error factor"
    else:
        ef = _expansion_factor(risk_of_incorrect_acceptance)
        denom = tolerable_misstatement - expected_misstatement * ef
        if denom <= 0:
            raise ValueError(
                "expected misstatement too close to tolerable; MUS not appropriate (raise TM or "
                "lower EM)"
            )
        ref = (
            "AU-C 530 / AS 2315 MUS; expansion-factor method (PROVISIONAL factors, memo Q1); "
            "n = ceil(PV*RF0/(TM - EM*EF))"
        )
    n = math.ceil(population_value * rf0 / denom)
    interval = population_value / n
    return MusPlan(
        population_value=population_value,
        tolerable_misstatement=tolerable_misstatement,
        expected_misstatement=expected_misstatement,
        risk_of_incorrect_acceptance=risk_of_incorrect_acceptance,
        reliability_factor=rf0,
        sample_size=n,
        sampling_interval=interval,
        top_stratum_threshold=interval,
        exclusions="zero and negative book values have no selection chance; test separately",
        formula_ref=ref,
    )


def mus_select(
    items: Sequence[tuple[str, float]],
    plan: MusPlan,
    *,
    random_start: float,
) -> MusSelection:
    """Fixed-interval dollar-unit selection with a caller-supplied random start.

    Every dollar is a sampling unit, so larger balances are proportionally more likely; any item
    at or above the interval is certainty-selected (top stratum). Zero/negative balances are
    excluded and counted (they must be tested as a separate population). ``random_start`` is an
    argument (not drawn internally) so the selection is exactly re-performable -- the AS 1215
    re-performability requirement expressed in code.
    """
    interval = plan.sampling_interval
    if not 0 <= random_start < interval:
        raise ValueError("random_start must be in [0, sampling_interval)")

    selected: list[MusItem] = []
    excluded = 0
    cumulative = 0.0
    next_hit = random_start
    for unit_id, book in items:
        if book <= 0:
            excluded += 1
            continue
        top = book >= interval
        start, end = cumulative, cumulative + book
        if top:
            # Certainty item: guaranteed to contain >= one selection point; record once.
            selected.append(MusItem(unit_id=unit_id, book_value=book, is_top_stratum=True))
            cumulative = end
            while next_hit < cumulative:
                next_hit += interval
            continue
        if start <= next_hit < end:
            selected.append(MusItem(unit_id=unit_id, book_value=book, is_top_stratum=False))
            while next_hit < end:
                next_hit += interval
        cumulative = end
    return MusSelection(
        selected=tuple(selected),
        sampling_interval=interval,
        random_start=random_start,
        excluded_zero_or_negative=excluded,
    )


def mus_evaluate(
    plan: MusPlan,
    audited: Sequence[tuple[str, float, float]],
) -> MusEvaluation:
    """Project misstatements to an upper misstatement limit (Stringer bound) and compare to TM.

    ``audited`` is ``(unit_id, book_value, audited_value)`` for each sampled item. For items below
    the interval the tainting (book - audited)/book is projected over the interval; for
    top-stratum items the actual difference is used (no projection). UML = basic precision +
    projected misstatement + incremental allowance for sampling risk, evaluated with the Poisson
    reliability factors. UML > TM means the population is not accepted.
    """
    interval = plan.sampling_interval
    risk = plan.risk_of_incorrect_acceptance
    rf0 = reliability_factor(0, risk)
    basic_precision = rf0 * interval

    below: list[MusMisstatement] = []
    top_actual = 0.0
    per_item: list[MusMisstatement] = []
    for unit_id, book, aud in audited:
        if book <= 0:
            raise ValueError(f"audited item {unit_id!r} has non-positive book value")
        tainting = (book - aud) / book
        top = book >= interval
        if top:
            actual = book - aud
            top_actual += actual
            item = MusMisstatement(unit_id, book, aud, tainting, actual, True)
            per_item.append(item)
        else:
            projected = tainting * interval
            item = MusMisstatement(unit_id, book, aud, tainting, projected, False)
            per_item.append(item)
            below.append(item)

    # Overstatement Stringer bound: rank positive taintings descending, apply incremental factors.
    overstatements = sorted(
        (it for it in below if it.tainting > 0), key=lambda it: it.tainting, reverse=True
    )
    projected_total = sum(it.projected for it in below)  # net projection incl. understatements
    incremental = 0.0
    for k, it in enumerate(overstatements, start=1):
        rf_k = reliability_factor(k, risk)
        rf_prev = reliability_factor(k - 1, risk)
        incremental += (rf_k - rf_prev - 1.0) * it.projected

    uml = basic_precision + projected_total + incremental + top_actual
    return MusEvaluation(
        basic_precision=basic_precision,
        projected_misstatement=projected_total + top_actual,
        incremental_allowance=incremental,
        upper_misstatement_limit=uml,
        tolerable_misstatement=plan.tolerable_misstatement,
        conclusion="accept" if uml <= plan.tolerable_misstatement else "do_not_accept",
        per_item=tuple(per_item),
        formula_ref="AU-C 530 / AS 2315 MUS; Stringer bound with Poisson reliability factors",
    )


# --------------------------------------------------------------------------- #
# GL / trial-balance tie-out
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class GlLine:
    key: str
    amount: float


@dataclass(frozen=True)
class ReconcilingItem:
    label: str
    amount: float
    kind: str  # "timing" | "unexplained" | "adjustment"


@dataclass(frozen=True)
class GlTieOut:
    gl_total: float
    subledger_total: float
    difference: float
    matched: int
    unmatched_gl: tuple[GlLine, ...]
    unmatched_subledger: tuple[GlLine, ...]
    reconciling_items: tuple[ReconcilingItem, ...]
    unexplained_value: float
    within_tolerance: bool
    tolerance: float


def _index(lines: Sequence[dict], key_field: str, amount_field: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in lines:
        if key_field not in row or amount_field not in row:
            raise ValueError(f"row missing {key_field!r}/{amount_field!r}: {row!r}")
        out[str(row[key_field])] = out.get(str(row[key_field]), 0.0) + float(row[amount_field])
    return out


def gl_tie_out(
    gl_lines: Sequence[dict],
    subledger_lines: Sequence[dict],
    *,
    key_field: str,
    amount_field: str,
    tolerance: float = 0.0,
    reconciling_items: Sequence[ReconcilingItem] = (),
) -> GlTieOut:
    """Tie a subledger (e.g. an inventory listing) to its GL control account.

    Matches lines by ``key_field``, totals each side, and reports the difference, lines present on
    only one side, and the value left unexplained after documented reconciling items. Identity
    (checked by the job's QA): gl_total - subledger_total == sum(reconciling_items) + unexplained.
    """
    if tolerance < 0:
        raise ValueError("tolerance must be >= 0")
    gl = _index(gl_lines, key_field, amount_field)
    sub = _index(subledger_lines, key_field, amount_field)

    gl_total = sum(gl.values())
    sub_total = sum(sub.values())
    difference = gl_total - sub_total

    matched = sum(1 for k in gl if k in sub)
    unmatched_gl = tuple(GlLine(k, v) for k, v in sorted(gl.items()) if k not in sub)
    unmatched_sub = tuple(GlLine(k, v) for k, v in sorted(sub.items()) if k not in gl)

    reconciled = sum(it.amount for it in reconciling_items)
    unexplained = difference - reconciled
    return GlTieOut(
        gl_total=gl_total,
        subledger_total=sub_total,
        difference=difference,
        matched=matched,
        unmatched_gl=unmatched_gl,
        unmatched_subledger=unmatched_sub,
        reconciling_items=tuple(reconciling_items),
        unexplained_value=unexplained,
        within_tolerance=abs(unexplained) <= tolerance,
        tolerance=tolerance,
    )


@dataclass(frozen=True)
class IpeAttestation:
    """AS 1105.10 completeness/accuracy record for information produced by the entity."""

    source_label: str
    row_count: int
    control_total: float
    tied_to: str
    tie_difference: float
    columns: tuple[str, ...]
    within_tolerance: bool


def ipe_attestation(
    lines: Sequence[dict],
    *,
    source_label: str,
    amount_field: str,
    tied_to: str,
    expected_total: float | None = None,
    tolerance: float = 0.0,
) -> IpeAttestation:
    """Build the on-the-workpaper record that company-produced data was tested for completeness
    and accuracy (row count + control total agreed to an independent figure)."""
    if not lines:
        raise ValueError("lines is empty; nothing to attest")
    columns = tuple(sorted(lines[0].keys()))
    control_total = sum(float(r[amount_field]) for r in lines)
    tie_difference = 0.0 if expected_total is None else control_total - expected_total
    return IpeAttestation(
        source_label=source_label,
        row_count=len(lines),
        control_total=control_total,
        tied_to=tied_to,
        tie_difference=tie_difference,
        columns=columns,
        within_tolerance=abs(tie_difference) <= tolerance,
    )


# --------------------------------------------------------------------------- #
# Calculation lineage / evidence record (read-path sibling of writeback.py)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class InputArtifact:
    path_label: str
    sha256: str
    n_rows: int
    columns: tuple[str, ...]
    control_total: float | None = None


@dataclass(frozen=True)
class QaCheck:
    """A positive attestation: what was checked, what was compared, and whether it passed.

    Unlike the orchestrator's list-of-failures QA, this records the checks that PASSED too, so the
    workpaper shows the review actually performed (AS 1105 appropriateness / AS 1215)."""

    name: str
    compared: str
    passed: bool


@dataclass(frozen=True)
class EvidenceRecord:
    run_id: str
    inputs: tuple[InputArtifact, ...]
    params_used: tuple[tuple[str, str], ...]
    formula_versions: tuple[tuple[str, str], ...]
    qa_attestation: tuple[QaCheck, ...]
    produced_at: str  # caller-supplied ISO timestamp; engine stays clock-free
    prepared_by: str  # engine identity, never a human name (the human signs at HANDOFF)
    output_sha256: str


def hash_file(path: str | Path, *, chunk_size: int = 65536) -> str:
    """Streaming SHA-256 of a file, for the input-provenance fingerprint."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def evidence_record(
    *,
    run_id: str,
    inputs: Sequence[InputArtifact],
    params_used: dict,
    qa_attestation: Sequence[QaCheck],
    output_payload: object,
    produced_at: str,
    prepared_by: str = f"linchpin/audit_evidence v{FORMULA_VERSION} (engine)",
    formula_versions: Sequence[tuple[str, str]] = (),
) -> EvidenceRecord:
    """Assemble a lineage record: input hashes, params as actually used, formula version, a
    positive QA attestation, and a hash of the output. Everything a reviewer needs to re-perform
    and to establish provenance (AS 1215 experienced-auditor / re-performability)."""
    if not run_id:
        raise ValueError("run_id is required")
    if not produced_at:
        raise ValueError("produced_at is required (engine is clock-free; pass the timestamp)")
    versions = tuple(formula_versions) or (("audit_evidence", FORMULA_VERSION),)
    params = tuple(sorted((str(k), str(v)) for k, v in params_used.items()))
    return EvidenceRecord(
        run_id=run_id,
        inputs=tuple(inputs),
        params_used=params,
        formula_versions=versions,
        qa_attestation=tuple(qa_attestation),
        produced_at=produced_at,
        prepared_by=prepared_by,
        output_sha256=_canonical_hash(output_payload),
    )


def evidence_to_dict(record: EvidenceRecord) -> dict:
    """Plain-dict view for serialization to ``evidence.json`` next to a workpaper."""
    return asdict(record)

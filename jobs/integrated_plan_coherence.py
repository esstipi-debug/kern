"""What A5's coherence checks are allowed to be COUNTED AS: the tally that keeps
an evaluated verdict apart from a check nobody could evaluate, and the verdict
that reads out of it.

Split out of ``jobs/integrated_plan.py``, which composes the pipeline (forecast
-> demand shaping -> purchase plan -> coherence checks -> cash calendar) and had
grown past the repo's 800-line file ceiling. The cut is along the axis, not along
an arbitrary line count: everything here answers ONE question -- given the
engine's raw results plus the record of which optional purchase inputs the stock
file never stated, which of those results is a reading and which is an absence
wearing a reading's clothes. ``src/sop_engine/`` is split by axis the same way,
and ``jobs/integrated_plan_cash.py`` is this module's older sibling.

Why the provenance lives on THIS side of the engine boundary:
``src.sop_engine.coherence`` never sees a CSV cell, so a ``0.0`` reaches it
indistinguishable from a ``0.0`` the client typed. Only the layer that read the
cell can tell the two apart (``jobs/risk_job.py``'s split, verbatim), and this
module is that layer's half of it. Nothing here re-runs a check; it re-reads the
ones the engine already ran.

Every name here travels UNCHANGED from the file it came out of, underscore
prefixes included, so the move can be checked by diffing bodies instead of being
re-read as a rewrite. ``jobs/intake.py``'s ``_norm``, imported by two other jobs,
is the same shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.sop_engine.coherence import (
    CHECK_BUDGET_FEASIBILITY,
    CHECK_PROMO_COVERAGE,
    CHECK_SERVICE_LEVEL,
    CoherenceResult,
    UnevaluableCheck,
)

# -- coherence verdict vocabulary ---------------------------------------------
# Named like jobs/cross_impact_job.py's: INDETERMINATE is a first-class outcome,
# not an error, and it outranks the clean verdict on purpose.
COHERENT = "coherent"
INCOHERENT = "incoherent"
INDETERMINATE = "indeterminate"
VERDICTS = (COHERENT, INCOHERENT, INDETERMINATE)

# Which purchase input each coherence check's PER-SKU verdict rests on. ``None``
# means the check reads no per-SKU purchase input at all (the budget check is
# portfolio-level by construction). A check kind absent from this map has no
# declared grounding and :func:`_grounding_column` raises rather than assume it
# is safe -- packs/source_to_pay/recovery.py's ``_unquantified`` rule: a code
# nobody classified must not fall through as zero.
_GROUNDING_COLUMN: dict[str, str | None] = {
    CHECK_SERVICE_LEVEL: "reorder_point",
    CHECK_PROMO_COVERAGE: "incoming_po",
    CHECK_BUDGET_FEASIBILITY: None,
}


@dataclass(frozen=True)
class CoherenceTally:
    """The coherence checks split into the three answers they can have: a pass,
    a fail, and the absence of either.

    The count that used to ship read "N/M passed" with M = every check RUN. A
    check nobody could evaluate inflated both sides of that fraction, so the
    fraction certified itself. Here M is what was EVALUATED, and the checks that
    were not are counted apart, named, and carried with the input whose absence
    cost them their verdict. The counts are derived, never stored, so no caller
    can hand this object a total that its own contents contradict.
    """

    passed: tuple[CoherenceResult, ...]
    failed: tuple[CoherenceResult, ...]
    unevaluable: tuple[UnevaluableCheck, ...]

    @property
    def n_passed(self) -> int:
        return len(self.passed)

    @property
    def n_failed(self) -> int:
        return len(self.failed)

    @property
    def n_evaluated(self) -> int:
        return len(self.passed) + len(self.failed)

    @property
    def n_unevaluable(self) -> int:
        return len(self.unevaluable)

    @property
    def n_total(self) -> int:
        return self.n_evaluated + self.n_unevaluable

    @property
    def missing_columns(self) -> tuple[str, ...]:
        """The distinct inputs whose absence cost a check its verdict, for the ask."""
        return tuple(sorted({u.column for u in self.unevaluable}))

    @property
    def headline(self) -> str:
        """The one line every summary, deck and KPI derives from, so the gap cannot
        survive in one rendering and vanish in another."""
        head = (
            "no coherence check could be evaluated" if not self.n_evaluated
            else f"{self.n_passed}/{self.n_evaluated} evaluated coherence check(s) passed"
        )
        if self.n_failed:
            head += f", {self.n_failed} FAILED"
        if self.unevaluable:
            head += (f", {self.n_unevaluable} NOT EVALUABLE "
                     f"({', '.join(self.missing_columns)} not supplied)")
        return head


def _verdict(tally: CoherenceTally) -> str:
    """First match wins. INDETERMINATE outranks INCOHERENT and COHERENT alike: a
    plan with an unmeasured check has not been found coherent OR incoherent, and
    a declared gap must never be reported as either (jobs/cross_impact_job.py's
    ``_global_verdict``)."""
    if tally.unevaluable:
        return INDETERMINATE
    if tally.failed:
        return INCOHERENT
    return COHERENT


def _grounding_column(result: CoherenceResult) -> str | None:
    """Which purchase input this check's per-SKU verdict RESTS ON, or ``None``
    when it rests on no per-SKU purchase input at all.

    Raises for a check kind nobody classified. A fourth check added to the engine
    must not default to "evaluable": that default is precisely how a verdict built
    on a filled-in number ships unnoticed, so the cost of adding a check is one
    line in ``_GROUNDING_COLUMN`` (the rule ``packs/source_to_pay/recovery.py``'s
    ``_unquantified`` already enforces for exception codes).
    """
    try:
        return _GROUNDING_COLUMN[result.check]
    except KeyError:
        raise KeyError(
            f"{result.check!r} has no declared grounding column: classify it explicitly "
            "in _GROUNDING_COLUMN (the purchase input its verdict reads, or None if it "
            "reads none) rather than letting it pass as evaluable by default"
        ) from None


def _unsupplied_grounding(
    result: CoherenceResult, unsupplied: dict[str, frozenset[str]]
) -> str | None:
    """The input this result's verdict rests on AND nobody supplied, or ``None``
    when the verdict stands on numbers that were actually stated.

    A PORTFOLIO-level result (``product_id is None``) reads no SKU's purchase
    inputs -- the promo check's "no SKU carries a demand lift, check not
    applicable" is a real, self-declared verdict about the demand plan, and the
    budget check is portfolio-level by construction -- so neither can be voided
    by a column some SKU happens to be missing.
    """
    column = _grounding_column(result)
    if column is None or result.product_id is None:
        return None
    return column if column in unsupplied.get(result.product_id, frozenset()) else None


def _absence_reason(result: CoherenceResult, column: str) -> str:
    return (
        f"{result.product_id}: the stock file states no {column}, so this check has no "
        f"number to compare against -- neither a pass nor a fail can be stated from an "
        f"unsupplied {column} (supply it, or the 0 you actually mean by it)."
    )


def _tally(
    checks: tuple[CoherenceResult, ...], unsupplied: dict[str, frozenset[str]]
) -> CoherenceTally:
    """Split the engine's results into verdicts and declared absences.

    Every result the engine produced is accounted for exactly once: a check whose
    grounding input was never supplied is not dropped, it is re-declared as the
    absence it always was, with the column named.
    """
    passed: list[CoherenceResult] = []
    failed: list[CoherenceResult] = []
    unevaluable: list[UnevaluableCheck] = []
    for c in checks:
        column = _unsupplied_grounding(c, unsupplied)
        if column is not None:
            unevaluable.append(
                UnevaluableCheck(c.check, c.product_id, column, _absence_reason(c, column))
            )
        elif c.passed:
            passed.append(c)
        else:
            failed.append(c)
    return CoherenceTally(tuple(passed), tuple(failed), tuple(unevaluable))

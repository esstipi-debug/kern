"""Shared negative-quantity counter for every tool that ingests quantities.

Counts, never mutates. The five quantity-ingesting tools each read their own
CSV (a deliberate convention -- only ``inventory_optimization`` goes through
``jobs/intake.py``), so the shared piece cannot be a single intake step: it is
this audit, computed at ``prepare()`` time, carried on the payload into
``run()``, hung on the report, and gated in the tool's ``qa()``.

Routing the signal into ``qa()`` is what makes it bite: ``qa_issues`` is the
only channel the orchestrator's "QA fails => no deliverable" gate respects.
An escalation raised later, in ``tool.options()``, runs AFTER that gate
(``scm_agent/orchestrator.py``: qa at line 156, options at line 169) and so can
never block a deliverable.

Kern cannot tell a legitimate return from an inverted sign from a mis-mapped
column, so it does not guess: negatives are reported, not reinterpreted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

#: Share of negative rows above which a FLOW column stops being explainable as
#: ordinary returns. Override per run with params["negative_quantity_threshold"].
DEFAULT_NEGATIVE_THRESHOLD = 0.02

FLOW = "flow"
LEVEL = "level"


@dataclass(frozen=True)
class QuantityAudit:
    """How many negative quantities a source file carried, and whether that is material.

    ``kind`` decides the materiality rule, because the two kinds of column fail
    differently:

    - ``FLOW`` (demand, sales, usage): an isolated negative row is a normal
      return. Material when the negative share exceeds ``threshold``, or when
      any entity's aggregate goes negative (which no volume of returns
      explains -- that is an inverted sign or a mis-mapped column).
    - ``LEVEL`` (on-hand stock): a negative level admits no "return" reading at
      all. It means the stock ledger is broken, so ANY negative is material.
    """

    column: str
    kind: str
    n_rows: int
    n_negative: int
    negative_total: float          # sum of the negative values; <= 0
    n_entities_net_negative: int
    threshold: float = DEFAULT_NEGATIVE_THRESHOLD

    @property
    def negative_fraction(self) -> float:
        return self.n_negative / self.n_rows if self.n_rows else 0.0

    @property
    def is_material(self) -> bool:
        if not self.n_negative:
            return False
        if self.kind == LEVEL:
            return True
        return self.negative_fraction > self.threshold or self.n_entities_net_negative > 0

    def issues(self) -> list[str]:
        """QA issues. Empty when there is nothing material to block on."""
        if not self.is_material:
            return []
        if self.kind == LEVEL:
            return [
                f"{self.n_negative} of {self.n_rows} row(s) carry a negative {self.column} "
                f"(total {self.negative_total:,.1f}) - a negative stock level is a broken "
                "ledger, not a return; correct the source file before planning on it"
            ]
        if self.n_entities_net_negative:
            return [
                f"{self.n_entities_net_negative} SKU(s) have a negative aggregate "
                f"{self.column} - returns cannot exceed sales, so this is an inverted "
                "sign or a mis-mapped column; correct the source file before planning on it"
            ]
        return [
            f"{self.n_negative} of {self.n_rows} row(s) carry a negative {self.column} "
            f"({self.negative_fraction * 100:.1f}%, over the "
            f"{self.threshold * 100:.0f}% tolerance) - too many to read as returns; "
            "verify the sign convention and the column mapping"
        ]

    def disclosure(self) -> str:
        """One line for the deliverable/residual, written even when immaterial.

        The immaterial case is the one that used to vanish entirely, so it is
        the case this exists for. Empty string when there is nothing to say.
        """
        if not self.n_negative:
            return ""
        return (
            f"{self.n_negative} of {self.n_rows} source row(s) carried a negative "
            f"{self.column} (total {self.negative_total:,.1f}). Kern cannot tell a "
            "return from an inverted sign, so these rows were not reinterpreted."
        )


def audit_quantities(
    df: pd.DataFrame,
    *,
    quantity_col: str,
    product_col: str | None = None,
    kind: str = FLOW,
    threshold: float = DEFAULT_NEGATIVE_THRESHOLD,
) -> QuantityAudit:
    """Count negatives in ``quantity_col`` without touching ``df``.

    Non-numeric cells are coerced to NaN and counted as neither negative nor
    net-negative -- they are a different defect, already handled by each tool's
    own column validation.
    """
    values = pd.to_numeric(df[quantity_col], errors="coerce")
    negative_mask = values < 0
    n_negative = int(negative_mask.sum())
    negative_total = float(values[negative_mask].sum()) if n_negative else 0.0
    if not math.isfinite(negative_total):
        negative_total = 0.0

    n_entities_net_negative = 0
    if product_col is not None and n_negative and kind == FLOW:
        totals = values.groupby(df[product_col]).sum()
        n_entities_net_negative = int((totals < 0).sum())

    return QuantityAudit(
        column=quantity_col,
        kind=kind,
        n_rows=int(len(df)),
        n_negative=n_negative,
        negative_total=negative_total,
        n_entities_net_negative=n_entities_net_negative,
        threshold=threshold,
    )

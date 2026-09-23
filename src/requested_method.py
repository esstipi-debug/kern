"""The problem behind the request: a brief that names EOQ, and SKUs where EOQ does not apply.

A brief that says "calcula el EOQ" names a SOLUTION. The problem behind it is
"how much should I order, and when". ``inventory_optimization`` already answers
the problem, not the literal request: ``jobs/inventory_optimization.py::run``
gives an intermittent SKU a periodic (R, S) order-up-to policy and no EOQ
quantity at all. What it never did was tell the requester that the method they
named is not the one they got, and why -- so a buyer who asked for EOQ got
no EOQ quantity for part of the catalog and no sentence explaining it.

This module closes that loop and nothing else. It does not change a single
number: it recognises that the brief named EOQ, counts the priced SKUs whose
demand the engine judged intermittent, and writes the sentence that says so.
Silence when the brief did not name EOQ, or when EOQ held for every SKU.

Split in two because the pipeline is: ``names_eoq`` runs at ``prepare`` time,
the only stage that sees the brief, and ``eoq_reframe`` at ``run`` time, the
only stage that sees which SKUs came out intermittent.

Pure: no I/O, no LLM. Its one import outside the stdlib is the ADI threshold
from ``src.forecasting``, imported rather than copied so the sentence can never
quote a cut-off the engine did not use. The same brief and the same SKUs return
the same sentence, in the same order.

Grounded in L3 (knowledge/scm-books/graph.json):
  - ``knowledge::economic_order_quantity`` -- Vandeput (2020), Ch.2: the
    deterministic lot size, which assumes a steady demand rate.
  - ``knowledge::sbc_classification`` -- Boylan & Syntetos (2021), Ch.5: the
    ADI cut-off that separates regular from intermittent timing.
  - ``knowledge::order_up_to_policy`` -- Boylan & Syntetos (2021), Ch.8: (R, S)
    policies perform well for intermittent items.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from src.forecasting import INTERMITTENT_ADI_THRESHOLD

# How many SKU ids the sentence names before it says "+N more". The sentence rides
# in the run summary the operator reads before opening anything; a 400-id list
# there is noise, and the full list is already in the deliverable's policy column.
MAX_NAMED = 5

# Ways a brief names EOQ, matched on lowercase text with accents stripped. Kept to
# the method's own names on purpose: "order quantity" or "cuanto pedir" name the
# PROBLEM, not the solution, and a brief that asks the problem has nothing to be
# told. "Wilson" only counts next to formula/model -- it is also a surname.
_EOQ_PATTERNS = (
    re.compile(r"\beoq\b"),
    re.compile(r"\beconomic order quantity\b"),
    re.compile(r"\blote economico\b"),
    re.compile(r"\bcantidad economica de (?:pedido|compra|orden)\b"),
    re.compile(r"\b(?:formula|modelo) de wilson\b"),
    re.compile(r"\bwilson (?:formula|model)\b"),
)


def _fold(text: str) -> str:
    """Lowercase with accents stripped, so "Lote Económico" matches "lote economico"."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def names_eoq(brief: str) -> bool:
    """True when the brief asks for EOQ by name (en/es), not merely for a quantity."""
    folded = _fold(brief or "")
    return any(p.search(folded) for p in _EOQ_PATTERNS)


@dataclass(frozen=True)
class EoqReframe:
    """EOQ was requested; these priced SKUs got a periodic (R, S) policy instead."""

    n_priced: int
    intermittent: tuple[str, ...]
    adi_threshold: float = INTERMITTENT_ADI_THRESHOLD

    def sentence(self) -> str:
        """One ASCII line for the run summary (console prints must survive cp1252)."""
        named = ", ".join(self.intermittent[:MAX_NAMED])
        extra = len(self.intermittent) - MAX_NAMED
        if extra > 0:
            named = f"{named} (+{extra} more)"
        one = len(self.intermittent) == 1
        return (
            f"You asked for EOQ: {len(self.intermittent)} of {self.n_priced} SKUs "
            f"{'has' if one else 'have'} intermittent demand (ADI >= {self.adi_threshold:g}), "
            f"where EOQ's steady-demand assumption does not hold, so "
            f"{'it' if one else 'they'} got a periodic (R, S) order-up-to policy and no "
            f"EOQ quantity (Boylan & Syntetos 2021, Ch.5 and Ch.8): {named}."
        )


def eoq_reframe(priced: Iterable[tuple[str, bool]]) -> EoqReframe | None:
    """The reframe to tell a requester who named EOQ, or None when EOQ held everywhere.

    Takes no brief on purpose: the brief is only visible at ``prepare`` time, so
    the caller decides there with :func:`names_eoq` and calls this at ``run`` time
    only when it said yes.

    ``priced`` is ``(product_id, intermittent)`` for every SKU that received a
    policy. SKUs the engine skipped (no history, zero demand, no cost) are the
    caller's to leave out: they got no quantity for reasons of their own, which
    the report already counts, and blaming EOQ for them would be false.
    """
    rows = list(priced)
    intermittent = tuple(pid for pid, is_intermittent in rows if is_intermittent)
    if not intermittent:
        return None
    return EoqReframe(n_priced=len(rows), intermittent=intermittent)

"""A5 integrated planning v1 playbook (Linchpin 3.0 PR-20, plan section 5 A5
``balance``): a demand-history CSV + a stock/purchase-inputs CSV (+ optional
price history) -> ONE integrated deliverable that sequences the forecast,
demand shaping, and purchase plan into a single narrative with citable
cross-domain coherence checks.

**Scope boundary vs. the existing "sop" tool (READ BEFORE TOUCHING EITHER
MODULE).** ``src/sop.py`` + ``jobs/sop_job.py`` already answer a DIFFERENT
question: given ONE demand horizon, which of chase/level/hybrid aggregate
PRODUCTION strategies is cheapest (Chopra & Meindl's classic aggregate-
planning trade-off). This module answers a DIFFERENT question again: given
the LATEST forecast, any price-cut-implied demand shift, and the existing
purchase/inventory-constraint machinery, does the resulting plan actually
COHERE across those domains (does procurement have a PO to cover a planned
markdown's demand lift, does the budget fit, does inventory clear the
reorder point)? Neither module imports the other; ``src/sop.py`` and
``jobs/sop_job.py`` are UNTOUCHED by this PR and are not called from here --
the existing "sop" tool's aggregate-planning question is a different, later
step an operator could layer on top of THIS module's purchase plan, not an
input this v1 pipeline needs. (See ``src/sop_engine/__init__.py``'s module
docstring for the same boundary stated from the engine side.)

**Not registered as an agent Tool (see ``scm_agent/tools.py``).** Every
other registered Tool takes ONE ``request.data_path`` CSV; this playbook's
natural shape needs at minimum TWO files (a demand-history CSV for the
forecast step and a stock/purchase-inputs CSV for the purchase step), plus
two more OPTIONAL ones (price history for P2, and the same stock CSV doubles
as P4's liquidation input when it carries ``daily_demand`` too) -- that
multi-file shape does not fit the registry's single-``data_path`` contract
without either forcing an awkward one-CSV-with-everything schema or a
speculative multi-file convention no other tool needs yet (YAGNI). This
mirrors how ``jobs/package_deliverable.py`` composes several tools' own
outputs into one deck without itself being a registered Tool -- except this
module composes ``jobs/`` FUNCTIONS directly (forecast_job, elasticity_batch,
price_optimizer, markdown_liquidation_job, ``src.sop_engine``) rather than
running other tools through the ``Orchestrator``/registry, matching how
``jobs/markdown_liquidation_job.py`` itself already composes
``jobs.excess_obsolete_job`` and ``jobs.pricing`` directly. The operator
entry point is a CLI (``examples/run_integrated_plan.py``), the same shape
``examples/run_price_intel.py`` already established for a job with its own
multi-file intake and its own ``write_deliverable``.

**Pipeline (strictly sequential -- see ``src.sop_engine.engine`` for why):**

1. Forecast: ``jobs.forecast_job.prepare`` + ``run`` over the demand-history
   CSV -- v1's "reconciliation" is exactly reading this latest run (plan
   section 5's own scoping; no hierarchical reconciliation machinery). Its
   cycle memory -- did accuracy improve or worsen since the last plan -- is
   opt-in and NAMED: ``params['state_client_id']`` selects ONE client's state
   partition (``jobs.forecast_cycle.store_from_params``), and omitting it
   leaves the memory off, with the deliverable saying so in words. Omission
   never falls back to a partition shared with another client: that fallback
   is what made client B's deck quote client A's MAPE as B's own baseline.
2. Demand shaping (P2): if ``params['price_history_path']`` is given, batch-
   fits elasticity (``src.elasticity_batch``) and optimizes prices
   (``src.price_optimizer``) -- PR-16's P2, reused directly (no dedicated P2
   job exists yet in this repo; this is its first jobs/-level consumer for
   demand shaping specifically, alongside the existing
   ``jobs.repricing``/``jobs.markdown_liquidation_job`` consumers).
3. Demand shaping (P4): the SAME stock CSV, when it also carries
   ``daily_demand`` (E&O's own required column), is run through
   ``jobs.markdown_liquidation_job`` to surface any liquidation-implied
   demand lift -- best-effort: gracefully skipped (never a hard failure) when
   the stock CSV lacks E&O's required columns, via ``params['include_
   liquidation'] = False`` or missing ``daily_demand``.
4. Purchase plan (step 3): ``src.sop_engine.purchase_plan`` against the
   stock CSV's on_hand/unit_cost/reorder_point/incoming_po/MOQ/order_multiple.
5. Cash calendar (step 5): ``src.sop_engine.cash_plan`` dates each purchase
   line by the supplier's payment terms and adds the disbursements up per
   week, against ``params['cash_cap_per_week']`` when one is given. A purchase
   TOTAL is not a cash decision -- the question a buyer answers is whether the
   money fits the week it leaves. The terms column is optional like the rest;
   what is NOT optional is that a line planned on
   ``cash_plan.DEFAULT_PAYMENT_TERMS`` says so (``payment_terms_source``), so
   the calendar can never be read as if the plazo had been looked up.
6. Coherence checks (step 4): ``src.sop_engine.coherence`` -- the >=3 citable
   checks.
7. ONE deliverable (:func:`write_deliverable`): E4 (lang)/E5 (citation
   gate)/E6 (branding), per Golden Rule 13 -- not four tool reports stapled
   together.

**Where the code lives.** This file is the pipeline and the job's contract
(``prepare`` -> ``run`` -> ``verify`` -> ``write_deliverable``). Each axis it
composes is a sibling module, the way ``src/sop_engine/`` is split by axis:

* ``jobs/integrated_plan_inputs.py``    -- the OPTIONAL inputs (P2 price history,
  P4 liquidation, the ``as_of`` anchor) and the rule each degrades by. The
  REQUIRED stock file's own column sniffing stays here: it is the layer that
  records which optional purchase inputs went unstated, which is what the
  silent-zero fix below rests on.
* ``jobs/integrated_plan_coherence.py`` -- the tally: which of the engine's
  results is a reading and which is an absence, and the verdict that follows.
* ``jobs/integrated_plan_cash.py``      -- step 5's rendering: the weekly
  disbursement rows, the purchase rows they are dated from, cash findings/KPIs.
* ``jobs/integrated_plan_deck.py``      -- the demand/coherence CSV row shapes
  and the deck's findings, KPIs and recommendations.

An earlier round of this split stopped halfway: it moved step 5 out and then put
more back, so the file GREW 130 net lines while the extraction read as done. It
sat 251 lines past the repo's 800-line ceiling when that was first written up and
319 past it by the time it was closed, with this docstring DOCUMENTING the overrun
instead of resolving it. A violation a comment admits to is not a smaller
violation -- it is the same one, harder to notice.
Every module named above is under the ceiling and no function in any of them runs
past 50 lines; ``tests/test_integrated_plan_techo_de_archivo.py`` is what keeps
it that way, and it measures the siblings too so the next axis cannot simply
fatten on the other side of the cut.

QA (``jobs.qa.verify_integrated_plan``/``integrated_plan_passed``) checks
structural soundness (finite numbers, all 3 required check kinds present,
internally-consistent counts) -- a FAILED coherence check is an intended
FINDING this deliverable reports to a human, not a QA failure that blocks
the deliverable (mirrors every other job's QA gate: it guards the DATA is
sound, not that the business outcome is good). :func:`verify` is the other
half: it guards what the plan is allowed to CLAIM.

**The silent zero this module used to sign.** ``reorder_point`` and
``incoming_po`` are optional columns, and an absent one used to reach
``SkuPurchaseInputs`` as ``0.0``. A reorder buffer of ``0.0`` is a buffer
every plan clears, so the service-level check passed for free and the deck
reported "3/3 coherence check(s) passed ... no cross-domain conflict detected"
off a number nobody had supplied. Present-but-empty was worse: the column
NAME existed, so the cell reached the check as ``nan`` and manufactured a
citable-looking stockout ("below its nan-unit reorder buffer"). Both shapes
are one defect -- a check that resolves BY THE ABSENCE of the number it exists
to compare against certifies a plan nobody verified.

The fix splits the work the way ``jobs/risk_job.py`` splits it for TTR/TTS.
This module -- the only layer that sees the CSV cell -- records, per SKU, WHICH
optional inputs the file actually stated (``prepare``'s ``unsupplied_inputs``);
``src.sop_engine.coherence`` gains nothing but the vocabulary to express a
check with no verdict (``UnevaluableCheck``). Every claim downstream is then
made from :class:`CoherenceTally`, which counts evaluated results and declared
absences separately, and never from ``plan.n_checks_passed``, which is the
engine's arithmetic over placeholders. A ``0.0`` the client DID state is a
policy and is judged like any other number -- refusing to judge it would be
the mirror-image dishonesty.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from jobs import forecast_cycle, forecast_job
from jobs.forecast_job import ForecastJobReport
from jobs.integrated_plan_cash import cash_rows, purchase_rows
from jobs.integrated_plan_coherence import (
    COHERENT,
    INCOHERENT,  # noqa: F401  -- re-export, see the note below
    INDETERMINATE,  # noqa: F401  -- re-export, see the note below
    VERDICTS,
    CoherenceTally,
    _grounding_column,  # noqa: F401  -- re-export, see the note below
    _tally,
    _unsupplied_grounding,
    _verdict,
)
from jobs.integrated_plan_deck import (
    _DATA_SOURCES,
    _RESIDUAL,
    _deck_findings,
    _deck_kpis,
    _deck_recommendations,
    check_rows,
    demand_rows,
)
from jobs.integrated_plan_inputs import _as_of, _build_liquidation_lines, _build_price_shifts
from scm_agent.citation_gate import filter_citations
from scm_agent.knowledge import KnowledgeBase
from src import i18n
from src.deliverable import DEFAULT_BRANDING, Branding, Deliverable
from src.export import write_summary_csv
from src.sop_engine.cash_plan import DEFAULT_PAYMENT_TERMS, parse_payment_terms
from src.sop_engine.coherence import NOT_EVALUABLE, UnevaluableCheck  # noqa: F401  -- re-export, see below
from src.sop_engine.demand_plan import NO_SHIFT_SOURCE
from src.sop_engine.engine import IntegratedPlanReport, run_integrated_plan
from src.sop_engine.purchase_plan import SkuPurchaseInputs

# The names marked ``noqa: F401`` above are RE-EXPORTS, not leftovers. They moved
# out with the coherence and deck axes and this file no longer calls them, but it
# is still the playbook's single import for a caller -- ``jobs.qa``, the CLI in
# ``examples/`` and the tests all read the verdict vocabulary, the check-kind
# classifier and the CSV status vocabulary from ``jobs.integrated_plan``. Dropping
# them would charge every call site for an extraction it did not ask for. The
# marker says the import IS the point, so a later reader does not "clean it up".

TOOL_KEY = "sop"  # A5 is S&OP-flavored demand/supply reconciliation; reuses the already-verified
# "sop" anchor concepts (vollmann_sop, aggregate_planning) in scm_agent/citation_gate.py's
# TOOL_CONCEPTS map -- no new entry needed for this playbook.

_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "Product", "product")
_ONHAND_COLS = ("on_hand", "quantity", "qty", "stock", "units", "On Hand")
_COST_COLS = ("unit_cost", "cost", "Unit Cost", "price")
_REORDER_COLS = ("reorder_point", "rop", "reorder_pt", "reorder_level")
_INCOMING_PO_COLS = ("incoming_po", "po_incoming", "on_order", "in_transit", "open_po")
_MOQ_COLS = ("minimum_order_quantity", "moq", "min_order_qty")
_MULTIPLE_COLS = ("order_multiple", "case_pack", "pack_size")
# The supplier's payment condition -- TEXT, not a number, so it never goes
# through _cell(). It is the only input here that dates the money: without it
# every line falls back to cash_plan.DEFAULT_PAYMENT_TERMS and says so.
#
# NO GENERIC "terms" ALIAS, on purpose. In a procurement export "terms" names
# INCOTERMS (FOB / CIF / DDP / EXW) at least as often as it names a plazo -- so
# often that jobs/landed_cost_job.py, in this same repo, already reads that exact
# column name as an Incoterm (its _INCOTERM_COLS). Sniffing it here turned a
# COLLISION OF NAMES into a hard contract: SkuPurchaseInputs refuses "FOB" as an
# unreadable plazo, so one Incoterms column killed prepare() for the WHOLE
# portfolio. The spellings kept below all carry the word the concept is named
# after; none of them can mean a delivery term.
_PAYMENT_TERMS_COLS = ("payment_terms", "payment_term", "supplier_terms", "payment_condition")

# What one NOT-EVALUABLE check costs the deck's confidence. Deliberately local
# rather than imported, for the reason jobs/cross_impact_job.py states for its
# own: nosilo_core.guided.COVERAGE_PENALTY has no key for this gap and raises on
# an unknown one, by design.
_UNEVALUABLE_PENALTY = 0.20
_CONFIDENCE_FLOOR = 0.10
# This capability's ceiling when the purchase inputs are NOT the problem.
_DECK_BASE_CONFIDENCE = 0.75

# The arithmetic value an unstated optional input takes in SkuPurchaseInputs,
# whose fields are plain floats. Never a reading -- see :func:`_or_placeholder`.
_PLACEHOLDER = 0.0

_CITATION_KEYWORDS = (
    "sales and operations planning", "s&op", "demand supply reconciliation",
    "integrated business planning", "aggregate planning", "demand plan",
)

# These L3 citations ground the S&OP *method* (which books back integrated
# demand-supply planning), not the client's specific numbers -- so they are
# grounded on the fixed S&OP keyword set above, NOT on the free-text brief, and
# are the same canonical sources for every integrated-plan deliverable. Two
# defects (3.0-audit finding #7) drove this design, both reproduced against the
# committed graph:
#   1. RECALL. The S&OP concept cluster is fragmented across several disconnected
#      components (the code-bridged `vollmann_sop` anchor is islanded from the
#      `aggregate_planning`/`sales_and_operations_planning` cluster). Grounding a
#      pool of only the top-3 candidates could land all three in different islands
#      and every one be dropped by the (deliberately strict) 2-hop citation gate,
#      silently degrading realistic briefs to ZERO citations (6/8 in a battery).
#      Fixed by grounding a WIDER pool so on-topic candidates ranked below #3 still
#      reach the gate, then showing a tight, capped set.
#   2. PRECISION. Feeding the free-text brief into grounding (where it carries more
#      weight than the keyword set) let incidental brief tokens pass the 2-hop gate
#      -- which is itself permissive for this anchor (`aggregate_planning` reaches
#      ~19% of the graph within 2 hops via the shared book-hub node). A brief
#      saying "budget cap" surfaced an emissions "cap-and-trade" citation; an
#      EOQ-flavored brief surfaced EOQ/reorder-point over the real S&OP nodes.
#      Fixed by grounding on the keyword set only, so the citations are
#      deterministic and on-topic regardless of the client's wording.
# The gate itself (anchors, MAX_HOPS, MIN_CITATIONS) is untouched.
_CANDIDATE_POOL = 6   # candidates grounded and offered to the strict gate
_MAX_CITATIONS = 3    # kept, on-topic survivors ultimately shown (tight display)


@dataclass(frozen=True)
class IntegratedPlanBundle:
    """The full A5 v1 result: the forecast step's own report (never hidden --
    a client can see the underlying per-SKU forecasts) plus the integrated
    plan (demand/purchase/coherence) and which forecasted SKUs were excluded
    for lack of matching purchase-plan data (never a silent drop -- Golden
    Rule 14).

    ``tally``/``verdict`` are the provenance-aware reading of ``plan.checks``;
    ``unsupplied_inputs`` is the evidence they rest on, carried so :func:`verify`
    can re-derive both instead of taking their word for it (the same stored-not-
    derived split ``jobs/cross_impact_job.py`` keeps for its coordination
    verdict).
    """

    forecast_report: ForecastJobReport
    plan: IntegratedPlanReport
    dropped_no_purchase_data: tuple[str, ...]
    summary: str
    tally: CoherenceTally
    verdict: str
    # product_id -> the optional purchase inputs the stock file did NOT state.
    unsupplied_inputs: dict[str, frozenset[str]]

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"unknown coherence verdict {self.verdict!r} (expected one of {VERDICTS})")
        if not self.summary.strip():
            raise ValueError("an integrated plan must carry a legible summary")


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _cell(row: pd.Series, col: str | None) -> float | None:
    """The number the stock file actually held, or None when it held nothing.

    An absent COLUMN and an empty CELL are the same answer -- "nobody wrote this
    down" -- and both must stay distinguishable from a 0 the client did write.
    Testing the column name alone was the defect's second shape: it read ``nan``
    and fabricated a stockout out of it.
    """
    if not col or not pd.notna(row[col]):
        return None
    return float(row[col])


def _text_cell(row: pd.Series, col: str | None) -> str:
    """The TEXT the stock file held, or the empty string when it held nothing.

    Payment terms are the one purchase input that is not a number, so ``_cell``
    (which casts to float) cannot read them. The empty string is the value
    ``SkuPurchaseInputs.payment_terms`` treats as "nobody wrote this down" --
    the only case allowed to fall back to the declared default.

    An INTEGRAL FLOAT is written back as the integer the client typed. A
    payment-terms column holding plain day counts with a single blank cell is
    read by pandas as ``float64`` (the blank forces it), so ``30`` arrives here
    as ``30.0`` and a bare ``str()`` would hand ``parse_payment_terms`` a
    ``"30.0"`` nobody wrote -- which it rejected, killing :func:`prepare` for
    the whole portfolio over one empty cell in an OPTIONAL column. Only the
    all-zero fraction is dropped: ``30.5`` still travels as-is and is still
    refused downstream.
    """
    return "" if not col else _text_value(row[col])


def _text_value(value: object) -> str:
    """:func:`_text_cell`'s body, reachable without a row.

    Split out so :func:`_payment_terms_column` can read a whole column through
    exactly the same normalisation the rows will get. Reading it any other way
    would judge a float64 plazo column by the ``"30.0"`` pandas renders rather
    than by the ``30`` the client typed, and reject the very column this
    function exists to rescue.
    """
    if not pd.notna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _or_placeholder(value: float | None) -> float:
    """The arithmetic value ``SkuPurchaseInputs``' float field demands when nothing
    was stated -- NOT a reading. ``unsupplied_inputs``, built alongside it, is what
    every claim about this SKU is judged against."""
    return _PLACEHOLDER if value is None else value


def _payment_terms_column(df: pd.DataFrame, params: dict) -> str | None:
    """The column holding the supplier plazo, or ``None`` when the file has none.

    Two callers, two contracts. A column named in ``params['payment_terms_col']``
    is taken at its word: the caller ASSERTED that column is the payment
    condition, so an unreadable value in it is a real data error and must still
    stop the run (fail fast at the boundary) -- degrading it quietly would turn
    the caller's assertion into our assumption.

    A column found by NAME is different: nobody declared it, this module GUESSED
    it, and a guess must not be able to kill a portfolio. So the guess is checked
    against the column's CONTENT, and a column whose stated values do not read as
    plazos is treated as never having been found. That is the general form of the
    "terms"/Incoterms defect (see :data:`_PAYMENT_TERMS_COLS`): dropping the one
    ambiguous alias fixes the case that was reported, but any name-based sniff
    can still meet a column that happens to be spelled like ours, and the failure
    mode must be "nobody stated the plazo", never a dead run.

    ALL-OR-NOTHING on purpose. The question a sniffer answers is "is this the
    payment-terms column", which is a property of the COLUMN. Reading the plazos
    out of a column only partly made of plazos would date real money from a
    column this module is not sure about -- strictly worse than declaring the
    plazo unstated, which ``resolve_payment_terms`` stamps ``TERMS_ASSUMED`` and
    the cash summary names SKU by SKU.
    """
    if params.get("payment_terms_col"):
        return _pick_column(df, params["payment_terms_col"], _PAYMENT_TERMS_COLS)
    col = _pick_column(df, None, _PAYMENT_TERMS_COLS)
    if col is None:
        return None
    for value in df[col]:
        text = _text_value(value)
        if not text:
            continue  # a blank cell is "nobody wrote this down", not a wrong guess
        try:
            parse_payment_terms(text)
        except ValueError:
            return None
    return col


def _required_columns(df: pd.DataFrame, params: dict) -> tuple[str, str, str]:
    """``(product_id, on_hand, unit_cost)`` column names, or ``ValueError`` naming
    the ones it could not find and the columns it did see.

    Fail fast at the boundary: without these three there is no purchase line at
    all, so a partial plan built from whatever the file happened to carry would
    be worse than no plan (repo convention: validate at the system boundary).
    """
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    on_hand = _pick_column(df, params.get("on_hand_col"), _ONHAND_COLS)
    cost = _pick_column(df, params.get("cost_col"), _COST_COLS)
    missing = [n for n, c in (("product_id", product), ("on_hand", on_hand), ("unit_cost", cost)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(
            f"could not find {', '.join(missing)} for the purchase-plan inputs; pass them in params "
            f"(columns seen: {cols})"
        )
    return product, on_hand, cost


def _prepare_sku_inputs(
    df: pd.DataFrame, params: dict
) -> tuple[dict[str, SkuPurchaseInputs], dict[str, frozenset[str]]]:
    """Sniff the stock/purchase-inputs columns and build one
    :class:`~src.sop_engine.purchase_plan.SkuPurchaseInputs` per row, PLUS -- per
    SKU -- which of the optional inputs the file did not state.

    ``product_id``/``on_hand``/``unit_cost`` are required. ``reorder_point``/
    ``incoming_po``/``minimum_order_quantity``/``order_multiple`` are optional and
    enter the engine as ``0.0`` when unstated, because the engine's fields are
    plain floats -- but that ``0.0`` is a placeholder, and the second return value
    is what keeps it out of every downstream verdict. The provenance is recorded
    HERE, at the boundary that saw the cell, rather than teaching the shared
    coherence engine about absence (``jobs/risk_job.py``'s split, verbatim).
    """
    product, on_hand, cost = _required_columns(df, params)
    optional_cols = {
        "reorder_point": _pick_column(df, params.get("reorder_col"), _REORDER_COLS),
        "incoming_po": _pick_column(df, params.get("incoming_po_col"), _INCOMING_PO_COLS),
        "minimum_order_quantity": _pick_column(df, params.get("moq_col"), _MOQ_COLS),
        "order_multiple": _pick_column(df, params.get("order_multiple_col"), _MULTIPLE_COLS),
    }
    # Deliberately NOT in optional_cols/unsupplied_inputs: that structure feeds
    # the coherence-gap machinery, and no coherence check reads a payment term
    # (_GROUNDING_COLUMN would have nothing to map it to). The provenance of the
    # plazo travels PER LINE instead, in PurchasePlanLine.payment_terms_source.
    terms_col = _payment_terms_column(df, params)

    out: dict[str, SkuPurchaseInputs] = {}
    unsupplied: dict[str, frozenset[str]] = {}
    for _, row in df.iterrows():
        pid = str(row[product]).strip()
        stated = {name: _cell(row, col) for name, col in optional_cols.items()}
        unsupplied[pid] = frozenset(name for name, value in stated.items() if value is None)
        out[pid] = SkuPurchaseInputs(
            product_id=pid,
            on_hand=float(row[on_hand]),
            unit_cost=float(row[cost]),
            reorder_point=_or_placeholder(stated["reorder_point"]),
            incoming_po=_or_placeholder(stated["incoming_po"]),
            minimum_order_quantity=_or_placeholder(stated["minimum_order_quantity"]),
            order_multiple=_or_placeholder(stated["order_multiple"]),
            payment_terms=_text_cell(row, terms_col),
        )
    return out, unsupplied


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read the demand-history CSV (forecast, step 1) and the required
    ``params['stock_path']`` CSV (purchase inputs, step 3 -- optionally also
    P4's liquidation input, step 2), plus the optional
    ``params['price_history_path']`` (P2, step 2). Cycle memory: see step 1."""
    params = params or {}
    stock_path = params.get("stock_path")
    if not stock_path:
        raise ValueError(
            "params['stock_path'] is required: a CSV with product_id, on_hand, unit_cost "
            "[, reorder_point, incoming_po, minimum_order_quantity, order_multiple, daily_demand]"
        )

    series_by_name = forecast_job.prepare(data_path, params)
    forecast_report = forecast_job.run(
        series_by_name,
        holdout_fraction=params.get("holdout_fraction", 0.25),
        min_backtest_periods=params.get("min_backtest_periods", 4),
        store=forecast_cycle.store_from_params(params),  # cycle memory: see step 1
    )
    forecast = {s.name: s.forecast for s in forecast_report.skus}

    stock_df = pd.read_csv(stock_path)
    sku_inputs, unsupplied_inputs = _prepare_sku_inputs(stock_df, params)
    unit_costs = {pid: inp.unit_cost for pid, inp in sku_inputs.items()}

    price_shifts = _build_price_shifts(params, unit_costs)
    liquidation_lines = _build_liquidation_lines(stock_df, params)

    budget = params.get("budget")
    cash_cap = params.get("cash_cap_per_week")
    return {
        "forecast_report": forecast_report,
        "forecast": forecast,
        "sku_inputs": sku_inputs,
        # Which optional purchase inputs the stock file did NOT state, per SKU.
        # Travels with sku_inputs because the 0.0 inside those is a placeholder
        # and this is the only thing that says so.
        "unsupplied_inputs": unsupplied_inputs,
        "price_shifts": price_shifts,
        "liquidation_lines": liquidation_lines,
        "budget": float(budget) if budget is not None else None,
        # The two cash inputs: what a SKU with no stated plazo is planned on, and
        # the money that may leave in any ONE week (distinct from `budget`, which
        # caps the investment HELD). Both optional; an absent weekly cap yields a
        # calendar with no verdict rather than a fabricated one.
        "default_payment_terms": str(params.get("default_payment_terms") or DEFAULT_PAYMENT_TERMS),
        "cash_cap_per_week": float(cash_cap) if cash_cap is not None else None,
        "as_of": _as_of(params.get("as_of")),
    }


def _bundle_summary(
    payload: dict, plan: IntegratedPlanReport, tally: CoherenceTally, dropped: tuple[str, ...]
) -> str:
    """The one line a caller is guaranteed to read.

    NOT ``plan.summary``: that sentence counts every check the engine RAN as a
    verdict, including the ones it resolved against a placeholder. The gap is
    declared here instead (``jobs/risk_job.py:363``'s rule), and :func:`verify`
    refuses a summary that drops it.
    """
    summary = (
        f"{payload['forecast_report'].summary} "
        f"Integrated plan over {plan.n_skus} SKU(s): {tally.headline}."
    )
    if dropped:
        shown = ", ".join(dropped[:5]) + ("..." if len(dropped) > 5 else "")
        summary += f" {len(dropped)} forecast SKU(s) excluded for lack of purchase-plan data: {shown}."
    return summary


def run(payload: dict) -> IntegratedPlanBundle:
    """Run the sequential A5 v1 pipeline. Forecasted SKUs without a matching
    stock-file row are excluded from the purchase/coherence steps -- named
    in ``dropped_no_purchase_data``, never silently dropped.

    ``payload['unsupplied_inputs']`` is REQUIRED, with no default: a caller that
    did not build its payload with :func:`prepare` cannot tell an absent input
    from a stated zero, and defaulting to "everything was supplied" would quietly
    restore the exact defect this module was fixed for.
    """
    if "unsupplied_inputs" not in payload:
        raise KeyError(
            "payload['unsupplied_inputs'] is required: it records, per SKU, which optional "
            "purchase inputs the stock file never stated. Build the payload with prepare(); "
            "assuming everything was supplied is how a coherence verdict standing on a 0.0 "
            "nobody wrote gets signed as coherent"
        )
    forecast: dict[str, float] = payload["forecast"]
    sku_inputs: dict[str, SkuPurchaseInputs] = payload["sku_inputs"]
    unsupplied_inputs: dict[str, frozenset[str]] = payload["unsupplied_inputs"]

    common = sorted(set(forecast) & set(sku_inputs))
    dropped = tuple(sorted(set(forecast) - set(sku_inputs)))
    restricted_forecast = {pid: forecast[pid] for pid in common}

    plan = run_integrated_plan(
        restricted_forecast, sku_inputs,
        price_shifts=payload["price_shifts"], liquidation_lines=payload["liquidation_lines"],
        budget=payload["budget"],
        # .get() on purpose, unlike unsupplied_inputs above: a payload built
        # before these existed still produces a correct plan (the engine's own
        # declared defaults), whereas a missing unsupplied_inputs would produce
        # a WRONG verdict. Absence is only fatal where it can forge a claim.
        default_payment_terms=payload.get("default_payment_terms", DEFAULT_PAYMENT_TERMS),
        cash_cap_per_week=payload.get("cash_cap_per_week"),
        as_of=payload.get("as_of"),
    )
    tally = _tally(plan.checks, unsupplied_inputs)
    return IntegratedPlanBundle(
        forecast_report=payload["forecast_report"], plan=plan,
        dropped_no_purchase_data=dropped,
        summary=_bundle_summary(payload, plan, tally, dropped),
        tally=tally, verdict=_verdict(tally), unsupplied_inputs=unsupplied_inputs,
    )


def verify(bundle: IntegratedPlanBundle) -> list[str]:
    """What this plan is ALLOWED TO CLAIM. Empty list = it may ship as it stands.

    Bites both ways on purpose (``jobs/fefo_job.py``'s rule): it vetoes a verdict
    counted from a number nobody supplied AND a gap declared where the number was
    supplied, so neither "call everything evaluable" nor "call everything a gap"
    is a passing implementation. The GAP itself is never the issue -- a plan
    reporting INDETERMINATE with the missing column named and its confidence
    degraded is a valid delivery. What must never ship is a coherence certificate
    resting on an input the client never sent.

    Structural soundness is ``jobs.qa.verify_integrated_plan``'s job, not this one.
    """
    issues: list[str] = []
    tally = bundle.tally

    honest = _verdict(tally)
    if bundle.verdict != honest:
        issues.append(
            f"verdict {bundle.verdict!r} contradicts the tally it claims to summarise "
            f"({tally.n_passed} passed, {tally.n_failed} failed, {tally.n_unevaluable} not "
            f"evaluable) -- the honest verdict is {honest!r}")
    if tally.unevaluable and bundle.verdict == COHERENT:
        issues.append(
            f"a clean verdict cannot be issued while {tally.n_unevaluable} coherence check(s) "
            f"are NOT EVALUABLE ({', '.join(tally.missing_columns)} not supplied)")

    # Re-derive both sides from the evidence instead of believing the tally.
    for result in tally.passed + tally.failed:
        column = _unsupplied_grounding(result, bundle.unsupplied_inputs)
        if column is not None:
            issues.append(
                f"{result.check} for {result.product_id} is counted as an evaluated verdict "
                f"but rests on a 0.0 nobody stated: the stock file supplies no {column}")
    for gap in tally.unevaluable:
        if gap.column not in bundle.unsupplied_inputs.get(gap.product_id, frozenset()):
            issues.append(
                f"fabricated gap: {gap.check} for {gap.product_id} is declared NOT EVALUABLE "
                f"on {gap.column}, which the stock file did supply -- refusing to judge a "
                f"number the client stated is the mirror image of judging one they did not")

    if tally.unevaluable and tally.headline not in bundle.summary:
        issues.append(
            "the summary reports the plan without disclosing that "
            f"{tally.n_unevaluable} check(s) are NOT EVALUABLE -- the text must not read "
            "clean when the basis is missing")

    return issues


def write_operational(bundle: IntegratedPlanBundle, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one CSV per pipeline step (demand
    plan, purchase plan, coherence checks, cash commitment) -- distinct schemas,
    so distinct files, all under the SAME output directory (never four separate
    tool reports; see the module docstring for what "one deliverable" means)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    plan = bundle.plan
    # The only thing this function still decides: which checks lost their verdict.
    # That reading belongs to the bundle's tally, not to the row shape, so it is
    # resolved here and handed to the renderer already decided.
    gaps = {(u.check, u.product_id): u for u in bundle.tally.unevaluable}
    return {
        "demand_plan": write_summary_csv(demand_rows(plan), d / "demand_plan.csv"),
        "purchase_plan": write_summary_csv(purchase_rows(plan), d / "purchase_plan.csv"),
        "coherence_checks": write_summary_csv(check_rows(plan, gaps), d / "coherence_checks.csv"),
        "cash_commitment": write_summary_csv(cash_rows(plan), d / "cash_commitment.csv"),
    }


def _degrade(base: float, tally: CoherenceTally) -> float:
    """Cap a caller-supplied confidence by the coherence coverage it does not have.

    The caller's optimism does not survive contact with a missing input: a plan with
    an unmeasured check cannot claim the certainty of one whose checks were all
    measured, however sure the caller was (jobs/intake_scan_job.py's rule for its own
    registry coverage, jobs/risk_job.py's for its resilience test).
    """
    return max(_CONFIDENCE_FLOOR, base - _UNEVALUABLE_PENALTY * tally.n_unevaluable)


def build_deck(
    bundle: IntegratedPlanBundle,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float | None = None,
    lang: str = "en",
    branding: Branding | None = None,
) -> Deliverable:
    """Compose ONE integrated narrative across forecast -> demand shaping ->
    purchase plan -> coherence checks (Golden Rule 13's E4/E6 hooks; never
    four tool reports stapled together).

    Every coherence claim comes from ``bundle.tally``, never from
    ``plan.n_checks_passed``/``n_checks``: those are the engine's arithmetic over
    inputs it had to treat as real, so on a stock file with no reorder_point they
    said 3/3 -- the most favourable statement this tool can make, issued from
    nothing. ``confidence`` is DERIVED when not supplied, and treated as a ceiling
    when it is.
    """
    plan = bundle.plan
    tally = bundle.tally
    shaped = [line for line in plan.demand_plan if line.source != NO_SHIFT_SOURCE]

    summary = (
        f"Integrated plan over {plan.n_skus} SKU(s), sequencing the latest forecast, any price-cut/"
        f"liquidation demand shaping, and the resulting purchase plan into one view: "
        f"{tally.headline}"
        + (" -- see Key Findings" if tally.failed or tally.unevaluable else "")
        + "."
    )

    return Deliverable(
        title="Integrated Plan (Forecast -> Demand -> Purchase, S&OP-style)",
        client=client,
        summary=summary,
        findings=tuple(_deck_findings(bundle, shaped)),
        kpis=tuple(_deck_kpis(bundle, shaped)),
        data_sources=_DATA_SOURCES,
        recommendations=_deck_recommendations(tally),
        citations=tuple(citations),
        confidence=_degrade(_DECK_BASE_CONFIDENCE if confidence is None else confidence, tally),
        residual=_RESIDUAL,
        prepared=prepared,
        lang=lang,
        branding=branding if branding is not None else DEFAULT_BRANDING,
    )


def gated_citations(
    brief: str = "",
    *,
    kb: KnowledgeBase | None = None,
    limit: int = _CANDIDATE_POOL,
    max_shown: int = _MAX_CITATIONS,
) -> tuple[str, ...]:
    """The E5-gated L3 citations for this playbook (golden rule 7 + the
    citation gate) -- reuses the "sop" ``TOOL_CONCEPTS`` anchors verbatim
    (no parallel citation mechanism, same pattern
    ``jobs/repricing.py``/``jobs/price_intelligence.py`` already established).

    These citations ground the S&OP *method*, so grounding runs on the fixed
    ``_CITATION_KEYWORDS`` set only -- ``brief`` is deliberately NOT fed into the
    query (see the module comment on ``_CANDIDATE_POOL``): the client's free-text
    wording carries more grounding weight than the keyword set and let incidental
    tokens surface off-topic citations past the permissive 2-hop gate. ``limit``
    grounds a wider candidate pool than we display so the strict gate can reach
    past the S&OP cluster's graph fragmentation; ``filter_citations`` preserves
    grounding rank, so ``[:max_shown]`` keeps the top on-topic survivors as a
    tight set. ``brief`` is retained in the signature for call-site stability."""
    kb = kb or KnowledgeBase()
    candidates = kb.ground_citations_detailed(_CITATION_KEYWORDS, "", limit=limit)
    return filter_citations(kb, TOOL_KEY, candidates).kept[:max_shown]


def write_deliverable(
    bundle: IntegratedPlanBundle,
    *,
    out_dir: str | Path,
    client: str = "Client",
    brief: str = "",
    prepared: str = "",
    lang: str = i18n.DEFAULT_LANG,
    branding: Branding | None = None,
    confidence: float | None = None,
    kb: KnowledgeBase | None = None,
) -> dict[str, Path]:
    """The full deliverable: 3 operational CSVs + ONE integrated report.md
    + workbook.xlsx (via ``Deliverable.write_all``) -- always E5-gates its
    citations. The standalone entry point used by
    ``examples/run_integrated_plan.py`` and this module's own tests."""
    citations = gated_citations(brief, kb=kb)
    deliverable = build_deck(
        bundle, client=client, prepared=prepared, citations=citations,
        confidence=confidence, lang=lang, branding=branding,
    )
    written = write_operational(bundle, out_dir, client)
    written.update(deliverable.write_all(out_dir))
    return written

"""Cycle memory for the demand forecast: freeze this cycle, read the previous one.

WHY THIS EXISTS. A MAPE with nothing beside it is a number, not a measurement.
"Did it get better or worse" is the half of forecast accuracy a demand planner
is actually paid for, and it cannot be answered unless the cycle being replaced
was frozen before it was overwritten. The signed bias is the same argument one
level down -- direction, not just magnitude (Vandeput 2021, sec 2.2, the source
``jobs/forecast_job.py`` already cites for the per-SKU ``bias`` field).

WHERE EACH THING LANDS. Two of the domains contracted in
``src/state/system_state.py`` (``DOMAIN_COLUMNS``), used literally:

  "forecast"  product_id / period / forecast_qty  -- the frozen point forecast
  "outcomes"  product_id / tool / metric / value  -- the measured accuracy,
                                                     metric "mape" and "bias"

The ``tool`` column is what keeps this job's rows apart from the other writer of
"outcomes" (``examples/run_world_sim.py`` records under its own tool). It is also
why the read scans the index backwards filtering on ``tool`` instead of taking
``system_state.latest()``: ``latest()`` is the last snapshot of the domain no
matter who wrote it, so a single simulation run in between would hide the real
previous forecast cycle and the report would then claim -- falsely -- that there
is none. See :func:`read_previous` for why the scan is per-record rather than
``system_state.history()``.

This module lives in ``jobs/`` and not in ``src/`` because it touches disk and
speaks pandas; ``src/`` is the pure engine. Reading and writing are both best
effort, over NAMED conditions rather than over anything that can go wrong: the
memory is a side effect of the deliverable, not the deliverable, so a store that
refuses the write -- or a payload the store's own contract says may be gone, or
one that is there and will not parse -- hands the reason back as text (which the
report prints) rather than taking the forecast down with it. A disk that failed
underneath the read is NOT one of those; see :func:`read_previous` for why
swallowing it would be worse than dying.

And the memory is OPT-IN. Every function here takes the ``StateStore`` as an
argument and none of them can invent one; ``forecast_job.run`` without a store
reports :data:`NO_STORE_MEMORY` / :data:`NO_STORE_FREEZE` instead of resolving
the shared unnamed partition on the caller's behalf. Which client's history a run
reads is an identity decision, and the one kind of value a library must never
guess is whose data this is.

Opt-in only counts if there is a way to opt IN, and :func:`store_from_params` is
it: a caller names its partition with ``params["state_client_id"]`` and gets
that client's memory, isolated from every other client's by a separate SQLite
file (``src.state.store.store_for``). No key means no memory -- never a shared
default -- so two clients in one process cannot land on one partition by
omission, which is exactly how the leak happened the first time.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pandas as pd

from src.state.record import record_forecast, record_outcome
from src.state.store import (
    DEFAULT_BASE_PATH,
    DuplicateCycleError,
    MissingPayloadError,
    StateStore,
    store_for,
)
from src.state.system_state import CycleOrderError, SchemaValidationError

if TYPE_CHECKING:  # only for the annotation; importing it for real would be circular
    from jobs.forecast_job import SkuForecast

#: Discriminates this job's rows inside the shared "outcomes" domain.
TOOL = "forecast_job"
METRIC_MAPE = "mape"
METRIC_BIAS = "bias"

#: How far back to look for the previous cycle. Two years of monthly cycles --
#: far enough that a normal gap in operation still finds a comparison, short
#: enough that the read cost stays bounded. Past the window the report says it
#: found no previous cycle, which is honest: a two-year-old MAPE is not a
#: baseline anyone should be measured against.
_LOOKBACK_CYCLES = 24

_REQUIRED_OUTCOME_COLUMNS = frozenset({"product_id", "tool", "metric", "value"})


@dataclass(frozen=True)
class PreviousCycle:
    """The last cycle this job froze: how wrong it turned out to be, per SKU and overall."""

    cycle_id: str
    mean_mape: float
    mean_bias: float
    mape_by_sku: dict[str, float]


@dataclass(frozen=True)
class CycleMemory:
    """What the store could say about the previous cycle -- and why, when it could not.

    ``previous is None`` has THREE very different causes and the report must not
    print them the same: there genuinely is no earlier cycle (``note`` empty),
    there is one and it could not be read (``note`` carries the reason), or
    nobody named a store so no cycle was ever looked for (``note`` is
    :data:`NO_STORE_READ`).
    """

    previous: PreviousCycle | None
    note: str = ""


@dataclass(frozen=True)
class FrozenCycle:
    """What happened when this cycle was written down.

    ``cycle_id`` is ``None`` when nothing landed, and ``note`` always says why --
    a freeze that silently did nothing would let the next run believe it is the
    first one ever, which is a different (and false) statement.
    """

    cycle_id: str | None
    note: str


#: What a run WITHOUT a store reports instead of a comparison, and instead of a
#: freeze. Two sentences and not one because they answer two questions the
#: deliverable asks separately ("what changed since last cycle" / "was this cycle
#: written down"), and a reader who only sees one of them still gets the whole
#: reason.
NO_STORE_READ = (
    "cycle memory is off for this run: no store was named, so no previous cycle"
    " was looked for -- pass store= to compare against the last one"
)
NO_STORE_FROZEN = (
    "this cycle was NOT frozen: no store was named, so the next run will have no"
    " baseline from this one either -- pass store= to keep cycle memory"
)

#: The memory and the freeze of a run that was handed no store.
#:
#: WHY THIS EXISTS INSTEAD OF ``default_store()``. Resolving ``store=None`` to
#: the shared unnamed partition made ``run()`` read and write one directory on
#: behalf of every caller that never named a client -- and the three callers in
#: this tree (``jobs/integrated_plan.py``, ``jobs/seo_priority.py``,
#: ``scm_agent/tools.py``) all pass nothing. Two datasets in one process then
#: compared against each other: client B's deck printed "vs cycle <A's cycle>:
#: MAPE worsened 0.5% -> 35.0%", which is A's accuracy quoted as B's baseline.
#: ``src/state/store.default_store`` says that partition "is no longer reachable
#: by accident ... landing here is now always a written-down decision"; this
#: keeps that true. Frozen module-level singletons: both are immutable, and
#: there is nothing per-run to put in them.
NO_STORE_MEMORY = CycleMemory(None, NO_STORE_READ)
NO_STORE_FREEZE = FrozenCycle(None, NO_STORE_FROZEN)


#: The params key that NAMES the partition a run's cycle memory lives in.
#:
#: Deliberately NOT ``client`` / ``request.client``. That field is a
#: caller-typed display label -- ``webapp/app.py``'s ``_get_orchestrator``
#: refuses to trust it for profile lookup ("honoring it ... would let any caller
#: pull another client's real cost parameters by naming them") and
#: ``src.state.store.store_for`` repeats the warning for partitioning ("Feed
#: this an authenticated client id, never that label"). A separate key keeps the
#: two apart: the label is display copy on the deck, this is storage identity,
#: and nothing auto-promotes one into the other.
PARAM_CLIENT_ID = "state_client_id"

#: Optional params key: where partitions are rooted. Absent means
#: ``src.state.store.DEFAULT_BASE_PATH`` (``LINCHPIN_STATE_PATH`` or
#: ``data/state``). Follows the ``stock_path`` / ``price_history_path`` naming
#: the same ``params`` dict already uses for every other file location.
PARAM_BASE_PATH = "state_path"


def store_from_params(params: dict | None) -> StateStore | None:
    """The store this run's cycle memory belongs to, as NAMED by the caller.

    This is the declared path from a caller's ``params`` to one client's
    partition. It exists because the three production callers of
    ``forecast_job.run`` (``jobs/integrated_plan.py``, ``jobs/seo_priority.py``,
    ``scm_agent/tools.py``) all receive their configuration as a ``params``
    dict and nothing else -- ``scm_agent``'s Tool contract is literally
    ``prepare(request, provider)`` / ``run(payload, params)``, so a keyword
    argument on those functions would have no caller able to set it. Making the
    memory opt-in without this left the capability unreachable: every real run
    printed :data:`NO_STORE_READ`.

    ABSENT KEY MEANS OFF, AND NEVER THE SHARED PARTITION. ``store_for(None)``
    resolves to the unnamed partition every client would share; this function
    returns ``None`` instead, so cycle memory stays off until somebody writes
    down whose it is. A resolution channel where a missing key lands on one
    global directory IS the bug this whole opt-in exists to close -- it would
    just have moved from ``run()``'s default argument into here.

    AN UNUSABLE ID IS RAISED, NOT DOWNGRADED. ``store_for`` rejects a
    client id rather than rewriting it (two clients must never be sanitized onto
    one directory), and this keeps that: a blank or separator-bearing id raises
    ``ClientIdError``. Degrading it to "memory off" would make the deliverable
    print "no store was named" over a run where one WAS named and was bad --
    a false sentence, which is the failure this module keeps chasing out.
    """
    params = params or {}
    client_id = params.get(PARAM_CLIENT_ID)
    if client_id is None:
        return None
    return store_for(client_id, base_path=params.get(PARAM_BASE_PATH) or DEFAULT_BASE_PATH)


def now_cycle_id() -> str:
    """A cycle id for "now", ISO-8601 UTC.

    ``system_state._cycle_sort_key`` parses a plain integer first and an ISO
    timestamp second, and orders every integer before every timestamp -- so an
    ISO cycle coexists in one domain with the integer week ids
    ``examples/run_world_sim.py`` writes without tripping ``CycleOrderError``.
    """
    return datetime.now(timezone.utc).isoformat()


def mean_finite(values: Iterable[float]) -> float:
    """Mean over the finite values, or NaN when there are none.

    NaN and not 0.0 on purpose: zero error is a claim (a perfect forecast), and
    "nothing was measurable" must not be reported as one.
    """
    finite = [float(v) for v in values if math.isfinite(v)]
    return sum(finite) / len(finite) if finite else float("nan")


def _metric_by_sku(rows: pd.DataFrame, metric: str) -> dict[str, float]:
    """product_id -> value for one metric, keeping only the finite numbers."""
    selected = rows[rows["metric"] == metric]
    by_sku: dict[str, float] = {}
    for product_id, value in zip(selected["product_id"], selected["value"], strict=False):
        number = float(value)
        if math.isfinite(number):
            by_sku[str(product_id)] = number
    return by_sku


def read_previous(store: StateStore, *, lookback: int = _LOOKBACK_CYCLES) -> CycleMemory:
    """The most recent cycle THIS job froze, or an empty memory carrying the reason.

    An empty memory is never softened into zeros: a zero delta reads as "the
    forecast did not change", which is a claim nobody made.

    Walks the index newest-first and loads one payload at a time, instead of
    ``system_state.history()``, for two reasons that point the same way. It needs
    exactly ONE snapshot -- the newest written by this tool -- and ``history()``
    materializes the whole window to find it. And ``history()`` is all-or-nothing:
    one indexed row whose file is gone would take the other twenty-three with it.
    That state is reachable by the store's own contract -- ``prune_before``
    documents converging on "a store someone has hand-cleaned", and a crash
    mid-prune leaves a row without its file on purpose -- so a parquet deleted by
    hand two years ago must cost the comparison, not today's forecast.

    ``MissingPayloadError`` is caught BY NAME, not through its ``OSError`` base.
    On the deleted parquet the two read identically -- which is why this arm was
    an ``except OSError`` for two rounds -- but ``OSError`` is also
    ``PermissionError``, ``OSError(ENOSPC)`` and a closed descriptor: failures of
    the MACHINE, not states the store's contract reaches, and ones that would very
    likely have read fine on a retry. Degrading on those hands the report "there is
    no previous cycle" while the disk is on fire, and a best-effort reader that
    cannot tell the two apart is not degrading on a condition, it is guessing.
    ``ValueError`` stays alongside it: a file that IS there and will not parse, the
    shape pyarrow and pandas both report a corrupt payload in. Anything else
    propagates and takes the run down, on purpose. Both caught conditions name
    themselves in the note, so "there is no previous cycle" and "the previous cycle
    would not load" never print as the same sentence.
    """
    records = store.list_records("outcomes")[-lookback:] if lookback > 0 else []
    unreadable: list[str] = []
    for record in reversed(records):
        try:
            payload = store.load_payload(record)
        except (MissingPayloadError, ValueError) as exc:
            unreadable.append(f"{record.cycle_id} ({type(exc).__name__})")
            continue
        if not _REQUIRED_OUTCOME_COLUMNS <= set(payload.columns):
            continue
        rows = payload[payload["tool"] == TOOL]
        mape_by_sku = _metric_by_sku(rows, METRIC_MAPE)
        if not mape_by_sku:
            continue
        return CycleMemory(PreviousCycle(
            cycle_id=record.cycle_id,
            mean_mape=mean_finite(mape_by_sku.values()),
            mean_bias=mean_finite(_metric_by_sku(rows, METRIC_BIAS).values()),
            mape_by_sku=mape_by_sku,
        ), _skipped_note(unreadable))
    return CycleMemory(None, _skipped_note(unreadable))


def _skipped_note(unreadable: list[str]) -> str:
    """Name the stored cycles that could not be loaded. Empty when there were none."""
    if not unreadable:
        return ""
    return f"{len(unreadable)} stored cycle(s) could not be read: {', '.join(unreadable[:3])}"


def freeze(store: StateStore, cycle_id: str, skus: Iterable[SkuForecast]) -> FrozenCycle:
    """Append this cycle's point forecast and its measured accuracy to the store.

    Non-finite values are dropped rather than coerced: ``forecast_qty`` is a
    non-negative float by contract, and a SKU with too little history to
    backtest has ``mape=inf`` -- writing either as a zero would fabricate a
    measurement that was never taken.
    """
    skus = tuple(skus)
    forecast_rows = [
        {"product_id": s.name, "period": cycle_id, "forecast_qty": float(s.forecast)}
        for s in skus
        if math.isfinite(s.forecast) and s.forecast >= 0.0
    ]
    outcome_rows = [
        {"product_id": s.name, "tool": TOOL, "metric": metric,
         "value": float(value), "period": cycle_id}
        for s in skus
        for metric, value in ((METRIC_MAPE, s.mape), (METRIC_BIAS, s.bias))
        if math.isfinite(value)
    ]
    if not outcome_rows:
        return FrozenCycle(None, "nothing to freeze: no SKU produced a finite MAPE or bias")
    try:
        if forecast_rows:
            record_forecast(store, cycle_id, forecast_rows)
        record_outcome(store, cycle_id, outcome_rows)
    except (CycleOrderError, DuplicateCycleError, SchemaValidationError, OSError) as exc:
        return FrozenCycle(None, f"this cycle was NOT frozen ({type(exc).__name__}): {exc}")
    return FrozenCycle(cycle_id, f"frozen as cycle {cycle_id}")


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _mape_words(before: float, after: float) -> str:
    if not (math.isfinite(before) and math.isfinite(after)):
        return "not comparable (one of the two cycles has no finite MAPE)"
    delta = after - before
    direction = "improved" if delta < 0 else ("worsened" if delta > 0 else "unchanged")
    return f"{direction}, {_pct(before)} -> {_pct(after)} ({delta * 100:+.1f} pp)"


def _bias_words(before: float, after: float) -> str:
    if not (math.isfinite(before) and math.isfinite(after)):
        return "not comparable (one of the two cycles has no finite bias)"
    if abs(after) < abs(before):
        direction = "closer to zero"
    elif abs(after) > abs(before):
        direction = "further from zero"
    else:
        direction = "unchanged"
    return f"{before:+.4f} -> {after:+.4f} ({direction})"


def _no_previous_head(note: str) -> str:
    """The head of the sentence when there is nothing to compare against.

    Three notes, three sentences. The no-store case is NOT phrased as "could not
    be read": nothing failed, nobody looked, and telling a client their history
    was unreadable when it was never opened is a different (false) claim.
    """
    if note == NO_STORE_READ:
        return note
    if note:
        return f"no previous cycle to compare against ({note})"
    return (
        "first forecast cycle on record - there is no previous cycle to compare against, "
        "so no improvement or deterioration can be claimed yet"
    )


def no_baseline_reason(memory: CycleMemory) -> str:
    """Short, TRUE label for why there is no delta -- "" when there is one.

    Kept apart from :func:`describe` because the deck's KPI has room for four
    words, not a sentence, and the four words it used to hardcode ("first
    cycle") became a lie the moment a run could reach "no previous cycle"
    without ever having searched for one.
    """
    if memory.previous is not None:
        return ""
    if memory.note == NO_STORE_READ:
        return "no store: cycle memory off"
    if memory.note:
        return "previous cycle unreadable"
    return "first cycle"


def describe(
    memory: CycleMemory, mean_mape: float, mean_bias: float, frozen: FrozenCycle
) -> str:
    """The sentence the client reads: better, worse, or no previous cycle at all."""
    previous = memory.previous
    if previous is None:
        head = _no_previous_head(memory.note)
    else:
        head = (
            f"vs cycle {previous.cycle_id}: mean MAPE {_mape_words(previous.mean_mape, mean_mape)}"
            f"; mean signed bias {_bias_words(previous.mean_bias, mean_bias)}"
        )
        if memory.note:
            head = f"{head} (note: {memory.note})"
    return f"{head}. {frozen.note}."


def compare(
    memory: CycleMemory, mean_mape: float, mean_bias: float, frozen: FrozenCycle
) -> dict:
    """The report's cycle-comparison fields, as FLAT scalars.

    Flat on purpose. A nested record would be a single ``None`` on the run with
    no previous cycle and expand to ``previous_cycle.mape`` on the run that has
    one -- two different field names for the same quantity, which nothing
    downstream (nor any audit of two consecutive runs) could line up.
    """
    previous = memory.previous
    return {
        "cycle_id": frozen.cycle_id,
        "previous_cycle_id": None if previous is None else previous.cycle_id,
        "previous_mean_mape": None if previous is None else previous.mean_mape,
        "previous_mean_bias": None if previous is None else previous.mean_bias,
        "mean_mape_vs_previous": None if previous is None else mean_mape - previous.mean_mape,
        "mean_bias_vs_previous": None if previous is None else mean_bias - previous.mean_bias,
        "no_baseline_reason": no_baseline_reason(memory),
        "cycle_comparison": describe(memory, mean_mape, mean_bias, frozen),
    }

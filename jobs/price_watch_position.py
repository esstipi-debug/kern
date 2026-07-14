"""Tool-layer wiring pieces shared between ``examples/run_price_watch.py``'s
CLI ``run_pipeline()`` and ``scm_agent/tools.py``'s ``price_watch_tool()``
run hook (final whole-branch review, Finding 1: the registered agent tool
used to discard the homologation report and never build the price-position
or price-priority steps at all, so an agent-routed call produced only
``price_watch_cycle.csv`` while the CLI produced the full deliverable set).

:func:`price_report_from_confirmed_pairs` is PORTED, not reinvented, from
``examples/run_price_watch.py``'s own (previously private) helper -- both the
CLI and the tool now call this ONE function instead of maintaining two
copies (the exact kind of drift Finding 2 of this same review flagged for
the acquisition prefix).

:class:`PriceWatchToolReport` bundles every report one full tool-routed run
produces (cycle + homologation always; price_report/priority optional, None
when a call has nothing to build them from) into the SINGLE object the
``Tool`` interface's ``Produced.report`` threads through ``qa``/``options``/
``deliver``. Its passthrough properties mean ``jobs.qa.verify_price_watch``
and ``scm_agent.tool_options.price_watch_options`` (both already duck-typed
against ``PriceWatchCycleReport``'s own shape, per their own docstrings) keep
working completely UNCHANGED against this bundle -- this module is additive,
never a competing report shape, and the R5 pending-ceiling-raise safety
property (both hooks read ``pending_escalations`` straight off ``cycle``)
is untouched by construction.

Kept OUT of ``jobs/price_watch.py`` (already near the repo's 800-line file
cap -- see that module's own docstring) and out of
``jobs/price_watch_deliverable.py`` (that module is imported EAGERLY at the
top of ``scm_agent/tools.py`` and deliberately avoids importing
``jobs.price_intelligence``/``jobs.price_watch`` at its own top level to
dodge the documented circular-import hazard -- see its own docstring). This
module imports both, so it is ALWAYS imported lazily (inside a function
body), exactly like every other ``jobs.price_watch``/``jobs.price_priority``
import in ``scm_agent/tools.py``.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from jobs.price_intelligence import PriceIntelReport, RowOutcome
from jobs.price_priority import PricePriorityReport
from jobs.price_watch import PriceWatchCycleReport
from scm_agent.events import Event
from src.guided import GuidedOutcome
from src.pricing_intel.homologate import HomologationReport
from src.pricing_intel.ledger import PriceLedger
from src.pricing_intel.match.sku_map import SkuMapEntry


def price_report_from_confirmed_pairs(
    confirmed_pairs: Sequence[SkuMapEntry],
    our_prices: dict[str, Decimal],
    ledger: PriceLedger,
    *,
    now: datetime,
    sla_hours: float,
) -> PriceIntelReport:
    """Assemble a :class:`PriceIntelReport` from a watch cycle's OWN fresh
    ledger observations (never a second acquisition run) -- the "minimal
    price-position input" built from the homologation results crossed with
    our own prices. Reuses the exact ``CompetitorOffer`` rows the cycle
    appended; the only competitor-vs-our price math (``position_index``)
    lives in the reused ``price_intelligence``/``price_priority`` consumers,
    never here -- this function only collects offers and rolls up honest
    bookkeeping counts. A confirmed pair with no accepted observation this
    cycle is recorded as a skipped row (golden rule 14), never silently
    absent."""
    offers = []
    rows: list[RowOutcome] = []
    products = {e.our_product_id for e in confirmed_pairs}
    for entry in confirmed_pairs:
        record = ledger.latest_by_sku(entry.site, entry.competitor_sku_ref)
        if record is not None and record.offer.matched_product_id:
            offers.append(record.offer)
            rows.append(RowOutcome(
                product_id=entry.our_product_id, site=entry.site,
                competitor_url=entry.competitor_sku_ref, status="accepted", reason="ok", offer=record.offer,
            ))
        else:
            rows.append(RowOutcome(
                product_id=entry.our_product_id, site=entry.site,
                competitor_url=entry.competitor_sku_ref, status="skipped",
                reason="no_accepted_observation_this_cycle",
            ))

    covered = {o.matched_product_id for o in offers if o.matched_product_id}
    n_products = len(products)
    coverage_pct = (len(covered) / n_products) if n_products else 0.0
    if offers:
        avg_freshness = statistics.fmean((now - o.observed_at).total_seconds() / 3600.0 for o in offers)
    else:
        avg_freshness = 0.0
    tier_mix = dict(Counter(o.acquisition_tier for o in offers))
    summary = (
        f"Discovery price position across {n_products} confirmed pair(s): "
        f"{len(covered)} product(s) have a current competitor read from this watch cycle."
    )
    return PriceIntelReport(
        n_products=n_products, n_products_covered=len(covered), coverage_pct=coverage_pct,
        offers=tuple(offers), our_prices=dict(our_prices), rows=tuple(rows),
        quarantine_rate=0.0, avg_freshness_hours=avg_freshness, sla_hours=sla_hours,
        tier_mix=tier_mix, stale_events=(), now=now, summary=summary,
    )


@dataclass(frozen=True)
class PriceWatchToolReport:
    """Everything one ``price_watch_tool()`` run produced, bundled behind the
    ``Tool`` interface's single ``Produced.report`` object. ``cycle``/
    ``homologation`` are always present (every run does both steps);
    ``price_report``/``priority`` are ``None`` when a call had nothing to
    build them from (no confirmed pairs yet, or no ``catalog_path`` supplied
    for the ABC-XYZ side of the priority step -- an honest degrade, never a
    fabricated report).

    The passthrough properties below are the ENTIRE reason ``jobs.qa.
    verify_price_watch``/``scm_agent.tool_options.price_watch_options`` need
    no changes: both already read a report duck-typed against
    ``PriceWatchCycleReport``'s own public shape (``pairs_checked``,
    ``outcomes``, ``pending_escalations``), which is exactly what these
    properties forward, unchanged, straight from ``cycle``."""

    cycle: PriceWatchCycleReport
    homologation: HomologationReport
    price_report: PriceIntelReport | None = None
    priority: PricePriorityReport | None = None

    @property
    def now(self) -> datetime:
        return self.cycle.now

    @property
    def pairs_checked(self) -> int:
        return self.cycle.pairs_checked

    @property
    def outcomes(self) -> tuple:
        return self.cycle.outcomes

    @property
    def pending_escalations(self) -> tuple[GuidedOutcome, ...]:
        return self.cycle.pending_escalations

    @property
    def scaled_watches(self) -> tuple:
        return self.cycle.scaled_watches

    @property
    def events(self) -> tuple[Event, ...]:
        return self.cycle.events

    @property
    def summary(self) -> str:
        return self.cycle.summary

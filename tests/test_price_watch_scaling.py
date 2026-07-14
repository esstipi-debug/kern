"""Tests for jobs/price_watch.py's Task 9 additions (Discovery-Assisted Price
Intel plan, PR-9): wiring the R5 bounded auto-scaling guard
(``src.pricing_intel.watch_policy.plan_watch_escalation``, PR-8) into the
recurring watch cycle -- the PHYSICAL call-chain location of the guard.

R5 acceptance criterion (CONFIRMED with the user, plan 2026-07-13): "if the
engine wants to raise a tier beyond what's approved, it surfaces as a
pending-approval option, never executes alone." That guarantee is proven HERE,
not in PR-8: PR-8 built a perfect pure guard; this cycle is where it must
ACTUALLY be called, in the right place, BEFORE any tier/cadence change is
applied, on the ONE code path that can change a tier.

Highest-stakes tests in the whole plan:
- a high-value SKU wanting a faster cadence WITHIN the approved tier ceiling has
  that cadence tightened in-process, with NO human-approval escalation;
- a high-value SKU wanting a tier ABOVE the approved ceiling surfaces a
  pending-approval ``GuidedOutcome`` and applies NOTHING -- the SKU's effective
  acquisition tier is UNCHANGED (still L1, never the desired L2), and the
  site's on-disk ceiling is never raised;
- the "checked before apply" short-circuit is proven by SPYING: on
  needs_ceiling_raise, the guard IS consulted and the apply helper is NEVER
  reached (order proof, not a happens-to-look-right-by-accident check);
- every surfaced escalation passes the REAL ``verify_guided``/``passed_guided``
  gate from ``src/guided.py``.

Same offline convention as tests/test_price_watch_cycle.py: every httpx.Client is
built on httpx.MockTransport; discovered-retailer.test (approved to L1) is a
committed config/sites/*.yaml fixture, no new fixture needed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import httpx

from jobs import price_watch as pw
from jobs import price_watch_scaling as pws
from jobs.price_watch import SkuScalingRequest
from scm_agent.events import EventLedger
from src.guided import passed_guided, verify_guided
from src.pricing_intel.acquire import base
from src.pricing_intel.ledger import PriceLedger
from src.pricing_intel.match.sku_map import AUTO_CONFIRMED_BY, SkuMap
from src.pricing_intel.models import MatchCandidate
from src.pricing_intel.watch_policy import plan_watch_escalation as real_plan_watch_escalation

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pricing_intel"

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)

# PR-1's committed auto-onboarding fixture: approved to L1 exactly, rate limit
# 5.0s -- the exact "approved to L1, wants L2" case R5 exists to protect.
DISCOVERY_SITE = "discovered-retailer.test"
DISCOVERY_URL = "https://discovered-retailer.test/p/1"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _jsonld_html() -> str:
    # Hand-verified fixture (see its own docstring): price=199.99, USD, InStock.
    return (FIXTURES / "jsonld_clean.html").read_text(encoding="utf-8")


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=_jsonld_html())


def _seed_confirmed_pair(
    sku_map: SkuMap,
    *,
    our_product_id: str = "SKU-100",
    site: str = DISCOVERY_SITE,
    competitor_sku_ref: str = DISCOVERY_URL,
) -> None:
    sku_map.record(
        MatchCandidate(
            our_product_id=our_product_id, competitor_sku_ref=competitor_sku_ref, site=site,
            method="probabilistic", score=0.95, status="confirmed", reason="hand-verified-fixture",
            confirmed_by=AUTO_CONFIRMED_BY, confirmed_at=NOW,
        ),
        now=NOW,
    )


def _wants(tier: str, cadence_hours: float, rank: str = "A"):
    """A scaling-request source that returns the SAME desire for every pair."""

    def scaling_request_for(entry) -> SkuScalingRequest:
        return SkuScalingRequest(
            desired_tier=tier, desired_cadence_hours=cadence_hours, sku_value_rank=rank,
        )

    return scaling_request_for


# -- test_high_value_within_ceiling_tightens_cadence --------------------------


def test_high_value_within_ceiling_tightens_cadence(tmp_path) -> None:
    """A high-value SKU wants a faster (1h vs the 4h default) re-check at a tier
    WITHIN the approved ceiling (L1). That is a scheduling change against a
    compliance envelope that already exists -- safe to apply, no human needed."""
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(sku_map)
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    report = pw.run_price_watch_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(_ok_handler),
        now=NOW, scaling_request_for=_wants("L1", 1.0),
    )

    assert len(report.scaled_watches) == 1
    sw = report.scaled_watches[0]
    assert sw.site == DISCOVERY_SITE
    assert sw.competitor_sku_ref == DISCOVERY_URL
    assert sw.matched_product_id == "SKU-100"
    assert sw.applied_cadence_hours == 1.0  # 1h < 4h default, above the 5s floor
    # WITHIN the ceiling -> nothing needs human approval.
    assert report.pending_escalations == ()
    # the SKU is still acquired normally this cycle.
    assert report.outcomes[0].status == "accepted"

    sku_map.close()
    ledger.close()
    event_ledger.close()


# -- test_high_value_beyond_ceiling_surfaces_escalation_applies_nothing --------


def test_high_value_beyond_ceiling_surfaces_escalation_applies_nothing(tmp_path) -> None:
    """R5, the single most important guarantee: a SKU wanting acquisition tier
    L2 -- ABOVE the approved L1 ceiling -- NEVER gets it applied silently. The
    ceiling raise surfaces as a pending-approval GuidedOutcome and NOTHING is
    applied to the SKU's tier: the (only) acquisition this cycle stays at L1,
    and the site's on-disk ceiling is never raised."""
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(sku_map)
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    fetches = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        fetches["count"] += 1
        return httpx.Response(200, text=_jsonld_html())

    report = pw.run_price_watch_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(handler),
        now=NOW, scaling_request_for=_wants("L2", 1.0),
    )

    # SURFACED for a human, never executed.
    assert len(report.pending_escalations) == 1
    guided = report.pending_escalations[0]
    assert guided.status == "handoff"
    assert guided.status != "executed"

    # NOTHING applied to this SKU's tier/cadence.
    assert report.scaled_watches == ()

    # The SKU's EFFECTIVE tier is UNCHANGED: the only acquisition attempted this
    # cycle was at L1 (never L2). The ledger record proves the tier actually used.
    assert fetches["count"] == 1
    record = ledger.latest_by_sku(DISCOVERY_SITE, DISCOVERY_URL)
    assert record is not None
    assert record.offer.acquisition_tier == "L1"
    assert report.outcomes[0].status == "accepted"

    # The engine NEVER raised the on-disk ceiling on its own (the guard is pure).
    assert base.require_approved_site(DISCOVERY_SITE).max_tier_allowed == "L1"

    sku_map.close()
    ledger.close()
    event_ledger.close()


# -- test_escalation_checked_before_apply (THE order/short-circuit proof) ------


def test_escalation_checked_before_apply(tmp_path, monkeypatch) -> None:
    """The "checked before apply" proof. SPY on plan_watch_escalation AND on the
    apply helper. On needs_ceiling_raise, the guard MUST be consulted and the
    apply path MUST be unreachable -- proving the tier/cadence change is decided
    by the guard BEFORE anything could be applied, not merely that the final
    state happens to look right."""
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(sku_map)
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    guard_spy = MagicMock(side_effect=real_plan_watch_escalation)
    monkeypatch.setattr(pws.watch_policy, "plan_watch_escalation", guard_spy)
    apply_spy = MagicMock(side_effect=pws._apply_scaled_cadence)
    monkeypatch.setattr(pws, "_apply_scaled_cadence", apply_spy)

    report = pw.run_price_watch_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(_ok_handler),
        now=NOW, scaling_request_for=_wants("L2", 1.0),
    )

    guard_spy.assert_called_once()  # the guard WAS consulted...
    apply_spy.assert_not_called()   # ...and the apply path was NEVER reached.
    assert len(report.pending_escalations) == 1
    assert report.scaled_watches == ()

    sku_map.close()
    ledger.close()
    event_ledger.close()


def test_apply_reached_on_within_ceiling_is_the_positive_control(tmp_path, monkeypatch) -> None:
    """Positive control for the order proof above: the SAME apply spy DOES fire
    on the approved_within_ceiling branch -- so its non-call on
    needs_ceiling_raise is a real short-circuit, not a spy that never fires."""
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(sku_map)
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    apply_spy = MagicMock(side_effect=pws._apply_scaled_cadence)
    monkeypatch.setattr(pws, "_apply_scaled_cadence", apply_spy)

    report = pw.run_price_watch_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(_ok_handler),
        now=NOW, scaling_request_for=_wants("L1", 1.0),
    )

    apply_spy.assert_called_once()
    assert len(report.scaled_watches) == 1

    sku_map.close()
    ledger.close()
    event_ledger.close()


# -- test_pending_escalations_pass_verify_guided ------------------------------


def test_pending_escalations_pass_verify_guided(tmp_path) -> None:
    """Every surfaced escalation honors src/guided.py's never-unprotected
    contract -- checked with the REAL verify_guided/passed_guided, not a
    hand-rolled equivalent."""
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(sku_map)
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    report = pw.run_price_watch_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(_ok_handler),
        now=NOW, scaling_request_for=_wants("L2", 1.0),
    )

    assert len(report.pending_escalations) >= 1
    for guided in report.pending_escalations:
        assert verify_guided(guided) == []
        assert passed_guided(guided) is True

    sku_map.close()
    ledger.close()
    event_ledger.close()


# -- supporting coverage ------------------------------------------------------


def test_no_change_when_not_tighter(tmp_path) -> None:
    """A request whose cadence is NOT faster than the current one (and a tier
    within the ceiling) resolves to no_change: nothing applied, nothing
    escalated -- but the SKU is still acquired normally."""
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(sku_map)
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    report = pw.run_price_watch_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(_ok_handler),
        now=NOW, scaling_request_for=_wants("L1", 10.0),  # 10h is slower than the 4h default
    )

    assert report.scaled_watches == ()
    assert report.pending_escalations == ()
    assert report.outcomes[0].status == "accepted"

    sku_map.close()
    ledger.close()
    event_ledger.close()


def test_no_scaling_source_is_a_clean_passthrough(tmp_path) -> None:
    """Default (no scaling_request_for): the cycle behaves exactly as PR-6 --
    no escalations, no scaled watches, the guard is never even consulted."""
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(sku_map)
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    report = pw.run_price_watch_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(_ok_handler),
        now=NOW,
    )

    assert report.pending_escalations == ()
    assert report.scaled_watches == ()
    assert report.outcomes[0].status == "accepted"

    sku_map.close()
    ledger.close()
    event_ledger.close()


def test_unapproved_site_is_never_scaled(tmp_path) -> None:
    """A pair on a site that isn't approved cannot be scaled -- scaling never
    bypasses the acquisition-approval gate. The pair is honestly skipped and no
    escalation is fabricated for a site the engine can't even acquire from."""
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(
        sku_map, site="never-onboarded.test", competitor_sku_ref="https://never-onboarded.test/p/1",
    )
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("an unconfigured domain must never be fetched")

    report = pw.run_price_watch_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(handler),
        now=NOW, scaling_request_for=_wants("L2", 1.0),
    )

    assert report.scaled_watches == ()
    assert report.pending_escalations == ()
    assert report.outcomes[0].status == "skipped"
    assert "site_not_approved" in report.outcomes[0].reason

    sku_map.close()
    ledger.close()
    event_ledger.close()

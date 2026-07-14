"""Tests for Task 11 / PR-11: exposing the discovery-assisted price-watch
playbook (``jobs/price_watch.py``, Tasks 3/5/6/9) as a routable agent tool --
``scm_agent.tools.price_watch_tool()`` + ``scm_agent.tool_options.
price_watch_options`` + ``jobs.qa.verify_price_watch`` + ``jobs.
price_watch_deliverable.write_operational``.

This PR adds NO new business logic (no new match/acquisition/escalation
decision) -- it only wires already-tested machinery behind the ``Tool``
interface. The one genuinely new, safety-critical piece of logic is the
``options`` hook's R5 handling: a pending ceiling-raise escalation
(``PriceWatchCycleReport.pending_escalations``, Task 9) must NEVER be
flattened into the happy-path ranked options or reported as ``EXECUTED`` --
see ``test_options_surfaces_pending_ceiling_raise`` below, the highest-stakes
test in this file.

NOTE on the "39th tool" framing in the original task brief: by the time this
PR landed, ``digital_twin_tool`` (a parallel, unrelated plan) had already been
merged as the 33rd registered tool, and ``price_intelligence_tool`` (an
earlier PR in THIS plan) as the 39th -- so ``price_watch_tool`` is actually
the 40th. ``test_registry_now_has_40_tools`` asserts the real, current count,
not the brief's stale arithmetic.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pandas as pd
import pytest

from jobs import price_watch_deliverable, qa
from jobs.price_intelligence import PriceIntelReport
from jobs.price_monitor import PairOutcome
from jobs.price_priority import ExcludedSku, PricePriorityReport, SkuPriceAction
from jobs.price_watch import PriceWatchCycleReport
from jobs.price_watch_position import PriceWatchToolReport
from scm_agent import intent, llm, tool_options, tools
from scm_agent.events import EventLedger
from scm_agent.orchestrator import Orchestrator
from src.guided import EXECUTED, HANDOFF, OPTIONS, as_executed, passed_guided
from src.pricing_intel.homologate import HomologationReport, HomologationRow
from src.pricing_intel.ledger import PriceLedger
from src.pricing_intel.match.fuzzy import ProductAttributes
from src.pricing_intel.match.sku_map import SkuMap
from src.pricing_intel.models import SiteConfig
from src.pricing_intel.watch_policy import NEEDS_CEILING_RAISE, plan_watch_escalation

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pricing_intel"

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
SITE = "discovered-retailer.test"
REF = "https://discovered-retailer.test/p/1"
PID = "SKU-100"
VALID_EAN13 = "4006381333931"  # standard GS1/IFA demo EAN-13 (check-digit valid)


# -- fixtures -------------------------------------------------------------


def _outcome(
    status: str = "accepted", reason: str = "ok", *, site: str = SITE, ref: str = REF, pid: str = PID,
) -> PairOutcome:
    return PairOutcome(site=site, competitor_sku_ref=ref, matched_product_id=pid, status=status, reason=reason)


def _report(outcomes: list[PairOutcome], pending=()) -> PriceWatchCycleReport:
    return PriceWatchCycleReport(
        now=NOW, pairs_checked=len(outcomes), outcomes=tuple(outcomes), pending_escalations=tuple(pending),
    )


def _site_config(max_tier: str = "L1") -> SiteConfig:
    return SiteConfig(
        domain=SITE, robots_txt_respected=True, robots_checked_at="2026-07-01",
        tos_summary="auto-approved, robots.txt only", tos_decision="limited",
        rate_limit_seconds=5.0, max_tier_allowed=max_tier,
    )


def _pending_ceiling_raise():
    """A REAL needs_ceiling_raise GuidedOutcome, built through the actual R5
    guard (``watch_policy.plan_watch_escalation``, Task 9's own dependency) --
    not hand-faked, so the escalation-surfacing tests below prove the tool
    layer carries the genuine artifact through, not a look-alike stand-in."""
    decision = plan_watch_escalation(
        site_config=_site_config("L1"), current_cadence_hours=4.0, desired_cadence_hours=1.0,
        desired_tier="L2", sku_value_rank="A", now=NOW,
    )
    assert decision.kind == NEEDS_CEILING_RAISE  # sanity: this really is a ceiling-raise case
    return decision.guided


def _homologation_report() -> HomologationReport:
    row = HomologationRow(
        our_product_id=PID, competitor_sku_ref=REF, site=SITE, method="gtin",
        score=0.99, status="confirmed", reason="exact gtin match", confirmed_by="auto",
    )
    return HomologationReport(rows=(row,), n_confirmed=1, n_suspect=0, n_unmatched=0, unmatched=())


def _price_report() -> PriceIntelReport:
    return PriceIntelReport(
        n_products=1, n_products_covered=0, coverage_pct=0.0, offers=(), our_prices={},
        rows=(), quarantine_rate=0.0, avg_freshness_hours=0.0, sla_hours=48.0,
        tier_mix={}, stale_events=(), now=NOW, summary="x",
    )


def _priority_report() -> PricePriorityReport:
    action = SkuPriceAction(
        product_id=PID, action="vigilar", abc="A", xyz="X", position_index=None,
        competitor_read="insufficient_signal", reason="no confirmed read yet",
    )
    excluded = ExcludedSku(product_id="SKU-EXCLUDED", reason="missing from the price-position input")
    return PricePriorityReport(
        actions=(action,), excluded=(excluded,), n_igualar_precio=0, n_oportunidad_subir=0,
        n_vigilar=1, n_ignorar_bajo_valor=0, n_excluded=1, band=0.05, summary="x",
    )


# -- registration + routing ------------------------------------------------


def test_tool_registered_and_routable():
    reg = tools.build_default_registry()
    tool = reg.get("price_watch")

    assert tool.key == "price_watch"
    assert tool.requires_data is False
    assert tool.options is tool_options.price_watch_options

    classified = intent.classify(
        "descubre productos de la competencia y homologa productos competencia, luego vigila los "
        "precios de la competencia",
        reg, llm.RulesFallback(),
    )
    assert classified.job_type == "price_watch"


def test_routes_on_english_discovery_phrasing_without_colliding_with_price_intelligence():
    reg = tools.build_default_registry()

    classified = intent.classify(
        "start a recurring competitor price watch: run a price watch cycle covering competitor "
        "price discovery for this new site",
        reg, llm.RulesFallback(),
    )
    assert classified.job_type == "price_watch"


def test_registry_now_has_40_tools():
    reg = tools.build_default_registry()
    assert len(reg.list()) == 40


def test_scm_agent_tools_module_imports_without_circular_import():
    """Proves the lazy-import recipe: a FRESH process importing scm_agent.tools
    (which registers price_watch_tool()) and building the registry must succeed --
    a top-of-module `from jobs import price_watch`/`price_priority` would recreate
    the circular-import hazard price_intelligence_tool() already documents (both
    modules import scm_agent.* transitively). Run in a subprocess, not in-process:
    within the same pytest session scm_agent is already cached in sys.modules, so
    an in-process import would hit the cache and never actually re-exercise the
    package's real init order."""
    result = subprocess.run(
        [sys.executable, "-c", "from scm_agent import tools; tools.build_default_registry(); print('ok')"],
        cwd=str(REPO_ROOT), env={**os.environ, "PYTHONPATH": "."},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


# -- options hook: happy path -----------------------------------------------


def test_options_hook_returns_protected_outcome():
    report = _report([_outcome(), _outcome(status="skipped", reason="tier_not_approved", ref=REF + "2")])

    outcome = tool_options.price_watch_options(report)

    assert passed_guided(outcome)
    assert outcome.status == OPTIONS
    assert len(outcome.options) >= 2
    assert sum(1 for o in outcome.options if o.recommended) == 1
    assert all(o.action for o in outcome.options)


def test_options_hook_ranks_flagged_investigation_first_when_present():
    report = _report([_outcome(status="quarantined", reason="delta_exceeded")])

    outcome = tool_options.price_watch_options(report)

    assert passed_guided(outcome)
    assert "flagged" in outcome.options[0].label.lower() or "flagged" in outcome.options[1].label.lower()


# -- options hook: R5 safety-critical --------------------------------------


def test_options_surfaces_pending_ceiling_raise():
    """THE highest-stakes test in this file: when the cycle produced a pending
    ceiling-raise request (Task 9's R5), the options hook must NEVER report
    EXECUTED and must NEVER silently flatten the escalation into the ordinary
    ranked-options list -- it must genuinely carry the escalation (the exact
    prepared HandoffPacket) so an operator actually sees it."""
    pending = _pending_ceiling_raise()
    report = _report([_outcome()], pending=[pending])

    outcome = tool_options.price_watch_options(report)

    assert passed_guided(outcome)
    assert outcome.status != EXECUTED
    assert outcome.status == HANDOFF
    # genuinely carries the escalation -- not dropped, not silently flattened.
    assert len(outcome.handoffs) == 1
    assert outcome.handoffs[0].title == pending.handoffs[0].title
    assert "ceiling" in outcome.handoffs[0].title.lower()
    assert outcome.handoffs[0].steps == pending.handoffs[0].steps
    assert outcome.residuals  # the human-owner residual travels too.
    # the routine ranked next steps are NOT lost either -- still visible at
    # the outcome's top level (mirrors src.escalation._maybe_escalate).
    assert len(outcome.options) >= 2


def test_options_handles_multiple_pending_escalations():
    p1 = _pending_ceiling_raise()
    p2 = _pending_ceiling_raise()
    report = _report([_outcome()], pending=[p1, p2])

    outcome = tool_options.price_watch_options(report)

    assert passed_guided(outcome)
    assert outcome.status == HANDOFF
    assert len(outcome.handoffs) == 2


# -- QA gate: structural + R5 safety net (checked again, not just options) --


def test_qa_passes_a_clean_cycle():
    report = _report([_outcome(), _outcome(status="skipped", reason="circuit_open", ref=REF + "2")])
    assert qa.verify_price_watch(report) == []


def test_qa_flags_pairs_checked_mismatch():
    report = _report([_outcome()])
    bad = replace(report, pairs_checked=5)

    issues = qa.verify_price_watch(bad)

    assert any("pairs_checked" in i for i in issues)


def test_qa_flags_invalid_outcome_status():
    report = _report([_outcome(status="bogus")])

    issues = qa.verify_price_watch(report)

    assert any("invalid status" in i for i in issues)


def test_qa_passes_a_genuine_pending_ceiling_raise():
    report = _report([_outcome()], pending=[_pending_ceiling_raise()])
    assert qa.verify_price_watch(report) == []


def test_qa_flags_a_pending_escalation_wrongly_reported_as_executed():
    """R5 defense in depth: even if a future bug made some caller hand the QA
    gate a pending_escalations entry with status EXECUTED, verify_price_watch
    must catch it independently of the options hook."""
    bad = as_executed("should never happen for a tier raise")
    report = _report([_outcome()], pending=[bad])

    issues = qa.verify_price_watch(report)

    assert any("EXECUTED" in i for i in issues)


# -- deliverable writer -------------------------------------------------------


def test_write_operational_writes_one_row_per_outcome(tmp_path):
    report = _report([_outcome(), _outcome(status="skipped", reason="tier_not_approved", ref=REF + "2")])

    written = price_watch_deliverable.write_operational(report, tmp_path, client="Acme")

    df = pd.read_csv(written["csv"])
    assert len(df) == 2
    assert set(df["status"]) == {"accepted", "skipped"}


def test_write_operational_writes_header_only_csv_when_no_outcomes(tmp_path):
    report = _report([])

    written = price_watch_deliverable.write_operational(report, tmp_path)

    df = pd.read_csv(written["csv"])
    assert len(df) == 0
    assert list(df.columns) == ["site", "competitor_sku_ref", "matched_product_id", "status", "reason"]


def test_write_operational_omits_extra_files_for_a_bare_cycle_report(tmp_path):
    """A plain ``PriceWatchCycleReport`` (no ``homologation``/``price_report``/
    ``priority`` attributes -- what every prior caller of this function
    already passes) must keep writing ONLY the cycle CSV: fully backward
    compatible with the pre-Finding-1 behavior."""
    report = _report([_outcome()])

    written = price_watch_deliverable.write_operational(report, tmp_path)

    assert set(written) == {"csv"}


def test_write_operational_writes_full_deliverable_set_when_bundle_has_extra_reports(tmp_path):
    """Finding 1 (final whole-branch review): when the tool's Produced.report
    is a ``PriceWatchToolReport`` bundle carrying homologation/price_report/
    priority, deliver() must write the FULL deliverable set -- the same
    files the CLI (examples/run_price_watch.py) produces -- not just the
    watch-cycle CSV."""
    bundle = PriceWatchToolReport(
        cycle=_report([_outcome()]), homologation=_homologation_report(),
        price_report=_price_report(), priority=_priority_report(),
    )

    written = price_watch_deliverable.write_operational(bundle, tmp_path, client="Acme")

    assert set(written) == {
        "csv", "homologation_table", "homologation_unmatched",
        "price_position_matrix", "ledger_export", "price_priority", "price_priority_excluded",
    }
    for path in written.values():
        assert path.exists()

    table = pd.read_csv(written["homologation_table"])
    assert PID in table["my_sku"].astype(str).tolist()
    assert table.iloc[0]["status"] == "confirmed"

    priority = pd.read_csv(written["price_priority"])
    assert PID in priority["product_id"].astype(str).tolist()

    excluded = pd.read_csv(written["price_priority_excluded"])
    assert "SKU-EXCLUDED" in excluded["product_id"].astype(str).tolist()


def test_write_operational_writes_partial_set_when_only_homologation_present(tmp_path):
    """A call with confirmed pairs but no catalog_path (so no ABC-XYZ/priority
    input) writes homologation + cycle, honestly omitting price_priority --
    never a fabricated priority plan (golden rule 14 applied to deliverable
    presence itself)."""
    bundle = PriceWatchToolReport(cycle=_report([_outcome()]), homologation=_homologation_report())

    written = price_watch_deliverable.write_operational(bundle, tmp_path)

    assert set(written) == {"csv", "homologation_table", "homologation_unmatched"}


# -- prepare(): needs seed_url, gated crawl -----------------------------------


def test_prepare_needs_data_without_a_seed_url():
    from scm_agent.types import JobRequest

    reg = tools.build_default_registry()
    tool = reg.get("price_watch")

    prep = tool.prepare(JobRequest(brief="watch competitor prices"), llm.RulesFallback())

    assert prep.status == "needs_data"
    assert prep.messages


# -- end-to-end through the orchestrator (prepare -> run -> qa -> deliver) --


_PRODUCT_HTML = """<html><head><title>Widget</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Widget",
 "offers":{"@type":"Offer","price":"9.99","priceCurrency":"USD"}}
</script></head><body><h1>Widget</h1></body></html>"""


def test_runs_end_to_end_through_the_orchestrator(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from jobs import price_watch as pw

    domain = "shop.example.test"
    seed_url = f"https://{domain}/"
    df = pd.DataFrame([{"url": f"https://{domain}/p/1", "status": 200, "title": "Widget",
                         "page_html": _PRODUCT_HTML}])
    monkeypatch.setattr(pw, "_crawl_domain", lambda seed, **kwargs: df)

    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run(
        "watch competitor prices via a price watch cycle: descubre productos de la competencia",
        client="Acme", out_dir=tmp_path,
        overrides={
            "seed_url": seed_url,
            "config_dir": tmp_path / "sites",
            "robots_reader": lambda robots_url, user_agent: True,
            "now": NOW,
        },
    )

    assert res.status == "ok", res.summary
    assert res.tool == "price_watch"
    assert res.guided is not None
    # no confirmed pairs exist yet (no our_catalog supplied) -> the happy-path
    # ranked options, never a dead end.
    assert res.guided.status == OPTIONS
    assert Path(res.deliverables["csv"]).exists()
    written_csv = pd.read_csv(res.deliverables["csv"])
    assert list(written_csv.columns) == ["site", "competitor_sku_ref", "matched_product_id", "status", "reason"]


# -- Finding 1 (final whole-branch review): the registered tool must reach --
# -- the SAME full deliverable set the CLI produces, not just the cycle CSV --


_GTIN_DISCOVERY_HTML = (
    '<html><head><title>Acme Widget Pro 3000</title>'
    '<script type="application/ld+json">'
    '{"@context":"https://schema.org","@type":"Product",'
    '"name":"Acme Widget Pro 3000 Deluxe Edition","brand":"Acme",'
    '"offers":{"@type":"Offer","price":"249.00","priceCurrency":"USD",'
    f'"gtin13":"{VALID_EAN13}","availability":"https://schema.org/InStock"}}}}'
    '</script></head><body><h1>Acme Widget Pro 3000</h1></body></html>'
)


def _pdp_html() -> str:
    return (FIXTURES / "jsonld_clean.html").read_text(encoding="utf-8")


def test_runs_end_to_end_through_the_orchestrator_produces_full_deliverable_set(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
):
    """The highest-value test for Finding 1: a request routed through the
    REGISTERED AGENT TOOL (not the CLI's own run_pipeline) with a catalog
    supplied must produce the SAME full deliverable set the CLI does --
    homologation_table.csv, price_watch_cycle.csv, price_position_matrix.xlsx,
    and price_priority.csv -- proving the tool no longer discards the
    homologation report or silently drops the price-position/priority steps."""
    from jobs import price_watch as pw

    domain = "fulltool.example.test"
    seed_url = f"https://{domain}/"
    df = pd.DataFrame([{
        "url": f"https://{domain}/p/aw-3000", "status": 200, "title": "Acme Widget Pro 3000",
        "page_html": _GTIN_DISCOVERY_HTML,
    }])
    monkeypatch.setattr(pw, "_crawl_domain", lambda seed, **kwargs: df)

    catalog_path = tmp_path / "our_catalog.csv"
    pd.DataFrame([{
        "product_id": "our-acme", "title": "Acme Widget Pro 3000 Deluxe Edition", "brand": "Acme",
        "gtin": VALID_EAN13, "our_price": 260.00, "demand": 120, "unit_cost": 180.0,
    }]).to_csv(catalog_path, index=False)

    def pdp_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_pdp_html())

    http_client = httpx.Client(transport=httpx.MockTransport(pdp_handler))
    sku_map = SkuMap(tmp_path / "sku_map")
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    try:
        orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

        res = orch.run(
            "watch competitor prices via a price watch cycle: descubre productos de la competencia",
            client="Acme", out_dir=tmp_path,
            overrides={
                "seed_url": seed_url,
                "config_dir": tmp_path / "sites",
                "robots_reader": lambda robots_url, user_agent: True,
                "now": NOW,
                "our_catalog": [ProductAttributes("our-acme", "Acme Widget Pro 3000 Deluxe Edition", "Acme", {})],
                "our_gtins": {"our-acme": VALID_EAN13},
                "our_prices": {"our-acme": Decimal("260.00")},
                "catalog_path": str(catalog_path),
                "http_client": http_client,
                "sku_map": sku_map,
                "ledger": ledger,
                "event_ledger": event_ledger,
            },
        )

        assert res.status == "ok", res.summary
        assert res.tool == "price_watch"
        assert res.guided is not None

        # the FULL deliverable set, not just the watch-cycle CSV.
        for key in (
            "csv", "homologation_table", "homologation_unmatched",
            "price_position_matrix", "ledger_export", "price_priority", "price_priority_excluded",
        ):
            assert key in res.deliverables, f"missing deliverable {key!r}: {sorted(res.deliverables)}"
            assert Path(res.deliverables[key]).exists()

        # homologation_table.csv genuinely names our confirmed SKU/competitor pair.
        table = pd.read_csv(res.deliverables["homologation_table"])
        assert "our-acme" in table["my_sku"].astype(str).tolist()
        assert table.iloc[0]["status"] == "confirmed"

        # the watch cycle actually re-acquired the confirmed pair's current price.
        cycle_csv = pd.read_csv(res.deliverables["csv"])
        assert "accepted" in cycle_csv["status"].astype(str).tolist()

        # per-SKU price_priority.csv carries our-acme with a genuine competitor
        # read and one of the honest enumerated actions.
        priority = pd.read_csv(res.deliverables["price_priority"])
        acme_rows = priority[priority["product_id"] == "our-acme"]
        assert len(acme_rows) == 1
        assert acme_rows.iloc[0]["competitor_read"] == "confirmed"
        valid_actions = {"igualar_precio", "oportunidad_subir", "vigilar", "ignorar_bajo_valor"}
        assert acme_rows.iloc[0]["action"] in valid_actions
    finally:
        http_client.close()
        sku_map.close()
        ledger.close()
        event_ledger.close()


def test_runs_end_to_end_without_a_catalog_path_omits_priority_honestly(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
):
    """Without a catalog_path, the tool must NOT crash and must NOT fabricate
    a price_priority.csv -- it degrades honestly, still delivering whatever
    it legitimately can (the cycle CSV; homologation table stays empty since
    no our_catalog was supplied either)."""
    from jobs import price_watch as pw

    domain = "shop.example.test"
    seed_url = f"https://{domain}/"
    df = pd.DataFrame([{"url": f"https://{domain}/p/1", "status": 200, "title": "Widget",
                         "page_html": _PRODUCT_HTML}])
    monkeypatch.setattr(pw, "_crawl_domain", lambda seed, **kwargs: df)

    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run(
        "watch competitor prices via a price watch cycle: descubre productos de la competencia",
        client="Acme", out_dir=tmp_path,
        overrides={
            "seed_url": seed_url,
            "config_dir": tmp_path / "sites",
            "robots_reader": lambda robots_url, user_agent: True,
            "now": NOW,
        },
    )

    assert res.status == "ok", res.summary
    assert "price_priority" not in res.deliverables
    assert "csv" in res.deliverables

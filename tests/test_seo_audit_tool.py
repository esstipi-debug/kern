"""Wiring tests for the ``seo_audit`` Tool: registration, routing, and the
prepare -> run -> qa -> deliver path through the registry's own Tool object.

``jobs/seo_audit.py`` shipped fully built and covered (tests/test_seo_audit_job.py
exercises the crawl adapter, ranking, Lighthouse degradation and the CSV
round-trip) but was never registered, so no brief could reach it. These tests
pin only what the registration itself adds -- the ``JobRequest`` -> ``Prepared``
translation and the compliance gate surfacing as a *question* rather than a
traceback -- deliberately not re-testing the job's own behaviour.

Sibling of tests/test_intake_scan_tool.py, the tool this one is modelled on:
same shape, and the same honest "no L3 anchor exists" outcome (see
test_seo_audit_has_no_concept_map_by_design below).

No test here touches the network: the domain gate refuses before any crawl
attempt, and the end-to-end path feeds ``run`` a hand-built payload exactly as
tests/test_seo_audit_job.py does.
"""

from __future__ import annotations

from nosilo_core import llm
from nosilo_core.types import JobRequest

from jobs import seo_audit as sa
from scm_agent import citation_gate, intent, tools
from src.seo import crawl_audit as ca

_SEED = "https://shop.example.com/"
_DOMAIN = "shop.example.com"


def _tool():
    return tools.build_default_registry().get("seo_audit")


def _payload() -> dict:
    """The same hand-built, network-free payload shape jobs/seo_audit.run takes."""
    pages = [
        ca.CrawledPage(
            url="https://shop.example.com/", status_code=200, title="Home",
            meta_description="Welcome", canonical="https://shop.example.com/",
            html="<html><body><h1>Home</h1></body></html>",
        ),
        ca.CrawledPage(
            url="https://shop.example.com/no-title", status_code=200, title=None,
            meta_description=None, canonical="https://shop.example.com/no-title",
            html="<html><body><h1>Sin titulo</h1></body></html>",
        ),
    ]
    return {
        "domain": _DOMAIN,
        "pages": pages,
        "top_n": 20,
        "lighthouse": sa.LighthouseResult(
            False, _SEED, None, None, None, "lighthouse checks skipped (run_lighthouse=False)"
        ),
    }


# -- registration & routing ---------------------------------------------------


def test_registry_includes_seo_audit_tool():
    tool = _tool()
    assert tool.key == "seo_audit"
    # The job's input is a seed URL in params, not a data_path -- same contract
    # as price_watch, the other URL-seeded crawling tool.
    assert tool.requires_data is False


def test_an_seo_brief_routes_to_seo_audit():
    reg = tools.build_default_registry()
    res = intent.classify(
        "run a technical seo audit of my site: broken internal links, missing meta "
        "descriptions and whether robots txt and sitemap are there",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "seo_audit"


def test_seo_keywords_do_not_steal_a_data_quality_brief():
    """seo_audit talks about pages, tags and crawling; it must never win a brief
    that is really about the SKU master (dedup/GTIN), and that brief must still
    reach data_quality rather than falling to ambiguity."""
    reg = tools.build_default_registry()
    res = intent.classify(
        "audit our sku master for duplicate skus and validate gtins (data quality / mdm)",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "data_quality"


def test_seo_keywords_do_not_steal_a_price_watch_brief():
    """The other URL-seeded crawler. A competitor-price brief must stay there."""
    reg = tools.build_default_registry()
    res = intent.classify(
        "vigila los precios de la competencia y homologa productos competencia "
        "en un price watch cycle",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "price_watch"


# -- prepare: the two gates, neither of which may crawl or raise --------------


def test_prepare_without_a_seed_url_asks_for_one():
    prepared = _tool().prepare(JobRequest(brief="audit my seo"), llm.RulesFallback())
    assert prepared.status == "needs_data"
    assert any("seed_url" in m for m in prepared.messages)


def test_prepare_without_confirmed_domain_asks_before_crawling(monkeypatch):
    """The compliance gate is a QUESTION, not a failure -- and it must be
    answered before the job is entered at all, so no crawl can start."""
    def _never(*args, **kwargs):
        raise AssertionError("jobs.seo_audit.prepare must not run without confirmed_domain")

    monkeypatch.setattr(sa, "prepare", _never)
    prepared = _tool().prepare(
        JobRequest(brief="audit my seo", params={"seed_url": _SEED}), llm.RulesFallback()
    )
    assert prepared.status == "needs_clarification"
    assert any("confirmed_domain" in m for m in prepared.messages)


def test_prepare_translates_a_domain_mismatch_into_a_question():
    """DomainNotConfirmedError is the job refusing to crawl an unnamed domain.
    The tool must surface it as needs_clarification, never let it escape as a
    traceback. The job raises this before any network call (see
    tests/test_seo_audit_job.py::test_prepare_raises_before_any_crawl_attempt...)."""
    prepared = _tool().prepare(
        JobRequest(
            brief="audit my seo",
            params={"seed_url": _SEED, "confirmed_domain": "not-the-same.example.com"},
        ),
        llm.RulesFallback(),
    )
    assert prepared.status == "needs_clarification"
    assert any("confirmed_domain" in m for m in prepared.messages)


# -- run / qa / deliver through the Tool object -------------------------------


def test_run_qa_and_deliver_go_through_the_tool(tmp_path):
    tool = _tool()
    produced = tool.run(_payload(), {})
    assert produced.summary
    assert produced.report.pages_crawled == 2
    assert tool.qa(produced.report) == []

    paths = tool.deliver(produced.report, tmp_path / "out", "Acme")
    assert paths["issues_csv"].exists()
    assert paths["findings_csv"].exists()


def test_qa_failure_is_reported_rather_than_raised():
    """An empty crawl is a QA failure ("no pages were crawled"), which the
    orchestrator turns into qa_failed / no deliverable -- not an exception."""
    tool = _tool()
    produced = tool.run({**_payload(), "pages": []}, {})
    assert tool.qa(produced.report) != []


# -- citations: the honest zero ------------------------------------------------


def test_seo_audit_has_no_concept_map_by_design():
    """The 27-source books graph is SCM/OR/inventory/forecasting/pricing theory
    and carries no SEO source at all: no `search_engine_optimization` node
    exists. Anchoring this tool on a closer-sounding node would repeat the
    vehicle_routing false-friend ("Logistics as the Vehicle for Change"), so it
    is deliberately left out of TOOL_CONCEPTS -- the intake_scan precedent.
    Fix by ingesting SEO theory, not by a looser anchor."""
    assert "seo_audit" not in citation_gate.TOOL_CONCEPTS

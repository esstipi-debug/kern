"""Tests for the scm_agent orchestrator package."""

import importlib
from pathlib import Path

import pytest

from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from scm_agent.registry import Prepared, Produced, Tool, ToolRegistry
from scm_agent.types import JobRequest, JobResult
from src import client_profile


def test_job_request_defaults():
    req = JobRequest(brief="set up reorder points")
    assert req.brief == "set up reorder points"
    assert req.data_path is None
    assert req.job_type is None
    assert req.params == {}
    assert req.client == "Client"


def test_job_result_holds_status_and_deliverables():
    res = JobResult(
        status="ok",
        tool="inventory_optimization",
        confidence=0.9,
        deliverables={"report": "out/report.md"},
        summary="done",
    )
    assert res.status == "ok"
    assert res.qa_issues == []
    assert res.clarifications == []
    assert res.deliverables["report"].endswith("report.md")


def test_rules_fallback_is_unavailable_and_inert():
    p = llm.RulesFallback()
    assert p.available() is False
    assert p.complete("anything") == ""
    assert p.extract("anything", {}) == {}


def test_get_provider_without_key_returns_rules_fallback(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = llm.get_provider()
    assert isinstance(p, llm.RulesFallback)
    assert p.available() is False


def test_parse_json_object_extracts_embedded_object():
    text = 'Sure! Here it is:\n```json\n{"job_type": "pricing", "n": 3}\n```\nThanks'
    obj = llm.parse_json_object(text)
    assert obj == {"job_type": "pricing", "n": 3}


def test_parse_json_object_returns_empty_on_garbage():
    assert llm.parse_json_object("no json here") == {}
    assert llm.parse_json_object("") == {}


def test_claude_provider_reports_available_without_network():
    # available() must not require the SDK or a network call
    p = llm.ClaudeProvider(api_key="sk-test", model="claude-opus-4-8")
    assert p.available() is True


def _dummy_tool(key, keywords, requires_data=True):
    return Tool(
        key=key, title=key.title(), description=f"{key} tool",
        intent_keywords=tuple(keywords), requires_data=requires_data,
        prepare=lambda req, prov: Prepared(status="ok", payload=None),
        run=lambda payload, params: Produced(report=None, summary="ok"),
        qa=lambda report: [],
        deliver=lambda report, out_dir, client: {},
    )


def test_registry_register_get_list():
    reg = ToolRegistry()
    t = _dummy_tool("inventory_optimization", ["reorder", "inventory"])
    reg.register(t)
    assert reg.get("inventory_optimization") is t
    assert [x.key for x in reg.list()] == ["inventory_optimization"]


def test_registry_rejects_duplicate_key():
    reg = ToolRegistry()
    reg.register(_dummy_tool("pricing", ["price"]))
    with pytest.raises(ValueError):
        reg.register(_dummy_tool("pricing", ["price"]))


def test_registry_get_unknown_raises_keyerror():
    reg = ToolRegistry()
    with pytest.raises(KeyError):
        reg.get("nope")


def test_registry_match_scores_by_keyword_hits():
    reg = ToolRegistry()
    reg.register(_dummy_tool("inventory_optimization", ["reorder", "safety stock", "inventory"]))
    reg.register(_dummy_tool("pricing", ["price", "elasticity", "margin"]))
    ranked = reg.match("set up reorder points and safety stock for my inventory")
    assert ranked[0][0].key == "inventory_optimization"
    assert ranked[0][1] >= 3  # three keyword hits
    assert ranked[1][1] == 0  # pricing has no hits


# ---------------------------------------------------------------------------
# Task 7 — tools.py
# ---------------------------------------------------------------------------

PORTFOLIO = "data/sample_demand_portfolio.csv"
PRICING_CSV = "data/sample_pricing.csv"
_CODE_GRAPH = Path(__file__).resolve().parent.parent / "graphify-out" / "graph.json"


def test_build_default_registry_tools():
    reg = tools.build_default_registry()
    keys = {t.key for t in reg.list()}
    assert keys == {"inventory_optimization", "pricing", "leadership_chain", "cost_to_serve", "sop", "abc_xyz", "sourcing", "ddmrp", "landed_cost", "whatif", "financial_kpis", "reconciliation", "returns", "warehouse_layout", "queuing", "scheduling", "risk", "forecast", "data_quality", "dea", "acceptance_sampling", "earned_value", "learning_curve", "odoo_replenishment", "excel_replenishment", "newsvendor", "cycle_count", "multi_echelon", "transportation", "fefo", "slotting", "simulation", "excess_obsolete", "facility_location", "drp"}
    assert reg.get("leadership_chain").requires_data is False
    assert reg.get("odoo_replenishment").requires_data is False
    assert reg.get("inventory_optimization").requires_data is True
    assert reg.get("cost_to_serve").requires_data is True
    assert reg.get("sop").requires_data is True
    assert reg.get("abc_xyz").requires_data is True


def test_inventory_tool_pipeline_on_sample(tmp_path):
    from scm_agent import llm
    t = tools.inventory_tool()
    req = JobRequest(brief="reorder points", data_path=PORTFOLIO)
    prep = t.prepare(req, llm.RulesFallback())
    assert prep.status == "ok"
    produced = t.run(prep.payload, {})
    assert t.qa(produced.report) == []
    written = t.deliver(produced.report, tmp_path, "Acme")
    assert written["excel"].exists() and written["report"].exists()


def test_inventory_tool_reports_needs_data_when_columns_undetectable(tmp_path):
    from scm_agent import llm
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n", encoding="utf-8")
    t = tools.inventory_tool()
    prep = t.prepare(JobRequest(brief="reorder", data_path=str(bad)), llm.RulesFallback())
    assert prep.status == "needs_data"
    assert prep.messages


def test_pricing_tool_pipeline_on_sample(tmp_path):
    from scm_agent import llm
    t = tools.pricing_tool()
    prep = t.prepare(JobRequest(brief="optimal price", data_path=PRICING_CSV), llm.RulesFallback())
    assert prep.status == "ok"
    produced = t.run(prep.payload, {})
    assert t.qa(produced.report) == []
    written = t.deliver(produced.report, tmp_path, "Acme")
    assert written["excel"].exists()


def test_leadership_tool_with_scores_in_params(tmp_path):
    from scm_agent import llm
    t = tools.leadership_tool()
    req = JobRequest(brief="evaluate our SC leadership", params={"scores": "3 2 3 1 1", "name": "Equipo X"})
    prep = t.prepare(req, llm.RulesFallback())
    assert prep.status == "ok"
    produced = t.run(prep.payload, {})
    assert t.qa(produced.report) == []
    written = t.deliver(produced.report, tmp_path, "Acme")
    assert written["chart"].exists() and written["report"].exists()


def test_leadership_tool_needs_clarification_without_scores_or_llm():
    from scm_agent import llm
    t = tools.leadership_tool()
    prep = t.prepare(JobRequest(brief="how is my leadership?"), llm.RulesFallback())
    assert prep.status == "needs_clarification"
    assert len(prep.messages) >= 10  # the diagnostic questions


# ---------------------------------------------------------------------------
# Task 8 — intent.py
# ---------------------------------------------------------------------------


class _FakeProvider:
    def __init__(self, *, extract_obj=None, complete_text="", available=True):
        self._extract = extract_obj or {}
        self._complete = complete_text
        self._available = available

    def available(self):
        return self._available

    def complete(self, prompt):
        return self._complete

    def extract(self, prompt, schema):
        return dict(self._extract)


def test_classify_routes_inventory_brief():
    reg = tools.build_default_registry()
    res = intent.classify("set up reorder points and safety stock", reg, _FakeProvider(available=False))
    assert res.job_type == "inventory_optimization"
    assert res.confidence > 0


def test_classify_routes_pricing_and_leadership():
    reg = tools.build_default_registry()
    p = _FakeProvider(available=False)
    assert intent.classify("what price maximizes profit", reg, p).job_type == "pricing"
    assert intent.classify("evaluate our supply chain leadership (CHAIN)", reg, p).job_type == "leadership_chain"


def test_classify_override_wins():
    reg = tools.build_default_registry()
    res = intent.classify("anything", reg, _FakeProvider(available=False), job_type_override="pricing")
    assert res.job_type == "pricing" and res.confidence == 1.0


def test_classify_ambiguous_without_llm_returns_candidates():
    reg = tools.build_default_registry()
    res = intent.classify("help me with my supply chain", reg, _FakeProvider(available=False))
    assert res.job_type is None
    assert res.candidates  # something to disambiguate


def test_classify_uses_llm_when_rules_are_ambiguous():
    reg = tools.build_default_registry()
    prov = _FakeProvider(extract_obj={"job_type": "pricing"}, available=True)
    res = intent.classify("help me with my supply chain", reg, prov)
    assert res.job_type == "pricing"
    assert res.confidence == pytest.approx(0.6)


def test_leadership_tool_scores_via_llm_provider():
    # the leadership LLM-extraction branch in tools.py, exercised deterministically
    t = tools.leadership_tool()
    prov = _FakeProvider(
        extract_obj={"C": 3, "H": 2, "A": 3, "I": 1, "N": 1, "evidence": {"C": "convoca a otras áreas"}},
        available=True,
    )
    prep = t.prepare(JobRequest(brief="great ops, never presents to the board"), prov)
    assert prep.status == "ok"
    assert prep.payload.scores == {"C": 3, "H": 2, "A": 3, "I": 1, "N": 1}
    assert prep.payload.evidence["C"] == "convoca a otras áreas"


# ---------------------------------------------------------------------------
# Task 9 — orchestrator.py + __init__.py exports
# ---------------------------------------------------------------------------


def _rules_orch():
    from scm_agent import llm
    return Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())


def test_orchestrator_inventory_end_to_end(tmp_path):
    res = _rules_orch().run("set up reorder points and safety stock", data_path=PORTFOLIO,
                            client="Acme", out_dir=tmp_path)
    assert res.status == "ok"
    assert res.tool == "inventory_optimization"
    assert "excel" in res.deliverables and Path(res.deliverables["excel"]).exists()


def test_orchestrator_pricing_end_to_end(tmp_path):
    res = _rules_orch().run("what price maximizes profit", data_path=PRICING_CSV, out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "pricing"
    assert Path(res.deliverables["report"]).exists()


def test_orchestrator_leadership_via_params(tmp_path):
    res = _rules_orch().run("evaluate our SC leadership", overrides={"scores": "3 2 3 1 1", "name": "Equipo X"},
                            out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "leadership_chain"
    assert Path(res.deliverables["chart"]).exists()
    assert Path(res.deliverables["report"]).exists()


def test_orchestrator_needs_data_when_required_file_missing(tmp_path):
    res = _rules_orch().run("set up reorder points", out_dir=tmp_path)
    assert res.status == "needs_data" and res.tool == "inventory_optimization"


def test_orchestrator_needs_clarification_on_ambiguous_brief(tmp_path):
    res = _rules_orch().run("help me with my supply chain", out_dir=tmp_path)
    assert res.status == "needs_clarification"
    assert res.clarifications


def test_orchestrator_leadership_needs_clarification_without_scores(tmp_path):
    res = _rules_orch().run("how strong is our leadership?", out_dir=tmp_path)
    assert res.status == "needs_clarification"
    assert len(res.clarifications) >= 10


# ---------------------------------------------------------------------------
# Client profile — durable per-client parameters (fills gaps, never overrides)
# ---------------------------------------------------------------------------


def test_orchestrator_never_applies_a_profile_to_the_generic_client_label(tmp_path):
    # Regression guard: "Client" is JobRequest's own default AND what the webapp
    # substitutes for a blank/anonymous submission — it must never resolve to a
    # shared profile, even if one exists on disk (e.g. hand-created by an operator).
    clients_root = tmp_path / "clients"
    stray = clients_root / "client"
    stray.mkdir(parents=True)
    (stray / "profile.json").write_text(
        '{"client_id": "client", "display_name": "Client", "service_level": 0.5}',
        encoding="utf-8",
    )
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback(),
                        clients_root=clients_root)
    res = orch.run("set up reorder points and safety stock", data_path=PORTFOLIO,
                   out_dir=tmp_path / "out")  # client left at its default ("Client")
    assert res.status == "ok"
    assert "95%" in res.summary  # generic engine default, not the stray profile's 50%


def test_orchestrator_uses_client_profile_to_fill_param_gaps(tmp_path):
    clients_root = tmp_path / "clients"
    client_profile.upsert_profile("acme", "Acme", root=clients_root, service_level=0.99, holding_rate=0.40)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback(),
                        clients_root=clients_root)
    res = orch.run("set up reorder points and safety stock", data_path=PORTFOLIO,
                   client="Acme", out_dir=tmp_path / "out")
    assert res.status == "ok"
    assert "99%" in res.summary  # from the client profile, not the 95% hardcoded default


def test_orchestrator_explicit_override_still_wins_over_profile(tmp_path):
    clients_root = tmp_path / "clients"
    client_profile.upsert_profile("acme", "Acme", root=clients_root, service_level=0.99)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback(),
                        clients_root=clients_root)
    res = orch.run("set up reorder points and safety stock", data_path=PORTFOLIO, client="Acme",
                   overrides={"service_level": 0.80}, out_dir=tmp_path / "out")
    assert res.status == "ok"
    assert "80%" in res.summary


def test_orchestrator_without_profile_behaves_as_before(tmp_path):
    # No profile recorded for this client -> silently falls back to the engine's
    # hardcoded default, exactly like before client profiles existed.
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback(),
                        clients_root=tmp_path / "clients")
    res = orch.run("set up reorder points and safety stock", data_path=PORTFOLIO,
                   client="Brand New Co", out_dir=tmp_path / "out")
    assert res.status == "ok"
    assert "95%" in res.summary


def test_orchestrator_strict_params_asks_once_for_a_new_client(tmp_path):
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback(),
                        clients_root=tmp_path / "clients")
    res = orch.run("set up reorder points and safety stock", data_path=PORTFOLIO,
                   client="Nuevo Cliente", strict_params=True, out_dir=tmp_path / "out")
    assert res.status == "needs_clarification"
    assert res.tool == "inventory_optimization"
    assert any("holding_rate" in c for c in res.clarifications)
    assert any("service_level" in c for c in res.clarifications)
    assert res.deliverables == {}


def test_orchestrator_strict_params_passes_once_profile_has_the_fields(tmp_path):
    clients_root = tmp_path / "clients"
    client_profile.upsert_profile("nuevo-cliente", "Nuevo Cliente", root=clients_root,
                                  holding_rate=0.3, service_level=0.9)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback(),
                        clients_root=clients_root)
    res = orch.run("set up reorder points and safety stock", data_path=PORTFOLIO,
                   client="Nuevo Cliente", strict_params=True, out_dir=tmp_path / "out")
    assert res.status == "ok"


def test_orchestrator_strict_params_ignored_by_tools_with_no_required_params(tmp_path):
    # pricing declares no required_client_params -> strict_params is a no-op for it
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback(),
                        clients_root=tmp_path / "clients")
    res = orch.run("what price maximizes profit", data_path=PRICING_CSV,
                   client="Nuevo Cliente", strict_params=True, out_dir=tmp_path / "out")
    assert res.status == "ok"


def test_orchestrator_unslugifiable_client_label_still_runs(tmp_path):
    # Regression guard: labels with nothing slugifiable ('...') were always legal
    # display copy — the profile lookup must degrade to "no profile", not kill the run.
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback(),
                        clients_root=tmp_path / "clients")
    res = orch.run("set up reorder points and safety stock", data_path=PORTFOLIO,
                   client="...", out_dir=tmp_path / "out")
    assert res.status == "ok"
    assert "95%" in res.summary


def test_orchestrator_surfaces_corrupt_profile_clearly(tmp_path):
    clients_root = tmp_path / "clients"
    stray = clients_root / "acme"
    stray.mkdir(parents=True)
    (stray / "profile.json").write_text("{not valid json", encoding="utf-8")
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback(),
                        clients_root=clients_root)
    res = orch.run("set up reorder points and safety stock", data_path=PORTFOLIO,
                   client="Acme", out_dir=tmp_path / "out")
    assert res.status == "error"
    assert "corrupt client profile" in res.summary  # not the generic "internal error"
    assert res.clarifications  # actionable: fix/delete/rewrite the file


def test_orchestrator_clients_root_none_disables_profiles(tmp_path):
    # Multi-tenant surfaces (webapp/MCP) construct the orchestrator with
    # clients_root=None so a caller-typed label can never pull a stored profile.
    clients_root = tmp_path / "clients"
    client_profile.upsert_profile("acme", "Acme", root=clients_root, service_level=0.99)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback(),
                        clients_root=None)
    res = orch.run("set up reorder points and safety stock", data_path=PORTFOLIO,
                   client="Acme", out_dir=tmp_path / "out")
    assert res.status == "ok"
    assert "95%" in res.summary  # generic default; the on-disk profile is ignored


def test_orchestrator_prepare_sees_profile_merged_params(tmp_path):
    # Several tools bake params into their payload at prepare time (multi_echelon
    # service_level, simulation order_cost) — the merged params must reach prepare.
    clients_root = tmp_path / "clients"
    client_profile.upsert_profile("acme", "Acme", root=clients_root, order_cost=42.0)
    seen: dict = {}

    def probe_prepare(req, prov):
        seen.update(req.params)
        return Prepared(status="ok", payload=None)

    reg = ToolRegistry()
    reg.register(Tool(
        key="probe", title="Probe", description="probe tool",
        intent_keywords=("probe",), requires_data=False,
        prepare=probe_prepare,
        run=lambda payload, params: Produced(report=None, summary="ok"),
        qa=lambda report: [],
        deliver=lambda report, out_dir, client: {},
    ))
    orch = Orchestrator(registry=reg, provider=llm.RulesFallback(), clients_root=clients_root)
    res = orch.run("probe", client="Acme", out_dir=tmp_path / "out")
    assert res.status == "ok"
    assert seen.get("order_cost") == 42.0


def test_inventory_prepare_uses_profile_lead_time_when_csv_has_none(tmp_path):
    csv = tmp_path / "demand.csv"
    rows = ["date,product_id,quantity,unit_cost"]
    for week in range(1, 9):
        rows.append(f"2026-0{(week - 1) // 4 + 1}-{(week - 1) % 4 * 7 + 1:02d},SKU-1,10,5.0")
    csv.write_text("\n".join(rows) + "\n", encoding="utf-8")
    t = tools.inventory_tool()
    req = JobRequest(brief="reorder points", data_path=str(csv), params={"lead_time_days": 30.0})
    prep = t.prepare(req, llm.RulesFallback())
    assert prep.status == "ok"
    assert (prep.payload["lead_time_days"] == 30.0).all()


def test_orchestrator_excel_replenishment_end_to_end(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Stock"
    ws.append(["Codigo", "Stock", "Punto Reorden"])
    ws.append(["SKU-001", 42, 50])
    ws.append(["SKU-002", 130, 80])
    planilla = tmp_path / "planilla.xlsx"
    wb.save(planilla)
    res = _rules_orch().run("actualiza la reposicion de mi planilla", data_path=str(planilla),
                            client="Acme", out_dir=tmp_path / "out")
    assert res.status == "ok"
    assert res.tool == "excel_replenishment"
    assert res.guided is not None and res.guided.options  # ranked, executable options
    assert Path(res.deliverables["csv"]).exists()


def test_orchestrator_excel_replenishment_needs_data_on_csv(tmp_path):
    csv = tmp_path / "demand.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    res = _rules_orch().run("excel replenishment", data_path=str(csv),
                            job_type="excel_replenishment", out_dir=tmp_path / "out")
    assert res.status == "needs_data"
    assert any(".xlsx" in c for c in res.clarifications)


def test_orchestrator_qa_failed_writes_no_deliverables(tmp_path):
    orch = _rules_orch()
    tool = orch.registry.get("leadership_chain")
    # Tool is a frozen dataclass; bypass __setattr__ to force a QA failure
    # (same trick the existing jobs tests use on frozen records).
    object.__setattr__(tool, "qa", lambda report: ["forced issue"])
    res = orch.run("evaluate leadership", overrides={"scores": "3 2 3 1 1"}, out_dir=tmp_path)
    assert res.status == "qa_failed"
    assert res.qa_issues == ["forced issue"]
    assert res.deliverables == {}


def test_orchestrator_narrative_upgrade_with_provider(tmp_path):
    prov = _FakeProvider(complete_text="Upgraded narrative.", available=True)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=prov)
    res = orch.run("evaluate leadership", overrides={"scores": "3 2 3 1 1"}, job_type="leadership_chain",
                   out_dir=tmp_path)
    assert res.status == "ok"
    assert res.summary == "Upgraded narrative."


def test_package_exports():
    import scm_agent
    assert hasattr(scm_agent, "Orchestrator")
    assert hasattr(scm_agent, "JobRequest")
    assert hasattr(scm_agent, "JobResult")
    assert hasattr(scm_agent, "build_default_registry")
    assert hasattr(scm_agent, "get_provider")
    assert hasattr(scm_agent, "KnowledgeBase")


# ---------------------------------------------------------------------------
# L3 grounding — orchestrator cites domain knowledge per job
# ---------------------------------------------------------------------------


def test_job_result_citations_default_empty():
    res = JobResult(status="ok", tool="x", confidence=1.0, deliverables={}, summary="s")
    assert res.citations == []


def test_orchestrator_grounds_inventory_job_with_citations(tmp_path):
    # Real books graph is committed -> an inventory job should cite SCM concepts.
    res = _rules_orch().run("set up reorder points and safety stock", data_path=PORTFOLIO,
                            client="Acme", out_dir=tmp_path)
    assert res.status == "ok"
    assert res.citations  # non-empty
    assert all(" — " in c for c in res.citations)  # "Concept — source loc" shape


def test_orchestrator_degrades_without_knowledge_graph(tmp_path):
    from scm_agent import llm
    from scm_agent.knowledge import KnowledgeBase
    empty_kb = KnowledgeBase(books_path=tmp_path / "no.json", code_path=tmp_path / "no2.json")
    orch = Orchestrator(registry=tools.build_default_registry(),
                        provider=llm.RulesFallback(), knowledge=empty_kb)
    res = orch.run("set up reorder points", data_path=PORTFOLIO, out_dir=tmp_path)
    assert res.status == "ok"
    assert res.citations == []  # no graph -> no citations, job still succeeds


def test_orchestrator_narrative_weaves_citations_when_llm_present(tmp_path):
    prov = _FakeProvider(complete_text="Grounded narrative.", available=True)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=prov)
    res = orch.run("reorder points and safety stock", data_path=PORTFOLIO, out_dir=tmp_path)
    assert res.status == "ok"
    assert res.summary == "Grounded narrative."
    assert res.citations  # citations still attached alongside the LLM summary


@pytest.mark.skipif(not _CODE_GRAPH.exists(), reason="code graph is gitignored / not built")
def test_orchestrator_bridges_a_citation_to_source_code(tmp_path):
    # With the code graph present, L3 grounding bridges at least one cited concept
    # to the src/ module that implements it (theory -> code), e.g. EOQ -> src/eoq.py.
    res = _rules_orch().run("set up reorder points and safety stock", data_path=PORTFOLIO,
                            client="Acme", out_dir=tmp_path)
    assert res.status == "ok"
    bridged = [c for c in res.citations if "  -> " in c]
    assert bridged, res.citations
    assert all(" — " in c for c in bridged)  # the theory half is preserved
    assert any("src/" in c and ".py" in c for c in bridged)  # code half points at a module


# ---------------------------------------------------------------------------
# Task 10 — run_agent.py CLI
# ---------------------------------------------------------------------------


def test_cli_leadership_happy_path(tmp_path, capsys):
    run_agent = importlib.import_module("examples.run_agent")
    code = run_agent.main([
        "--brief", "evaluate our SC leadership", "--job", "leadership_chain",
        "--scores", "3 2 3 1 1", "--name", "Equipo X", "--out", str(tmp_path),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "leadership_chain" in out
    assert (tmp_path / "leadership_chain" / "chain_profile.png").exists()


def test_cli_needs_data_returns_nonzero(tmp_path):
    run_agent = importlib.import_module("examples.run_agent")
    code = run_agent.main(["--brief", "set up reorder points", "--out", str(tmp_path)])
    assert code == 1


def test_cli_inventory_happy_path(tmp_path):
    run_agent = importlib.import_module("examples.run_agent")
    code = run_agent.main(["--brief", "reorder points and safety stock",
                           "--data", PORTFOLIO, "--out", str(tmp_path)])
    assert code == 0
    assert (tmp_path / "inventory_optimization" / "inventory_plan.xlsx").exists()


def test_claude_provider_complete_and_extract_with_fake_client():
    # Lock the Anthropic SDK call shape without the SDK installed or any network:
    # inject a fake client so _ensure_client never imports anthropic.
    from scm_agent import llm

    class _FakeBlock:
        def __init__(self, text):
            self.type = "text"
            self.text = text

    class _FakeMessage:
        def __init__(self, text):
            self.content = [_FakeBlock(text)]

    captured: dict = {}

    class _FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeMessage('{"job_type": "pricing"}')

    class _FakeClient:
        def __init__(self):
            self.messages = _FakeMessages()

    provider = llm.ClaudeProvider(api_key="sk-test", model="claude-opus-4-8")
    provider._client = _FakeClient()  # bypass the lazy SDK import

    assert provider.complete("hello") == '{"job_type": "pricing"}'
    assert captured["model"] == "claude-opus-4-8"
    assert captured["max_tokens"] == 1024
    assert captured["messages"] == [{"role": "user", "content": "hello"}]

    assert provider.extract("hello", {"type": "object"}) == {"job_type": "pricing"}

"""Commercial packages: spec integrity, the package-level QA gate, and the three
end-to-end demo runs (diagnostico / starter / growth)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from examples.run_package import DEMO_PARAMS, build_demo_intake
from scm_agent.package_specs import DIAGNOSTICO, GROWTH, PACKAGES, STARTER, get_package
from scm_agent.packages import missing_required_inputs, run_package
from scm_agent.registry import ToolRegistry
from scm_agent.tools import build_default_registry


class _NoKnowledge:
    """Citation-free stand-in so tests never load the books graph."""

    def ground_citations(self, keywords, brief, limit=5):
        return []

    def warnings(self):
        return []


@pytest.fixture(scope="module")
def demo_intake(tmp_path_factory) -> Path:
    return build_demo_intake(tmp_path_factory.mktemp("intake"))


def _run(spec, intake, out, **kwargs):
    kwargs.setdefault("params", dict(DEMO_PARAMS))
    kwargs.setdefault("knowledge", _NoKnowledge())
    kwargs.setdefault("clients_root", None)
    return run_package(spec, intake, out_dir=out, **kwargs)


# ---- spec integrity -----------------------------------------------------------

def test_every_step_tool_exists_in_registry():
    reg = build_default_registry()
    for spec in PACKAGES.values():
        for step in spec.steps:
            reg.get(step.tool_key)  # KeyError => spec drifted from the registry


def test_every_step_slot_exists_in_spec_inputs():
    for spec in PACKAGES.values():
        slots = {i.slot for i in spec.inputs}
        for step in spec.steps:
            if step.input_slot is not None:
                assert step.input_slot in slots, (spec.key, step.tool_key)


def test_no_duplicate_steps():
    for spec in PACKAGES.values():
        keys = spec.tool_keys()
        assert len(keys) == len(set(keys)), spec.key


def test_scope_matches_monetization_brief():
    """The brief (documentation/MONETIZATION_BRIEF.md) is the commercial source of
    truth: 4 tools in the diagnostic sprint, 8 in Starter, 26 in Growth (Starter +
    the diagnostic's E&O and financial KPIs + 16 more)."""
    assert set(DIAGNOSTICO.tool_keys()) == {
        "data_quality", "abc_xyz", "excess_obsolete", "financial_kpis",
    }
    assert set(STARTER.tool_keys()) == {
        "forecast", "abc_xyz", "whatif", "inventory_optimization", "newsvendor",
        "excel_replenishment", "cycle_count", "data_quality",
    }
    assert set(GROWTH.tool_keys()) == set(STARTER.tool_keys()) | {
        "excess_obsolete", "financial_kpis",
        "multi_echelon", "ddmrp", "simulation", "drp", "odoo_replenishment",
        "reconciliation", "fefo", "sourcing", "landed_cost", "acceptance_sampling",
        "pricing", "cost_to_serve", "learning_curve", "returns", "risk", "dea",
    }
    assert len(GROWTH.tool_keys()) == 26


def test_derive_steps_follow_their_source():
    for spec in PACKAGES.values():
        seen: set[str] = set()
        for step in spec.steps:
            if step.tool_key == "cycle_count" and step.derive is not None:
                assert "abc_xyz" in seen, f"{spec.key}: derive before its source"
            seen.add(step.tool_key)


def test_get_package_unknown_key():
    with pytest.raises(KeyError):
        get_package("no-such-package")


# ---- intake gating ------------------------------------------------------------

def test_missing_required_inputs_lists_the_checklist(tmp_path):
    missing = missing_required_inputs(DIAGNOSTICO, tmp_path)
    assert len(missing) == 4
    assert any(line.startswith("maestro.csv") for line in missing)
    assert any("product_id, cogs, avg_inventory_value" in line for line in missing)


def test_empty_intake_blocks_package_and_writes_nothing(tmp_path):
    out = tmp_path / "out"
    result = _run(DIAGNOSTICO, tmp_path / "empty", out)
    assert result.status == "needs_data"
    assert result.deliverables == {}
    assert len(result.missing_inputs) == 4
    assert not (out / "diagnostico").exists()


def test_optional_steps_skip_without_blocking(demo_intake, tmp_path):
    """Growth without any optional file still delivers: optional steps skip."""
    partial = tmp_path / "partial"
    partial.mkdir()
    for name in ("ventas.csv", "maestro.csv", "planilla.xlsx", "supuestos.csv",
                 "stock.csv", "finanzas.csv", "pedidos.csv"):
        (partial / name).write_bytes((demo_intake / name).read_bytes())
    params = {k: v for k, v in DEMO_PARAMS.items() if k != "use_odoo"}
    result = _run(GROWTH, partial, tmp_path / "out", params=params)
    assert result.status == "ok"
    skipped = {s.tool_key for s in result.steps if s.status == "skipped"}
    assert "reconciliation" in skipped and "odoo_replenishment" in skipped
    executed = {s.tool_key for s in result.steps if s.status == "ok"}
    assert {"forecast", "pricing", "cost_to_serve"} <= executed
    # the coverage table records why each optional step was skipped
    odoo = next(s for s in result.steps if s.tool_key == "odoo_replenishment")
    assert "Odoo" in odoo.skip_reason


# ---- the package-level QA gate --------------------------------------------------

def _registry_with_failing(key: str) -> ToolRegistry:
    reg = build_default_registry()
    broken = ToolRegistry()
    for tool in reg.list():
        if tool.key == key:
            tool = replace(tool, qa=lambda report: ["forced QA failure (test)"])
        broken.register(tool)
    return broken


def test_one_failing_qa_blocks_the_whole_package(demo_intake, tmp_path):
    """The per-tool guarantee, lifted to the package: ONE tool failing QA means
    NOTHING is written - not even the deliverables of the tools that passed."""
    out = tmp_path / "out"
    result = _run(DIAGNOSTICO, demo_intake, out,
                  registry=_registry_with_failing("financial_kpis"))
    assert result.status == "qa_failed"
    assert result.deliverables == {}
    assert any("forced QA failure" in issue for issue in result.qa_issues)
    assert not (out / "diagnostico").exists(), "QA failed but files were written"


def test_optional_step_qa_failure_also_blocks(demo_intake, tmp_path):
    """An optional step that RAN and failed QA blocks too: a package is one
    coherent deliverable. The escape hatch is removing that input and rerunning."""
    out = tmp_path / "out"
    result = _run(GROWTH, demo_intake, out, registry=_registry_with_failing("fefo"))
    assert result.status == "qa_failed"
    assert result.deliverables == {}
    assert not (out / "growth").exists()


def test_required_step_error_blocks_before_writing(demo_intake, tmp_path):
    reg = build_default_registry()
    broken = ToolRegistry()
    for tool in reg.list():
        if tool.key == "abc_xyz":
            def _boom(payload, params):
                raise RuntimeError("engine exploded (test)")
            tool = replace(tool, run=_boom)
        broken.register(tool)
    out = tmp_path / "out"
    result = _run(DIAGNOSTICO, demo_intake, out, registry=broken)
    assert result.status == "error"
    assert result.deliverables == {}
    assert not (out / "diagnostico").exists()


# ---- end-to-end on the demo intake ----------------------------------------------

def _assert_delivered(result, out: Path, spec_key: str, n_tools: int):
    assert result.status == "ok", (result.summary, result.qa_issues)
    executed = [s for s in result.steps if s.status == "ok"]
    assert len(executed) == n_tools
    root = out / spec_key
    assert (root / "deliverable.md").exists()
    assert (root / "deliverable.xlsx").exists()
    for step in executed:
        assert (root / step.tool_key).is_dir(), step.tool_key


def test_diagnostico_end_to_end(demo_intake, tmp_path):
    out = tmp_path / "out"
    result = _run(DIAGNOSTICO, demo_intake, out)
    _assert_delivered(result, out, "diagnostico", 4)


def test_starter_end_to_end(demo_intake, tmp_path):
    out = tmp_path / "out"
    result = _run(STARTER, demo_intake, out)
    _assert_delivered(result, out, "starter", 8)
    # the consolidated deck names every executed tool in its coverage table
    deck = (out / "starter" / "deliverable.md").read_text(encoding="utf-8")
    assert "Cycle-Count Program" in deck and "planilla.xlsx" in deck


def test_growth_end_to_end(demo_intake, tmp_path):
    out = tmp_path / "out"
    result = _run(GROWTH, demo_intake, out)
    _assert_delivered(result, out, "growth", 26)


def test_whatif_fallback_template_runs_and_is_flagged(demo_intake, tmp_path):
    partial = tmp_path / "partial"
    partial.mkdir()
    for name in ("ventas.csv", "maestro.csv", "planilla.xlsx"):
        (partial / name).write_bytes((demo_intake / name).read_bytes())
    out = tmp_path / "out"
    result = _run(STARTER, partial, out)
    assert result.status == "ok"
    whatif = next(s for s in result.steps if s.tool_key == "whatif")
    assert whatif.status == "ok"
    assert "rangos estandar" in whatif.summary
    deck = (out / "starter" / "deliverable.md").read_text(encoding="utf-8")
    assert "plantilla estandar" in deck


def test_cycle_count_derives_from_abc_classification(demo_intake, tmp_path):
    """The count program must count the same SKUs abc_xyz classified - the client
    never sends a second, pre-classified list."""
    out = tmp_path / "out"
    result = _run(STARTER, demo_intake, out)
    abc = next(s for s in result.steps if s.tool_key == "abc_xyz").report
    cc = next(s for s in result.steps if s.tool_key == "cycle_count").report
    assert cc.n_items == abc.n_skus
    abc_classes = {c.product_id: c.abc for c in abc.classifications}
    scheduled = {t.product_id for t in cc.schedule}
    assert scheduled <= set(abc_classes)

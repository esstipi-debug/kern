"""The ranked options menu is rendered into the deck, not just JobResult.guided (point 2).

The sellable artifact (the deck) now carries the same >=2 ranked, executable options - with
the recommended one marked - that the agent returns, so the document the client reads ends in
choices to act, not only a dashboard.
"""

from pathlib import Path

import pandas as pd

from nosilo_core import llm
from nosilo_core.guided import ExecutionOption
from scm_agent import tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable


def test_deliverable_renders_the_options_section():
    d = Deliverable(
        title="Study", client="Acme", summary="summary",
        options=(
            ExecutionOption(label="Do X", summary="best move", recommended=True,
                            action="apply X", tradeoffs="fast"),
            ExecutionOption(label="Do Y", summary="the alternative", action="apply Y", tradeoffs="slow"),
        ),
    )
    md = d.to_markdown()

    assert md.isascii()
    assert "## Options to act" in md
    assert "Do X" in md and "_(recommended)_" in md
    assert "Action: apply X" in md and "Trade-off: fast" in md
    assert "Do Y" in md


def test_deliverable_without_options_omits_the_section():
    d = Deliverable(title="Study", client="Acme", summary="summary")
    assert "## Options to act" not in d.to_markdown()


def test_deck_file_carries_the_ranked_options_end_to_end(tmp_path):
    csv = tmp_path / "counts.csv"
    pd.DataFrame({
        "product_id": ["A", "B"], "system_qty": [100.0, 50.0],
        "physical_qty": [100.0, 48.0], "unit_cost": [5.0, 2.0],
    }).to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("reconcile the physical count and report inventory record accuracy",
                   data_path=str(csv), client="Acme", out_dir=tmp_path)

    assert res.tool == "reconciliation"
    deck_md = Path(res.deliverables["deck_report"]).read_text(encoding="utf-8")
    assert "## Options to act" in deck_md
    assert "_(recommended)_" in deck_md           # the recommended choice is flagged in the deck

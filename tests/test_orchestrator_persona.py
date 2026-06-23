"""The orchestrator narrates in its operating mode's persona (wire-up, plan item).

`modes.py` gives each mode a distinct persona (inventory specialist vs SCM
consultant), but until now the orchestrator's narrative ignored it -
`orchestrator_for(SCM)` and `orchestrator_for(INVENTORY)` produced identical
client-facing summaries. This wires the persona into `_narrative` so the voice
matches the role. Backward-compatible: no persona => the prompt is unchanged.
"""

from scm_agent.modes import INVENTORY, SCM, orchestrator_for
from scm_agent.orchestrator import Orchestrator
from scm_agent.tools import build_default_registry


class _RecordingProvider:
    """Available provider that records every prompt it is asked to complete."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def available(self) -> bool:
        return True

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "narrative"

    def extract(self, prompt: str, schema: dict) -> dict:
        self.prompts.append(prompt)
        return {}


def _narrative_prompt(rec: _RecordingProvider) -> str:
    """The summary-rewrite prompt, identifiable by its instruction."""
    return next(p for p in rec.prompts if "Rewrite this" in p)


def test_persona_is_injected_into_the_narrative_prompt(tmp_path):
    rec = _RecordingProvider()
    persona = "a supply chain manager and consultant"
    orch = Orchestrator(registry=build_default_registry(), provider=rec, persona=persona)

    orch.run("evaluate leadership", overrides={"scores": "3 2 3 1 1"},
             job_type="leadership_chain", out_dir=tmp_path)

    assert persona in _narrative_prompt(rec)


def test_no_persona_leaves_the_prompt_unchanged(tmp_path):
    rec = _RecordingProvider()
    orch = Orchestrator(registry=build_default_registry(), provider=rec)  # no persona

    orch.run("evaluate leadership", overrides={"scores": "3 2 3 1 1"},
             job_type="leadership_chain", out_dir=tmp_path)

    prompt = _narrative_prompt(rec)
    assert "You are" not in prompt
    assert prompt.startswith("Rewrite this")


def test_orchestrator_for_carries_each_mode_persona():
    assert orchestrator_for(SCM).persona == SCM.persona
    assert orchestrator_for(INVENTORY).persona == INVENTORY.persona
    # The two modes have genuinely different voices.
    assert SCM.persona != INVENTORY.persona


def test_default_orchestrator_has_no_persona():
    assert Orchestrator(registry=build_default_registry()).persona == ""

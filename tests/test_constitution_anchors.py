"""The constitution names real code, and keeps naming it.

`.specify/memory/constitution.md` is only worth having if the paths and symbols
it legislates about still exist. A constitution that rots is worse than none:
it reads as authoritative while pointing at files that moved three refactors
ago. This is the same meta-test pattern the repo already uses for CI config
drift (`test_ci_lint_and_coverage_scope.py`, `test_coverage_gate.py`) --
Principle V applied to the document that states Principle V.

The check runs in both directions on purpose:

* every anchor below must exist in the repo (the code moved -> fail), and
* every anchor below must still be quoted in the constitution (someone edited
  the document and dropped a law -> fail).

One direction alone is a half-guard. Without the second, deleting a principle
leaves its anchor silently unreferenced and the test still green.

Writing this table by hand rather than parsing every backtick in the markdown
is deliberate: a generic parser trips over `NaN`, `uv.lock` and prose like
`pytest tests/ -q`, and a guard that cries wolf gets deleted.
"""
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CONSTITUTION = REPO / ".specify" / "memory" / "constitution.md"

# (path relative to the repo root, symbol that must appear inside it or None)
ANCHORS: tuple[tuple[str, str | None], ...] = (
    # Principle I -- the eight fixed places a new capability touches.
    ("scm_agent/tools.py", "from jobs import ("),
    ("scm_agent/tools.py", "def build_default_registry"),
    ("scm_agent/citation_gate.py", "TOOL_CONCEPTS"),
    ("src/i18n.py", "TOOL_TITLES"),
    ("tests/test_scm_agent.py", "def test_build_default_registry_tools"),
    ("tests/test_price_watch_tool.py", "def test_registry_tool_count_matches_the_pinned_list"),
    ("scm_agent/tool_options.py", None),
    ("jobs/intake.py", None),
    # ...and the two surfaces it explicitly tells you NOT to touch.
    ("webapp/how_it_works_data.py", None),
    ("webapp/mcp_tool_specs.py", None),
    # Principles II and III -- the QA gate and loud degradation.
    ("jobs/qa.py", None),
    # Principle IV -- the data contract.
    ("tests/test_tool_data_contract.py", "UNDECLARED_TOOLS"),
    ("src/intake_scan.py", None),
    # Principle V -- the gates that are tests.
    ("tests/test_ci_lint_and_coverage_scope.py", None),
    ("tests/test_coverage_gate.py", None),
    ("tests/test_pack_boundary.py", None),
    ("tests/test_citation_gate.py", None),
    ("tests/test_no_core_gate.py", None),
    # Runtime constraints and workflow.
    (".github/workflows/tests.yml", "prod-boot"),
    (".claude/hooks/uv-guard.sh", None),
    ("uv.lock", None),
    ("Makefile", None),
    ("CLAUDE.md", None),
    ("README.md", None),
    ("CONTRIBUTING.md", None),
)


@pytest.fixture(scope="module")
def constitution() -> str:
    assert CONSTITUTION.is_file(), f"the constitution is missing at {CONSTITUTION}"
    return CONSTITUTION.read_text(encoding="utf-8")


@pytest.mark.parametrize("relpath,symbol", ANCHORS, ids=[f"{p}::{s}" for p, s in ANCHORS])
def test_anchor_exists_in_the_repo(relpath: str, symbol: str | None):
    """The code the constitution legislates about is still where it says."""
    target = REPO / relpath
    assert target.is_file(), (
        f"the constitution names {relpath}, which does not exist. Either restore it "
        f"or amend .specify/memory/constitution.md in the same PR that moved it."
    )
    if symbol is None:
        return
    body = target.read_text(encoding="utf-8", errors="replace")
    assert symbol in body, (
        f"the constitution names `{symbol}` in {relpath}, which no longer contains it. "
        f"Renaming a symbol the constitution depends on means amending the constitution."
    )


@pytest.mark.parametrize("relpath", sorted({p for p, _ in ANCHORS}))
def test_anchor_is_still_quoted_in_the_constitution(constitution: str, relpath: str):
    """Nobody quietly deleted the principle this anchor belongs to."""
    assert relpath in constitution, (
        f"{relpath} is pinned by this test but the constitution no longer mentions it. "
        f"If the principle was removed on purpose, remove its anchor here too -- and "
        f"say in the PR body which failure can no longer occur."
    )


def test_constitution_has_no_unfilled_template_placeholders(constitution: str):
    """A shipped template stub is worse than an empty file: it looks filled in."""
    leftovers = [
        token
        for token in ("[PROJECT_NAME]", "PRINCIPLE_1_NAME", "[GOVERNANCE_RULES]",
                      "[CONSTITUTION_VERSION]", "[RATIFICATION_DATE]", "SECTION_2_NAME")
        if token in constitution
    ]
    assert not leftovers, f"unfilled spec-kit placeholders left in the constitution: {leftovers}"

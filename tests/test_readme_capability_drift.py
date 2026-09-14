"""Guard: the README's capability list must equal the registry, exactly.

The README documents all registered tools by hand. It has already drifted --
the same block claimed "48 agent-routable capabilities" and "49 registered
tools" at once -- and a README that miscounts the product is worse than one
that says nothing, because a reader has no way to tell which half is stale.

Two guards, deliberately different in kind:

* **Set equality**, not a count. A count check passes when a tool is renamed
  or one is added while another is dropped -- the exact cases where the prose
  around the table is most likely wrong too.
* **Every number in the prose**, matched against ``len(registry)``. The
  original drift lived in the sentences, not the table.

Fixing a failure means editing the README, not this test -- ``tools.py`` is the
source of truth (see the note above the table there).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scm_agent.tools import build_default_registry  # noqa: E402

_README = _REPO_ROOT / "README.md"

# The <details> block holding the by-area table. Anchored on the summary line so
# a table added elsewhere in the README is not silently picked up instead.
_TABLE_BLOCK = re.compile(
    # El ancla sigue la redaccion REAL del README. Cambio de "All N capabilities,
    # by area" a "The full list, by area (N tools)" y el guard quedo ciego: el
    # bloque no matcheaba y el test fallaba por ausencia, no por deriva. Se acepta
    # cualquiera de las dos, para que un reordenamiento del README no vuelva a
    # apagar el unico chequeo que mantiene honesto el conteo.
    r"<summary><b>(?:[^<]*?)(?:All \d+ capabilities, by area|The full list, by area \(\d+ tools\))</b></summary>(.*?)</details>",
    re.DOTALL,
)
# A row of the by-area table: "| **Area name** | `tool_a` * `tool_b` |".
_AREA_ROW = re.compile(r"^\|\s*\*\*.+?\*\*\s*\|(.*)\|\s*$", re.MULTILINE)
_TOOL_KEY = re.compile(r"`([a-z][a-z0-9_]*)`")


def _readme_text() -> str:
    return _README.read_text(encoding="utf-8")


def _documented_tool_keys() -> set[str]:
    block = _TABLE_BLOCK.search(_readme_text())
    assert block is not None, (
        "the README no longer contains the by-area capability table block "
        "('All N capabilities, by area' or 'The full list, by area (N tools)') "
        "-- either restore it or update this guard's anchor"
    )
    keys: set[str] = set()
    for row in _AREA_ROW.finditer(block.group(1)):
        keys.update(_TOOL_KEY.findall(row.group(1)))
    return keys


def test_readme_documents_exactly_the_registered_tools() -> None:
    registered = {tool.key for tool in build_default_registry().list()}
    documented = _documented_tool_keys()

    missing = sorted(registered - documented)
    invented = sorted(documented - registered)
    assert not missing, f"registered but absent from the README table: {missing}"
    assert not invented, f"in the README table but not registered: {invented}"


@pytest.mark.parametrize(
    "pattern",
    [
        # "All 49 capabilities, one router" / "49 agent-routable capabilities"
        r"(\d+)\s+(?:agent-routable\s+)?capabilit",
        # "49 registered tools" / "tool (49 registered)"
        r"(\d+)\s+registered",
        # "the full list, by area (49 tools)"
        r"\((\d+)\s+tools\)",
    ],
)
def test_every_capability_count_in_the_prose_matches_the_registry(pattern: str) -> None:
    expected = len(build_default_registry().list())
    claimed = {int(n) for n in re.findall(pattern, _readme_text(), re.IGNORECASE)}
    stale = sorted(n for n in claimed if n != expected)
    assert not stale, (
        f"README claims {stale} for a count the registry puts at {expected} "
        f"(pattern: {pattern!r})"
    )

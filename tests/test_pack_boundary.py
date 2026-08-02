"""The architecture test: can a second domain pack live on the core alone?

``tests/test_core_boundary.py`` checks the arrow does not point *down* - that the
core never imports a domain. This file checks the more interesting direction:
that a domain pack never needs to reach *sideways* into another domain.

If ``packs/source_to_pay`` had to import anything from ``src``, the supply-chain
package would still be carrying something every domain needs, the extraction
would have moved the wrong seven modules, and "a source-to-pay pack can reuse the
core" would be a claim rather than a fact. This is the check that would have
caught that, and it is why the pack was built at all.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKS = ROOT / "packs"

#: Other domains. A pack may depend on the core and on stdlib, never on a peer.
FOREIGN_DOMAINS = frozenset({"src", "scm_agent", "jobs", "webapp", "warehouse"})

#: What a pack is allowed to import besides stdlib and the core. Empty on
#: purpose: source-to-pay needs no third-party dependency at all, and keeping
#: that visible means the next pack has to justify the first one it adds.
ALLOWED_THIRD_PARTY: frozenset[str] = frozenset()

PACK_MODULES = sorted(PACKS.rglob("*.py"))


def _module_id(path: Path) -> str:
    return path.relative_to(PACKS).as_posix()


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_there_is_at_least_one_pack_to_check() -> None:
    """Guard against this whole file silently passing on an empty directory."""
    assert PACK_MODULES, "no pack modules found - this file would pass vacuously"


@pytest.mark.parametrize("module", PACK_MODULES, ids=_module_id)
def test_a_pack_never_imports_another_domain(module: Path) -> None:
    offenders = sorted(_imported_roots(module) & FOREIGN_DOMAINS)
    assert not offenders, (
        f"packs/{_module_id(module)} imports {offenders}, which belongs to another domain.\n"
        "A pack may depend on nosilo_core and on stdlib. Reaching into a peer domain means "
        "either the thing being imported is core material that has not been extracted yet, or "
        "the two packs are modelling the same concept twice and should share an entity type."
    )


@pytest.mark.parametrize("module", PACK_MODULES, ids=_module_id)
def test_a_pack_stays_on_stdlib_and_the_core(module: Path) -> None:
    stdlib = set(sys.stdlib_module_names)
    external = sorted(
        _imported_roots(module) - stdlib - FOREIGN_DOMAINS - ALLOWED_THIRD_PARTY - {"nosilo_core", "packs"}
    )
    assert not external, (
        f"packs/{_module_id(module)} pulls in {external}.\n"
        "source-to-pay needs no third-party dependency; add one to ALLOWED_THIRD_PARTY here "
        "only with a reason, so the cost of each new pack stays visible."
    )


def test_the_pack_imports_the_core_rather_than_reimplementing_it() -> None:
    """A pack that imports nothing from the core is not reusing anything.

    The inverse failure of the test above, and the easier one to miss: a pack
    could satisfy every boundary rule by simply duplicating the machinery, which
    would leave the architecture looking clean and buying nothing.
    """
    used = set()
    for module in PACK_MODULES:
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").startswith(
                "nosilo_core"
            ):
                used.add(node.module)
    assert {
        "nosilo_core.access",
        "nosilo_core.entities",
        "nosilo_core.registry",
        "nosilo_core.guided",
    } <= used, f"the pack only reuses {sorted(used)}"

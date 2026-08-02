"""The dependency direction between ``nosilo_core`` and the domain, enforced.

``nosilo_core`` is the domain-agnostic control plane; ``src`` / ``scm_agent`` /
``jobs`` / ``webapp`` are the supply-chain domain built on top of it. The whole
point of the split is that a *second* domain pack can reuse the core without
inheriting SCM — and that only holds while the arrow points one way.

Nothing about that is self-enforcing: one convenient ``from src.forecasting
import ...`` inside the core silently re-couples the two, and the next domain
pack quietly becomes impossible. These tests are the thing that makes the
extraction stick, so they are deliberately strict and explain the fix when they
fail rather than just asserting False.

They walk the AST rather than grepping the source, so a lazy import buried
inside a function body is caught exactly like a top-level one.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parent.parent / "nosilo_core"

#: Packages that constitute "the domain". The core may not import any of them.
DOMAIN_PACKAGES = frozenset({"src", "scm_agent", "jobs", "webapp", "examples", "warehouse"})

#: Third-party distributions the core is allowed to touch. Kept tiny on purpose:
#: the core should stay liftable into its own package with a near-empty install.
#: ``anthropic`` is imported lazily inside ``llm.ClaudeProvider`` and the module
#: degrades to ``RulesFallback`` without it, so it is optional in practice.
ALLOWED_THIRD_PARTY = frozenset({"anthropic"})

CORE_MODULES = sorted(p for p in CORE.glob("*.py") if p.name != "__init__.py")


def _imported_roots(path: Path) -> set[str]:
    """Every top-level package name imported by ``path``, at any nesting depth.

    Relative imports (``from .guided import ...``) are intentionally skipped:
    they are internal to the core and are what we *want* it to use.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("module", CORE_MODULES, ids=lambda p: p.name)
def test_core_never_imports_the_domain(module: Path) -> None:
    """nosilo_core must not depend on src / scm_agent / jobs / webapp."""
    offenders = sorted(_imported_roots(module) & DOMAIN_PACKAGES)
    assert not offenders, (
        f"nosilo_core/{module.name} imports domain package(s) {offenders}.\n"
        "The core is domain-agnostic by contract: a second domain pack "
        "(source-to-pay, order-to-cash, ...) must be able to reuse it without "
        "inheriting supply chain.\n"
        "If the core genuinely needs this, the thing you are adding is domain "
        "logic and belongs in the domain package instead - or the core needs a "
        "protocol the domain implements, injected by the caller."
    )


@pytest.mark.parametrize("module", CORE_MODULES, ids=lambda p: p.name)
def test_core_dependencies_stay_stdlib(module: Path) -> None:
    """The core stays stdlib-only apart from an explicit, tiny allowlist."""
    stdlib = set(sys.stdlib_module_names)
    third_party = sorted(_imported_roots(module) - stdlib - ALLOWED_THIRD_PARTY - DOMAIN_PACKAGES)
    assert not third_party, (
        f"nosilo_core/{module.name} pulls in third-party package(s) {third_party}.\n"
        f"The core's dependency surface is deliberately {sorted(ALLOWED_THIRD_PARTY)} plus stdlib "
        "so it stays liftable into its own distribution.\n"
        "Add it to ALLOWED_THIRD_PARTY here only if the core truly cannot work "
        "without it; otherwise inject it from the domain side."
    )


@pytest.mark.parametrize("module", CORE_MODULES, ids=lambda p: p.name)
def test_core_has_no_dynamic_domain_imports(module: Path) -> None:
    """AST checks miss ``import_module("src.x")`` / ``sys.modules["src.x"]``."""
    source = module.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module))
    offenders = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.split(".")[0] in DOMAIN_PACKAGES
        and (node.value in DOMAIN_PACKAGES or "." in node.value)
    }
    assert not offenders, (
        f"nosilo_core/{module.name} references domain module path(s) "
        f"{sorted(offenders)} as a string - a dynamic import escapes the AST check "
        "above but re-couples the core just the same."
    )


def test_every_core_module_is_exported() -> None:
    """``nosilo_core.__all__`` stays in step with the files on disk.

    Cheap guard against a module being added to the core without anyone
    deciding it belongs there.
    """
    import nosilo_core

    on_disk = {p.stem for p in CORE_MODULES}
    exported = set(nosilo_core.__all__)
    assert on_disk == exported, (
        f"nosilo_core/ has {sorted(on_disk - exported)} not listed in __all__ and "
        f"{sorted(exported - on_disk)} listed but missing on disk.\n"
        "Adding a module to the core is an architectural decision - make it explicit."
    )

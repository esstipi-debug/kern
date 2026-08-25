"""Detect whether a test file (transitively) imports nosilo_core.

Used by tests/conftest.py when KERN_CI_NO_CORE=1 so GitHub Actions can still
gate engine/listing changes without a private-repo credential. The scanner is
AST-only: it never imports the modules under test.
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_LOCAL_ROOTS = frozenset(
    {"src", "scm_agent", "jobs", "packs", "webapp", "warehouse", "examples"}
)

_memo: dict[Path, bool] = {}


def depends_on_nosilo_core(path: Path) -> bool:
    """True if *path* or a local module it imports mentions nosilo_core."""
    return _walk(path.resolve(), visiting=set())


def _walk(path: Path, visiting: set[Path]) -> bool:
    cached = _memo.get(path)
    if cached is not None:
        return cached
    if path in visiting:
        return False
    if not path.is_file() or path.suffix != ".py":
        return False
    try:
        path.relative_to(_REPO)
    except ValueError:
        return False

    visiting.add(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        _memo[path] = False
        return False

    for node in ast.walk(tree):
        if _imports_nosilo(node):
            _memo[path] = True
            return True
        for target in _imported_local_paths(path, node):
            if _walk(target, visiting):
                _memo[path] = True
                return True
    _memo[path] = False
    return False


def _imports_nosilo(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name.split(".", 1)[0] == "nosilo_core" for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module:
        return node.module.split(".", 1)[0] == "nosilo_core"
    return False


def _imported_local_paths(path: Path, node: ast.AST) -> list[Path]:
    found: list[Path] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            resolved = _resolve_absolute(alias.name)
            if resolved is not None:
                found.append(resolved)
    elif isinstance(node, ast.ImportFrom):
        found.extend(_resolve_from(path, node))
    return found


def _resolve_absolute(mod: str) -> Path | None:
    top = mod.split(".", 1)[0]
    if top == "nosilo_core":
        return None
    if top not in _LOCAL_ROOTS:
        return None
    return _module_path(_REPO.joinpath(*mod.split(".")))


def _resolve_from(path: Path, node: ast.ImportFrom) -> list[Path]:
    found: list[Path] = []
    if node.level and node.level > 0:
        rel = path.parent
        for _ in range(node.level - 1):
            rel = rel.parent
        if node.module:
            rel = rel.joinpath(*node.module.split("."))
        package = _module_path(rel)
        if package is not None:
            found.append(package)
        for alias in node.names:
            if alias.name == "*":
                continue
            sub = _module_path(rel / alias.name)
            if sub is not None:
                found.append(sub)
        return found
    if not node.module:
        return found
    top = node.module.split(".", 1)[0]
    if top == "nosilo_core":
        return found
    if top not in _LOCAL_ROOTS:
        return found
    base = _REPO.joinpath(*node.module.split("."))
    package = _module_path(base)
    if package is not None:
        found.append(package)
    for alias in node.names:
        if alias.name == "*":
            continue
        sub = _module_path(base / alias.name)
        if sub is not None:
            found.append(sub)
    return found


def _module_path(base: Path) -> Path | None:
    py = base.with_suffix(".py")
    if py.is_file():
        return py
    init = base / "__init__.py"
    if init.is_file():
        return init
    return None

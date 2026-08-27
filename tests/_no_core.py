"""Detect whether a test file (transitively) imports nosilo_core.

Used by tests/conftest.py when KERN_CI_NO_CORE=1 so GitHub Actions can still
gate engine/listing changes without a private-repo credential. The scanner is
AST-only: it never imports the modules under test.

Python executes every parent package ``__init__.py`` before the leaf module, so
``from scm_agent.events import EventLedger`` still runs ``scm_agent/__init__.py``
(which imports nosilo_core). ``importlib.import_module("examples.run_repricing")``
is the same as an import for collection purposes. Subprocess CLIs are the same
at runtime: a string ``examples/run_package.py`` or a ``python -c`` snippet that
``import webapp.app`` still loads nosilo_core, even though the test file itself
does not. All of those have to count, or pytest dies on collection or on the
first subprocess.
"""
from __future__ import annotations

import ast
import textwrap
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
        snippet = _string_value(node)
        if snippet and _snippet_reaches_nosilo(snippet, visiting):
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
            found.extend(_paths_for_module(alias.name))
    elif isinstance(node, ast.ImportFrom):
        found.extend(_resolve_from(path, node))
    else:
        literal = _import_module_literal(node)
        if literal:
            found.extend(_paths_for_module(literal))
    return found


def _paths_for_module(mod: str) -> list[Path]:
    """Leaf module plus every parent package ``__init__.py`` Python would run."""
    top = mod.split(".", 1)[0]
    if top == "nosilo_core" or top not in _LOCAL_ROOTS:
        return []
    found = _package_init_chain(mod)
    resolved = _module_path(_REPO.joinpath(*mod.split(".")))
    if resolved is not None:
        found.append(resolved)
    return found


def _package_init_chain(mod: str) -> list[Path]:
    """``scm_agent.events`` -> ``scm_agent/__init__.py`` (not the leaf)."""
    parts = mod.split(".")
    found: list[Path] = []
    accum = _REPO
    for part in parts[:-1]:
        accum = accum / part
        init = accum / "__init__.py"
        if init.is_file():
            found.append(init)
    return found


def _string_value(node: ast.AST) -> str | None:
    """String literals, including f-string constant parts (``python -c`` scripts)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("0")
        return "".join(parts)
    return None


def _maybe_local_script(value: str) -> Path | None:
    """``examples/run_package.py`` as a subprocess argv, not a Python import."""
    text = value.strip().replace("\\", "/")
    if not text.endswith(".py") or "\n" in text or text.startswith("/"):
        return None
    parts = Path(text).parts
    if not parts or ".." in parts or parts[0] not in _LOCAL_ROOTS:
        return None
    candidate = (_REPO.joinpath(*parts)).resolve()
    try:
        candidate.relative_to(_REPO)
    except ValueError:
        return None
    if candidate.is_file() and candidate.suffix == ".py":
        return candidate
    return None


def _snippet_reaches_nosilo(snippet: str, visiting: set[Path]) -> bool:
    script = _maybe_local_script(snippet)
    if script is not None and _walk(script, visiting):
        return True
    if "import" not in snippet:
        return False
    try:
        tree = ast.parse(textwrap.dedent(snippet))
    except SyntaxError:
        return False
    dummy = _REPO / "_snippet.py"
    for node in ast.walk(tree):
        if _imports_nosilo(node):
            return True
        for target in _imported_local_paths(dummy, node):
            if _walk(target, visiting):
                return True
    return False


def _import_module_literal(node: ast.AST) -> str | None:
    """``importlib.import_module("examples.run_repricing")`` -- string arg only."""
    if not isinstance(node, ast.Call) or not node.args:
        return None
    func = node.func
    is_import_module = (
        isinstance(func, ast.Attribute)
        and func.attr == "import_module"
        and isinstance(func.value, ast.Name)
        and func.value.id == "importlib"
    ) or (isinstance(func, ast.Name) and func.id == "import_module")
    if not is_import_module:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _resolve_from(path: Path, node: ast.ImportFrom) -> list[Path]:
    found: list[Path] = []
    if node.level and node.level > 0:
        rel = path.parent
        for _ in range(node.level - 1):
            rel = rel.parent
        if node.module:
            rel = rel.joinpath(*node.module.split("."))
        found.extend(_ancestor_init_paths(rel if rel.is_dir() else rel.parent))
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
    found.extend(_package_init_chain(node.module))
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


def _ancestor_init_paths(package_dir: Path) -> list[Path]:
    """Parent ``__init__.py`` files Python already ran to reach this package."""
    found: list[Path] = []
    try:
        package_dir.resolve().relative_to(_REPO)
    except ValueError:
        return found
    cur = package_dir.resolve()
    while cur != _REPO and _REPO in cur.parents:
        init = cur / "__init__.py"
        if init.is_file():
            found.append(init)
        cur = cur.parent
    return found


def _module_path(base: Path) -> Path | None:
    py = base.with_suffix(".py")
    if py.is_file():
        return py
    init = base / "__init__.py"
    if init.is_file():
        return init
    return None

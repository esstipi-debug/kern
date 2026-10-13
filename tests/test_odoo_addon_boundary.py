"""The Odoo addon stays a thin client: the engine never ships to the customer.

``odoo_addon/<version>/kern_dry_run/`` is the one artefact that leaves this
repository and lands in a third party's Odoo (the Apps Store, or a client's
Odoo.sh). Its whole security story is that it is a *window*: it reads a few
confirmed-sale fields, POSTs them to Kern over HTTPS with an ``X-API-Key``, and
renders what comes back. The engine, the knowledge graph and the writeback
machinery stay on the server, and the Store reviewer sees a wizard — never the
motor.

That property is currently true and completely unenforced. Nothing stops a
convenient ``from src.forecasting import ...`` inside the addon: it would work
locally, pass CI, ship to a customer, and hand over the part of Kern that is
actually defensible. It would also silently relicense the dispute — the addon is
OPL-1 and the engine is proprietary, so an import across that line drags engine
code into a distribution governed by another contract.

These tests are the thing that makes the split real. They walk the AST rather
than grepping, so a lazy import inside a function body is caught like any other,
and they explain the fix when they fail instead of just asserting False.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ADDON_ROOT = Path(__file__).resolve().parent.parent / "odoo_addon"

#: "The engine". The addon may never import any of these: the arrow points one
#: way, and a module a customer installs cannot carry the product.
ENGINE_PACKAGES = frozenset(
    {"src", "scm_agent", "jobs", "webapp", "nosilo_core", "packs", "examples", "warehouse"}
)

#: Third-party distributions that have no business in an addon the client runs.
#: A hit here means someone started building a second engine inside the module.
FORBIDDEN_THIRD_PARTY = frozenset(
    {
        "pandas", "numpy", "scipy", "matplotlib", "openpyxl", "yaml", "pyyaml",
        "requests", "httpx", "bs4", "lxml", "anthropic", "openai", "mcp",
        "fastapi", "starlette", "uvicorn", "sqlalchemy", "duckdb",
    }
)

#: Odoo is the host framework — the module is *for* it, so it is expected.
ALLOWED_THIRD_PARTY = frozenset({"odoo"})

#: The harness runs inside `odoo shell` and resolves the module by its bare name.
HARNESS_EXTRA_ALLOWED = frozenset({"mcp_client"})

#: Line budgets. Current size is ~390 lines per version: the allowance is
#: headroom for real work, not room to grow a second engine behind the wizard.
MODULE_LINE_BUDGET = 700
HARNESS_LINE_BUDGET = 250

#: A credential committed to a module that ships to third parties is a leak that
#: cannot be revoked by rotating a server-side key.
CREDENTIAL_PATTERNS = (
    re.compile(r"lpk_[A-Za-z0-9_\-]{10,}"),          # Kern MCP key
    re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),          # PEM block
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),             # OpenAI-style secret
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),      # GitHub token
)

MODULE_DIRS = sorted(p for p in ADDON_ROOT.glob("*/kern_dry_run") if p.is_dir())
HARNESS = ADDON_ROOT / "test"
PY_FILES = sorted(p for p in ADDON_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path) -> str:
    """Stable pytest id; bare filenames collide between 17.0 and 19.0."""
    return path.relative_to(ADDON_ROOT).as_posix()


def _is_module_file(path: Path) -> bool:
    return any(path.is_relative_to(d) for d in MODULE_DIRS)


def _is_harness_file(path: Path) -> bool:
    return HARNESS in path.parents


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def _imported_roots(path: Path) -> set[str]:
    """Every top-level package name imported by ``path``, at any nesting depth.

    Relative imports (``from .lib.mcp_client import ...``) are internal to the
    addon and are exactly what we want it to use, so they are skipped.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _manifest(module_dir: Path) -> dict:
    """``__manifest__.py`` is a dict literal — evaluate it without importing."""
    text = (module_dir / "__manifest__.py").read_text(encoding="utf-8")
    value = ast.literal_eval(text)
    assert isinstance(value, dict), f"{_rel(module_dir)}/__manifest__.py is not a dict literal"
    return value


# --------------------------------------------------------------------------- #
# The addon stays in its lane
# --------------------------------------------------------------------------- #


def test_no_python_lives_outside_a_module_or_the_harness() -> None:
    """Python under ``odoo_addon/`` belongs to a version module or the harness.

    A stray script at ``odoo_addon/`` root is how a helper quietly becomes
    something that ships without a manifest, a licence or a review.
    """
    strays = sorted(
        _rel(p)
        for p in PY_FILES
        if not _is_module_file(p) and not _is_harness_file(p)
    )
    assert not strays, (
        f"Python outside every version module and outside the harness: {strays}.\n"
        "Everything that ships must live in odoo_addon/<version>/kern_dry_run/ so it "
        "carries a manifest, a LICENSE and a COPYRIGHT. Test and build tooling goes in "
        "odoo_addon/test/. If this file is neither, it does not belong under odoo_addon/."
    )


@pytest.mark.parametrize("module", [p for p in PY_FILES if _is_module_file(p)], ids=_rel)
def test_module_never_imports_the_engine(module: Path) -> None:
    """The shipped module must not import src / scm_agent / nosilo_core / ..."""
    offenders = sorted(_imported_roots(module) & ENGINE_PACKAGES)
    assert not offenders, (
        f"{_rel(module)} imports engine package(s) {offenders}.\n"
        "The addon is a thin client: it POSTs confirmed-sale fields to Kern over HTTPS "
        "and renders the response. The engine, the knowledge graph and the writeback "
        "machinery stay on the server — that split is the product's main defence, and "
        "it is also a licensing boundary (the addon is OPL-1, the engine proprietary).\n"
        "If the module genuinely needs more, the thing you are adding is an endpoint on "
        "Kern's side, not engine code on the client's."
    )


@pytest.mark.parametrize("module", [p for p in PY_FILES if _is_module_file(p)], ids=_rel)
def test_module_stays_odoo_and_stdlib(module: Path) -> None:
    """An addon installs into someone else's Odoo: no new distributions, ever."""
    stdlib = set(sys.stdlib_module_names)
    offenders = sorted(
        _imported_roots(module) - stdlib - ALLOWED_THIRD_PARTY - FORBIDDEN_THIRD_PARTY
    )
    forbidden = sorted(_imported_roots(module) & FORBIDDEN_THIRD_PARTY)
    assert not forbidden, (
        f"{_rel(module)} pulls in third-party package(s) {forbidden}.\n"
        "The module's dependency surface is Odoo plus the standard library, on purpose: "
        "a client installs this into a live ERP, and every extra wheel is a new supply "
        "chain they did not ask for. Do the work on Kern's side and fetch the result."
    )
    assert not offenders, (
        f"{_rel(module)} imports {offenders}, which is neither Odoo nor the standard "
        "library nor a known name.\n"
        "Either it is a dependency that must not ship (see the note above), or it is a "
        "typo for something internal — relative imports (`from .lib.mcp_client import ...`) "
        "are the way to reach the rest of the module."
    )


@pytest.mark.parametrize("module_dir", MODULE_DIRS, ids=_rel)
def test_module_stays_within_its_line_budget(module_dir: Path) -> None:
    """A module a client installs cannot grow into an engine.

    The budget is deliberately generous against today's size (~390 lines): it is
    not a style limit, it is the boundary that makes "the motor does not travel"
    fail a build instead of failing a contract review.
    """
    total = sum(_line_count(p) for p in sorted(module_dir.rglob("*.py")))
    assert total <= MODULE_LINE_BUDGET, (
        f"{_rel(module_dir)} is {total} lines of Python, over the {MODULE_LINE_BUDGET} budget.\n"
        "The addon is a window, not a second implementation. If the module really needs "
        "this much, the functionality belongs behind a Kern endpoint — that keeps it "
        "out of the client's ERP, out of the OPL-1 distribution and out of reach of "
        "anyone who installs the module."
    )


def test_harness_stays_within_its_line_budget() -> None:
    """The test harness is tooling, not a place to park a feature."""
    total = sum(_line_count(p) for p in sorted(HARNESS.rglob("*.py")))
    assert total <= HARNESS_LINE_BUDGET, (
        f"odoo_addon/test/ is {total} lines, over the {HARNESS_LINE_BUDGET} budget.\n"
        "This harness exists to prove the module installs and renders. Anything larger "
        "belongs in this repo's own tests/ (tests/test_odoo_addon_boundary.py included)."
    )


@pytest.mark.parametrize("harness", [p for p in PY_FILES if _is_harness_file(p)], ids=_rel)
def test_harness_also_never_imports_the_engine(harness: Path) -> None:
    """The harness runs inside a client-shaped Odoo; same boundary applies."""
    roots = _imported_roots(harness)
    offenders = sorted(roots & ENGINE_PACKAGES)
    assert not offenders, (
        f"{_rel(harness)} imports engine package(s) {offenders}.\n"
        "The harness installs the module into a real Odoo and checks it renders — it is "
        "the customer's view of the addon. Importing the engine here would prove the "
        "module works only in a checkout that has Kern, which is precisely the situation "
        "the split exists to avoid."
    )
    unexpected = sorted(
        roots - set(sys.stdlib_module_names) - ALLOWED_THIRD_PARTY
        - HARNESS_EXTRA_ALLOWED - FORBIDDEN_THIRD_PARTY
    )
    assert not unexpected, (
        f"{_rel(harness)} imports {unexpected}, which the harness is not expected to need.\n"
        f"Its allowlist is Odoo + stdlib + {sorted(HARNESS_EXTRA_ALLOWED)}."
    )


# --------------------------------------------------------------------------- #
# The distributed artefact is complete and correctly licensed
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("module_dir", MODULE_DIRS, ids=_rel)
def test_every_version_module_ships_its_manifest_and_licence(module_dir: Path) -> None:
    """Odoo will not install a module without a manifest, and the Store requires
    the licence files to be present and consistent with the manifest."""
    required = ["__manifest__.py", "__init__.py", "LICENSE", "COPYRIGHT"]
    missing = [name for name in required if not (module_dir / name).is_file()]
    assert not missing, (
        f"{_rel(module_dir)} is missing {missing}.\n"
        "A module published to the Apps Store needs its manifest and the OPL-1 text "
        "next to it; the reviewer checks exactly this."
    )


@pytest.mark.parametrize("module_dir", MODULE_DIRS, ids=_rel)
def test_addon_licence_is_not_inherited_from_the_engine(module_dir: Path) -> None:
    """Two artefacts, two contracts — and they must not silently merge.

    Now that the root LICENSE is proprietary rather than MIT, the tempting mistake
    is to let one document cover both: either the module ships under the engine's
    terms (which the Apps Store will not carry), or a root-level "all rights
    reserved" quietly swallows a distribution that has to be OPL-1.
    `tests/test_odoo_addon_listing.py` owns the OPL-1 text and the Store fields;
    this test owns only the separation between the two licences.
    """
    root_licence = (ADDON_ROOT.parent / "LICENSE").read_text(encoding="utf-8", errors="replace")
    addon_licence = (module_dir / "LICENSE").read_text(encoding="utf-8", errors="replace")
    assert addon_licence.strip() != root_licence.strip(), (
        f"{_rel(module_dir)}/LICENSE is byte-identical to the repository's root LICENSE.\n"
        "The module that ships to customers is OPL-1; the engine is proprietary and stays "
        "in-house. They are deliberately different documents — selling the addon does not "
        "license the engine, and the engine's terms cannot be shipped to the Store."
    )
    assert "Odoo Proprietary License" in addon_licence, (
        f"{_rel(module_dir)}/LICENSE is not the Odoo Proprietary License text.\n"
        "The manifest declares OPL-1, so the file next to it has to be that licence — and "
        "it must not be the root LICENSE copied over."
    )


def test_versions_agree_on_the_distribution_contract() -> None:
    """17.0 and 19.0 are ports of one product, not two products."""
    if len(MODULE_DIRS) < 2:
        pytest.skip("only one version module present")
    manifests = {_rel(d): _manifest(d) for d in MODULE_DIRS}
    keys = ("name", "license", "author", "depends")
    reference_name, reference = next(iter(manifests.items()))
    for name, manifest in manifests.items():
        differing = [k for k in keys if manifest.get(k) != reference.get(k)]
        assert not differing, (
            f"{name} and {reference_name} disagree on {differing}.\n"
            f"  {reference_name}: { {k: reference.get(k) for k in differing} }\n"
            f"  {name}: { {k: manifests[name].get(k) for k in differing} }\n"
            "These fields describe the same product to the same Store. Fix the port, or "
            "if the difference is intentional, say so here so the next person can tell "
            "a decision from drift."
        )


# --------------------------------------------------------------------------- #
# Nothing secret ships to a third party
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", PY_FILES, ids=_rel)
def test_no_credential_is_embedded(path: Path) -> None:
    """A key committed here ships to every customer and cannot be rotated away."""
    text = path.read_text(encoding="utf-8", errors="replace")
    hits = [pattern.pattern for pattern in CREDENTIAL_PATTERNS if pattern.search(text)]
    assert not hits, (
        f"{_rel(path)} matches credential pattern(s) {hits}.\n"
        "The module reads its key from Odoo's own configuration "
        "(ir.config_parameter 'kern_dry_run.api_key'); it must never carry one. "
        "A key in a distributed artefact is a key you cannot revoke."
    )

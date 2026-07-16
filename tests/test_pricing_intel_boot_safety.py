"""Regression guard: the app must boot with ONLY the base ``.[web,mcp]``
production deps -- i.e. WITHOUT the optional ``pricing-intel``/``dataquality``
extras (bs4, price-parser, rapidfuzz, python-stdnum).

Why this exists: those extras are pulled into the dev/CI environment
*transitively* (e.g. ``extruct`` -> ``mf2py`` -> ``beautifulsoup4``), so a
hard ``from bs4 import ...`` at module level passes the whole normal test
suite AND CI, yet crashes the real Fly deployment on boot -- exactly what
happened when the price-watch tools put ``src.pricing_intel`` on the app's
import chain (``webapp.app`` -> ``scm_agent`` -> ``tools`` ->
``pricing_intel``). This test reproduces the production condition by blocking
those top-level packages in a subprocess and asserting the app still imports,
so the regression can never reach prod silently again.

If this fails: some boot-chain module gained a *module-level* import of an
optional-extra dependency. Move that import inside the function that uses it
(see ``src/pricing_intel/extract.py::_load_beautifulsoup`` /
``normalize.py::_load_price_class`` for the pattern).
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _prod_like_env() -> dict[str, str]:
    # Inherit the real environment (Windows needs SystemRoot/SystemDrive to
    # initialize sockets/asyncio) and only force PYTHONPATH to the repo root.
    return {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}

# Top-level import names of the optional-extra deps that prod's `.[web,mcp]`
# install does NOT include. Blocking these simulates the production container.
_BLOCKED = ("bs4", "price_parser", "rapidfuzz", "stdnum")

_BOOT_SCRIPT = textwrap.dedent(
    f"""
    import sys

    _BLOCKED = {_BLOCKED!r}

    class _Blocker:
        # A meta_path finder that makes the optional-extra packages look absent,
        # exactly as they are in the production `.[web,mcp]` image.
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in _BLOCKED:
                raise ModuleNotFoundError(f"blocked-for-test: {{name}}")
            return None

    sys.meta_path.insert(0, _Blocker())

    # The two imports that matter: the ASGI app the way uvicorn loads it, and
    # the pricing_intel package that regressed. Neither may hard-require an extra.
    import webapp.app  # noqa: F401
    import src.pricing_intel  # noqa: F401
    print("BOOT_OK")
    """
)


def test_app_boots_without_optional_pricing_extras():
    proc = subprocess.run(
        [sys.executable, "-c", _BOOT_SCRIPT],
        cwd=str(_REPO_ROOT),
        env=_prod_like_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "webapp.app / src.pricing_intel failed to import without the optional "
        f"pricing extras (a module-level import of one of {_BLOCKED} crept back):\n{proc.stderr}"
    )
    assert "BOOT_OK" in proc.stdout, proc.stdout + proc.stderr


def test_lazy_loaders_raise_actionable_errors_when_dep_missing():
    """The extraction path degrades loudly, not silently, when the extra is
    absent -- a clear 'install the pricing-intel extra' message, not a bare
    ModuleNotFoundError swallowed as 'no price found'."""
    script = textwrap.dedent(
        """
        import sys

        class _Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in ("bs4", "price_parser"):
                    raise ModuleNotFoundError("blocked-for-test")
                return None

        sys.meta_path.insert(0, _Blocker())

        from src.pricing_intel.extract import ExtractionDependencyMissing, _load_beautifulsoup
        from src.pricing_intel.normalize import PriceParserUnavailable, _load_price_class

        try:
            _load_beautifulsoup()
            raise SystemExit("bs4 loader did not raise")
        except ExtractionDependencyMissing as exc:
            assert "pricing-intel" in str(exc)

        try:
            _load_price_class()
            raise SystemExit("price_parser loader did not raise")
        except PriceParserUnavailable as exc:
            assert "pricing-intel" in str(exc)

        print("LOADERS_OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_REPO_ROOT),
        env=_prod_like_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "LOADERS_OK" in proc.stdout, proc.stdout + proc.stderr

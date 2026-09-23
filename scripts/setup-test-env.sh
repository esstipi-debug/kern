#!/bin/bash
# Kern -- reproducible test environment.
#
# One command to take a fresh container (or a bare venv) to a state where the
# FULL suite runs green and every capability can be exercised:
#
#     bash scripts/setup-test-env.sh
#
# Why this exists (the failure it fixes): `pip install -r requirements-dev.txt`
# is all-or-nothing. In this container two things abort the WHOLE transaction --
# so a plain install leaves you with *zero* packages, not "most of them":
#
#   1. `extruct` -> `jstyleson` fails to build a wheel against the container's
#      old setuptools ("AttributeError: install_layout"). Upgrading pip /
#      setuptools / wheel first makes it build.
#   2. Debian-managed packages (cryptography, PyJWT, wheel, setuptools) can't be
#      cleanly uninstalled by pip ("RECORD file not found"), so any dependency
#      that wants to upgrade them aborts the run. `--ignore-installed` on those
#      specific packages sidesteps it without touching the rest.
#
# Idempotent and non-interactive: safe to run repeatedly.
set -euo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PIP="python -m pip"
QUIET="${KERN_SETUP_QUIET:-}"      # set KERN_SETUP_QUIET=1 to silence pip
PIP_FLAGS=""
[ -n "$QUIET" ] && PIP_FLAGS="-q"

say() { echo "[setup-test-env] $*"; }

# 1. Modern build tooling -- unblocks the jstyleson/extruct wheel build.
#    --ignore-installed dodges the Debian "RECORD file not found" uninstall error.
say "upgrading pip / setuptools / wheel"
$PIP install $PIP_FLAGS --ignore-installed -U pip setuptools wheel

# 2. Pre-clear the Debian-managed packages that heavier extras try to upgrade,
#    so the main install below never aborts trying to uninstall them.
#    PyJWT is the one the header named but this step skipped: `mcp` pulls a
#    newer PyJWT, pip tries to uninstall Debian's 2.7.0, hits "no RECORD file"
#    and the step-3 transaction dies with ZERO packages installed (seen
#    2026-09-23; the SessionStart hook hides it behind `|| true`).
say "reinstalling system-managed conflict packages (cryptography, PyJWT)"
$PIP install $PIP_FLAGS --ignore-installed cryptography PyJWT || true

# 3. The full dev install (mirrors CI's tests.yml: engine + tests + web + all
#    capability extras, incl. pricing-intel/extruct, forecasting, state,
#    tower, dataquality, matching, elasticity, seo). NOTE: src/mcdm.py is
#    base-deps-only (numpy/scipy, no pymcdm), so no MCDM library is verified.
say "installing requirements-dev.txt (this is the full capability set)"
$PIP install $PIP_FLAGS -r "$PROJECT_DIR/requirements-dev.txt"

# 4. Verify the extras that silently degrade when absent actually import, so a
#    half-install surfaces here instead of as a mysterious test failure later.
say "verifying key imports"
python - <<'PY'
import importlib, sys

# (module, what it powers)
checks = [
    ("pandas", "engine core"),
    ("scipy", "engine core"),
    ("pytest", "test runner"),
    ("fastapi", "webapp + test client"),
    ("statsforecast", "modern forecasting (forecast extra)"),
    ("pandera", "state snapshot contracts (state extra)"),
    ("apscheduler", "Tower scheduler (tower extra)"),
    ("rapidfuzz", "dedup/matching (dataquality extra)"),
    ("bs4", "HTML extraction (pricing-intel extra)"),
    ("extruct", "structured price extraction (pricing-intel extra)"),
    ("mcp", "MCP server (mcp extra)"),
]
missing = []
for mod, what in checks:
    try:
        importlib.import_module(mod)
    except Exception as exc:  # noqa: BLE001
        missing.append((mod, what, f"{type(exc).__name__}: {exc}"))

if missing:
    print("  MISSING (capability will silently degrade):")
    for mod, what, err in missing:
        print(f"    - {mod:16s} {what}  ({err})")
    sys.exit(1)
print("  all key imports OK")
PY

say "done. Next: PYTHONPATH=. pytest tests/ -q     (or) PYTHONPATH=. python examples/run_capability_smoke.py"

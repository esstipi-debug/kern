#!/bin/bash
# Kern — SessionStart hook.
#
# Keeps every session ready to work AND keeps the L3 *code graph* fresh, so the
# agent's theory<->code grounding (scm_agent/knowledge.py, the "Fuentes" behind
# each deliverable) always reflects the code as it is right now.
#
#   1. (remote/web only) install Python deps so `pytest` + `ruff` run out of the box.
#   2. Persist PYTHONPATH and graphify's bin dir for the session's later shells.
#   3. Ensure `graphify` is installed, then refresh `graphify-out/` — the code
#      graph read by the grounding layer. It is gitignored and regenerable, so
#      we rebuild it on every session (incremental after the first, via its cache).
#
# Idempotent and non-interactive: safe to run repeatedly.
set -euo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
LOCAL_BIN="$HOME/.local/bin"
export PATH="$LOCAL_BIN:$PATH"

# -- 1. dependencies -- the remote container starts fresh; locally the developer
#       owns their own venv, so only auto-install in the web environment.
if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then
  python -m pip install -q --upgrade pip >/dev/null 2>&1 || true
  pip install -q -r "$PROJECT_DIR/requirements-dev.txt" >/dev/null 2>&1 || true
fi

# -- 2. persist env for the session's later shells (tests use PYTHONPATH=.) --
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export PYTHONPATH=\"$PROJECT_DIR\""
    echo "export PATH=\"$LOCAL_BIN:\$PATH\""
  } >> "$CLAUDE_ENV_FILE"
fi

# -- 3. graphify: refresh the code graph (read by the L3 grounding layer) --
if ! command -v graphify >/dev/null 2>&1; then
  if command -v uv >/dev/null 2>&1; then
    uv tool install graphifyy >/dev/null 2>&1 || true
  else
    pip install -q graphifyy >/dev/null 2>&1 || true
  fi
fi

if command -v graphify >/dev/null 2>&1; then
  if graphify update "$PROJECT_DIR" >/dev/null 2>&1; then
    echo "graphify: code graph refreshed (graphify-out/)"
  else
    echo "graphify: update skipped (non-fatal) — citations stay theory-only"
  fi
else
  echo "graphify: unavailable — install skipped, L3 citations stay theory-only"
fi

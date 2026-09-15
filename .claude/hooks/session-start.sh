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
#       Delegate to scripts/setup-test-env.sh: a plain `pip install -r` here
#       aborts the WHOLE transaction (leaving zero packages installed) when the
#       extruct/jstyleson wheel can't build against the container's old
#       setuptools, or when a heavy extra tries to upgrade a Debian-managed
#       package pip can't uninstall. The script fixes both; see its header.
if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then
  KERN_SETUP_QUIET=1 bash "$PROJECT_DIR/scripts/setup-test-env.sh" >/dev/null 2>&1 || true
fi

# -- 2. persist env for the session's later shells (tests use PYTHONPATH=.) --
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export PYTHONPATH=\"$PROJECT_DIR\""
    echo "export PATH=\"$LOCAL_BIN:\$PATH\""
  } >> "$CLAUDE_ENV_FILE"
fi

# -- 3. graphify: refresh the code graph (read by the L3 grounding layer) --
#
# Un install ROTO es peor que uno ausente, y `command -v` no los distingue. uv
# instala `graphify` como un trampoline .exe que hardcodea la ruta del entorno
# que lo creo; si ese entorno se borra, el .exe queda HUERFANO en ~/.local/bin:
# sigue en PATH, `command -v` sigue diciendo que si, y toda invocacion muere con
# "uv trampoline failed to canonicalize script path". El chequeo de presencia se
# saltaba entonces exactamente la reinstalacion que existe para hacer, y dejaba
# el grafo congelado detras de un aviso "non-fatal" que nadie mira.
#
# Dos correcciones, las dos verificadas el 2026-08-26 contra el binario roto:
#   1. Se prueba que ARRANQUE, no que EXISTA.
#   2. Se usa `--force`, NO `--reinstall`. Con trampolines huerfanos uv ya no
#      reconoce la herramienta (`uv tool list` no la listaba) y `--reinstall`
#      aborta con "Executables already exist ... use `--force` to overwrite".
#      `--force` cubre los tres casos con un solo camino: ausente, entorno roto,
#      y ejecutables huerfanos.
graphify_arranca() { command -v graphify >/dev/null 2>&1 && graphify --version >/dev/null 2>&1; }

if ! graphify_arranca; then
  if command -v uv >/dev/null 2>&1; then
    uv tool install --force graphifyy >/dev/null 2>&1 || true
  else
    python -m pip install -q --force-reinstall graphifyy >/dev/null 2>&1 || true
  fi
fi

if graphify_arranca; then
  if graphify update "$PROJECT_DIR" >/dev/null 2>&1; then
    echo "graphify: code graph refreshed (graphify-out/)"
  else
    echo "graphify: update skipped (non-fatal) — citations stay theory-only"
  fi
else
  echo "graphify: unavailable — install/repair failed, L3 citations stay theory-only"
fi

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
#
# Se instala CON el extra [mcp]. `.mcp.json` levanta `graphify-mcp`, y sin el
# extra ese binario existe, esta en PATH y muere al primer import ("mcp not
# installed"): Claude Code solo lo ve como "Connection closed", y la sesion pierde
# las tools de graphify sin que nada lo diga. Pasaba en cada sesion web hasta el
# 2026-09-23, y `graphify_arranca` no lo veia, porque `graphify` si arrancaba.
GRAPHIFY_PKG='graphifyy[mcp]'
GRAPH_JSON="$PROJECT_DIR/graphify-out/graph.json"

graphify_arranca() { command -v graphify >/dev/null 2>&1 && graphify --version >/dev/null 2>&1; }

# El mismo criterio para el server: que CONTESTE un `initialize`, no que exista.
# Sin grafo no hay nada que servir, asi que eso cuenta como "no se puede probar",
# no como roto. `timeout` solo si es el de coreutils: en Windows el de System32
# es otro comando (espera N segundos), y `--version` los distingue.
graphify_mcp_contesta() {
  [ -f "$GRAPH_JSON" ] || return 0
  command -v graphify-mcp >/dev/null 2>&1 || return 1
  local limite=() salida
  timeout --version >/dev/null 2>&1 && limite=(timeout 120)
  # Se captura y despues se mira, en vez de `| grep -q`: con pipefail, un grep que
  # corta la tuberia al primer match le puede dar SIGPIPE al server y convertir
  # un "contesto" en "no contesto".
  salida="$(printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"kern-session-start","version":"0"}}}' \
    | ${limite[@]+"${limite[@]}"} graphify-mcp "$GRAPH_JSON" 2>/dev/null || true)"
  case "$salida" in *'"serverInfo"'*) return 0 ;; *) return 1 ;; esac
}

instalar_graphify() {
  if command -v uv >/dev/null 2>&1; then
    uv tool install --force "$GRAPHIFY_PKG" >/dev/null 2>&1 || true
  else
    python -m pip install -q --force-reinstall "$GRAPHIFY_PKG" >/dev/null 2>&1 || true
  fi
}

# Cuando el refresco no corre, el grafo que queda NO es "sin grafo": knowledge.py
# (bridge/explain/implements) y los tests que fijan el grafo real lo siguen
# leyendo. El aviso anterior decia "citations stay theory-only", que es cierto
# solo si graph.json no existe; con un grafo viejo, el 2026-09-23 escondio un
# test rojo en #375 hasta que otra sesion lo regenero a mano.
avisar_grafo_viejo() {
  local motivo="$1" construido="at an unknown date" desde
  if [ ! -f "$GRAPH_JSON" ]; then
    echo "graphify: $motivo -- no code graph at all (graphify-out/graph.json missing), code citations unavailable"
    return
  fi
  if desde="$(date -r "$GRAPH_JSON" +%s 2>/dev/null)"; then
    construido="$(date -r "$GRAPH_JSON" '+%Y-%m-%d %H:%M'), $(( ($(date +%s) - desde) / 86400 )) days ago"
  fi
  echo "graphify: $motivo -- code graph is STALE, built $construido;" \
    "bridge/explain/implements and tests/test_knowledge.py still read it." \
    "Rebuild: PYTHONHASHSEED=0 graphify update ."
}

if ! graphify_arranca; then
  instalar_graphify
fi

if graphify_arranca; then
  # GRAPHIFY_NO_BACKUP: con nombres de comunidad curados, graphify copia el grafo
  # previo a graphify-out/<fecha>/ en cada dia que cambia -- ~36 MB por dia, que
  # nadie borra, de un directorio que es regenerable por definicion.
  #
  # PYTHONHASHSEED=0: desde 0.9.66, si la semilla no esta puesta, `update` se
  # relanza a si mismo para fijarla (el clustering depende del orden de los sets).
  # En Linux eso reemplaza el proceso y anda. En Windows `os.execvpe` NO reemplaza
  # nada: arranca un proceso nuevo y el original termina en el acto. Medido el
  # 2026-09-23 en esta maquina contra el repo entero:
  #   - 0.9.66 (uv tool): el relanzamiento busca `~/.local/bin/graphify` sin .exe
  #     y muere con "can't open file"; a veces con codigo de salida 0.
  #   - 0.9.67 (arreglo de upstream, se relanza con `-m graphify`, #3779): vuelve
  #     en 3 s con error y el grafo no aparece nunca (120 s mirando).
  #   - `python -m graphify update` en 0.9.66: vuelve en 0 s con exito, y el
  #     grafo aparece ~50 s DESPUES, escrito por un proceso suelto -- el hook
  #     diria "refreshed" antes de tiempo y nunca veria una falla.
  # Con la semilla ya puesta graphify no se relanza (respeta una semilla
  # explicita), corre sincronico y su codigo de salida vuelve a decir la verdad.
  # 0 es exactamente la que graphify fija, asi que el grafo sale identico.
  if salida_update="$(GRAPHIFY_NO_BACKUP=1 PYTHONHASHSEED=0 graphify update "$PROJECT_DIR" 2>&1)"; then
    echo "graphify: code graph refreshed (graphify-out/)"
  else
    avisar_grafo_viejo "update FAILED ($(printf '%s\n' "$salida_update" | tail -n 1))"
  fi
  if ! graphify_mcp_contesta; then
    instalar_graphify
    if graphify_mcp_contesta; then
      echo "graphify: MCP server repaired (reinstalled with the [mcp] extra)"
    else
      echo "graphify: MCP server does not answer -- graphify tools unavailable this session (CLI still works)"
    fi
  fi
else
  avisar_grafo_viejo "unavailable (install/repair failed)"
fi

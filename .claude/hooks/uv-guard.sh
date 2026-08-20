#!/bin/bash
# Kern — PreToolUse hook sobre Bash: los cambios de dependencias van por uv.
#
# Por que: el repo tiene `uv.lock`. Un `pip install` no lo actualiza, asi que la
# proxima persona (o el proximo contenedor) resuelve un arbol distinto al que se
# probo, y el lock miente en silencio. Es la misma clase de falla que arreglamos
# en el loop nosilo/kern: el artefacto dice una cosa y la realidad es otra.
#
# Bloquea SOLO lo que muta el entorno. `pip download`, `pip list`, `pip show`,
# `poetry show` y demas lectura pasan sin ruido: prohibirlas no protege el lock
# y solo entrena a ignorar el hook.
#
# `uv pip install` pasa — empieza con `uv`, que es exactamente el punto.
#
# Nota deliberada: `scripts/setup-test-env.sh` usa pip a proposito (ver su
# cabecera: un `pip install -r` plano aborta la transaccion entera en el
# contenedor). Corre desde el hook de SessionStart, no por la tool Bash, asi que
# este guard no lo intercepta; e invocado a mano su nombre no contiene `pip`.
#
# La DECISION vive en `uv_guard.py`, al lado. Este archivo solo mueve bytes: el
# guard es de los pocos controles que quedan con la CI roja, y uno que no se
# puede testear no es un control. Con la logica en un modulo importable,
# `tests/test_hooks_uv_guard.py` la fija en la suite en vez de en una verificacion
# manual que no se vuelve a correr. Se sigue parseando el JSON con Python porque
# en esta maquina no hay `jq`.
set -uo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PAYLOAD="$(cat)" AQUI="$AQUI" python - <<'PY'
import importlib.util
import json
import os
import sys

ruta = os.path.join(os.environ["AQUI"], "uv_guard.py")
spec = importlib.util.spec_from_file_location("uv_guard", ruta)
if spec is None or spec.loader is None:
    raise SystemExit(0)  # sin el modulo el hook se calla, no rompe la sesion
modulo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(modulo)

try:
    datos = json.loads(os.environ.get("PAYLOAD") or "{}")
except Exception:
    raise SystemExit(0)

comando = (datos.get("tool_input") or {}).get("command") or ""
if not comando:
    raise SystemExit(0)

mensaje = modulo.revisar(comando)
if mensaje:
    print(mensaje, file=sys.stderr)
    raise SystemExit(2)
raise SystemExit(0)
PY

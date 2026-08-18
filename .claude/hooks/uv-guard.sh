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
set -uo pipefail

PAYLOAD="$(cat)"

PAYLOAD="$PAYLOAD" python - <<'PY'
import json, os, re, sys

try:
    datos = json.loads(os.environ.get("PAYLOAD") or "{}")
except Exception:
    raise SystemExit(0)

comando = (datos.get("tool_input") or {}).get("command") or ""
if not comando:
    raise SystemExit(0)

# Subcomandos que no tocan el entorno instalado.
LECTURA = {
    "pip": {"download", "list", "show", "freeze", "check", "config", "cache", "index"},
    "poetry": {"show", "check", "search", "about", "env", "run", "version"},
    "pipenv": {"graph", "check"},
}

EQUIVALENTE = {
    ("pip", "install"): "uv pip install  (o `uv add` si va a pyproject.toml)",
    ("pip", "uninstall"): "uv pip uninstall",
    ("pip", "sync"): "uv pip sync",
    ("poetry", "add"): "uv add",
    ("poetry", "remove"): "uv remove",
    ("poetry", "install"): "uv sync",
    ("poetry", "update"): "uv lock --upgrade",
    ("poetry", "lock"): "uv lock",
    ("pipenv", "install"): "uv sync",
    ("pipenv", "uninstall"): "uv remove",
}

# Descarta asignaciones de entorno al principio (`VAR=1 pip install ...`).
PREFIJO_ENV = re.compile(r"^(?:\w+=\S*\s+)*")
INVOCACION = re.compile(r"^(?:python\d?\s+-m\s+)?(pip3?|poetry|pipenv)\b(.*)$")


def subcomando(resto):
    """El primer token que no sea bandera.

    `pip -q install x` tiene que leerse como `install`: si no, cualquier bandera
    antes del subcomando esquiva el guard entero.
    """
    for token in resto.split():
        if not token.startswith("-"):
            return token
    return ""


# Corta en segmentos ejecutables. El salto de linea se normaliza a `;` antes de
# partir, para no meter un escape de newline dentro de la clase de caracteres.
segmentos = re.split(r"&&|\|\||[;|]", comando.replace("\n", ";"))

for segmento in segmentos:
    limpio = PREFIJO_ENV.sub("", segmento.strip())
    coincidencia = INVOCACION.match(limpio)
    if not coincidencia:
        continue
    herramienta = coincidencia.group(1)
    sub = subcomando(coincidencia.group(2))
    canonica = "pip" if herramienta == "pip3" else herramienta
    # Sin subcomando real (`pip --version`, `pip`) no hay mutacion posible.
    if not sub or sub in LECTURA.get(canonica, set()):
        continue
    sugerencia = EQUIVALENTE.get((canonica, sub))
    print(
        "Bloqueado: `%s %s` mutaria el entorno sin tocar uv.lock.\n%s\nComando: %s"
        % (
            herramienta,
            sub,
            "Usa en su lugar: %s" % sugerencia if sugerencia else "Este repo maneja dependencias con uv.",
            segmento.strip(),
        ),
        file=sys.stderr,
    )
    raise SystemExit(2)

raise SystemExit(0)
PY

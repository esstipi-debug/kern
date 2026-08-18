#!/bin/bash
# Kern — PostToolUse hook: ruff en el momento de escritura.
#
# Por que existe: la CI de este repo esta roja desde el 2026-08-04 por falta de
# NOSILO_CORE_TOKEN, asi que el control tardio no existe. Mientras eso siga asi,
# el momento de escritura es el UNICO punto donde la calidad se sostiene sola.
#
# Que hace, y que deliberadamente NO hace:
#   1. `ruff check --fix` sobre el archivo editado, con la config del repo
#      (E,F,I en pyproject.toml). Arregla lo mecanico y se calla.
#   2. Lo que --fix no puede arreglar vuelve como feedback (exit 2), para que se
#      resuelva en el momento y no se arrastre al commit.
#   3. NUNCA corre `ruff format`. Medido el 2026-08-10: reformatearia 589 de 769
#      archivos, asi que cada cambio funcional viajaria enterrado en ruido de
#      diff. Formatear este repo es una decision aparte, no un efecto secundario
#      de tocar un archivo.
#
# Solo actua sobre .py dentro del repo: se puede trabajar en otro codebase desde
# aca, y ese no es asunto de este hook.
#
# Idempotente, no interactivo, y si ruff no esta no rompe la sesion.
set -uo pipefail

PAYLOAD="$(cat)"

# Un solo paso en Python: extrae el path, filtra por extension y verifica que
# caiga dentro del repo. En bash habria que pelear con los separadores de
# Windows, y ademas no hay `jq` en esta maquina.
ARCHIVO="$(PAYLOAD="$PAYLOAD" RAIZ="${CLAUDE_PROJECT_DIR:-$(pwd)}" python - <<'PY' 2>/dev/null
import json, os, re
from pathlib import Path


def normalizar(texto):
    """Acepta rutas Windows y rutas estilo MSYS (`/c/Users/...`) por igual.

    Git Bash devuelve la forma MSYS en `pwd` y Claude Code entrega la forma
    Windows. Sin esto la comparacion de contencion falla y el hook se calla
    justo cuando deberia actuar -- que es peor que no tenerlo, porque se
    parece a "todo limpio".
    """
    coincidencia = re.match(r"^/([a-zA-Z])/(.*)$", texto)
    if coincidencia:
        return Path("%s:/%s" % (coincidencia.group(1).upper(), coincidencia.group(2)))
    return Path(texto)


try:
    datos = json.loads(os.environ.get("PAYLOAD") or "{}")
except Exception:
    raise SystemExit(0)

crudo = (datos.get("tool_input") or {}).get("file_path") or ""
if not crudo:
    raise SystemExit(0)

ruta = normalizar(crudo)
if ruta.suffix != ".py" or not ruta.is_file():
    raise SystemExit(0)

try:
    ruta.resolve().relative_to(normalizar(os.environ["RAIZ"]).resolve())
except (ValueError, OSError):
    raise SystemExit(0)

print(ruta.resolve())
PY
)"

[ -n "$ARCHIVO" ] || exit 0

if command -v ruff >/dev/null 2>&1; then
  RUFF=(ruff)
elif python -m ruff --version >/dev/null 2>&1; then
  RUFF=(python -m ruff)
else
  exit 0
fi

"${RUFF[@]}" check --fix --quiet "$ARCHIVO" >/dev/null 2>&1 || true

RESTANTES="$("${RUFF[@]}" check --quiet --output-format concise "$ARCHIVO" 2>/dev/null || true)"

if [ -n "$RESTANTES" ]; then
  {
    echo "ruff dejo violaciones que --fix no arregla solo:"
    echo "$RESTANTES"
    echo ""
    echo "Arreglalas ahora: la CI no las va a agarrar despues."
  } >&2
  exit 2
fi

exit 0

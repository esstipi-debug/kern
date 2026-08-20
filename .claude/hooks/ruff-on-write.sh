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
#   4. NUNCA toca una zona excluida. Ver abajo.
#
# Solo actua sobre .py dentro del repo: se puede trabajar en otro codebase desde
# aca, y ese no es asunto de este hook.
#
# ZONAS EXCLUIDAS
# ---------------
# La lista NO se copia aca: se importa de `scripts/nosilo_kern_loop.py`
# (EXCLUSION_PREFIXES). Dos copias de la misma lista se desincronizan, y un hook
# que cree proteger una ruta que el loop ya dejo de proteger es precisamente la
# clase de mentira que este repo viene cazando -- y encima silenciosa. Una sola
# fuente.
#
# Division del trabajo: el hook NUNCA toca una zona excluida; el loop SI la
# reporta (su fase DETECT las ve, su fase PROPOSE no las arregla). Un desvio
# ahi no se pierde: solo no lo arregla nadie automaticamente.
#
# Si la lista no se puede leer, el hook NO lintea. Preferimos que no haga nada
# antes que auto-arreglar un archivo que quizas estaba protegido: un hook roto
# que no hace nada se nota, uno que reescribe writeback.py no.
#
# Idempotente, no interactivo, y si ruff no esta no rompe la sesion.
set -uo pipefail

PAYLOAD="$(cat)"

# Un solo paso en Python: extrae el path, filtra por extension, verifica que
# caiga dentro del repo y decide si esta en zona excluida. En bash habria que
# pelear con los separadores de Windows, y ademas no hay `jq` en esta maquina.
VEREDICTO="$(PAYLOAD="$PAYLOAD" RAIZ="${CLAUDE_PROJECT_DIR:-$(pwd)}" python - <<'PY' 2>/dev/null
import json, os, re, sys
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

raiz = normalizar(os.environ["RAIZ"]).resolve()
try:
    relativa = ruta.resolve().relative_to(raiz).as_posix()
except (ValueError, OSError):
    raise SystemExit(0)

# Fuente unica: la lista vive en el loop, no aca.
if str(raiz) not in sys.path:
    sys.path.insert(0, str(raiz))
try:
    from scripts.nosilo_kern_loop import EXCLUSION_PREFIXES
except Exception as exc:
    print("SINLISTA %s: %s" % (type(exc).__name__, exc))
    raise SystemExit(0)

if any(relativa.startswith(prefijo) for prefijo in EXCLUSION_PREFIXES):
    print("EXCLUIDO %s" % relativa)
    raise SystemExit(0)

print("LINTEAR %s" % ruta.resolve())
PY
)"

case "$VEREDICTO" in
  LINTEAR\ *)  ARCHIVO="${VEREDICTO#LINTEAR }" ;;
  EXCLUIDO\ *) echo "[hook] zona excluida, no se toca: ${VEREDICTO#EXCLUIDO }" >&2; exit 0 ;;
  SINLISTA\ *) echo "[hook] no se pudo leer EXCLUSION_PREFIXES (${VEREDICTO#SINLISTA }); no se lintea nada" >&2; exit 0 ;;
  *)           exit 0 ;;
esac

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

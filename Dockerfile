# syntax=docker/dockerfile:1

# Production image for the Inventory Planner webapp (webapp/app.py), including
# the mounted read-only MCP server (webapp/mcp_server.py, /mcp).
#
# `scm_agent`, `jobs`, `webapp`, `warehouse` are imported relative to the repo
# root (sys.path, not a setuptools package) - PYTHONPATH=/app plus WORKDIR /app
# reproduces that. Only `src` and `packs` are actual installed packages (see
# [tool.setuptools] in pyproject.toml), via `pip install -e .`.
#
# La imagen se construye SIN NINGUN SECRETO desde el 2026-09-11: nosilo_core
# vive en este repo (nosilo_core/), asi que `docker build -t kern .` alcanza.
# Antes hacian falta un PAT o una deploy key para resolver una dependencia
# privada; eso costo diez dias sin imagen publicada sobre una premisa que
# resulto falsa ("BuildKit no reenvia SSH"), y dejaba a cualquier partner que
# quisiera correr Kern pidiendole credenciales a una sola persona.
#
# See docs/DEPLOYMENT.md.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1


COPY . .


# [core] quedo VACIO (ver pyproject.toml) pero se sigue nombrando: es el comando
# escrito en docs/DEPLOYMENT.md y en varias sesiones, y un extra que desaparece
# rompe esos comandos con un error que no dice que paso.
#
# UNA sola linea de install editable con lista de extras en todo el archivo,
# comentarios incluidos: tests/test_ci_prod_boot_extras.py escanea este Dockerfile
# y compara sus extras contra los otros tres caminos de install. Una imagen que
# pierde un extra es un crash de arranque que nadie ve hasta produccion.
RUN pip install -e ".[core,web,mcp,pricing-intel,dataquality]"

EXPOSE 8000

CMD ["sh", "-c", "uvicorn webapp.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]

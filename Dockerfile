# Production image for the Inventory Planner webapp (webapp/app.py), including
# the mounted read-only MCP server (webapp/mcp_server.py, /mcp).
#
# `scm_agent`, `jobs`, `webapp`, `warehouse` are imported relative to the repo
# root (sys.path, not a setuptools package) - PYTHONPATH=/app plus WORKDIR /app
# reproduces that. Only `src` is an actual installed package (see
# [tool.setuptools] in pyproject.toml), via `pip install -e .`.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY . .

# [web] = fastapi/uvicorn/python-multipart, [mcp] = the mcp package - both are
# hard imports of webapp/app.py (it unconditionally mounts /mcp), not optional.
RUN pip install -e ".[web,mcp]"

EXPOSE 8000

CMD ["sh", "-c", "uvicorn webapp.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]

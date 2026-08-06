# syntax=docker/dockerfile:1
# The syntax directive is load-bearing, not decoration: `RUN --mount=type=secret`
# below needs a BuildKit frontend, and pinning it here means the build does not
# depend on whatever the daemon's default happens to be.

# Production image for the Inventory Planner webapp (webapp/app.py), including
# the mounted read-only MCP server (webapp/mcp_server.py, /mcp).
#
# `scm_agent`, `jobs`, `webapp`, `warehouse` are imported relative to the repo
# root (sys.path, not a setuptools package) - PYTHONPATH=/app plus WORKDIR /app
# reproduces that. Only `src` and `packs` are actual installed packages (see
# [tool.setuptools] in pyproject.toml), via `pip install -e .`.
#
# BUILDING THIS IMAGE NEEDS A SECRET. nosilo-core is a private dependency
# resolved over git+https, so:
#
#   DOCKER_BUILDKIT=1 docker build \
#     --secret id=nosilo_token,env=NOSILO_CORE_TOKEN -t kern .
#
# See docs/DEPLOYMENT.md and the core's CONSUMING.md.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# git is required to resolve the nosilo-core dependency (a git+https URL) and is
# NOT part of python:*-slim - without it pip fails with a bare "git: not found"
# that reads like a broken image rather than a missing package. Its own layer so
# it stays cached across source changes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY . .

# [web] = fastapi/uvicorn/python-multipart, [mcp] = the mcp package - both are
# hard imports of webapp/app.py (it unconditionally mounts /mcp), not optional.
# [pricing-intel,dataquality] = extruct/lxml/price-parser/chompjs/rapidfuzz/
# python-stdnum, needed for the price_intelligence/price_watch tools and the
# public POST /api/demo-price-scan lead magnet (webapp/demo_price_scan.py) to
# actually run instead of raising ExtractionDependencyMissing/
# PriceParserUnavailable uncaught (an unhandled 500 for a real visitor). Safe
# to add post-#155/#156: every import from these extras is now lazy-loaded
# (src/pricing_intel/extract.py, normalize.py, match/*.py) and the `prod-boot`
# CI job (.github/workflows/tests.yml) guards against a future regression of
# the original bs4 ModuleNotFoundError boot crash.
# The credential reaches git through GIT_CONFIG_COUNT/KEY/VALUE rather than
# `git config --global`, because the latter writes the token into /root/.gitconfig
# and that file is part of the committed layer - readable by anyone who pulls the
# image. Supplied via env, it exists only in this process. The secret mount does
# the same job for the file itself: /run/secrets/nosilo_token is never written to
# a layer. Between the two, nothing about the token survives this RUN.
# Offline escape hatch for LOCAL builds (CORE_FROM_WHEEL=1). The secret above is
# the right mechanism for CI and for the operator's deploy, and it is pure
# friction on a developer machine where nosilo_core is already installed in the
# venv: there is nothing to fetch. `vendor/` is versioned (empty but for its
# README) precisely so this COPY cannot fail; the wheels inside are gitignored,
# because a private dependency's wheel does not belong in this repo.
#
# The `sed` on that path is load-bearing, not tidying: pyproject declares
# nosilo-core as a DIRECT URL, and pip re-resolves a direct-URL requirement even
# when a matching version is already installed. Flattening it to a bare name
# INSIDE the image is what lets pip accept the wheel as satisfying it. The
# working tree's pyproject.toml is never touched.
ARG CORE_FROM_WHEEL=0
COPY vendor/ /wheels/

RUN --mount=type=secret,id=nosilo_token \
    if [ "${CORE_FROM_WHEEL}" = "1" ]; then \
        set -e; \
        if ! ls /wheels/nosilo_core-*.whl >/dev/null 2>&1; then \
            echo "ERROR: CORE_FROM_WHEEL=1 but no nosilo_core wheel in vendor/." >&2; \
            echo "See vendor/README.md for how to pack the installed package." >&2; \
            exit 1; \
        fi; \
        pip install /wheels/nosilo_core-*.whl; \
        sed -i 's|"nosilo-core @ git+https://[^"]*"|"nosilo-core"|' pyproject.toml; \
        pip install -e ".[web,mcp,pricing-intel,dataquality]"; \
    else \
        if [ ! -s /run/secrets/nosilo_token ]; then \
            echo "ERROR: build secret 'nosilo_token' is missing or empty." >&2; \
            echo "nosilo-core is a private dependency. Build with:" >&2; \
            echo "  docker build --secret id=nosilo_token,env=NOSILO_CORE_TOKEN ..." >&2; \
            echo "...or build offline from a local wheel:" >&2; \
            echo "  docker build --build-arg CORE_FROM_WHEEL=1 ...  (see vendor/README.md)" >&2; \
            echo "See docs/DEPLOYMENT.md." >&2; \
            exit 1; \
        fi; \
        GIT_CONFIG_COUNT=1 \
        GIT_CONFIG_KEY_0="url.https://x-access-token:$(cat /run/secrets/nosilo_token)@github.com/.insteadOf" \
        GIT_CONFIG_VALUE_0="https://github.com/" \
        pip install -e ".[web,mcp,pricing-intel,dataquality]"; \
    fi

EXPOSE 8000

CMD ["sh", "-c", "uvicorn webapp.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]

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
# BUILDING THIS IMAGE NEEDS A CREDENTIAL for nosilo-core, a private dependency
# resolved over git+https. Either one works:
#
#   # PAT - what Fly and Railway use; neither has an agent to forward.
#   DOCKER_BUILDKIT=1 docker build \
#     --secret id=nosilo_token,env=NOSILO_CORE_TOKEN -t kern .
#
#   # read-only deploy key, over BuildKit's SSH agent forwarding - what CI uses.
#   ssh-add ~/.ssh/nosilo_core
#   DOCKER_BUILDKIT=1 docker build --ssh default -t kern .
#
# The token path is first because it is what production already runs. The SSH
# path exists because a deploy key does not expire and a PAT does, and because
# this repo has NOSILO_CORE_SSH_KEY configured and no PAT at all - without it,
# CI cannot build this image and the published image never happens.
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

# [core] = nosilo-core, the private control plane. It is NOT in
# [project.dependencies] on purpose (so `pip install -e .` works for an external
# evaluator with no token), which means it only arrives if an install line names
# this extra -- and between #226 (2026-08-03, which moved nosilo_core out of the
# repo into that extra) and 2026-08-23 this line did not. The image kept booting
# only because the last one was built from a tree that still carried
# nosilo_core/ as source; a build from a clean checkout produced an image that
# died at `import webapp.app` with ModuleNotFoundError: nosilo_core, because
# webapp/app.py:40 -> jobs/abc_xyz_job.py:18 hard-imports nosilo_core.guided.
# Reproduced 2026-08-23 in a fresh py3.12 venv with this exact line, minus
# [core]. Nothing reported it: the `prod-boot` job below has never once run
# against a tree where nosilo_core is external -- the last green CI run
# (2026-08-03T16:14Z) tested a merge ref that predated #226, and the token was
# never configured, so every run since died at the auth step.
#
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
# The SSH host key is PINNED rather than ssh-keyscan'd, the same call
# .github/actions/authenticate-core makes: a keyscan trusts whatever answers, and
# on a connection that carries a credential that is trust-on-first-use against a
# man-in-the-middle. If GitHub rotates it, this fails loudly and is a one-line fix.
#
# The credential reaches git through GIT_CONFIG_COUNT/KEY/VALUE rather than
# `git config --global`, because the latter writes the token into /root/.gitconfig
# and that file is part of the committed layer - readable by anyone who pulls the
# image. Supplied via env, it exists only in this process. The secret mount does
# the same job for the file itself: /run/secrets/nosilo_token is never written to
# a layer. Between the two, nothing about the token survives this RUN.
#
# Two branches, one rule: whichever credential BuildKit was handed. The token
# branch is byte-for-byte what it always was, so Fly and Railway are untouched.
# The SSH branch is new; `--mount=type=ssh` forwards the agent socket and sets
# SSH_AUTH_SOCK inside this RUN. (`.github/actions/authenticate-core` says
# BuildKit "no tiene equivalente para una clave SSH" - it does, and that belief
# is why .github/workflows/docker.yml has had its triggers off since 2026-08-26.)
#
# openssh-client is installed and purged inside this single RUN so the SSH branch
# adds nothing to the published image, and the token branch never installs it.
# The branches decide only HOW git authenticates; the install line itself is
# single and literal. That is not cosmetic: tests/test_ci_prod_boot_extras.py
# scans this file for exactly ONE editable-install line carrying an extras list,
# and compares those extras against the other three install paths - an image
# that silently lost the `core` extra is a boot crash nobody sees until
# production. Hiding the extras behind a shell variable broke that guard, and so
# does writing a second example of the line anywhere in this file, comments
# included: the scanner reads the whole Dockerfile, not just its RUN steps.
RUN --mount=type=secret,id=nosilo_token --mount=type=ssh \
    set -e; \
    SSH_INSTALLED=0; \
    if [ -s /run/secrets/nosilo_token ]; then \
        GIT_URL_KEY="url.https://x-access-token:$(cat /run/secrets/nosilo_token)@github.com/.insteadOf"; \
    elif [ -n "${SSH_AUTH_SOCK:-}" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends openssh-client; \
        SSH_INSTALLED=1; \
        mkdir -p ~/.ssh && chmod 700 ~/.ssh; \
        printf '%s\n' \
          'github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl' \
          > ~/.ssh/known_hosts; \
        chmod 600 ~/.ssh/known_hosts; \
        GIT_URL_KEY="url.git@github.com:.insteadOf"; \
    else \
        echo "ERROR: no credential for the private nosilo-core dependency." >&2; \
        echo "Build with ONE of:" >&2; \
        echo "  docker build --secret id=nosilo_token,env=NOSILO_CORE_TOKEN ..." >&2; \
        echo "  docker build --ssh default ...   (deploy key already in the agent)" >&2; \
        echo "See docs/DEPLOYMENT.md." >&2; \
        exit 1; \
    fi; \
    export GIT_CONFIG_COUNT=1 \
    && export GIT_CONFIG_KEY_0="$GIT_URL_KEY" \
    && export GIT_CONFIG_VALUE_0="https://github.com/"; \
    pip install -e ".[core,web,mcp,pricing-intel,dataquality]"; \
    if [ "$SSH_INSTALLED" = "1" ]; then \
        apt-get purge -y openssh-client \
        && apt-get autoremove -y \
        && rm -rf /var/lib/apt/lists/* ~/.ssh; \
    fi

EXPOSE 8000

CMD ["sh", "-c", "uvicorn webapp.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]

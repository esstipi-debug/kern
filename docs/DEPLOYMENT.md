# Deployment & hardening

The web app (`webapp/`) is safe for local/internal use out of the box, but a
public deploy needs the access controls turned on and TLS terminated at a proxy.
This is the checklist. See [SECURITY.md](../SECURITY.md) for the threat model and
the controls already enforced in code.

## 0. The private dependency (do this before any deploy path)

`nosilo-core` — the control plane the app is built on — lives in a **private**
repository and is pinned by tag in `requirements.txt` and `pyproject.toml`. Every
build path resolves it over `git+https`, so every build path needs a credential.
Without one the build fails at dependency resolution, before any of the rest of
this document matters.

### CI: use a deploy key, not the token

A fine-grained PAT **expires** — a year at most, and GitHub proposes 30 days by
default. When it does, CI fails at dependency resolution before a single test
runs, and nothing announces it: the red X looks like any other failing build.
That is not hypothetical. `NOSILO_CORE_TOKEN` was never set at all, both jobs
aborted at that step, and **sixteen days of merges went in without a gate**
before anyone looked.

A deploy key has no expiry, so for GitHub Actions:

1. Generate a keypair. It never leaves your machine except as described below:

       ssh-keygen -t ed25519 -N "" -C "kern CI -> nosilo-core" -f nosilo_core_ci

2. `nosilo_core_ci.pub` → `esstipi-debug/nosilo-core` → Settings → Deploy keys →
   Add. **Leave "Allow write access" unchecked.**
3. `nosilo_core_ci` (the private half) → the `kern` repo's Actions secrets, as
   `NOSILO_CORE_SSH_KEY`. This reads it from stdin, so it never lands in your
   shell history:

       gh secret set NOSILO_CORE_SSH_KEY --repo esstipi-debug/kern < nosilo_core_ci

4. Delete both local files. The public half is on GitHub, the private half is in
   the secret, and a copy on disk is one more place it can leak from.

`.github/actions/authenticate-core` prefers the deploy key, falls back to the
PAT, and fails naming both when neither is set. The workflow **does not invoke**
that action when both secrets are empty: a detect step reads them as env
(GitHub rejects `if: ${{ secrets.* }}` at parse time) and emits
`steps.core.outputs.present`. Empty → install without the private git URL and
run the no-core pytest gate (`KERN_CI_NO_CORE=1`) so listing/engine changes
still get a check. With the PAT it emits a
`::warning::` on every run saying that gate has an expiry date — the failure mode
above does not announce itself, so this does it for us.

**This does not change the published dependency.** `requirements.txt` and
`pyproject.toml` keep the `git+https://` URL; only the runner rewrites it to SSH
in its own git config. Docker, Fly, Railway and a plain local `pip install` are
untouched and still use the token — which is why the token is not going away.
There is a test (`tests/test_ci_authenticate_core.py`) that fails if someone
switches the dependency itself to `git+ssh://`.

### The token (still needed for every non-CI path)

1. GitHub → Settings → Developer settings → **Fine-grained personal access token**
2. Repository access: **only** `esstipi-debug/nosilo-core`
3. Permissions: **Contents → Read-only**. Nothing else.

Then give it to whichever build path you use:

| Path | How | Where the credential lives |
|---|---|---|
| **GitHub Actions** | Actions secret `NOSILO_CORE_SSH_KEY` (preferred) or `NOSILO_CORE_TOKEN` | Only in the runner, only for the job |
| **Docker / Fly** | `--secret id=nosilo_token,env=NOSILO_CORE_TOKEN`, **or** `--ssh default` with the deploy key in your agent | Only in the RUN that installs; never in a layer |
| **Railway** | Service variable `NOSILO_CORE_TOKEN` | ⚠️ also present at runtime — see below |

```bash
export NOSILO_CORE_TOKEN=github_pat_...

# Docker (BuildKit is the default in modern Docker; the syntax directive at the
# top of the Dockerfile pins the frontend that provides --mount=type=secret)
docker build --secret id=nosilo_token,env=NOSILO_CORE_TOKEN -t kern .

# Fly uses the same Dockerfile
fly deploy --build-secret nosilo_token="$NOSILO_CORE_TOKEN"

# Railway reads it as a build-time variable
railway variables set NOSILO_CORE_TOKEN="$NOSILO_CORE_TOKEN"
```

**No PAT? Build with the deploy key instead.** BuildKit forwards an SSH agent
socket into the build, so the same key that authenticates CI also builds the
image - and unlike a PAT it does not expire:

```bash
ssh-add ~/.ssh/nosilo_core          # the private half of the deploy key
docker build --ssh default -t kern .
```

The Dockerfile takes the token when one is mounted and falls back to the agent
otherwise, so Fly and Railway keep working exactly as above. This is what
`.github/workflows/docker.yml` uses: the repo has `NOSILO_CORE_SSH_KEY` and no
PAT, and that mismatch is why the image workflow sat with its triggers off from
2026-08-26 until 2026-09-05 and no image was ever published.

**Why the token is never written with `git config --global`.** That command
writes it into `/root/.gitconfig`, which is part of the committed image layer and
readable by anyone who pulls it. Every path here passes the credential through
`GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0`/`GIT_CONFIG_VALUE_0` instead, which git
reads from the environment and never persists.

**Railway is the weak spot, and it is inherent.** Nixpacks has no equivalent of a
BuildKit secret mount, so the token has to be a service variable — and Railway
service variables are present in the runtime environment too, not just the build.
Anything that can read the app's environment can read the token. That is why the
scope above is one repository, read-only: it is the difference between a leak
that exposes source and one that exposes an account. Rotate it if the deploy
environment is ever compromised, and prefer the Fly path when the choice is open.

## 0b. Where Kern writes: `KERN_DATA_DIR`

Everything Kern persists — the events ledger, the autonomy store, the scheduler
jobstore, the state store, the pricing ledger, the SKU map, the MCP key store and
the two lead directories — lives under **one** root:

```bash
docker run -p 8000:8000 -e KERN_DATA_DIR=/data -v kern_data:/data   ghcr.io/esstipi-debug/kern:main
```

That is the whole contract. Without it, Kern still runs — it writes into `data/`
inside the container, which is the image's ephemeral rootfs and is wiped on every
restart. **A client who wants to keep anything sets this one variable.**

Three levels of precedence, most specific first:

| Level | Wins when | Used by |
|---|---|---|
| the store's own `LINCHPIN_*_PATH` / `_DIR` | it is set | this deploy's `fly.toml`, which sets eight of them by hand |
| `KERN_DATA_DIR` | no per-store variable | a self-serve client, and anyone who wants one knob |
| the store's historical default | neither | local development, unchanged |

With `KERN_DATA_DIR` unset nothing moves, so adding it to an existing deploy is a
no-op until you set it. `KERN_DATA_DIR=/data` reproduces exactly the layout the
eight `fly.toml` lines already produce — `tests/test_data_paths.py` reads
`fly.toml` and asserts that equivalence, so the two cannot drift apart.

The registry lives in [`src/data_paths.py`](../src/data_paths.py). **A new durable
store goes in it**, not in a fresh `"data/..."` literal; a guard test fails the
build otherwise. That guard is also what caught `LINCHPIN_SCHEDULER_PATH`, the one
durable store PR #188 missed when it re-rooted the other five.

Read-only configuration (`config/markets`, `config/sites`, `config/verticals`,
`config/monitors.yaml`, `config/retention.yaml`, `config/autonomy.yaml`,
`config/event_routing.yaml`) is deliberately **not** under this root: it ships
inside the image, and pointing it at a client's empty volume would break the app.

## 0c. Auth: `KERN_API_KEY`, y por que grita si falta

`POST /api/jobs` corre trabajos contra tus datos y `GET /jobs-output/*` sirve los
entregables. La key los cierra:

```bash
docker run -p 8000:8000 -e KERN_API_KEY=<una-clave-larga>   ghcr.io/esstipi-debug/kern:main
```

`LINCHPIN_API_KEY` sigue funcionando y **gana si estan las dos** — es la que este
deploy tiene como secret de Fly, y no se toca. `KERN_API_KEY` es el nombre del
contrato self-serve.

Tres estados, no dos:

| Estado | Cuando | Que pasa al arrancar |
|---|---|---|
| **configurada** | la variable trae valor | silencio; el gate esta puesto |
| **apagada a proposito** | la variable esta declarada y vacia | dos lineas en INFO: sigue abierto, pero fue una decision |
| **sin configurar** | ninguna de las dos existe | banner en WARNING de diez lineas, con el comando para cerrarlo |

La distincion entre las dos ultimas es deliberada: un duenno-operador solo en su
LAN que apaga la auth no tiene que recibir la misma alarma que alguien que se la
olvido y expuso el puerto. Repetir un WARNING que el operador ya decidio ignorar
entrena a ignorar los WARNING.

**El banner NO mira `LINCHPIN_ENV`.** El aviso que existia antes,
`security.production_warnings()`, arranca con `if ENV != "production": return []`,
y un cliente que corre `docker run` no setea `LINCHPIN_ENV` — asi que el unico
aviso que habia estaba apagado justo en el caso que importa. Medido antes de este
cambio, importando `webapp.app` con el entorno limpio: `API_KEY=''`,
`RATE_LIMIT=0` y **cero lineas de log sobre seguridad**.

`production_warnings()` queda igual: `LINCHPIN_REQUIRE_SECURE` se niega a bootear
cuando esa lista trae algo, y volverla no vacia en development habria roto el
arranque de cualquiera que la tuviera puesta. El banner es un canal nuevo, no un
reemplazo.

## 1. Production checklist

```bash
export LINCHPIN_ENV=production         # turns on the boot-time hardening check
export LINCHPIN_API_KEY=$(openssl rand -hex 24)   # require X-API-Key on POST /api/jobs
export LINCHPIN_RATE_LIMIT=60          # requests/window/IP (0 = off)
export LINCHPIN_RATE_WINDOW=60         # seconds
export LINCHPIN_CORS_ORIGINS=https://app.example.com   # omit for same-origin only
export LINCHPIN_LOG_JSON=1             # structured access logs to stdout
export LINCHPIN_MCP_ALLOWED_HOSTS=linchpin.fly.dev   # your real deploy host(s), comma-separated
# Optional: refuse to boot if the above leave the API unauthenticated/unthrottled
export LINCHPIN_REQUIRE_SECURE=1
```

`LINCHPIN_MCP_ALLOWED_HOSTS` matters specifically for `/mcp` (see
[MCP_SERVER.md](MCP_SERVER.md)): FastMCP's own DNS-rebinding protection only
auto-allows `127.0.0.1`/`localhost`/`::1` by Host header, so **every real
client request to a public deploy 421s without this set** — the per-client
`X-API-Key` gate still runs first and looks fine on its own, which is exactly
how this shipped broken once already (caught 2026-07-03, see
`linchpin-odoo-store-module` project notes). Bare hostname, no port, no
scheme — e.g. `linchpin.fly.dev`, not `https://linchpin.fly.dev:443`.

With `LINCHPIN_ENV=production`, the app logs a loud warning at startup for any
missing control (no API key, no rate limit). With `LINCHPIN_REQUIRE_SECURE=1` it
**refuses to boot** instead — so an unsecured public deploy fails fast, not silent.

## 2. Run it

```bash
pip install -e ".[core,web,mcp]"
uvicorn webapp.app:app --host 0.0.0.0 --port 8000 --workers 4
```

`[core]` is nosilo-core, and it needs the credential from section 0 — this is the
one command on this page that resolves the private dependency directly rather
than through a build system. It is not optional: `nosilo-core` is deliberately
out of `[project.dependencies]` (so `pip install -e .` works for someone with no
token), so it arrives only when an install line names this extra. Without it
`uvicorn` dies at import with `ModuleNotFoundError: No module named
'nosilo_core'` — `webapp/app.py` → `jobs/abc_xyz_job.py` hard-imports
`nosilo_core.guided`. Same failure mode as the missing `[mcp]` below, same
cause: a bespoke install command that drifted from what the app actually
imports. Reproduced in an isolated py3.12 venv on 2026-08-23, before and after.

`[mcp]` is required, not optional, despite the name: `webapp/app.py` unconditionally
mounts the MCP server (`/mcp`) at import time, so `webapp.mcp_server`'s `from
mcp.server.fastmcp import FastMCP` hard-fails without it — `pip install -e ".[web]"`
alone raises `ModuleNotFoundError: No module named 'mcp'`. Verified locally by
building a fresh venv with only `.[web]` and confirming the exact failure before
this line was corrected (2026-07-03).

The orchestrator and forecast cache are per-process, so scale with `--workers`
(or multiple replicas) behind the proxy. Job output is written under
`webapp/_jobs_output/` and swept after `JOBS_TTL_SECONDS` (1 h); mount it on a
disk with room for transient deliverables, or front it with object storage.

## 2a. Quick path: Fly.io (recommended for the first public deploy)

TLS, the public URL, and the reverse proxy are all handled by Fly's edge —
step 3 (nginx/Caddy) is not needed on this path. A `Dockerfile` + `fly.toml` at
the repo root already declare the build, start command, health check, and a
persistent Volume mount; this is the remaining setup, and it needs YOUR Fly.io
account (the CLI can't authenticate non-interactively):

```bash
fly auth login                 # opens a browser — do this yourself
fly apps create <your-app-name> --org personal   # app names are globally unique
fly volumes create linchpin_data --size 1 --region iad
fly secrets set LINCHPIN_API_KEY=$(openssl rand -hex 24) \
                LINCHPIN_APPROVAL_SECRET=$(openssl rand -hex 24) \
                LINCHPIN_RATE_LIMIT=60 \
                LINCHPIN_MCP_ALLOWED_HOSTS=<your-app-name>.fly.dev
fly deploy --app <your-app-name>
```

**Verified end-to-end against a live Fly account (2026-07-03)** — this
whole path was actually run, not just written down, and two real bugs only
showed up at that point (fixed in `fly.toml`, described here so a future
redeploy doesn't reintroduce them):

1. **2 workers OOM-killed a 512mb VM** within ~10-25s of boot, in a crash-restart
   loop (`fly logs` showed `Out of memory: Killed process ... (uvicorn)`).
   Each uvicorn worker loads its own copy of pandas/numpy/scipy plus the
   orchestrator and the L3 knowledge graph — nothing is shared across worker
   processes, so memory cost scales linearly with `--workers`. Fixed:
   `WEB_CONCURRENCY=1` in `fly.toml`'s `[env]` (confirmed stable indefinitely
   at 512mb with 1 worker). To run more than 1 worker, bump `[[vm]] memory` in
   `fly.toml` first (a real, small recurring cost beyond the free allowance) —
   don't just raise `WEB_CONCURRENCY` without also doing that.
2. **Mounting the Volume at `/app/data` shadowed the small static sample CSVs
   baked into the image at that same path** (`data/sample_demand_portfolio.csv`
   etc., read by the webapp at startup) — a Fly Volume mount, like any bind
   mount, hides whatever was already on disk at its destination. The app
   crashed with `FileNotFoundError`. Fixed: the Volume mounts at `/data`
   instead (a path with nothing else on it), and `LINCHPIN_MCP_KEYS_PATH`
   points there (`/data/mcp_keys.sqlite3`). If you add other paths that need
   to persist AND already ship data in the image, give them their own
   Volume-only path too — don't reuse a path the image also writes to.

Once live, the MCP server is reachable at `https://<your-app>.fly.dev/mcp` —
issue client keys with `fly ssh console -C "python examples/issue_mcp_key.py
issue '<client name>'" --app <your-app-name>` (runs inside the deployed
environment, against the mounted Volume at `/data`).

`--workers` maps to the app's `WEB_CONCURRENCY` env var (read by the
`Dockerfile`'s `CMD`, defaults to 2 if unset) — bump it on a paid plan with more
vCPU, or scale `min_machines_running`/add regions in `fly.toml`.

## 2b. Alternative: Railway

Kept for reference in case Railway becomes viable again later (e.g. a new
account/trial) — the same Fly-vs-Railway tradeoffs from `docs/DEPLOYMENT.md`'s
history still apply (both handle TLS/the public URL at their edge; Railway's
CLI setup is marginally simpler, Fly's free allowance doesn't expire the way a
time-limited trial does). A `railway.json` at the repo root still declares the
build/start commands and a health check:

```bash
railway login                 # opens a browser — do this yourself
railway init                  # or `railway link` if a project already exists
railway up                    # first deploy; re-run after any push, or connect
                               # the GitHub repo in the dashboard for auto-deploys
```

Then, in the Railway dashboard (no CLI equivalent for volumes yet):

1. **Add a Volume** mounted at `/app/data` — this is where
   `data/mcp_keys.sqlite3` (`src/mcp_keys.py`) and `data/writeback_ledger.sqlite3`
   (`src/writeback_store.py`) live. Without it, both reset on every redeploy
   (Railway's default filesystem is ephemeral) and every issued MCP key /
   writeback audit record is lost.
2. **Set environment variables** (Settings → Variables): everything in section 1
   above, plus generate a real `LINCHPIN_API_KEY` and `LINCHPIN_APPROVAL_SECRET`
   (`openssl rand -hex 24` for each — never reuse the examples from this doc).
3. **Generate a public domain** (Settings → Networking → "Generate Domain") —
   Railway issues a free `*.up.railway.app` subdomain with TLS already on; add a
   custom domain later if you want one.
4. Once live, the MCP server is reachable at `https://<your-app>.up.railway.app/mcp`
   — issue client keys with `python examples/issue_mcp_key.py issue "<client name>"`
   run **against the deployed instance's** key store (either run that script with
   `LINCHPIN_MCP_KEYS_PATH` pointed at a synced copy, or `railway run python
   examples/issue_mcp_key.py issue "..."` to execute it inside the deployed
   environment directly, against the mounted Volume).

`--workers` maps to Railway's `WEB_CONCURRENCY` env var (read by `railway.json`'s
start command, defaults to 2 if unset) — bump it on a paid plan with more vCPU.

## 3. Reverse proxy (TLS, HSTS, body limits) — non-Fly/Railway deploys only

The app speaks plain HTTP and caps uploads at **25 MB** (`MAX_UPLOAD_BYTES`).
Terminate TLS and mirror the body limit at the proxy so oversized requests are
rejected before they reach the app.

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name app.example.com;
    ssl_certificate     /etc/letsencrypt/live/app.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/app.example.com/privkey.pem;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    client_max_body_size 25m;            # match MAX_UPLOAD_BYTES
    proxy_read_timeout 120s;             # /api/jobs runs the engine + deliverables

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Request-ID $request_id;   # propagated into the access log
    }
}
```

### Caddy

```caddy
app.example.com {
    encode gzip
    request_body { max_size 25MB }
    reverse_proxy 127.0.0.1:8000 {
        header_up X-Request-ID {http.request.uuid}
    }
    header Strict-Transport-Security "max-age=31536000; includeSubDomains"
}
```

The app already sends `X-Frame-Options`, `X-Content-Type-Options`,
`Referrer-Policy`, `Permissions-Policy` and a path-aware CSP, so the proxy only
needs to add `HSTS` and terminate TLS.

## 4. `POST /api/jobs` under load

- **Per-request work is bounded.** Uploads are capped at 25 MB → `413`; numeric
  inputs are range-checked; the `(R,S)` simulation grid is bounded
  (`max_evaluations`) so a single job can't run away.
- **Throttle abusive clients** with `LINCHPIN_RATE_LIMIT` (the in-process limiter
  is per-worker — for a hard global limit, also cap at the proxy, e.g. nginx
  `limit_req`).
- **Each request is logged** on `linchpin.access` with an `X-Request-ID`,
  method, path, status and duration — set `LINCHPIN_LOG_JSON=1` to ship JSON lines
  to your log pipeline.

## 5. Knowledge-graph citations

The books graph is committed; the **code graph** (`graphify-out/`) is gitignored
and regenerated with `/graphify`. If it's absent or stale, `KnowledgeBase.warnings()`
surfaces it and the access/app logs flag it — code-side citations degrade to
theory-only rather than failing silently. Regenerate it as part of your build if
you rely on theory↔code citations in deliverables.

## 6. External cron: scheduled price-watch jobs

`POST /api/jobs/run-scheduled` (`webapp/app.py`) runs `PRICE_MONITOR_JOB` and
`PRICE_WATCH_JOB` (`jobs/scheduler.py`'s `JobRegistry`) synchronously, once, per
call — there is deliberately **no** in-process scheduler loop or background
thread (a persistent scraping thread inside the same 512mb/`WEB_CONCURRENCY=1`
process serving all web/API/MCP traffic was rejected as too risky; see the
endpoint's own docstring in `webapp/app.py`). Instead,
[`.github/workflows/price-watch-cron.yml`](../.github/workflows/price-watch-cron.yml)
is the external scheduler: a GitHub Actions workflow that POSTs to this
endpoint every 4 hours (`cron: "0 */4 * * *"`, matching
`jobs/price_monitor.py`'s `DEFAULT_CADENCE_HOURS`), plus `workflow_dispatch:`
for an on-demand manual run from the Actions tab.

**Setup required — a human must do this, it cannot be automated:**

1. Generate a key: the same value you set for `LINCHPIN_API_KEY` in production
   (see section 1 above; `openssl rand -hex 24` if you don't already have one).
2. In the GitHub repo, go to **Settings → Secrets and variables → Actions →
   New repository secret**, name it `KERN_JOBS_API_KEY`, and paste that value.
   This requires repo admin access — an agent cannot create repository secrets
   through the GitHub UI/API on your behalf.
3. Without step 2, every scheduled run fails fast with a clear
   `KERN_JOBS_API_KEY repository secret is not set` error in the Actions log
   (the workflow checks for this before making any request) — it does not
   silently no-op.

**Verifying it's working:**

- **Actions tab** (`github.com/<org>/<repo>/actions/workflows/price-watch-cron.yml`)
  — each run logs the HTTP status and the endpoint's full JSON response body.
  A run goes red (fails) on any non-2xx response (401 bad key, 409 an
  overlapping run was already in progress, 429 throttled below
  `LINCHPIN_SCHEDULED_MIN_INTERVAL_SECONDS`, 504 timed out) or on a 200 whose
  body carries `"status": "error"` (one of the two jobs failed without
  aborting the other — see the logged body for which one and why).
- **`GET /api/events`** on the deployed app after a scheduled run — a
  successful cycle promotes Control Tower events (e.g.
  `competitor_price_move`) that should show up in the recent-events list if
  the monitored competitor set actually moved.
- Trigger a manual run any time via **Actions → price-watch-cron → Run
  workflow** (`workflow_dispatch`) instead of waiting for the next scheduled
  tick, useful right after adding the secret to confirm the wiring end-to-end.

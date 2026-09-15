# Deployment & hardening

The web app (`webapp/`) is safe for local/internal use out of the box, but a
public deploy needs the access controls turned on and TLS terminated at a proxy.
This is the checklist. See [SECURITY.md](../SECURITY.md) for the threat model and
the controls already enforced in code.

## 0. There is no private dependency any more (2026-09-11)

`nosilo_core` — the control plane the app is built on — used to live in a private
repository, pinned by tag, resolved over `git+https`. **Every build path needed a
credential**, and that is gone: the code lives in this repo, in `nosilo_core/`.

```bash
docker build -t kern .          # no --secret, no --ssh, no agent
```

El comando de instalacion de la seccion 2 no cambia: sigue nombrando `[core]`,
que ahora esta vacio (ver el ultimo parrafo de esta seccion).

What it cost while it lasted, so nobody re-introduces it: a secret spread across
six build paths, one person who could rotate it, twenty-four days of CI running
blind on a PAT that was never set, ten days with no published image over a
premise that turned out to be false ("BuildKit does not forward SSH"), and any
partner wanting to run Kern having to ask someone for credentials. The package
had exactly one real consumer — this repo.

**What did NOT go away is the boundary.** `nosilo_core` stays domain-agnostic: it
knows nothing about SKUs, suppliers or safety stock, and `src/`/`packs/` depend on
it and never the reverse. Two tests enforce that direction
(`tests/test_core_boundary.py`, `tests/test_pack_boundary.py`), and they are the
reason the split can be undone without losing what it was for.
`tests/test_ci_prod_boot_extras.py` now fails if any install path asks for a
credential again.

The `[core]` extra is kept, empty, on purpose: `pip install -e ".[core,web,mcp]"`
is the command written in this page, in the Dockerfile and in several sessions'
notes. Deleting the extra breaks those commands with an error that does not say
what happened.

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

`[core]` esta VACIO desde el 2026-09-11 (ver seccion 0): `nosilo_core` vive en
este repo y no se instala. El nombre se conserva porque este comando esta escrito
en varios lugares, y un extra que desaparece los rompe con un error que no dice
que paso. Los que si importan son `[web]` y `[mcp]`. Same failure mode as the missing `[mcp]` below, same
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

## 7. Automated deployment (CI/CD)

`.github/workflows/fly-deploy.yml` deploys to production on every push to
`main` (flyctl's default rolling strategy, health-gated against `fly.toml`'s
existing `/api/health` check), followed by a post-deploy curl retry loop as
a second signal. If the release comes up unhealthy, the workflow fails red
â€” check `flyctl logs -a linchpin` and consider `flyctl releases rollback`.

**Requires two one-time manual steps â€” an agent cannot do either. Do step 2
before step 1, since step 1's secret lives inside the environment step 2
creates:**

1. **Create the `production` GitHub Environment** (repo Settings >
   Environments > New environment > name it exactly `production`) with:
   - **"Required reviewers" checked, add yourself.**
   - **"Deployment branches and tags" restricted to `main` only.** This
     matters because the workflow's `workflow_dispatch` trigger has no
     branch restriction of its own â€” without this environment-level
     restriction, someone with write access could manually dispatch the
     workflow against an arbitrary branch and an approver could unknowingly
     approve a non-`main` deploy.
2. **Add `FLY_API_TOKEN` as an Environment secret scoped to `production`**
   (inside that same environment's settings page: Environment secrets >
   Add secret â€” **not** repo-level Settings > Secrets and variables >
   Actions). Generate a deploy-scoped token with
   `flyctl tokens create deploy -a linchpin`, or from the Fly.io dashboard.
   **This must be an environment secret, not a repository secret** â€” a
   repository secret is readable by any workflow in this repo regardless of
   which (if any) environment it declares, which would let a future
   workflow (e.g. one the autonomous PR-merge loop adds) read
   `FLY_API_TOKEN` and deploy without ever triggering step 1's approval
   gate â€” silently defeating the entire point of this section. Until the
   environment secret is set, the workflow fails fast with a clear
   `::error::` message rather than silently no-op'ing.

**Why the approval gate matters here specifically:** this repo pairs
automated deployment with an autonomous PR-review-and-merge scheduled task
(see project memory â€” "kern-pr-review-loop") that can merge a PR to `main`
without a human clicking anything. The single Fly.io machine backing this
app is a memory-constrained 512MB shared-CPU VM with a **documented OOM
crash history** (see `fly.toml`'s own comment) and **no automated
rollback** â€” a bad autonomous merge reaching production unattended could
take the app down with nobody aware until someone happens to check. The
`production` environment's required-reviewer gate means PRs can still merge
automatically, but the actual deploy â€” the step that can break live traffic
â€” always waits for a human to click "approve" on the pending deployment in
GitHub's UI before it runs. This was an explicit design decision (not a
leftover TODO) after an adversarial review flagged the unattended-deploy
risk on this specific infrastructure; do not remove the `environment:` line
without addressing that risk another way (e.g. staging + auto-rollback)
first.

### Estado al 2026-08-25 — el push llega desactivado

El workflow se rescató de la rama `ci/fly-auto-deploy`, cuyo PR (#152) se
cerró sin mergear en agosto. Llega con el disparo por `push` **comentado**,
y por una razón concreta: el entorno `production` ya existe en el repo pero
está vacío — sin reviewers requeridos, sin restricción de rama y sin
`FLY_API_TOKEN`. Verificable con:

    gh api repos/esstipi-debug/kern/environments/production
    gh api repos/esstipi-debug/kern/environments/production/secrets

Con `push` activo en ese estado, cada merge a `main` fallaría en el primer
paso, y el gate de aprobación descrito arriba no estaría en efecto porque el
entorno no tiene reglas. Mientras tanto el deploy se dispara a mano desde
**Actions > Fly Deploy > Run workflow**.

Una vez hechos los dos pasos manuales, descomentar las dos líneas de `push`
en `.github/workflows/fly-deploy.yml` y el auto-deploy queda activo.

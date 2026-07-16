# Deployment & hardening

The web app (`webapp/`) is safe for local/internal use out of the box, but a
public deploy needs the access controls turned on and TLS terminated at a proxy.
This is the checklist. See [SECURITY.md](../SECURITY.md) for the threat model and
the controls already enforced in code.

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
pip install -e ".[web,mcp]"
uvicorn webapp.app:app --host 0.0.0.0 --port 8000 --workers 4
```

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

## 6. Automated deployment (CI/CD)

`.github/workflows/fly-deploy.yml` deploys to production on every push to
`main` (flyctl's default rolling strategy, health-gated against `fly.toml`'s
existing `/api/health` check), followed by a post-deploy curl retry loop as
a second signal. If the release comes up unhealthy, the workflow fails red
— check `flyctl logs -a linchpin` and consider `flyctl releases rollback`.

**Requires two one-time manual steps — an agent cannot do either:**

1. **Add the `FLY_API_TOKEN` repository secret** (Settings > Secrets and
   variables > Actions). Generate a deploy-scoped token with
   `flyctl tokens create deploy -a linchpin`, or from the Fly.io dashboard.
   Until it's set, the workflow fails fast with a clear `::error::` message
   rather than silently no-op'ing.
2. **Create the `production` GitHub Environment with a required reviewer**
   (repo Settings > Environments > New environment > name it exactly
   `production` > check "Required reviewers" > add yourself). The `deploy`
   job targets this environment; without the protection rule configured, it
   just runs unprotected the moment step 1's secret exists.

**Why the approval gate matters here specifically:** this repo pairs
automated deployment with an autonomous PR-review-and-merge scheduled task
(see project memory — "kern-pr-review-loop") that can merge a PR to `main`
without a human clicking anything. The single Fly.io machine backing this
app is a memory-constrained 512MB shared-CPU VM with a **documented OOM
crash history** (see `fly.toml`'s own comment) and **no automated
rollback** — a bad autonomous merge reaching production unattended could
take the app down with nobody aware until someone happens to check. The
`production` environment's required-reviewer gate means PRs can still merge
automatically, but the actual deploy — the step that can break live traffic
— always waits for a human to click "approve" on the pending deployment in
GitHub's UI before it runs. This was an explicit design decision (not a
leftover TODO) after an adversarial review flagged the unattended-deploy
risk on this specific infrastructure; do not remove the `environment:` line
without addressing that risk another way (e.g. staging + auto-rollback)
first.

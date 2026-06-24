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
# Optional: refuse to boot if the above leave the API unauthenticated/unthrottled
export LINCHPIN_REQUIRE_SECURE=1
```

With `LINCHPIN_ENV=production`, the app logs a loud warning at startup for any
missing control (no API key, no rate limit). With `LINCHPIN_REQUIRE_SECURE=1` it
**refuses to boot** instead — so an unsecured public deploy fails fast, not silent.

## 2. Run it

```bash
pip install -e ".[web]"
uvicorn webapp.app:app --host 0.0.0.0 --port 8000 --workers 4
```

The orchestrator and forecast cache are per-process, so scale with `--workers`
(or multiple replicas) behind the proxy. Job output is written under
`webapp/_jobs_output/` and swept after `JOBS_TTL_SECONDS` (1 h); mount it on a
disk with room for transient deliverables, or front it with object storage.

## 3. Reverse proxy (TLS, HSTS, body limits)

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

# Security

Linchpin is an analytical engine plus a thin HTTP layer over it. This document
states the threat model, the controls already enforced in code, the known
limitations, and how to report a vulnerability. Line references point at
[`webapp/app.py`](webapp/app.py) so every claim here is verifiable.

## Threat model

The HTTP surface (`webapp/app.py`) accepts three kinds of untrusted input:

1. **Query parameters** on `GET /api/portfolio` (numbers + a JSON override string).
2. **Form fields** on `POST /api/jobs` (`brief`, `client`, `job_type`, `params` JSON).
3. **A multipart file upload** on `POST /api/jobs` (the client's demand CSV/Excel).

The engine itself (`src/`) is pure computation over numpy/pandas — no shell, no
`eval`/`exec`, no SQL string-building, no network calls. The free-text `brief` is
*parsed* (rules + an optional LLM), never executed.

## Controls enforced in code

| Risk | Control | Where |
|------|---------|-------|
| Out-of-range / adversarial numeric params | Bounded `Query(...)` on every param (`service_level∈(0,1)`, `holding_rate∈(0,2]`, `budget≥0`, …) | [`app.py:264`](webapp/app.py#L264) |
| `Infinity`/`NaN` injected via JSON | Incoming JSON parsed with `parse_constant=_reject_nonfinite`; `lead_overrides` must be finite numbers in `(0, 52]` or `400` | [`app.py:275`](webapp/app.py#L275) |
| Invalid JSON emitted to clients | `SafeJSONResponse` serializes with `allow_nan=False` — non-finite floats raise instead of producing invalid JSON | [`app.py:59`](webapp/app.py#L59) |
| Malformed `params` body | Must parse to a JSON **object** or `400` | [`app.py:333`](webapp/app.py#L333) |
| Injection via the `client` label (lands in report headings) | Whitelist `re.sub(r"[^\w\s.,\-]", "", client)[:100]` | [`app.py:340`](webapp/app.py#L340) |
| **Path traversal / absolute-path write** in upload filename | Filename reduced to `os.path.basename`, `.`/`..` rejected, resolved parent pinned to the per-job dir | [`app.py:351`](webapp/app.py#L351) |
| **Upload size exhaustion** | Read capped at `MAX_UPLOAD_BYTES` (25 MB); over-limit → `413` | [`app.py:44`](webapp/app.py#L44), [`app.py:359`](webapp/app.py#L359) |
| Per-job output leaking across requests | Each job writes to an isolated `tempfile.mkdtemp` dir | [`app.py:346`](webapp/app.py#L346) |
| Unbounded disk growth | `_prune_old_jobs` sweeps job dirs older than `JOBS_TTL_SECONDS` (1 h) on each request | [`app.py:305`](webapp/app.py#L305) |
| Arbitrary file download | Download URLs are accepted only if `relative_to(JOBS_OUTPUT_DIR)`; anything outside is dropped | [`app.py:372`](webapp/app.py#L372) |
| Clickjacking · MIME-sniffing · referrer leak | Always-on headers — `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy` — plus a **path-aware CSP** (strict on the dashboard; relaxed only for the `/console` React/Babel prototype) | [`security.py`](webapp/security.py) |
| Brute force / abuse of `POST /api/jobs` | Opt-in sliding-window **rate limit** per client IP → `429` + `Retry-After` | [`security.py`](webapp/security.py) |
| Unauthorized access | Opt-in **API-key gate** (constant-time compare) → `401` | [`security.py`](webapp/security.py) |

These paths are regression-tested in [`tests/test_webapp.py`](tests/test_webapp.py)
(`test_input_validation`, `test_lead_overrides_rejects_nonfinite_and_out_of_range`,
`test_jobs_upload_filename_traversal_is_contained`, `test_jobs_upload_too_large_rejected`)
and [`tests/test_webapp_security.py`](tests/test_webapp_security.py) (headers, path-aware
CSP, rate-limit and API-key behaviour).

## Secret management

- No secrets are committed. Application code reads `ANTHROPIC_API_KEY` (optional —
  Claude-assisted parsing/narrative) and `MOONSHOT_API_KEY` (optional — only the
  external `graphify` build). The web app's hardening knobs (`LINCHPIN_API_KEY`,
  `LINCHPIN_RATE_LIMIT`, `LINCHPIN_RATE_WINDOW`, `LINCHPIN_CORS_ORIGINS`) are also
  env-driven; of those only `LINCHPIN_API_KEY` is a secret. See [`.env.example`](.env.example).
- `.env` and `.env.local` are git-ignored. The engine, web app, and tests all run
  with **zero** secrets configured; missing keys degrade gracefully to the
  rules-based path, they do not crash.

## Hardening for a public deploy

The app is safe for local/internal analyst use **out of the box**: the headers and
CSP are always on and the input/upload controls above are unconditional. The access
controls ship **built-in but opt-in**, so dev use is unchanged — set these
environment variables before exposing the app publicly:

| Variable | Effect | Default |
|----------|--------|---------|
| `LINCHPIN_API_KEY` | Require a matching `X-API-Key` header on `POST /api/jobs` | unset → open |
| `LINCHPIN_RATE_LIMIT` | Max requests per window per client IP (`0` disables) | `0` → off |
| `LINCHPIN_RATE_WINDOW` | Rate-limit window, seconds | `60` |
| `LINCHPIN_CORS_ORIGINS` | Comma-separated CORS allowlist | unset → same-origin only |
| `LINCHPIN_ENV` | `production` enables the boot-time hardening check | `development` |
| `LINCHPIN_REQUIRE_SECURE` | Refuse to boot if production is missing API key / rate limit | unset → warn only |
| `LINCHPIN_LOG_JSON` / `LINCHPIN_LOG_LEVEL` | Structured (JSON) access logs / level | plain / `INFO` |

**Fail-loud, not fail-silent.** With `LINCHPIN_ENV=production` the app logs a loud
warning at startup for any missing control; with `LINCHPIN_REQUIRE_SECURE=1` it
refuses to boot — so an unsecured public deploy can't slip through unnoticed. Every
request is logged on the `linchpin.access` logger with an `X-Request-ID`, status and
duration for centralized observability.

Still terminate **TLS and set `HSTS`** at your reverse proxy (nginx/Caddy) — the app
speaks plain HTTP and does not manage certificates. The `/console` prototype relaxes
its CSP to load React/Babel from unpkg; if you expose it publicly, prefer
self-hosting those assets. See **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** for proxy
configs (TLS, HSTS, `client_max_body_size`), worker scaling and load notes.

## Reporting a vulnerability

Please open a private report via GitHub Security Advisories on
[esstipi-debug/linchpin](https://github.com/esstipi-debug/linchpin/security/advisories/new),
or email the maintainer. Do not file public issues for security reports. We aim to
acknowledge within 72 hours.

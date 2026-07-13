# Security

Kern is an analytical engine plus a thin HTTP layer over it. This document
states the threat model, the controls already enforced in code, the known
limitations, and how to report a vulnerability. Line references point at
[`webapp/app.py`](webapp/app.py) so every claim here is verifiable.

## Threat model

The HTTP surface (`webapp/app.py`) accepts four kinds of untrusted input:

1. **Query parameters** on `GET /api/portfolio` (numbers + a JSON override string).
2. **Form fields** on `POST /api/jobs` (`brief`, `client`, `job_type`, `params` JSON).
3. **A multipart file upload** on `POST /api/jobs` (the client's demand CSV/Excel).
4. **The demo funnel** on `POST /api/demo-scan` (an email form field + a stock CSV
   upload). The upload reuses the exact controls of (3): 25 MB cap → `413`,
   basename-only filename pinned to an isolated per-request tempdir under the
   TTL-purged jobs area. The email is regex-validated and additionally reduced to
   a traversal-proof, **collision-free** single path segment
   (`webapp/demo_scan.py::safe_lead_dirname` — a sanitized prefix plus a short
   hash of the full normalized email, so two distinct addresses can never map to
   the same lead directory and silently overwrite each other's report) before
   any lead artifact is written; the raw upload is never copied into the lead's
   folder. CSV-supplied text (e.g. `product_id`) is collapsed to a conservative
   charset (`webapp/demo_scan.py::_md_safe`) before landing in the persisted
   `.md` artifacts, since those are read by the operator through a
   markdown/HTML-capable tool and are otherwise a stored-injection sink this
   repo's existing CSV/Excel formula-injection guard (`src/sanitize.py`) does
   not cover. This endpoint is unauthenticated by design (same as `/api/leads`
   — it *is* the lead magnet) and **relies on `LINCHPIN_RATE_LIMIT` being set in
   production**, same as every other public endpoint below; `LEAD_REPORTS_DIR`
   additionally self-caps at `MAX_LEAD_DIRS` (`app.py::_prune_excess_lead_dirs`,
   oldest-evicted) as defense in depth against a scripted fresh-email-per-request
   disk-exhaustion attempt while rate limiting is off by default.

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
| Lead PII (email) leaking via aggregate reporting | `GET /api/metrics` (E8, internal tooling) never returns a raw email; its "source"/"status"/"dataset" buckets (caller-controlled — a scripted `POST /api/leads`, or the filename a lead uploads on `/api/demo-scan`) are sanitized and length-capped before use as a bucket label (`app.py::_metrics_label`), and the number of distinct buckets is capped, with the overflow folded into `"other"` (`_metrics_bump`) — an attacker cannot inflate the response or smuggle PII-shaped text (e.g. an email-named upload) into it verbatim. Gated behind the same API-key control as `POST /api/jobs` | [`app.py`](webapp/app.py) (`api_metrics`, `_metrics_label`, `_metrics_bump`) |

These paths are regression-tested in [`tests/test_webapp.py`](tests/test_webapp.py)
(`test_input_validation`, `test_lead_overrides_rejects_nonfinite_and_out_of_range`,
`test_jobs_upload_filename_traversal_is_contained`, `test_jobs_upload_too_large_rejected`),
[`tests/test_webapp_security.py`](tests/test_webapp_security.py) (headers, path-aware
CSP, rate-limit and API-key behaviour), and
[`tests/test_webapp_metrics.py`](tests/test_webapp_metrics.py) (`GET /api/metrics`'s
aggregation correctness, malformed-line tolerance, label sanitization/bucket cap,
and its own API-key/rate-limit gating).

## Secret management

- No secrets are committed. Application code reads `ANTHROPIC_API_KEY` (optional —
  Claude-assisted parsing/narrative) and `MOONSHOT_API_KEY` (optional — only the
  external `graphify` build). The web app's hardening knobs (`LINCHPIN_API_KEY`,
  `LINCHPIN_RATE_LIMIT`, `LINCHPIN_RATE_WINDOW`, `LINCHPIN_CORS_ORIGINS`,
  `LINCHPIN_APPROVAL_SECRET`) are also env-driven; of those `LINCHPIN_API_KEY` and
  `LINCHPIN_APPROVAL_SECRET` are secrets. See [`.env.example`](.env.example).
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
| `LINCHPIN_API_KEY` | Require a matching `X-API-Key` header on `POST /api/jobs` and `GET /api/metrics` | unset → open |
| `LINCHPIN_RATE_LIMIT` | Max requests per window per client IP (`0` disables) | `0` → off |
| `LINCHPIN_RATE_WINDOW` | Rate-limit window, seconds | `60` |
| `LINCHPIN_CORS_ORIGINS` | Comma-separated CORS allowlist | unset → same-origin only |
| `LINCHPIN_APPROVAL_SECRET` | Signs writeback `Approval`s (`src/writeback.py`) so one can't be forged by constructing it directly | unset → unsigned |
| `LINCHPIN_ENV` | `production` enables the boot-time hardening check | `development` |
| `LINCHPIN_REQUIRE_SECURE` | Refuse to boot if production is missing API key / rate limit / approval secret | unset → warn only |
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
[esstipi-debug/kern](https://github.com/esstipi-debug/kern/security/advisories/new),
or email the maintainer. Do not file public issues for security reports. We aim to
acknowledge within 72 hours.

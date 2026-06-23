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
| Out-of-range / adversarial numeric params | Bounded `Query(...)` on every param (`service_level∈(0,1)`, `holding_rate∈(0,2]`, `budget≥0`, …) | [`app.py:252`](webapp/app.py#L252) |
| `Infinity`/`NaN` injected via JSON | Incoming JSON parsed with `parse_constant=_reject_nonfinite`; `lead_overrides` must be finite numbers in `(0, 52]` or `400` | [`app.py:260`](webapp/app.py#L260) |
| Invalid JSON emitted to clients | `SafeJSONResponse` serializes with `allow_nan=False` — non-finite floats raise instead of producing invalid JSON | [`app.py:57`](webapp/app.py#L57) |
| Malformed `params` body | Must parse to a JSON **object** or `400` | [`app.py:320`](webapp/app.py#L320) |
| Injection via the `client` label (lands in report headings) | Whitelist `re.sub(r"[^\w\s.,\-]", "", client)[:100]` | [`app.py:328`](webapp/app.py#L328) |
| **Path traversal / absolute-path write** in upload filename | Filename reduced to `os.path.basename`, `.`/`..` rejected, resolved parent pinned to the per-job dir | [`app.py:336`](webapp/app.py#L336) |
| **Upload size exhaustion** | Read capped at `MAX_UPLOAD_BYTES` (25 MB); over-limit → `413` | [`app.py:42`](webapp/app.py#L42), [`app.py:346`](webapp/app.py#L346) |
| Per-job output leaking across requests | Each job writes to an isolated `tempfile.mkdtemp` dir | [`app.py:334`](webapp/app.py#L334) |
| Unbounded disk growth | `_prune_old_jobs` sweeps job dirs older than `JOBS_TTL_SECONDS` (1 h) on each request | [`app.py:293`](webapp/app.py#L293) |
| Arbitrary file download | Download URLs are accepted only if `relative_to(JOBS_OUTPUT_DIR)`; anything outside is dropped | [`app.py:357`](webapp/app.py#L357) |

These paths are regression-tested: see `test_input_validation`,
`test_lead_overrides_rejects_nonfinite_and_out_of_range`,
`test_jobs_upload_filename_traversal_is_contained`, and
`test_jobs_upload_too_large_rejected` in [`tests/test_webapp.py`](tests/test_webapp.py).

## Secret management

- No secrets are committed. The only environment variables read by application
  code are `ANTHROPIC_API_KEY` (optional — enables Claude-assisted parsing and
  narrative) and `MOONSHOT_API_KEY` (optional — only for the external `graphify`
  code-graph build). See [`.env.example`](.env.example).
- `.env` and `.env.local` are git-ignored. The engine, web app, and tests all run
  with **zero** secrets configured; missing keys degrade gracefully to the
  rules-based path, they do not crash.

## Known limitations — read before a public deploy

The web app is built for **trusted / internal use or to run behind a gateway**.
It deliberately ships *without*:

- **Authentication / authorization** — add an API key or your IdP at the proxy.
- **Rate limiting** — front it with a reverse proxy (nginx/Caddy) or add
  `slowapi` before exposing `POST /api/jobs` publicly.
- **TLS and security headers** (`HSTS`, `X-Content-Type-Options`, CSP) — terminate
  TLS and set headers at the proxy.
- **A CORS allowlist** — restrict origins if you serve the API cross-site.

None of these are required for the intended local/internal analyst workflow; they
are the checklist for hardening Linchpin into a public SaaS.

## Reporting a vulnerability

Please open a private report via GitHub Security Advisories on
[esstipi-debug/linchpin](https://github.com/esstipi-debug/linchpin/security/advisories/new),
or email the maintainer. Do not file public issues for security reports. We aim to
acknowledge within 72 hours.

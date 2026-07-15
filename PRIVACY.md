# Privacy Policy

> ## DRAFT — DO NOT PUBLISH WITHOUT LEGAL REVIEW
>
> This document was written to describe, accurately and verifiably, what the
> Kern codebase actually does with data as of 2026-07-15 — it is not legal
> advice and not a finished, lawyer-approved policy. Every factual claim below
> was checked against the source files cited inline (`src/mcp_keys.py`,
> `src/client_profile.py`, `src/writeback_store.py`, `src/connectors/odoo.py`,
> `src/connectors/excel.py`, `scm_agent/llm.py`, `scm_agent/intent.py`,
> `scm_agent/tools.py`, `webapp/app.py`, `webapp/demo_scan.py`,
> `webapp/observability.py`, `webapp/offers.py`,
> `src/pricing_intel/acquire/watcher.py`, `fly.toml`,
> `odoo_addon/linchpin_dry_run/static/description/index.html`,
> [`SECURITY.md`](SECURITY.md), and
> [`documentation/legal/dpa-lite.md`](documentation/legal/dpa-lite.md)) — if
> the code changes, this document must be updated in the same PR, or it stops
> being accurate. Clauses marked `[REVIEW WITH A LAWYER: ...]` carry real legal
> risk (data-subject rights, breach notification, liability) and need a
> lawyer's eyes, familiar with the operator's actual jurisdiction, before this
> is published or relied on by a client. See also
> [`documentation/legal/dpa-lite.md`](documentation/legal/dpa-lite.md), the
> internal client-facing data-processing annex this document overlaps with but
> does not replace.

---

## Table of contents

1. [Overview](#1-overview)
2. [What data we collect](#2-what-data-we-collect)
3. [How we use it](#3-how-we-use-it)
4. [Third-party sharing](#4-third-party-sharing)
5. [Data retention and deletion](#5-data-retention-and-deletion)
6. [Security measures](#6-security-measures)
7. [MCP-specific: what happens when you connect Kern to Claude](#7-mcp-specific-what-happens-when-you-connect-kern-to-claude)
8. [Your rights](#8-your-rights)
9. [Children's privacy](#9-childrens-privacy)
10. [Changes to this policy](#10-changes-to-this-policy)
11. [Contact](#11-contact)

---

## 1 · Overview

This policy covers three things operated by
`[OPERATOR: legal entity name / DBA]` (referred to below as "Kern," "we," or
"the operator"):

- **Kern's public website** and marketing pages.
- **The hosted webapp** (the FastAPI dashboard and agent console described in
  [`SECURITY.md`](SECURITY.md), including `POST /api/jobs`, the demo funnel at
  `POST /api/demo-scan`/`/api/leads`, and any deployment reachable at a
  `*.fly.dev` address or a self-hosted equivalent).
- **The hosted MCP (Model Context Protocol) server**, which lets an MCP client
  such as Claude call Kern's supply-chain analysis tools directly.

Kern is a **B2B analytical engine**: a plain-language brief becomes a
QA-gated deliverable (Excel + report + chart) computed from business data a
client supplies. It is not a consumer social app and is not designed to
process personal data as part of its normal operation — see Section 2 for
the specific exception (the demo funnel) and its limits.

If you run your own self-hosted instance of Kern, this policy describes the
default, unmodified code's behavior. Your own deployment's actual data
handling depends on how you configure and operate it.

## 2 · What data we collect

Kern collects different data depending on how you interact with it.

**a. MCP API key holders (paying clients).** When we issue you an MCP API
key, we store a `client_name` string (chosen by the operator at issuance) and
issuance/usage timestamps (`src/mcp_keys.py`). We do not store your email,
postal address, or payment details as part of this record — see Section 4 for
how billing is actually handled.

**b. Business/operational data you supply for an analysis.** If you engage us
for a consulting package or use the webapp/MCP tools, you provide business
data such as: SKU and product identifiers, sales history, cost and price
data, and business parameters (target service level, holding cost rate, lead
times) (`src/client_profile.py`, stored per-client under
`clients/<slug>/profile.json`). If your engagement includes writeback to your
own Odoo instance, the same category of fields (plus supplier lead time and
your company name) flows through the Odoo connector
(`src/connectors/odoo.py`) — and **this writeback path does read and use your
on-hand stock quantities.** `OdooConnector.inventory_levels()` reads on-hand
quantities from Odoo's `stock.quant` model, and the writeback method itself,
`OdooConnector.stage_restock()`, calls `inventory_levels()` directly to
compute the reorder-point ("min qty") target it stages back into Odoo
(on-hand quantity plus the recommended restock amount). Do not confuse this
connector with the separate, genuinely read-only integration described next.

**Separately: the `linchpin_dry_run` Odoo Store add-on (a different
integration, read-only).** `odoo_addon/linchpin_dry_run/` is a distinct, free
add-on that runs *inside a client's own Odoo instance* — it is not the
writeback connector described above and shares none of its code path. Per its
own listing
(`odoo_addon/linchpin_dry_run/static/description/index.html`), it sends SKU,
order date, quantity sold, unit cost, primary supplier lead time, and the
client's company name to Kern's cloud service to produce a display-only
recommendation. It is *this* add-on — not the Odoo connector above — for
which **no stock or on-hand quantities are ever read or sent**, and it never
writes anything back to Odoo (it does not touch stock levels, reorder points,
or purchase orders). The two integrations have different data-handling
guarantees and must not be conflated with each other.

This data is meant to be aggregate, product/transaction-level business data —
**not personal data about identifiable individuals** (your employees, your
own end customers, etc.). If a file you supply happens to contain personal
data (for example, an end-customer's name in a sales-line export), that is
not something Kern's code detects or filters. Kern's engine has no PII
detection or redaction logic; it reads specific columns by name (SKU, date,
quantity, cost, and similar), which reduces — but does not eliminate — the
chance of ingesting a stray personal-data column. Treat this as "not designed
to process personal data by convention," not as a technical guarantee.

**c. Website visitors who use the demo funnel.** If you submit a lead form
(`POST /api/leads`) or run the demo scan (`POST /api/demo-scan`), we collect
your **email address**, and for the demo scan, a stock CSV you upload. This
is stored as plaintext in `webapp/_leads/leads.jsonl` and a per-lead report
directory (`webapp/demo_scan.py`, `webapp/app.py`). **This is the one place
in Kern's own code that stores personal data tied to a named individual.**

**d. Everyone who sends an HTTP request to the webapp or MCP server.** We log
source IP address, HTTP method, path, response status, and request duration
for every request, for operational monitoring (`webapp/observability.py`,
logger `linchpin.access`).

**e. MCP tool-call content.** See Section 7 — this is handled differently
from the categories above and is covered in its own section because it's the
question an MCP-directory reviewer or an MCP client user is most likely to
have.

We do **not** use cookies, browser sessions, or client-side analytics/
tracking scripts anywhere in the webapp — none were found in the code.

## 3 · How we use it

We use the data described in Section 2 **only** to: authenticate and
rate-limit API requests (MCP key holders); produce the deliverables you
contracted for (business data); respond to and follow up on demo/lead
inquiries (email addresses from the demo funnel); and monitor, debug, and
secure the service (request logs). We do not sell your data, and we do not
use your business data to train any third-party model — see Section 4 for
the narrow, specific cases where text is sent to a third-party LLM provider.

## 4 · Third-party sharing

We share data with the following third parties, only under the specific
conditions listed — nothing is shared by default beyond what's described
here.

| Third party | What they receive | When |
|---|---|---|
| **Anthropic (Claude API)** | Three distinct uses, none of which ever include your raw CSV rows or business data tables: **(1)** an already-computed result summary (narrative text + tool title), sent for wording polish/translation (`scm_agent/llm.py::narrative_rewrite`); **(2)** your free-text brief, when keyword-based routing can't determine a single clear tool to run (`scm_agent/intent.py::_llm_classify`); **(3)** your free-text brief, for the `leadership_chain` tool's CHAIN-model scoring (`scm_agent/tools.py`). | Only if the operator has configured `ANTHROPIC_API_KEY`. Without it, none of these three paths activate — deliverables are still produced, using deterministic template text, with zero external calls. |
| **Stripe** | Nothing from Kern's own code — Kern stores a static Stripe Payment Link URL (`webapp/offers.py::resolve_pagar_cta`) and redirects you to Stripe's own hosted checkout page. Card numbers, billing details, and payment data never pass through Kern's servers or code. | Only when you click a "pay" call-to-action that resolves to a configured Payment Link. |
| **Fly.io** | Hosts the application and, for at least the MCP key store, a volume-mounted file (`fly.toml` sets `LINCHPIN_MCP_KEYS_PATH=/data/mcp_keys.sqlite3` onto the mounted volume) — see Section 5 for why the writeback audit ledger and lead-report data are *not* confirmed to be on that same mounted volume. Fly's platform also receives whatever the app writes to its standard output — including the IP-address access logs described in Section 2(d) — through its log pipeline. | Always, for any deployment hosted on `*.fly.dev` or other Fly.io infrastructure. Does not apply to a self-hosted installation. |

No other LLM providers (OpenAI, Gemini, etc.) receive data from the running
application. Gemini/Kimi are referenced only as an offline development
dependency for rebuilding an internal reference knowledge graph, unrelated
to any client or user data path.

**changedetection.io** is not a recipient of your data — it's a separate,
self-hosted tool an operator may run to *send* competitor price observations
*into* Kern via `POST /api/watch` (`src/pricing_intel/acquire/watcher.py`).
No client data flows outward to it.

`[REVIEW WITH A LAWYER: confirm Anthropic's, Stripe's, and Fly.io's current
data-processing terms at the time this policy is published, and whether any
additional subprocessor needs to be listed depending on how a specific
instance of Kern is deployed.]`

## 5 · Data retention and deletion

Retention depends on which part of the system created the data — there is no
single retention rule:

- **Webapp job deliverables** (`POST /api/jobs`): automatically deleted one
  hour after generation (fixed 3600-second TTL, `webapp/app.py`).
- **CLI-run deliverables** (e.g. `examples/run_agent.py`, the default
  `deliverables/` folder): **no automatic deletion** — these persist on disk
  until the operator manually removes them.
- **Demo-funnel lead reports** (contain your email address — this is personal
  data): **deliberately excluded from any age-based deletion.** The only
  bound is a hard cap on total directory count (`MAX_LEAD_DIRS`), which
  evicts the oldest entries only once that cap is exceeded — this is not a
  time-based retention guarantee. **Whether this data actually survives a
  redeploy or restart on the live Fly.io deployment is not confirmed by this
  codebase alone:** the per-lead report directory (`LEAD_REPORTS_DIR`,
  `webapp/app.py`) defaults to a path under the app's own, non-mounted
  filesystem unless the operator separately sets `LINCHPIN_LEAD_REPORTS_DIR`
  to a `/data`-mounted path — `fly.toml` does not set that env var, and
  whether it has been set out-of-band as a Fly secret is not something the
  code can confirm (the operator launch checklist,
  `documentation/operator/09_checklist_lanzamiento.md`, still lists this as
  an unchecked to-do). Separately, the lead summary file
  (`webapp/_leads/leads.jsonl`) always writes to a fixed, repo-relative path
  with no env-var override at all, so on Fly.io it does not survive a
  redeploy regardless of configuration.
- **MCP API keys**: kept indefinitely. A revoked key is marked inactive
  rather than deleted, to preserve an audit trail; no automatic deletion path
  exists in the code. On the live Fly.io deployment, this store's path is
  set by `LINCHPIN_MCP_KEYS_PATH=/data/mcp_keys.sqlite3` in `fly.toml`, onto
  the volume-mounted `/data` directory — so this specific store is confirmed
  to persist across redeploys/restarts.
- **The writeback audit ledger**: also intended to be kept indefinitely, for
  the same audit-trail rationale as MCP API keys. However, **unlike the MCP
  key store, `fly.toml` sets no equivalent override for it**:
  `src/writeback_store.py`'s `DEFAULT_PATH` stays the relative, non-`/data`
  path `data/writeback_ledger.sqlite3`, and no code path we found overrides
  it for the production deployment. Whether the live Fly.io deployment's
  writeback ledger actually persists across a redeploy or restart is
  therefore **not confirmed by this codebase alone** — do not treat "kept
  indefinitely" as a guaranteed fact for this specific store on that
  deployment.
- **Client business-parameter profiles** (`clients/<slug>/profile.json`):
  kept indefinitely; no expiry logic exists.

`[REVIEW WITH A LAWYER: define a concrete retention period for each category
above and commit to a deletion-on-request process. Today, deletion outside
the webapp's 1-hour job TTL is a manual operator capability, not a
contractual commitment with a defined turnaround time — and the lead-report
case in particular retains personal data (an email address) with no
time-based automatic deletion at all.]`

## 6 · Security measures

Kern's technical security controls (input validation, upload handling, rate
limiting, API-key gating, security headers, secret management, and known
limitations such as TLS termination happening at the reverse proxy rather
than in the application) are documented in full, with file-and-line
references, in [`SECURITY.md`](SECURITY.md) — we cross-reference it here
rather than duplicating it, so the two documents can't drift out of sync.
Notably, **no encryption-at-rest is implemented or verified anywhere in the
code**; do not assume it without confirming your specific deployment's
storage layer independently.

To report a security vulnerability, follow the process in
[`SECURITY.md`](SECURITY.md#reporting-a-vulnerability).

## 7 · MCP-specific: what happens when you connect Kern to Claude

This section is about the hosted **MCP server** specifically — what an MCP
client (such as Claude, when you've connected Kern as a tool) sends to Kern,
and what Kern's server does or doesn't keep from that interaction.

- **Identity on the MCP surface.** Requests through the MCP server are
  authenticated by an API key, which resolves to a `client_name` label
  (`request.state.mcp_client_name` in `webapp/mcp_auth.py`). That label is
  used for rate-limit bucketing and access logging (Section 2d). Beyond that
  label, Kern does not track "which end user" within an organization is
  driving a given Claude session — the key identifies the key holder, not an
  individual Claude user.
- **Tool-call content is not logged.** We found no logging or `print` calls
  in `webapp/mcp_auth.py` or `webapp/mcp_server.py` that would record the
  arguments of an individual tool call, or any conversational/prompt content
  from your Claude session. No code path we found persists MCP tool-call
  arguments.
- **Business data flows the same way as elsewhere.** If a tool call involves
  business data (e.g. a writeback tool touching your Odoo instance), that
  data is handled per Section 2(b) and Section 4 — the MCP transport layer
  itself does not add any additional third-party sharing beyond what's
  described there.
- **The LLM-sharing cases in Section 4 apply here too.** If your MCP session
  triggers a tool that needs free-text intent classification or the
  `leadership_chain` tool, and the operator has `ANTHROPIC_API_KEY`
  configured, the same narrow text-only sharing with Anthropic described in
  Section 4 applies — never your raw business data rows.
- **What we could not fully verify from code alone:** whether Fly.io's own
  platform-level log retention for the process's standard-output stream
  (which includes source IPs, per Section 2d) is itself time-bounded is a
  property of Fly.io's infrastructure, not of this codebase, and is not
  something we can state a specific retention number for here.

## 8 · Your rights

`[REVIEW WITH A LAWYER: the specific data-subject rights that apply (e.g.
GDPR access/erasure/portability rights, CCPA rights) depend on the operator's
and the data subject's jurisdiction, which is not something determinable from
the codebase. The draft language below reflects the operational capability
that exists today — a manual, best-effort process — and should not be
represented as a specific regulatory commitment (a response SLA, a
formal legal basis for processing, an appointed data protection officer,
etc.) until a lawyer has reviewed which regime(s) actually apply.]`

Subject to the above, you may ask us at any time for: (a) a copy of the data
we hold about you; (b) deletion of that data, subject to any legal retention
obligation that may apply. We will respond to such requests on a best-effort
basis; a specific committed turnaround time is not yet defined — see the
`[REVIEW WITH A LAWYER]` note above.

## 9 · Children's privacy

Kern is a business-to-business analytical tool intended for use by
supply-chain, operations, and business professionals. It is not directed at,
and we do not knowingly collect data from, children.

## 10 · Changes to this policy

We may update this policy as Kern's data practices change. Material changes
will be reflected here with an updated effective date. Because this document
is meant to track the actual behavior of the code (per the warning banner
above), a change to how Kern collects, stores, or shares data should update
this document in the same change, not as an afterthought.

**Effective date:** `[OPERATOR: effective date]`

## 11 · Contact

For privacy questions, data requests, or concerns about this policy, contact:
`[OPERATOR: contact email for privacy inquiries]` (suggested default:
esstipi@gmail.com — confirm this is the intended contact address before
publishing, do not treat it as settled).

This policy is governed by `[OPERATOR: jurisdiction / governing law]`.
`[REVIEW WITH A LAWYER: the application is currently hosted on Fly.io's `iad`
(US, Virginia) region per `fly.toml`, but the operator's own legal
jurisdiction — where the operating entity is incorporated or based — is a
business fact, not something inferable from infrastructure configuration.
Do not assume hosting region implies governing law.]`

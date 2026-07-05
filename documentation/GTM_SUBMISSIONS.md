# GTM submissions — ready-to-execute packet

Everything below is **prepared and verified**; each submission needs only your
account login (they cannot be automated without impersonating you). Work top to
bottom — total effort is under an hour. Verified live before writing this:
`https://linchpin.fly.dev` answers 200 and `server.json` conforms to the official
MCP registry schema (2025-12-11).

---

## 1. Official MCP Registry (registry.modelcontextprotocol.io)

The canonical registry all aggregators increasingly index. `server.json` at the
repo root is already schema-valid.

1. Install the publisher CLI (one time):
   `go install github.com/modelcontextprotocol/registry/cmd/mcp-publisher@latest`
   (or download a release binary from the modelcontextprotocol/registry repo).
2. From the repo root: `mcp-publisher login github` (device-code flow with your
   `esstipi-debug` account — the `io.github.esstipi-debug/*` namespace is proven
   by that login).
3. `mcp-publisher publish` (reads `./server.json`).
4. Verify: `https://registry.modelcontextprotocol.io/v0/servers?search=linchpin`.

## 2. Glama (glama.ai)

Glama auto-indexes public GitHub MCP servers and lets authors claim them.

1. Log in at `https://glama.ai` with GitHub.
2. `https://glama.ai/mcp/servers` -> search "linchpin". If already indexed, use
   **Claim** on the listing; if not, use the add/submit flow and give the repo URL
   `https://github.com/esstipi-debug/linchpin`.
3. Point the listing at the remote endpoint `https://linchpin.fly.dev/mcp/`
   (streamable HTTP, X-API-Key header) and paste the short blurb below.

## 3. Smithery (smithery.ai)

1. Log in at `https://smithery.ai` with GitHub.
2. "Add server" -> connect `esstipi-debug/linchpin`.
3. Choose the **remote/hosted** server type with URL `https://linchpin.fly.dev/mcp/`
   (Linchpin is a hosted service with per-client keys — do NOT configure a local
   stdio launch; there is none).

## 4. PulseMCP (pulsemcp.com)

1. `https://www.pulsemcp.com/submit` — plain form, no account needed.
2. Fields: name **Linchpin**, repo `https://github.com/esstipi-debug/linchpin`,
   website `https://linchpin.fly.dev`, remote URL `https://linchpin.fly.dev/mcp/`,
   blurb below.

**Shared blurb (2-4 above):**
> AI-powered supply-chain and inventory optimization for autonomous agents:
> forecasting, ABC-XYZ classification, reorder policies, pricing, financial KPIs
> and data-quality audits, grounded in a knowledge graph of 24 SCM books.
> Read-only analysis over streamable HTTP; per-client API key (contact via the
> website — not self-serve yet).

## 5. Odoo Apps Store (apps.odoo.com)

The module (`odoo_addon/linchpin_dry_run/`, v17.0.1.0.0, OPL-1, icon +
`index.html` description page included) is install-verified. The store pulls from
a **dedicated repository whose branch name matches the Odoo version** with the
module at the top level — that repo is already prepared:
`https://github.com/esstipi-debug/linchpin-odoo-apps` (private), branch `17.0`,
`linchpin_dry_run/` at the root.

1. Log in at `https://apps.odoo.com` (create the free account with your GitHub
   email if you don't have one).
2. Top-right menu -> **Upload / your repositories** -> register
   `git@github.com:esstipi-debug/linchpin-odoo-apps.git` for version **17.0**.
3. Odoo shows a deploy/SSH key -> add it in GitHub: repo **Settings -> Deploy
   keys** (read-only) on `linchpin-odoo-apps`.
4. Trigger the scan; the store picks up `linchpin_dry_run` from the branch, using
   `static/description/index.html` + `icon.png` as the listing page.
5. Set the price (the module is OPL-1 licensed — paid is the norm; free also
   drives leads to the hosted service) and publish.

Keeping the store repo in sync after module changes in the main repo:

```
# from the main repo root, after a change under odoo_addon/linchpin_dry_run/
git -C ../linchpin-odoo-apps pull
robocopy odoo_addon/linchpin_dry_run ../linchpin-odoo-apps/linchpin_dry_run /MIR /XD __pycache__
git -C ../linchpin-odoo-apps add -A
git -C ../linchpin-odoo-apps commit -m "sync from main repo"
git -C ../linchpin-odoo-apps push
```

---

After 1-4 are live, add the listing URLs to `README.md` and the fly.io landing
page so the loop closes (visitors -> keys -> `examples/issue_mcp_key.py`).

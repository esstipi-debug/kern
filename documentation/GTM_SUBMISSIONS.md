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
   `https://github.com/esstipi-debug/kern`.
3. Point the listing at the remote endpoint `https://linchpin.fly.dev/mcp/`
   (streamable HTTP, X-API-Key header) and paste the short blurb below.

## 3. Smithery (smithery.ai)

1. Log in at `https://smithery.ai` with GitHub.
2. "Add server" -> connect `esstipi-debug/kern`.
3. Choose the **remote/hosted** server type with URL `https://linchpin.fly.dev/mcp/`
   (Kern is a hosted service with per-client keys — do NOT configure a local
   stdio launch; there is none).

## 4. PulseMCP (pulsemcp.com)

1. `https://www.pulsemcp.com/submit` — plain form, no account needed.
2. Fields: name **Kern**, repo `https://github.com/esstipi-debug/kern`,
   website `https://linchpin.fly.dev`, remote URL `https://linchpin.fly.dev/mcp/`,
   blurb below.

**Shared blurb (2-4 above):**
> AI-powered supply-chain and inventory optimization for autonomous agents:
> forecasting, ABC-XYZ classification, reorder policies, pricing, financial KPIs
> and data-quality audits, grounded in a knowledge graph of 24 SCM books.
> Read-only analysis over streamable HTTP; per-client API key (contact via the
> website — not self-serve yet).

## 5. Odoo Apps Store (apps.odoo.com)

The module lives at `odoo_addon/<serie>/kern_dry_run/` — hoy `17.0/` (v17.0.1.0.0,
install-verified) y `19.0/` (v19.0.1.0.0, el port). **Mismo nombre tecnico en las
dos series a proposito**: el Store trata un modulo como *la misma app* en varias
versiones solo si el directorio se llama igual en cada rama. OPL-1, icono +
`index.html` en las dos. Precio Store **gratis** (sin clave `price` en el
manifest): el listing es anzuelo; los retainers se venden fuera del Store.

El Store lee de un **repositorio dedicado cuyo nombre de rama coincide con la
version de Odoo**, con el modulo en la raiz:
`https://github.com/esstipi-debug/linchpin-odoo-apps` (privado). **No registrar
`esstipi-debug/kern`**: para un repo privado el Store pide acceso de lectura al
usuario GitHub `online-odoo`, y eso expondria el producto entero. El nombre del
repo no importa para la ficha; el aislamiento si.

A 2026-08-25 ese repo **todavia no existe**. El token de un Cloud Agent sobre
`kern` no puede crearlo. Un solo comando, con *tu* login de GitHub (no la
contraseña de apps.odoo.com, no secrets de nosilo-core):

```
gh repo create esstipi-debug/linchpin-odoo-apps --private --disable-issues --disable-wiki
python scripts/sync_linchpin_odoo_apps.py --push
```

Eso deja `kern_dry_run/` en la raiz de las ramas `17.0` y `19.0`. Regenerar
imagenes de ficha: `python scripts/gen_odoo_listing_assets.py` (no pisa `icon.png`).

**Listing assets (este repo, ya en el modulo):**

- `"support"`: `https://linchpin.fly.dev/paquetes` en ambos manifests.
- **Capturas**: `screenshot_wizard.png` + `screenshot_result.png` (mockups del
  wizard real, no una db live). Tambien incrustadas en `index.html`.
- **Cover**: `static/description/cover.png` es el primer item de `"images"`;
  `icon.png` queda como icono del modulo.
- **OPL-1**: `LICENSE` + `COPYRIGHT` viajan con el modulo.

Publicar sigue siendo tu login en apps.odoo.com:

1. Log in at `https://apps.odoo.com` (cuenta que ya tenes; crear una gratis con
   el email de GitHub si no).
2. Top-right menu -> **Upload / your repositories** -> registrar el repo por SSH
   con la rama como fragmento: `ssh://git@github.com/esstipi-debug/linchpin-odoo-apps#19.0`
   (y lo mismo con `#17.0` para la serie vieja).
3. Odoo shows a deploy/SSH key -> add it in GitHub: repo **Settings -> Deploy
   keys** (read-only) on `linchpin-odoo-apps`. Para repos privados hay que dar
   acceso de lectura al usuario GitHub `online-odoo` **en ese repo**, no en `kern`.
4. Trigger the scan; el Store toma `kern_dry_run` de la rama y usa
   `static/description/index.html` + `icon.png` como ficha.
5. Precio Store: **gratis** (anzuelo). No pongas 9 EUR. Un error de manifest
   **despublica todos los modulos del repositorio**, no solo el roto. Revisar
   el scan despues de cada push.

Keeping the store repo in sync after module changes in the main repo:

```
python scripts/sync_linchpin_odoo_apps.py --push
```

---

After the listing is live, add the Store URLs to `README.md` and the fly.io
landing page so the loop closes (visitors -> keys -> `examples/issue_mcp_key.py`).
That landing edit is **not** this playbook: the operator is redesigning the web
in another window.

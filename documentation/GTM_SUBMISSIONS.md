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

1. Install the publisher CLI (one time). `go install
   github.com/modelcontextprotocol/registry/cmd/mcp-publisher@latest` needs a Go
   toolchain, and **this machine has none** (checked 2026-08-28), so the release
   binary is the actual path: `mcp-publisher_windows_amd64.tar.gz`, 7.2 MB, from
   `modelcontextprotocol/registry` release `v1.8.1`. Each asset ships a sigstore
   bundle and an SBOM next to it — verify the signature rather than trusting the
   download, since this binary is about to hold a token for your GitHub account.
2. From the repo root: `mcp-publisher login github` (device-code flow with your
   `esstipi-debug` account — the `io.github.esstipi-debug/*` namespace is proven
   by that login).
3. `mcp-publisher publish` (reads `./server.json`).
4. Verify — **by name, not by the old brand**:
   `curl -s "https://registry.modelcontextprotocol.io/v0/servers?search=kern"`.
   The step used to say `search=linchpin`, which the rename left behind: the
   server is `io.github.esstipi-debug/kern`, so that query returns
   `{"servers":[],"metadata":{"count":0}}` **whether or not the publish
   succeeded**. A verification step that cannot fail is worse than none.

Two things about the payload, decided 2026-08-28:

- **No `repository` block.** It pointed at `esstipi-debug/kern`, which is private
  (`gh repo view` says `isPrivate: true`), so every visitor clicking through from
  a public listing would have got a 404. The field is optional — live listings
  publish without it — and "remote server, source not public" is the honest
  shape. Put it back the day the repo goes public.
- **Republishing needs a version bump.** `version` is `2.9.0` today. The registry
  rejects a second publish of the same version, so a listing refreshed after a
  deploy has to move that number first.

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

**Estado a 2026-08-25: ese repo ya existe y ya esta sincronizado.** Fue creado
el 2026-07-04, y el 2026-08-25 sus ramas se reescribieron desde `kern`: `17.0`
(`72468e7`) y `19.0` (`4defa85`, nueva), las dos con `kern_dry_run/` en la raiz.
La `17.0` traia el nombre viejo `linchpin_dry_run/` y se piso con force-push;
el estado anterior era `5edf4da`, por si hay que volver.

**No hace falta ningun `gh repo create`.** Un `gh` logueado como
`esstipi-debug` tiene `admin` sobre los dos repos. Si una sesion reporta 404
sobre `linchpin-odoo-apps`, es el alcance de *su* token y no un repo faltante:

```
gh api repos/esstipi-debug/linchpin-odoo-apps --jq .permissions
```

Para re-sincronizar despues de tocar el modulo, desde la raiz de `kern`:

```
python scripts/sync_linchpin_odoo_apps.py --push
```

Eso reescribe `kern_dry_run/` en la raiz de las ramas `17.0` y `19.0`. Regenerar
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
   **Ya invitado el 2026-08-25** (`online-odoo`, permiso `read`, sobre
   `linchpin-odoo-apps`). Sigue *pendiente de aceptacion* del lado de Odoo, y
   las invitaciones de GitHub **caducan a los 7 dias**: si el tramite en
   apps.odoo.com se pasa de esa ventana, reenviarla. Verificar con
   `gh api repos/esstipi-debug/linchpin-odoo-apps/invitations`.
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

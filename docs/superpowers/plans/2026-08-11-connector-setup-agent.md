# Plan — Agente de setup de conectores (ve la pantalla, scrapea, ejecuta con aprobación)

Fecha: 2026-08-11 · Estado: propuesto · Spec: `specs/2026-08-11-connector-setup-agent-design.md`

Objetivo: que un operador (nosotros) o el cliente conecten su tienda/ERP a Kern sin ingeniería
manual: el agente ve su navegador, scrapea sus sistemas, y deja el conector verificado y
operativo — escribiendo solo con aprobación humana sobre `nosilo_core.writeback`.

**Dependencia transversal previa (decisión del cliente):** primero mergear los conectores que ya
están hechos pero no en `main` (`shopify_replenishment`/`channel_replenishment`/Unleashed desde
`.wt-unleashed-erp`, SAP B1 desde `feat/sap-b1-connector`, B1 stock sync desde `shopify-stock-sync`).
El agente de setup conecta sistemas cuyo conector API ya existe; sin el merge, la Fase 1 solo
puede onboardear Odoo + Excel.

## Fase 1 — Bridge CDP + loop + recetas (dentro de Kern)

**Meta:** `python -m uvicorn webapp.app` + un Chrome del operador con `--remote-debugging-port`,
y desde la consola `/setup` se onbordea una tienda Shopify de prueba u Odoo demo, offline.

| # | Entregable | Archivos | Criterio de salida |
|---|---|---|---|
| 1.1 | Conductor CDP (snapshot/navigate/dom_query/click/type/eval_js/wait) con `FakeBrowser` para tests | `src/connectors/browser/__init__.py`, `cdp.py`, `fake.py` | `pytest tests/test_browser_cdp.py` verde, sin red |
| 1.2 | Redacción de snapshots (passwords opacos, PII mascarada) antes de `llm.py` | `src/connectors/browser/redact.py` | Test: snapshot no contiene password/PII del fixture |
| 1.3 | Allowlist de dominios por sesión; `navigate()` la aplica | `src/connectors/browser/scope.py` | Test: dominio fuera de lista → error, no navega |
| 1.4 | Loop `observa→decide→actúa→verifica` (reusa `llm.py`, QA gate, `GuidedOutcome`) | `scm_agent/setup.py`, `jobs/setup/base.py` | Test: receta contra `FakeBrowser` avanza paso a paso y se detiene en QA |
| 1.5 | Receta Shopify: catálogo, inventario por ubicación, orders → DTOs canónicos | `jobs/setup/shopify.py`, `src/connectors/browser/extractors.py` | Cross-check vs `demo_shopify_inventory` OK |
| 1.6 | Receta Odoo: quants, reordering rules, suppliers, lead times | `jobs/setup/odoo.py` | Cross-check vs `demo_odoo()` OK |
| 1.7 | Perfil de cliente + config de conector al terminar (idempotente) | `src/client_profile.py` (upsert), `src/connectors/browser/onboard.py` | Re-correr la receta no duplica (test) |
| 1.8 | Alto impacto T2: changeset stageado → `POST /api/approvals/{id}` → apply/rollback | `jobs/setup/actions.py` | Test: aprobar aplica, rechazar no toca nada, rollback revierte |
| 1.9 | Endpoints `POST /api/setup/sessions` + `WS /api/setup/sessions/{id}/ws` | `webapp/app.py` (o `webapp/setup_api.py` + route) | Auth+rate_limit igual que `/api/jobs`; no en MCP |
| 1.10 | Consola `/setup`: screenshot en vivo + pasos + Aprobar/Rechazar | `webapp/static/setup/` | Test HTTP de la sesión end-to-end con `FakeBrowser` |

**Extra:** `[browser]` = `playwright` (opcional, import lazy, nunca en `prod-boot`).

## Fase 2 — Producto: extensión de Chrome

**Meta:** el navegador del cliente se conecta a Kern desde su propia Chrome con una extensión; el
agente ve su pestaña real y las acciones de alto impacto llevan un botón de consentimiento
visible dentro de la extensión.

| # | Entregable | Archivos | Criterio de salida |
|---|---|---|---|
| 2.1 | Extensión mínima: websocket seguro + `chrome.scripting` para snapshot/DOM de la pestaña activa | `webapp/extension/` (manifest v3, background, content script) | Se instala en Chrome sin error; conecta contra un Kern de dev |
| 2.2 | Autenticación de la extensión contra Kern (reusa el key store MCP / API key) | `webapp/extension/auth.js`, `webapp/mcp_auth.py` | Handshake firmado; sin token no hay sesión |
| 2.3 | Consentimiento por acción de alto impacto dentro de la extensión | `webapp/extension/consent.js` | La extensión no ejecuta una acción sin confirmación visible |
| 2.4 | Mismo `BrowserDriver` sobre la extensión (la interfaz no cambia; el transporte sí) | `src/connectors/browser/extension.py` | Recetas de Fase 1 corren sin cambios contra la extensión |
| 2.5 | Guía de instalación por cliente (una página) | `webapp/setup_install_page.py` + doc | Un cliente lo instala siguiendo 3 pasos |

## Fase 3 — Verificación en vivo y autonomía

| # | Entregable | Criterio de salida |
|---|---|---|
| 3.1 | Piloto con 1-2 clientes reales (Shopify u Odoo) | Onboarding real medido: tiempo, aciertos de pasos, cross-checks |
| 3.2 | Ledger de aciertos de la receta por sistema → promoción T2→T1 solo con evidencia (`autonomy.py`) | El primer escalón T1 sale solo con historial suficiente |
| 3.3 | Recetas para SAP B1 y Unleashed (una vez mergeadas) | Mismo flujo, otros extractores |

## Orden de PRs sugerido (Fase 1)

1. `1.1+1.2+1.3` — conductor CDP + redacción + alcance (fundamento, sin agente todavía).
2. `1.4` — loop + QA + receta base contra `FakeBrowser`.
3. `1.5+1.6+1.7` — recetas Shopify/Odoo + perfil de cliente idempotente.
4. `1.8` — alto impacto T2 sobre writeback.
5. `1.9+1.10` — endpoints + consola; e2e offline.

Cada PR: rama → draft → CI verde (py3.11–3.13, `ruff`, `prod-boot`) → squash a `main`. Nunca
escribir a `main` directo.

## Riesgos

- **Wheels de Playwright en Windows/py3.11** → extra opcional + fallback CDP directo documentado.
- **Selectores de los admins cambian** → extractores como datos versionados + cross-check con la
  API como red de seguridad.
- **UI/UX del control de navegador** → la consola muestra siempre qué está haciendo el agente y
  hay un botón de **pausar/ceder el control** en todo momento.
- **Alcance**: no agregar scraping de competencia ni control de escritorio en Fase 1 (ver spec §8).

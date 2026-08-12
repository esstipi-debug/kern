# Agente de setup de conectores — ve la pantalla del usuario, scrapea y ejecuta con aprobación

Fecha: 2026-08-11 · Estado: diseño propuesto, sin implementar

## 1. El problema

Kern tiene conectores reales (`src/connectors/`: Odoo, Shopify inventory/prices, Excel, SAP B1,
Unleashed en ramas) y un plano de escritura segura (`nosilo_core.writeback`). Lo que NO tiene es
**nadie que deje un conector funcionando**: pedir credenciales, validar la conexión, mapear
SKUs/almacenes, verificar contra datos reales. Hoy eso es un trabajo manual de ingeniería por
cliente. El punto 4 del gap en la conversación (ver `CAPABILITY_EXPANSION_PLAN.md` §2.7/§2.8):
no existe un **agente de onboarding/setup**.

Este spec construye ese agente con tres capacidades que el cliente pidió explícitamente:

1. **Ve la pantalla del usuario** — controla el navegador del usuario (no espejo pasivo): navega,
   hace clic, lee, y el usuario ve cada paso en vivo. Patrón computer-use / browser-control.
2. **Hace scraping** — de los **sistemas del propio cliente** (admin de Shopify, Odoo, catálogo,
   stock) para auto-onboarding sin planillas exportadas.
3. **Funciones de alto impacto** — ejecuta acciones reales (ajustar stock, reorder rules, repricing)
   **con aprobación humana en pantalla** (T2), sobre el plano de writeback seguro que ya existe.

## 2. Decisiones tomadas

| Decisión | Valor | Por qué |
|---|---|---|
| Transporte de "ver la pantalla" | **Fase 1: bridge CDP local dentro de Kern.** Fase 2: **extensión de Chrome** como producto | La Fase 1 reusa el patrón gstack browse/pair-agent ya probado y vive en el repo. La Fase 2 es el empaquetado como producto (no requiere servidor con Chromium) |
| Lectura de los sistemas del cliente | **DOM scraping del admin propio + cross-check con la API** | El scraping ve lo que la API no expone o cuando no hay credenciales API todavía; el cross-check evita onboardear con datos incorrectos |
| Alcance de navegación | **Allowlist de dominios por cliente** (admin.shopify.com, el Odoo del cliente, etc.) | El agente nunca navega fuera del sistema que está conectando |
| Autonomía | **T2 — el agente arma el changeset completo, un humano aprueba en pantalla** | Igual que el resto de Kern: la autonomía se gana con evidencia medida (`scm_agent/autonomy.py`), no se declara |
| Escritura | **Solo vía `nosilo_core.writeback`** | Mismo contrato que `odoo.py`/`shopify_inventory.py`: dry-run → aprobación → apply idempotente → rollback. No se inventa un segundo plano |
| Credenciales del usuario | **Nunca se leen ni persisten.** Se reusa la sesión ya logueada; las API keys del cliente van al token store | El agente de navegador no captura `password` inputs (inputs de password se ven opacos), y no rota ni guarda la sesión del usuario |
| Progresión | **Fase 1 dentro de Kern (webapp) → Fase 2 producto (extensión)** | Confirmado con el cliente ("ambos, por fases") |

## 3. Lo que NO se construye

Casi todo el mecanismo ya existe en el repo. Este spec añade un **conductor de navegador + recetas
de onboarding**, no una plataforma nueva.

| Pieza | Dónde está ya |
|---|---|
| Plano de escritura segura | `nosilo_core.writeback` (external, v0.1.0): `stage` → `Approval` (TTL 900 s, firmado) → `apply` → `rollback` |
| Superficie de aprobación | `POST /api/approvals/{id}` + `GET /tower` (`webapp/app.py:1677`) |
| Conectores API de lectura/escritura | `src/connectors/odoo.py`, `shopify_inventory.py`, `excel.py`, (SAP B1, Unleashed en ramas) |
| Perfil de cliente por parámetros | `src/client_profile.py` (persistencia por slug de cliente) |
| Loop agéntico (clasificar → preparar → ejecutar → QA → entregar) | `scm_agent/orchestrator.py` + `scm_agent/llm.py` (Claude + fallback por reglas) |
| Autonomía con promoción por evidencia | `scm_agent/autonomy.py` (`T1/T2/T3`, `PromotionRecord`) |
| Ledger de eventos idempotente | `EventLedger` (SQLite, inyectable) |
| Scraping descubrimiento guiado | `jobs/price_watch.py` (auto-onboarding de sitios, robots.txt-only) |

## 4. Diseño

### 4.1 El conductor de navegador — `src/connectors/browser/`

Un módulo nuevo con una interfaz mínima (`BrowserDriver`) que habla CDP (Chrome DevTools
Protocol). Fase 1 lo hace **local**: el operador/usuario corre Kern con un navegador
arrancado con `--remote-debugging-port` (o el bridge lo lanza con Playwright headful). El agente
**nunca** toca el escritorio fuera del navegador.

```
BrowserDriver (Protocol)
 ├─ snapshot()            -> screenshot (PNG, redactado) + DOM accesible
 ├─ navigate(url)         -> solo dominios de la allowlist
 ├─ dom_query(selector)   -> scraping: texto/atributos de nodos
 ├─ click(selector) · type(selector, text)   -> interacción guiada
 ├─ eval_js(expr)         -> lectura avanzada (tablas, estado de SPA)
 └─ wait(selector)        -> sincronización con SPAs (Shopify/Odoo admin)
```

Transporte: Fase 1 = `playwright` (extra `[browser]`) sobre CDP. Alternativa sin dependencia
pesada documentada (cliente `websockets` + `httpx` directo a CDP) para entornos sin wheels.

### 4.2 El loop del agente — `scm_agent/setup.py`

Un loop **`observa → decide → actúa → verifica`** idéntico al resto de Kern, con una diferencia:
la "acción" es sobre el navegador, no sobre un CSV.

```
paso actual (de la receta)
  → snapshot()                    # el agente VE
  → LLM decide la siguiente acción (o "preguntar al usuario")
  → actúa (click/type/navigate/dom_query)
  → verifica que el resultado esperado ocurrió (QA gate)
  → avanza al siguiente paso de la receta
```

- **Recetas de onboarding** (`jobs/setup/`): secuencias por sistema (`shopify.py`, `odoo.py`,
  `unleashed.py`, `sap_b1.py`). Una receta es una lista declarativa de pasos: cada paso declara
  **objetivo, selectores, extracción (scraping), validación y "lo que NO hace"**.
- **QA gate**: un paso cuya validación falla no avanza; el agente reporta el screenshot y pregunta
  al usuario (estado `needs_clarification`, igual que los jobs).
- **Nunca desprotegido**: cada paso termina `EXECUTADO` o con un traspaso guiado (`GuidedOutcome`,
  `nosilo_core.guided`) — nunca en un callejón sin salida.

### 4.3 Scraping de los sistemas del cliente — `src/connectors/browser/extractors.py`

Extractores por sistema que normalizan a los DTOs canónicos de `src/connectors/__init__.py`
(`Product`, `InventoryLevel`, `Order`), para que el resto de Kern no cambie:

- **Shopify admin**: catálogo (products/variants), inventario por ubicación (la tabla de stock),
  orders. Selectores de `admin.shopify.com` puestos como datos versionados, no hardcodeados en el
  loop.
- **Odoo**: `product.template`/`product.product`, `stock.quant` por almacén, reordering rules,
  suppliers, lead times.
- Salida = un **perfil de cliente** (`client_profile.upsert_profile`) + la config del conector:
  ubicación/almacén por defecto, unidad de mapeo SKU, credenciales API **pedidas por separado**
  (nunca scrapeadas).
- **Cross-check obligatorio**: lo scrapeado del DOM se compara contra lo que la API lee
  (`odoo.inventory_levels()`, `ShopifyInventoryStore.read`). Si difieren más de un umbral, el
  onboarding se detiene en `needs_clarification`: no se onbordea con datos no verificados.

### 4.4 Funciones de alto impacto (T2)

Cuando la receta termina el onboarding, el agente entrega **un changeset stageado** — no escribe
nada solo:

1. Construye los cambios deseados (p. ej. reorder rule en Odoo = on-hand + restock; stock Shopify;
   repricing) como un `nosilo_core.writeback.Changeset`.
2. `writeback.stage(...)` → dry-run, una clave de idempotencia derivada del contenido.
3. La consola muestra el resumen ("Se ajustará el stock de 5 SKUs en Shopify") con botón
   **Aprobar / Ver detalle / Rechazar** → `POST /api/approvals/{id}` (TTL 900 s, firmado con
   `LINCHPIN_APPROVAL_SECRET`).
4. `writeback.apply` idempotente + `AuditEntry` + `rollback()` disponible.
5. El siguiente tick del sync continuo (B1, `jobs/shopify_stock_sync.py`) ya encuentra el
   conector verificado y operativo.

### 4.5 Sesión y consola — `webapp/`

- `POST /api/setup/sessions` → crea una sesión de setup (payload: `system` = shopify/odoo/…,
  `client` slug). Validada y rate-limited igual que `POST /api/jobs`.
- `WS /api/setup/sessions/{id}/ws` → websocket bidireccional: el navegador manda snapshots al
  agente; el agente manda acciones + estado de pasos a la UI; la UI manda aprobaciones.
- UI `webapp/static/setup/` — consola tipo copiloto: **video/screenshot en vivo del navegador del
  usuario**, lista de pasos de la receta, panel de estado, botón Aprobar/Rechazar de alto impacto.
- **No se expone en MCP.** La superficie MCP sigue read-only (ver `docs/MCP_SERVER.md`); un agente
  que controla el navegador del cliente es exactamente lo que no puede caer en un endpoint anónimo.

## 5. Seguridad

| Riesgo | Control |
|---|---|
| Navegar fuera del sistema del cliente | Allowlist de dominios por sesión; `navigate()` la aplica; fuera de lista = error + `needs_clarification` |
| Leer/guardar credenciales del usuario | Inputs `type=password` se ven opacos en el snapshot (redactados); el agente no lee el valor. La sesión logueada se reusa, no se copia ni persiste |
| Enviar PII al LLM | Snapshots redactados (mascar nombres de clientes, direcciones, correos) antes de mandarlos a `llm.py`; regla repo "nunca leer/superficie PII" |
| Escritura no autorizada a sistemas reales | Solo `nosilo_core.writeback`: T2, dry-run, aprobación firmada con TTL, apply idempotente, rollback |
| Abuso del websocket/endpoints | Auth `require_api_key` + rate_limit (igual que el resto de rutas mutantes); sesiones con timeout |
| Dependencia de wheels de Playwright | Extra `[browser]` opcional con fallback a CDP directo; nunca en el boot chain de prod (`prod-boot` importa `webapp.app` sin extras) |

## 6. Errores

| Situación | Comportamiento |
|---|---|
| El navegador no está conectado | La sesión queda `awaiting_browser`; la UI muestra las instrucciones de conexión (Fase 1) |
| Selector de la receta no aparece | QA gate: 2 reintentos con `wait`; después `needs_clarification` con screenshot |
| Lo scrapeado ≠ lo que dice la API | Cross-check superado el umbral → stop, `needs_clarification` |
| Aprobación rechazada | No se aplica nada; se registra la decisión en el audit; el changeset se descarta |
| Dominio fuera de allowlist | Error de sesión; no navega |
| Derivación de versión de API de Shopify | Reusa el aborto de A (`shopify_api_version.check_served`) |

## 7. Tests

Offline, sin red, patrón del repo: un `FakeBrowser` (`InMemoryShopifyInventory`-style) que
emula los admins con un DOM mínimo.

| Caso | Espera |
|---|---|
| Receta Shopify completa contra el fake | Perfil de cliente creado, cross-check OK, sin escrituras |
| Cross-check con divergencia | Onboarding se detiene en `needs_clarification`, no crea perfil |
| Paso con selector ausente | QA gate bloquea, reporta `needs_clarification` |
| Alto impacto | Changeset stageado, Shopify/Odoo sin tocar; tras aprobar, aplica; rollback revierte |
| Aprobación rechazada | Nada se aplica, audit registra el rechazo |
| Dominio fuera de allowlist | `navigate` falla, sesión marca error |
| Receta Odoo (reorder rule) | Mismo flujo, contra `demo_odoo()` |
| Conector ya onbordeado | La receta salta a verificación y no duplica pasos (idempotente) |

## 8. Fuera de alcance (por ahora)

- **Autonomía T1** — se activa solo con historial de aciertos medido (`autonomy.py`).
- **Control del escritorio completo** (no solo el navegador) — descartado en la decisión 1.
- **Scraping de competencia/mercados** — eso ya vive en `price_watch`/`price_intelligence` y no
  cambia con este spec (la decisión 2 fue "sistemas del cliente").
- **Webhooks/realtime** — sigue el pull + cron actual.
- **Amazon SP-API / FBA inbound** — planificado en `CAPABILITY_EXPANSION_PLAN.md` §2.7, no aquí.
- **Nuevos ERPs** (NetSuite, BC, Cinz, Zoho, QuickBooks, Xero) — misma receta, otros extractores;
  la receta por sistema es aditiva.

## 9. Lo que este trabajo NO verificará

Todo el testing es offline contra `FakeBrowser`. Que el agente onbordee una tienda Shopify y una
instancia de Odoo **reales** queda sin medir hasta que haya un cliente con credenciales — igual
que B1 (ver `2026-08-03-shopify-stock-sync-design.md` §8). No debe describirse como comprobado
hasta entonces.

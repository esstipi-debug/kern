# Linchpin 3.0 — Documento de desarrollo exhaustivo

**Versión:** 2.0 · **Fecha:** 12 jul 2026 · **Repo base:** [esstipi-debug/linchpin](https://github.com/esstipi-debug/linchpin) · **Base de código:** `main` @ `62844c3` (post-protocolo 2.0, épicas E1–E8)
**Diseño:** Fable 5 (investigación multi-agente + grounding contra el repo real). **Ejecución prevista:** Sonnet 5, PR por PR — cada PR de §12 es autosuficiente: archivos, interfaz, invariantes, tests y criterio de aceptación verificable, sin decisiones de juicio abiertas.
**Objetivo:** Control Tower (agente autónomo como diferenciador de venta — decisión del operador 2026-07-12) + Revenue Layer, con la inteligencia de precios como capacidad insignia ("el titán del pricing", §6).

**Decisiones del operador que gobiernan este documento (2026-07-12):**
1. **Tower primero** — el orden de PRs prioriza F0 + Track A antes que el titán.
2. **Runtime: Fly.io** — se extiende `linchpin.fly.dev` (ya desplegado) con volumen persistente.
3. **Multi-mercado desde el día 1** — LatAm + España/UE + US: MercadoLibre entra a L0, guardrails por jurisdicción declarativos.
4. **SEO dentro de 3.0** — Track B completo (S1–S5).

---

## 0. Protocolo de ejecución (para el agente implementador)

Antes de CUALQUIER PR de este plan:
1. Leer `CLAUDE.md` (raíz) y el `HANDOFF.md` de **main** (no el del worktree — puede estar desactualizado).
2. **Regla `PIPELINE.md`** (permanente, definida en HANDOFF): si existe un `PIPELINE.md` en la raíz con un deal activo, ese trabajo desplaza a este plan. El **Atajo de Revenue** (§3.1) es el camino preparado para ese caso.
3. Verificar `git status` — este repo tiene sesiones concurrentes; integra `origin/main` y re-corre la suite ANTES del squash-merge.
4. Flujo: rama feature → **draft PR** → CI verde (py3.11/3.12/3.13) → squash-merge. Nunca push directo a `main`.
5. Convenciones: ASCII-only en prints de consola (cp1252); tests `pytest tests/ -q` con `PYTHONPATH=.`; lint `ruff check src tests examples`; deps pesadas SIEMPRE en extras opcionales con fallback.
6. Cada PR de §12 lista su **verificación** — comandos concretos que deben pasar antes de abrir el PR. Si un paso exige credenciales que no existen, el PR se marca `[CRED]` y NO se intenta: se construye offline-first contra un stand-in (patrón `InMemoryOdoo`).
7. Tests con números de referencia verificados a mano — la cultura del repo (1,100+ tests). Un módulo sin ejemplo numérico de referencia no se mergea.

---

## 1. Anatomía del repo (lo que ya existe y NO se cambia)

```
scm_agent/     Orquestador: brief → intent.classify → registry.get(tool) → prepare → run → qa → deliver
  types.py     JobResult, statuses: ok · needs_clarification · needs_data · qa_failed · error
  registry.py  register() — agregar capacidad = 1 llamada, sin editar ruteo
  tools.py     build_default_registry() — fuente de verdad (37 tools en main @ 62844c3)
  intent.py    clasificación reglas + LLM opcional
  llm.py       Claude opcional; fallback determinista
  knowledge.py L3: grounding — cada resultado cita concepto + módulo src/
  guided_bridge.py  contrato never-unprotected (via src/guided.py)
jobs/          Playbooks: intake.detect_columns → run → qa.verify → deliverables.write_all
  intake.py    esquema canónico: date, product_id, quantity (+ unit_cost, lead_time_days opcionales)
  qa.py        invariantes por dominio (patrón verify_*() -> list[str] + *_passed() -> bool) + coverage_gate
src/           Motor: funciones puras (eoq, policies, safety_stock, forecasting, pricing, liquidation, alerting…)
webapp/        FastAPI: dashboard + POST /api/jobs + consola + maquinaria 2.0 (E1 /paquetes, E2 demo-scan,
               E4 lang es/en, E5 citation gate, E6 Branding white-label, E8 /api/metrics)
examples/      CLIs (run_agent.py, run_pricing_job.py, run_free_scan.py…)
tests/         1,100+ tests: ejemplos numéricos de referencia + agente + HTTP guards
```

**Activos 3.0 que el plan v1 ignoraba y este v2 reutiliza (verificado contra main):**
- `src/pricing.py` YA tiene `estimate_elasticity()` (log-log, `ElasticityFit` con `r_squared`/`identified`), `optimal_price_constant_elasticity()`, `fit_linear_demand()`, `optimal_price_linear()`, `markdown_price()`, `recommend_price()` → P2 **extiende**, no crea.
- PR #124 shippeó `src/liquidation.py` (243 líneas) + `jobs/markdown_liquidation_job.py` + tool registrada → P4 **ya existe en su núcleo**; 3.0 solo añade calendario Omnibus-safe y señal competitiva.
- `src/alerting.py` (`InventoryEvent`, `detect_events()`, `alerts_outcome()`) → la semilla exacta de los monitores A1.
- `src/writeback.py` (tiers read/reversible/irreversible, `Approval` TTL 900s, apply idempotente, rollback) → A3 y P3 extienden este plano, no lo duplican.
- Extras ya presentes en `pyproject`: `dataquality` ya trae **rapidfuzz + python-stdnum** (matching los reutiliza), `forecast` (statsforecast), `llm`, `web`, `mcdm`.

## 2. Reglas de oro 3.0 (las 6 heredadas + 8 nuevas)

Heredadas (obligatorias, sin cambios):
1. `src/` = funciones puras auditables; los playbooks componen, el motor no se toca para casos especiales.
2. QA gate con veto central: si `qa()` falla, no hay entregable. Sin excepciones.
3. Todo resultado se ancla al knowledge graph (cita concepto + función).
4. Escritura a sistemas externos SOLO por safe-staging (dry-run → tier de riesgo → aprobación TTL → apply idempotente → rollback).
5. Dependencias pesadas en extras opcionales de `pyproject` con fallback al núcleo numpy/pandas/scipy.
6. Cada módulo nuevo llega con tests de ejemplos de referencia (números verificados a mano).

Nuevas para 3.0 (los componentes continuos y los datos observados exigen reglas que 2.x no necesitaba):
7. **Procedencia total en datos observados:** todo dato externo (precio scrapeado, respuesta de API, señal de watcher) lleva tier de adquisición, extractor + versión, confianza y timestamp. El **gate de citas E5** es el mecanismo de enforcement — un entregable cuyo dato no cita procedencia no sale.
8. **Estado append-only:** snapshots, ledgers e historia de precios nunca se editan; las correcciones son filas nuevas con flag. (Safe-staging aplicado a datos.)
9. **Todo componente continuo degrada a batch:** scheduler, monitores y watchers deben poder ejecutarse one-shot vía CLI sin daemon. Si no corre en un test sin dormir procesos, no se mergea. (Esto también hace demo-able todo sin deploy.)
10. **LLM jamás silencioso en el camino de datos:** esquema estricto (pydantic), budget cap diario, y marca de procedencia `extractor=llm`. Una extracción LLM se re-verifica por vía determinista en la siguiente lectura.
11. **La autonomía se gana con evidencia:** una acción pasa de T2 (un click) a T1 (auto) SOLO por historial de A4 (N ciclos consecutivos con precisión ≥ umbral), nunca por edición manual de config. La degradación T1→T2 sí es inmediata ante cualquier fallo.
12. **Jurisdicción es configuración declarativa:** guardrails de precio (Omnibus UE, SERNAC/PROFECO/SIC, MAP US) viven en perfiles de mercado versionados, no en código con `if`.
13. **Los entregables nuevos pasan por la maquinaria 2.0:** E4 (idioma es/en), E5 (citas) y E6 (branding white-label) son obligatorios para todo entregable 3.0 — un partner revendiendo pricing-intel no puede filtrar marca Linchpin.
14. **Ningún cap silencioso:** si un componente limita cobertura (top-N competidores, sampling, budget LLM agotado), el recorte se declara en el entregable. Truncar sin decirlo = mentir con datos.

## 3. Mapa de builds 3.0 (orden: Tower primero)

| Fase | Bloque | Módulos | PRs (§12) |
| --- | --- | --- | --- |
| F0 | Base común | estado, bus de eventos, scheduler+notify, ruteo por evento | PR-1…4 |
| A | **Control Tower core** | monitores (desde `alerting.py`), autonomía T1/T2/T3, tab Tower + aprobaciones, verify A4, promoción T2→T1 | PR-5…9 |
| B | **Titán pricing** | `src/pricing_intel/` + tool `price_intelligence` + **oferta #9 + lead magnet** | PR-10…15 |
| C | Pricing avanzado + S&OP | P2 elasticidad batch, P5 guardrails, P3 repricing writeback, P4 v2, A5 sop_engine | PR-16…20 |
| D | SEO S1–S5 | S4 primero (cero deps), luego auditoría, schema/feeds, PDP, GEO | PR-21…25 |

### 3.1 El Atajo de Revenue (documentado, no default)
La investigación GTM verificó que el **mínimo vendible** es el modo one-shot del titán — *Diagnóstico de Posición de Precios* (§10) — y que sus PRs (10-slim → 11 → 12 → 13) **no dependen de F0 ni del Tower**: el modo one-shot sigue el patrón playbook existente (intake → run → qa → deliver), sin scheduler ni bus. **Regla:** si aparece un deal de pricing (`PIPELINE.md`), el implementador salta directo a PR-10…13 en modo slim y vuelve al orden Tower después. Sin deal, el orden es el de la tabla — decisión explícita del operador: el Tower es el diferenciador.

## 4. F0 — Base común (la plomería del Tower)

### 4.1 `src/state/` — estado del sistema
- **Archivos:** `system_state.py` (snapshot versionado por ciclo: stock, precios propios y de competidores, forecast vigente, decisiones emitidas, outcomes), `store.py` (SQLite índice/último estado + parquet particionado por fecha para historia).
- **Interfaz:** `snapshot(domain: str, payload: DataFrame, cycle_id: str)` / `latest(domain)` / `history(domain, window)`. Validación de esquemas con contratos [pandera](https://github.com/unionai-oss/pandera).
- **QA invariante:** append-only (regla 8); `cycle_id` monotónico; un snapshot con esquema inválido se rechaza con error explícito.
- **Persistencia en Fly (decisión de runtime):** un volumen (`fly volumes create`), SQLite replicado off-box con **Litestream 0.5.x → Tigris** (S3 de Fly) como sidecar supervisado — verificado: v0.5.x (formato LTX, point-in-time recovery) es el patrón que Fly recomienda hoy; los snapshots diarios de Fly NO son backup. El parquet es **derivado/regenerable desde SQLite** por diseño; si algún dominio crece a no-regenerable, sync nocturno a Tigris. Máquina always-on: `min_machines_running=1` (~USD 3–6/mes en shared-cpu-1x).

### 4.2 `scm_agent/events.py` — bus de eventos
- **Modelo:** `Event(id, type, severity, sku, source, payload, dedup_key, ts)`. Ledger idempotente en SQLite: mismo `dedup_key` en ventana = no re-emite.
- **Ruteo como dato:** `config/event_routing.yaml` versionado: `event_type → (tool, param_builder, autonomy_tier)`.

### 4.3 `jobs/scheduler.py` + notificación
- **APScheduler `>=3.11.3,<4`** (verificado: 4.0 sigue en alpha desde 2025 con advertencia explícita de no-producción; 3.11.3 es el estable mantenido). Corre **dentro del proceso FastAPI existente** (lifespan de la app en Fly — cero máquinas extra), jobstore SQLite en el volumen.
- **Notificación v1 = `notify()` con webhook POST plano vía httpx + retry** (Slack incoming webhook). Se elimina `apprise` del plan (YAGNI verificado — multiplexar 60 canales que no usamos); `slack-sdk` entra solo cuando el Tower necesite mensajes editables/threads (PR-7 lo decide). Todo detrás de una única función `notify()` para que el swap sea trivial.
- Digest diario narrado por LLM **detrás del QA gate**; cada job es una función pura idempotente — "adoptar Prefect después es envolver con decoradores, no reescribir" (una línea, cero provisioning hoy).

**Criterio de aceptación F0:** un evento sintético `stock_below_rop` dispara `inventory_optimization` del SKU, produce entregable QA-gated y notifica con link de aprobación. En CI, todo corre en modo batch one-shot (regla 9) — cero daemons en tests.

## 5. Track A — Control Tower (adelantado por decisión del operador)

| Capa | Archivos | Contenido | Base existente |
| --- | --- | --- | --- |
| A1 `sense` | `scm_agent/monitors.py` + `config/monitors.yaml` | Monitores puros sobre estado del sistema: ROP cruzado, σ_e fuera de banda, drift de lead time, stockout proyectado, exceso creciente. Emiten `Event` con dedup | **Generaliza `src/alerting.py`** (`detect_events` ya implementa el patrón puro evento-desde-datos) |
| A2 `decide` | `scm_agent/event_intent.py` | Ruteo evento→tool por `event_routing.yaml`; registry intacto | F0 |
| A3 `execute` | `scm_agent/autonomy.py` | Tiers por tipo de acción: T1 auto dentro de bandas; T2 un click (TTL); T3 paquete de escalamiento. | **Extiende `src/writeback.py`** (Approval TTL 900s, tiers de riesgo) + `src/escalation.py` |
| A4 `verify` | `src/verify/backtest.py`, `src/verify/reliability.py` | Predicho vs real por decisión; MAPE/WAPE/bias por SKU; confiabilidad por tool; recalibración de σ_e | `src/forecast_metrics` existente |
| A5 `balance` | `src/sop_engine/` + `jobs/integrated_plan.py` | v1: pipeline secuencial con checks de coherencia citables; v2 (CP-SAT) SOLO cuando A4 acredite v1 | va en Fase C (PR-20), necesita P2 |

- Webapp: tab **Tower** (eventos del día, acciones T1 auto-ejecutadas y auditadas, pendientes T2 con botón de aprobación TTL, confiabilidad A4 por tool) + `GET /api/events` + `POST /api/approvals/{id}`. Mismo patrón estático + fetch, sin build step.
- **Invariante A3/A4 (regla 11):** la promoción T2→T1 es un cambio de configuración PROPUESTO por A4 con evidencia adjunta y aprobado por el operador — auditable como cualquier changeset.
- **Valor demo desde el día 1:** los monitores A1 operan sobre las 37 tools y datos existentes — el Tower demuestra autonomía sin esperar al titán; el titán después le enchufa señales de mercado (§6.8).

## 6. EL TITÁN DEL PRICING — `src/pricing_intel/`

> Objetivo de diseño: la inteligencia de precios más **robusta, legal y barata por dato** posible. Titán no significa fuerza bruta: significa que nunca se cae, nunca se envenena con datos malos, y saca el máximo de fuentes estructuradas y APIs antes de tocar HTML frágil. Validación externa del diseño: PriceGhost (MIT) implementa de forma independiente la misma cascada JSON-LD-first con score de confianza y arbitraje humano — portamos sus patrones (no su código, es Node).

### 6.0 Principios no negociables
1. **API-first, structured-data-second, HTML-last** (jerarquía §6.2).
2. **Cero PII.** Solo datos públicos de producto/precio/stock. Nunca cuentas de usuario para scrapear.
3. **robots.txt y ToS por sitio** en registro versionado (`config/sites/*.yaml`) con decisión documentada por dominio. Sin YAML aprobado, el fetcher se niega a correr.
4. **Procedencia total** (regla 7) con E5 como enforcement.
5. **Cortesía técnica:** rate limit por dominio, jitter, cache condicional (ETag/If-Modified-Since), user-agent identificable. Nada de evasión anti-bot: si un sitio bloquea, se degrada de tier o se descarta el dominio — jamás se disfraza el fetcher.

### 6.1 Árbol de archivos
```
src/pricing_intel/
  __init__.py
  models.py        CompetitorOffer, PricePoint, MatchCandidate, SiteConfig (dataclasses frozen + pandera)
  ledger.py        PriceLedger: append-only parquet particionado + índice SQLite
  acquire/
    base.py        protocolo Fetcher (fetch(sku_ref) -> RawObservation) + circuit breaker
    meli_api.py    L0: MercadoLibre Items/Search API (EL marketplace del ICP LatAm)  [VERIFICAR-EN-PR: estado actual de auth/rate/ToS del API público MELI]
    amazon_api.py  L0 [CRED]: SP-API Product Pricing v2022-05-01 (getCompetitiveSummary) — exige Seller Central del cliente; DES-PRIORIZADO hasta tener cliente seller  [VERIFICAR-EN-PR: sunset de v0 getCompetitivePricing]
    shopify_api.py L0 [CRED]: precios propios multicanal (baseline)
    structured.py  L1: JSON-LD/microdata/OpenGraph de PDPs (extruct tras adapter — ver 6.4)
    watcher.py     L2: adaptador changedetection.io (webhook receiver) — requests-mode
    spiders/       L3: Scrapy >=2.17 por competidor crítico (1 spider = 1 clase, contrato común)
    browser.py     L3b: Playwright opcional (extra [browser], chromium --only-shell ~180MB)
  extract.py       cascada de extracción (ver 6.4)
  normalize.py     moneda/FX, unit price, pack size, envío, impuestos, promo flags — TODO precio
                   (incluso de API/JSON-LD) pasa por price-parser >=0.5.1 hacia Decimal
  match/
    gtin.py        exacto GTIN/EAN/UPC (python-stdnum — ya en extra dataquality)
    fuzzy.py       RapidFuzz blocking (ya en extra dataquality)
    probabilistic.py  Splink (Fellegi-Sunter)  [SPIKE-EN-PR: wheels duckdb en Windows py3.11;
                      fallback definido: score compuesto RapidFuzz + reglas de atributos]
    adjudicate.py  desempate LLM opcional (budget cap, regla 10) → propone, nunca confirma solo
  sanity.py        QA de datos scrapeados — cuarentena (ver 6.6)
  events.py        emisión a scm_agent.events (ver 6.8)
  metrics.py       cobertura, frescura, precisión, % por tier, tasa cuarentena, costo/1k obs
config/sites/      1 YAML por dominio: ToS, robots, rate, selectores versionados, tier máximo permitido
config/markets/    1 YAML por jurisdicción: reglas de descuento/MAP (regla 12; lo consume P5)
jobs/price_intelligence.py   playbook: intake refs → adquirir → matchear → sanity → entregable
tests/test_pricing_intel*.py fixtures HTML congeladas + golden parquet + property tests
```

### 6.2 Adquisición en 4 niveles
| Tier | Fuente | Notas verificadas |
| --- | --- | --- |
| **L0 — APIs oficiales** | **MercadoLibre** (ICP LatAm — prioridad 1), Amazon SP-API `[CRED]` (des-priorizado: el ICP ES/LatAm mayormente NO es seller de Amazon), Shopify Admin `[CRED]` (precios propios) | El plan v1 omitía MELI — corregido. La meta "≥70% L0+L1" será **L1-heavy en el primer cliente real** (L0 gated por credenciales) — esto es esperado, no un fallo |
| **L1 — Datos estructurados** | JSON-LD `Product`/`Offer` de PDPs (el schema es estable porque el sitio lo necesita para SEO) | extruct 0.18.0 tras adapter + fallback propio (6.4); price-parser 0.5.1 revivido (mar 2026, py3.9–3.14) |
| **L2 — Watcher** | changedetection.io self-hosted **requests-mode** (verificado: v0.55.x, Apache-2.0, detección nativa de precio/restock, webhooks; ~100MB RAM sin JS) como segundo process-group en la misma máquina Fly | **Fallback documentado:** si la watch-list es <20 URLs, un job APScheduler con hash-diff la reemplaza y elimina un contenedor entero. JS-rendering NO va en el watcher (leak conocido de su contenedor Playwright) — una PDP con JS se atiende por L3b |
| **L3 — Spiders dedicados** | Scrapy (`ROBOTSTXT_OBEY=True`, AUTOTHROTTLE) por competidor crítico (3–5 por cliente); Playwright solo si el precio se renderiza por JS | El más frágil — el último y el más testeado; ningún selector sin fixture congelada |

**Frescura:** competidores críticos cada 2–6 h — verificado contra el mercado: los rastreadores comerciales tipo Prisync entregan diario/3x-día en tiers estándar, así que 2–6 h ES competitivo; el claim se scope-a como "frescura de web de competidores" (los repricers de marketplace vía push API son otra clase de producto — no compararse con ellos). Cola larga: diaria. Frescura por-fuente visible en el entregable (`last_seen` por dato — regla 14).

### 6.3 Modelo de datos
```python
@dataclass(frozen=True)
class CompetitorOffer:
    observed_at: datetime      # UTC
    site: str                  # dominio normalizado
    competitor_sku_ref: str    # URL o ID externo (ASIN/MLA…)
    matched_product_id: str | None   # nuestro SKU (via match/)
    match_confidence: float    # 0-1; <umbral => no entra al ledger principal
    price: Decimal; currency: str; price_normalized: Decimal  # a moneda base, unit price
    shipping: Decimal | None; availability: str  # InStock/OutOfStock/Preorder
    promo_flag: bool; list_price: Decimal | None
    acquisition_tier: str      # L0/L1/L2/L3 (procedencia, regla 7)
    extractor: str; extractor_version: str; extraction_confidence: float
```
Ledger append-only (regla 8): parquet particionado `site/fecha` + índice SQLite con última observación por par sku↔competidor. Correcciones = filas nuevas con flag.

### 6.4 Cascada de extracción (`extract.py`)
Orden estricto, cada nivel con su confianza; se detiene en el primero que produce precio válido:
1. **JSON-LD** `Offer.price`+`priceCurrency`+`availability` — 0.98. Vía `extract_product_metadata()` (adapter): extruct==0.18.0 primero (semi-dormante pero sin alternativa mejor — verificado), y **fallback propio en el mismo adapter**: `script[type="application/ld+json"]` + json.loads + chompjs para JSON malformado. El adapter aísla el swap.
2. **Microdata/RDFa/OpenGraph** (`product:price:amount`) — 0.9.
3. **Selector CSS/XPath versionado** del `SiteConfig` (con fixture congelada) — 0.8.
4. **price-parser sobre texto candidato** (nodos con símbolo de moneda cerca del título) — 0.6.
5. **Extractor LLM** (HTML podado → esquema pydantic estricto) — 0.6, budget cap diario, solo si 1–4 fallan, marcado para re-verificación determinista (regla 10).
Si todo falla ⇒ evento `extraction_failed` (jamás un precio inventado). **Un precio dudoso es peor que ningún precio.** Patrón validado externamente por PriceGhost (cascada + confianza por candidato + arbitraje humano cuando las estrategias discrepan).

### 6.5 Matching de producto (`match/`)
Pipeline con estados, no un match binario:
1. **GTIN/EAN/UPC exacto** (check-digit, python-stdnum) → `confirmed` (0.99).
2. **Blocking barato** RapidFuzz (título+marca) → candidatos.
3. **Score probabilístico**: Splink (Fellegi-Sunter) sobre título/marca/atributos. **Spike obligatorio al inicio del PR-14:** wheels de splink/duckdb en Windows py3.11; si falla → fallback YA especificado: score compuesto RapidFuzz por campo + reglas de atributos (talla/pack/modelo) calibrado contra el set etiquetado.
4. **Adjudicación LLM opcional** franja 0.5–0.85 (mismo/distinto/variante + razón) → propone, nunca confirma solo.
5. **Revisión humana T2**: `sku_map` versionada con estados `confirmed / suspect / rejected` + quién/qué confirmó.

**Set etiquetado (PR-14, presupuestado desde el día 1):** candidato = WDC Products (Web Data Commons — pares etiquetados con GTIN) `[VERIFICAR-EN-PR: disponibilidad/licencia]` + 200–500 pares del primer vertical real etiquetados a mano. Meta: precisión ≥95% en `confirmed`.
**Invariante QA:** solo `confirmed` (o ≥0.9) alimenta P2/A5. Los `suspect` aparecen en sección aparte del entregable, marcados.
**El modo one-shot esquiva el cuello de botella:** cuando el cliente entrega el mapping URL↔SKU (refs CSV), esas refs SON matches confirmados por definición — el Diagnóstico vendible (§10) no espera al PR-14.

### 6.6 Sanidad de datos (`sanity.py`)
Reglas de cuarentena (todas con test):
- Precio ≤ 0, moneda desconocida, availability contradictoria ⇒ descarta con evento.
- |Δ| > 40% intradía sin `promo_flag` ⇒ cuarentena hasta segunda lectura confirmatoria (≤1 h).
- Outliers por MAD sobre ventana de 30 días del par sku↔competidor ⇒ cuarentena.
- Staleness: par crítico sin observar en 2× su SLA ⇒ evento `stale_feed` (el dato viejo se marca, no se borra).
- Sospecha de bloqueo (403/429/captcha/DOM vacío/precio idéntico semanas) ⇒ circuit breaker, degradar tier (L3→L2), evento `site_degraded`. Degradar, nunca evadir.

### 6.7 Cumplimiento por dominio
`config/sites/<dominio>.yaml` obliga: robots.txt respetado (sí/no + fecha), resumen ToS y decisión (permitido/limitado/prohibido), rate acordado, PII ("ninguna"). Sin YAML aprobado el fetcher no corre — mismo patrón del QA gate. Amazon jamás por HTML (SP-API existe para eso).

### 6.8 Eventos que emite → A1
`price_move(sku, competitor, old, new, %)` · `competitor_oos(sku)` · `promo_detected` · `map_violation` (solo mercados donde aplique — ver P5) · `new_competitor_listing` · `extraction_failed` / `site_degraded` / `stale_feed` (salud del titán).

### 6.9 Registro como capacidad
1. `jobs/price_intelligence.py`: intake refs (CSV URLs/IDs del cliente o descubrimiento asistido) → adquirir → matchear → sanity → entregable.
2. Invariantes en `jobs/qa.py` siguiendo el patrón real del repo (`verify_price_intel(report) -> list[str]` + `price_intel_passed()`): cobertura mínima (≥60% SKUs con ≥1 competidor confirmado), 0 filas de cuarentena en el entregable, frescura media dentro de SLA.
3. Entregables: `price_position_matrix.xlsx` + `report.md` con narrativa + **Fuentes** (procedencia por dato, gate E5) + `ledger_export.csv` — pasando por E4 (es/en) y E6 (branding).
4. `register()` en `build_default_registry()` (tool 38) + intent multi-palabra ("monitorea precios de la competencia", "donde estoy caro").
5. CLI `examples/run_price_intel.py --refs competitors.csv --client "Acme"` + tests.

### 6.10 Tests del titán
Fixtures HTML congeladas por sitio (goldens de extracción); property tests del normalizador con fixtures por locale (MXN "1.234,56", BRL "R$ 1.234,56", CLP "12.345", USD "$1,234.56", EUR "1.234,56 €"); golden parquet del ledger; contrato del protocolo Fetcher; simulacro de bloqueo (403→breaker→degradación); E2E del playbook contra sitio sintético servido por FastAPI en el test.

### 6.11 Métricas del titán (dashboard, tab Pricing)
% SKUs con ≥1 competidor `confirmed` · frescura media por tier · precisión de extracción (muestreo semanal vs lectura manual) · % observaciones por tier (meta ≥70% L0+L1 — **esperar L1-heavy hasta tener cliente con credenciales**) · tasa de cuarentena · costo por 1,000 observaciones.

## 7. Pricing P2–P5

| Módulo | Archivos | Núcleo (corregido con investigación) | QA invariantes |
| --- | --- | --- | --- |
| P2 `price_optimization` | `src/elasticity_batch.py`, `src/price_optimizer.py` — **extienden `src/pricing.py` existente** (`estimate_elasticity`/`ElasticityFit` ya operativos a nivel SKU) | Batch per-SKU log-log con **statsmodels** (coeficiente + IC out of the box) + **shrinkage empirical-Bayes hacia la elasticidad de categoría** (James-Stein/precision-weighted, ~50 líneas numpy) para SKUs con poca historia. **pymc-marketing ELIMINADO del plan** — verificado: no trae modelo de elasticidad de precio (es MMM/CLV) y su cadena pymc/pytensor es frágil en Windows py3.11 (g++ no detectado → sampling puro-Python). Bayesiano jerárquico real = extra opcional `[bayes]` con pymc crudo + caveat de toolchain documentado | Precio propuesto ≥ costo landed; elasticidad con IC que no cruce 0 para mover precio; sin señal ⇒ `needs_data`, no un número inventado (`ElasticityFit.identified` ya existe) |
| P5 `pricing_guardrails` | `src/pricing_guardrails.py` + `config/markets/*.yaml` (regla 12) | **Una primitiva de cumplimiento:** historia de precios propios append-only (SKU×mercado×canal×ts) + `prior_price_30d_lowest()` + gate pre-descuento. **UE (verificado):** Art. 6a Directiva 98/6/CE (Omnibus) + CJEU C-330/23 *Aldi Süd* — el % de descuento se CALCULA contra el mínimo de 30 días, no solo se muestra → gate duro (bloquea). **CL/MX/CO (verificado):** sin ventana codificada, pero SERNAC (Ley 19.496 arts. 28/35), PROFECO (LFPC, precios inflados pre-Buen Fin) y SIC (Ley 1480) exigen precio de referencia real → gate blando (warn + evidencia); el mismo log sirve de expediente de defensa en las 4 jurisdicciones. **MAP (verificado):** US legal como política unilateral (doctrina Colgate) → SOLO observar-y-alertar, jamás workflows de acuerdo/acknowledgment con retailers ni negociación (eso lo volvería vertical agreement); en UE/UK el MAP es RPM (restricción hardcore) → la misma señal se re-etiqueta "dispersión de precios / inteligencia de canal" y se suprime el lenguaje de 'violación' | Gate central: changeset sin explicación legible + citas ⇒ no sale. Todo cambio de precio registra: prior price 30d, base del %, ventana de campaña, excepción invocada |
| P3 `repricing_multichannel` | `src/connectors/{shopify,meli,odoo}_prices.py` `[CRED]` + `jobs/repricing.py` | Changeset de precios (dry-run) → safe-staging (writeback.py existente) → apply por canal → verificación post-apply (releer canal) | Todo cambio pasa P5 antes del staging; apply sin verificación = incidente |
| P4 `promo_liquidation` v2 | extiende `src/liquidation.py` + `jobs/markdown_liquidation_job.py` **(YA EXISTEN — PR #124)** | Añade: calendario de liquidación con recuperación esperada de caja, señal competitiva del titán (no liquidar por debajo del competidor sin razón citada), y validación Omnibus vía P5 | Descuento propuesto pasa `prior_price_30d_lowest()` del ledger propio |

## 8. Track B — SEO (S1–S5, dentro de 3.0 por decisión del operador)

| Módulo | Archivos | Notas |
| --- | --- | --- |
| S4 `inventory_aware_seo` — **primero** | `jobs/seo_priority.py` | Cruce `abc_xyz` + `excess_obsolete` + forecast; plan mensual 301/push/cut. **Cero deps nuevas** — por eso abre la fase |
| S1 `seo_audit` | `src/seo/crawl_audit.py` (advertools + extruct + Lighthouse CLI vía subprocess) + `jobs/seo_audit.py` | Reusa el adapter extruct de 6.4 |
| S2 `schema_feeds` | `src/seo/schema_gen.py` (JSON-LD desde catálogo + stock del estado del sistema), `feeds.py`, `llms_txt.py` | Simetría con el titán: nosotros LEEMOS JSON-LD de competidores; al cliente le GENERAMOS el suyo |
| S3 `pdp_content` | `src/seo/pdp_writer.py` | Esquema estricto de ficha verificable; QA: cada afirmación mapea a un campo del catálogo (regla 10) |
| S5 `geo_visibility` | `src/seo/geo_probe.py` | Sondas de citación en motores AI, share of voice |

## 9. Webapp y consola

Nuevas rutas: `GET /api/events` · `POST /api/approvals/{id}` (TTL un click) · `GET /api/price-position/{sku}` · webhook `POST /api/watch` (changedetection.io). Tabs nuevos: **Pricing** (posición vs competidores, frescura, cuarentena) y **Tower** (eventos, T1 ejecutadas, pendientes T2, confiabilidad A4). Mismo patrón sin build step.
**Reuso de la maquinaria 2.0 (obligatorio, regla 13):** el lead magnet **"scan de posición de precios" gratis** (N URLs de competidores → matriz teaser) replica el funnel demo-scan E2 ya desplegado y alimenta `leads.jsonl`/E8 metrics; entregables por E4 (es/en) y E6 (Branding).

## 10. Comercialización (nueva sección — el plan v1 violaba la regla "ningún tool suelto se vende")

Verificado contra `documentation/paquetes/` + MONETIZATION_BRIEF: hoy existen 8 ofertas y **ninguna** cubre inteligencia de precios de competidores → capacidad neta nueva.
1. **Oferta #9 — "Diagnóstico de Posición de Precios"** (one-shot, molde del Diagnóstico de Arranque): cliente entrega lista SKU + URLs/IDs de competidores; sprint 2 semanas; **USD 2,000–3,500**; entregable = `price_position_matrix.xlsx` + reporte con procedencia por dato + ledger. Se vende con PR-13 (modo one-shot, refs del cliente).
2. **Add-on de monitoreo continuo** (Growth/Scale): titán en modo continuo + eventos al Tower + digest. Precio como add-on mensual; se vende cuando PR-15 exista y haya Tower.
3. **Lead magnet:** scan gratis de 3–5 URLs → matriz teaser (funnel E2).
4. **La misma PR que shippea la capacidad actualiza `documentation/paquetes/` (one-pager #9) y MONETIZATION_BRIEF** — regla del brief: ninguna sección vende un tool suelto.

## 11. `pyproject` — extras nuevos (todo con fallback; pins verificados 2026-07)

```toml
pricing-intel = ["scrapy>=2.17,<3", "extruct==0.18.0", "price-parser>=0.5.1",
                 "chompjs>=1.2", "httpx>=0.28", "pandera>=0.32"]
                 # rapidfuzz y python-stdnum YA están en el extra dataquality — no duplicar
matching      = ["splink>=4"]        # spike Windows en PR-14; fallback RapidFuzz+reglas ya especificado
browser       = ["playwright>=1.61"] # + `playwright install chromium --only-shell` (~180MB)
repricing     = ["ShopifyAPI>=12.7", "python-amazon-sp-api>=2.1"]   # [CRED]
bayes         = ["pymc>=5"]          # opcional; caveat g++/MSVC en Windows documentado
tower         = ["APScheduler>=3.11.3,<4"]   # apprise ELIMINADO (webhook plano); slack-sdk solo si PR-7 necesita threads
seo           = ["advertools>=0.14", "pyarrow>=15"]
balance       = ["ortools>=9.15", "hierarchicalforecast>=1.5"]
```
P2 usa statsmodels (evaluar si entra al core o a un extra `elasticity` liviano — decisión en PR-16 según peso).

## 12. Secuencia de PRs (orden Tower-first; cada uno shippeable con tests verdes)

**Fase F0 — Base común**
| PR | Alcance | Criterio de aceptación (verificación concreta) |
| --- | --- | --- |
| 1 | `src/state/` + tests (rama `feat/state-snapshot-module` ya existe) | round-trip snapshot→latest→history; esquema inválido rechaza; append-only probado |
| 2 | `scm_agent/events.py` + ledger idempotente | evento duplicado (mismo dedup_key en ventana) no re-emite |
| 3 | `jobs/scheduler.py` (APScheduler in-process) + `notify()` webhook + digest | ciclo corre one-shot en CI sin daemon (regla 9); digest pasa QA gate |
| 4 | `config/event_routing.yaml` + `event_intent.py` | evento sintético `stock_below_rop` → `inventory_optimization` → entregable QA-gated + notificación |

**Fase A — Tower core**
| PR | Alcance | Criterio |
| --- | --- | --- |
| 5 | `monitors.py` + `config/monitors.yaml` (generaliza alerting.py) | 5 monitores emitiendo con dedup sobre dataset sintético |
| 6 | `autonomy.py` T1/T2/T3 (extiende writeback/guided/escalation) | T1 en banda se auto-ejecuta y audita; fuera de banda escala a T2 con Approval TTL |
| 7 | tab Tower + `GET /api/events` + `POST /api/approvals/{id}` | aprobación un-click aplica un changeset staged; HTTP guards testeados |
| 8 | `src/verify/` (A4: backtest + reliability) | reporte de confiabilidad por tool reproducible; recalibración de σ_e demostrada en backtest |
| 9 | promoción T2→T1 guiada por A4 (regla 11) | N ciclos consecutivos precisión ≥ umbral → PROPUESTA de promoción con evidencia; aplicación = changeset aprobado |

**Fase B — Titán** (el Atajo de Revenue §3.1 permite ejecutar 10-slim→11→12→13 fuera de orden ante un deal)
| PR | Alcance | Criterio |
| --- | --- | --- |
| 10 | `pricing_intel/models.py` + `ledger.py` | golden parquet estable; append-only probado |
| 11 | `structured.py` (L1) + `extract.py` cascada + `normalize.py` | fixtures de 10 sitios reales extraen precio correcto; fallback ld+json propio cubre extruct caído; property tests multi-locale |
| 12 | `sanity.py` + `config/sites/` gate + breaker básico | simulacro de bloqueo degrada sin caerse; cuarentena funciona; dominio sin YAML se niega a correr |
| 13 | tool `price_intelligence` + playbook + CLI + tab Pricing + **paquete #9 + lead magnet** | brief "donde estoy caro" → matriz + reporte con procedencia E5, lang E4, branding E6; one-pager #9 commiteado. **HITO VENDIBLE** |
| 14 | `match/` completo + `sku_map` versionada + set etiquetado | spike splink Windows resuelto (o fallback activado); precisión ≥95% en `tests/fixtures/matching_labeled.csv` |
| 15 | L0 MELI + L2 watcher + monitoreo continuo programado (usa F0) + eventos §6.8 al Tower | par sku↔competidor observado en ciclo programado emite `price_move` ruteado por A2 |

**Fase C — Pricing avanzado + S&OP**
| PR | Alcance | Criterio |
| --- | --- | --- |
| 16 | P2: `elasticity_batch.py` + `price_optimizer.py` (statsmodels + EB shrinkage) | ejemplo numérico de referencia reproducible; `needs_data` sin señal; shrinkage demostrado (SKU corto hereda categoría) |
| 17 | P5: `pricing_guardrails.py` + `config/markets/` + `prior_price_30d_lowest()` | changeset sin explicación no sale; gate UE bloquea % mal calculado (caso Aldi Süd en test); CL/MX/CO warn con evidencia |
| 18 | P3: conectores repricing `[CRED]` + safe-staging | dry-run→approve→apply→verificación post-apply contra stand-in offline (patrón InMemoryOdoo) |
| 19 | P4 v2: calendario sobre liquidation.py existente + señal competitiva + Omnibus | calendario con recuperación esperada; descuento valida contra ledger propio |
| 20 | A5: `sop_engine/` v1 secuencial | plan integrado con ≥3 checks de coherencia citables; el solver CP-SAT NO entra en v1 |

**Fase D — SEO**
| PR | Alcance | Criterio |
| --- | --- | --- |
| 21 | S4 `seo_priority` (cero deps) | plan mensual 301/push/cut cruzando abc_xyz+excess+forecast |
| 22 | S1 `seo_audit` | auditoría entrega top-20 issues accionables |
| 23 | S2 `schema_feeds` + `llms_txt` | JSON-LD generado válido en Rich Results test |
| 24 | S3 `pdp_content` | PDP 100% verificable contra catálogo (regla 10) |
| 25 | S5 `geo_visibility` | sondas de citación con share of voice reproducible |

## 13. Definition of done (global)

Cada módulo: función pura en `src/` + playbook + invariantes QA (patrón `verify_*`/`*_passed` real de `jobs/qa.py`) + entregable con Fuentes (gate E5) en es/en (E4) con branding (E6) + tests de referencia + entrada en el knowledge graph + sección en el README correspondiente. El titán además: `config/sites/` aprobado por dominio, métricas §6.11 en dashboard, y expectativa honesta de mix por tier (L1-heavy sin credenciales). La capacidad no está "done" hasta que su paquete comercial (§10) esté commiteado.

## 14. Riesgos de implementación

1. **Matching es el cuello real** (no el fetching): set etiquetado presupuestado desde el día 1 (PR-14); el modo one-shot vendible NO depende de él (refs del cliente = confirmadas).
2. **Selectores L3 se pudren:** cascada JSON-LD-first + breaker; ningún selector sin fixture congelada.
3. **extruct semi-dormante** (último release nov-2024): aislado tras adapter con fallback propio ld+json (PR-11); si extruct rompe en py3.13+, el fallback cubre el 90%.
4. **Splink en Windows:** spike al inicio del PR-14 con fallback completamente especificado — el PR no se bloquea.
5. **Tentación del solver global (A5):** v1 secuencial con checks; CP-SAT espera a que A4 acredite v1.
6. **LLM en el camino de datos:** regla 10 — esquema estricto, budget cap, procedencia, re-verificación.
7. **Persistencia en Fly:** máquina única + volumen = riesgo aceptado explícitamente con Litestream→Tigris como recuperación (RPO minutos); los snapshots de Fly no son backup.
8. **Credenciales:** todo `[CRED]` se construye offline-first contra stand-in; no pedir credenciales hasta que exista el cliente (doctrina del repo).
9. **Sesiones concurrentes en el repo:** re-verificar `git status` + HANDOFF de main antes de finalizar cualquier PR (colisiones aparecen a mitad de tarea).

## 15. Fuentes

**Repo (verificado en este diseño):** `CLAUDE.md` · `HANDOFF.md` (main, 2026-07-11) · `scm_agent/tools.py` (37 tools) · `src/pricing.py` (elasticidad existente) · PR #124 (`src/liquidation.py`) · `src/alerting.py` · `src/writeback.py` · `jobs/qa.py` · `documentation/MONETIZATION_BRIEF.md` · `documentation/ACQUISITION_PLAYBOOK.md` · `documentation/paquetes/`. La cita a `PITCH_AGENCIA_LINCHPIN.md` del v1 se elimina (archivo inexistente en el repo).
**Investigación externa (dossier 2026-07-12, 4 investigadores):**
- Ops: [APScheduler PyPI](https://pypi.org/project/APScheduler/) (4.0 alpha; 3.11.3 estable) · [Fly volumes](https://fly.io/docs/volumes/overview/) · [Litestream 0.5](https://github.com/benbjohnson/litestream/releases) · [Fly pricing](https://fly.io/docs/about/pricing/)
- Scraping: [changedetection.io](https://github.com/dgtlmoon/changedetection.io) (v0.55.x, Apache-2.0) · [Scrapy](https://pypi.org/project/Scrapy/) (2.17, jul-2026) · [extruct](https://github.com/scrapinghub/extruct) (0.18.0) · [price-parser](https://pypi.org/pypi/price-parser/json) (0.5.1, mar-2026) · [Playwright](https://pypi.org/pypi/playwright/json) (1.61) · [PriceGhost](https://github.com/clucraft/PriceGhost) (MIT — cascada validada) · [PriceBuddy](https://github.com/jez500/pricebuddy) (schema de scrape-rules, estudiar)
- Pricing science: [pymc-marketing](https://github.com/pymc-labs/pymc-marketing) (sin módulo de elasticidad de precio) · [pytensor g++ Windows](https://github.com/pymc-devs/pymc/issues/6562) · [tensor-house](https://github.com/ikatsov/tensor-house) (corpus de referencia) · [pricewars](https://github.com/hpi-epic/pricewars) (harness de backtest de repricing)
- Compliance: [Directiva 98/6/CE Art. 6a + guía 2021/C 526/02](https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:52021XC1229(06)) · CJEU C-330/23 *Aldi Süd* · [SERNAC](https://www.sernac.cl/portal/604/w3-article-56315.html) · [PROFECO LFPC](https://www.profeco.gob.mx/juridico/pdf/l_lfpc_ultimo_libro.pdf) · [SIC Colombia](https://www.sic.gov.co/inconvenientes-con-el-precio) · doctrina Colgate (MAP US)
- SLA de mercado: [Prisync](https://prisync.com/repricing-software/) (diario/3x-día estándar) · [Repricer.com](https://www.repricer.com/) (push API ≠ scraping — otra clase)

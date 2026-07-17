# Plan de Arquitectura: Kern Agency Platform

## Estado Actual (lo que existe)

| Capa | Estado | Archivos clave |
|---|---|---|
| **Engine** (análisis puro) | Maduro — 148 módulos | `src/eoq.py`, `src/safety_stock.py`, `src/forecasting.py` |
| **Jobs** (data prep) | Maduro — 61 jobs | `jobs/*_job.py` |
| **Orquestador** (agente) | Maduro | `scm_agent/orchestrator.py`, `tools.py`, `intent.py`, `llm.py` |
| **Webapp** (FastAPI) | Funcional pero single-tenant | `webapp/app.py` (1120+ líneas) |
| **MCP Server** (API externa) | Read-only, 33 tools | `webapp/mcp_server.py`, `mcp_auth.py`, `mcp_keys.py` |
| **Writeback** (escritura segura) | Maduro — 384 líneas | `src/writeback.py`, `src/writeback_store.py` |
| **Guided Execution** | Maduro — nunca dead-end | `src/guided.py`, `scm_agent/guided_bridge.py` |
| **Perfiles cliente** | Filesystem, single-writer | `src/client_profile.py` (293 líneas) |
| **Autonomía** (T1/T2/T3) | Config-driven | `config/autonomy.yaml`, `config/event_routing.yaml` |
| **Seguridad** | Básica — API key + rate limit | `webapp/security.py` (178 líneas) |
| **Conectores** | Odoo bidireccional, Excel bidireccional | `src/connectors/odoo.py` (785 líneas), `excel.py` |
| **CI/CD** | Existe — tests + cron price-watch | `.github/workflows/tests.yml`, `price-watch-cron.yml` |

## Lo que NO existe (gap crítico para agencia)

| Capa faltante | Impacto | Prioridad |
|---|---|---|
| **Cola de trabajos** | No hay gestión de carga concurrente — `asyncio.to_thread` sincrónico por request | P0 |
| **Aislamiento multi-tenant** | No hay base de datos — filesystem con `clients/` dir | P0 |
| **RBAC por herramienta** | Cualquier API key llama a cualquier endpoint | P0 |
| **Dashboard de operador** | No hay UI para gestionar clientes, ver uso, aprobar writebacks | P1 |
| **Registro de uso** (metering) | No hay logging de consumo para facturación | P1 |
| **SLA tracking** | No hay monitoreo de tiempos de respuesta/error rates por cliente | P1 |
| **Cola de notificaciones** | `jobs/notify.py` existe pero no hay sistema de colas real | P2 |

---

## Arquitectura Propuesta (4 capas)

```
┌─────────────────────────────────────────────────────────────┐
│                    CAPA 4: OPERATOR                          │
│  Dashboard UI · Client Management · Writeback Approvals     │
│  Usage Metering · SLA Monitoring · Billing Integration      │
├─────────────────────────────────────────────────────────────┤
│                    CAPA 3: AGENCY GATEWAY                    │
│  Queue System (Redis/Celery) · RBAC · Tenant Isolation     │
│  Rate Limiting · SLA Tracking · Usage Logging               │
├─────────────────────────────────────────────────────────────┤
│                    CAPA 2: AGENT ENGINE                      │
│  Orchestrator · Intent → Tool → QA → Deliver                │
│  Writeback Safety Plane · Guided Execution · Autonomy       │
│  MCP Server (read-only) · Per-client Profiles               │
├─────────────────────────────────────────────────────────────┤
│                    CAPA 1: ANALYTICAL CORE                   │
│  148 módulos puros (EOQ, safety stock, forecasting...)      │
│  61 jobs (data prep) · Knowledge Graph (25 sources)         │
│  Conectores (Odoo, Excel, Shopify, Meli)                    │
└─────────────────────────────────────────────────────────────┘
```

### Capa 1: Analytical Core (ya existe — no tocar)

- 148 módulos puros en `src/`
- 61 jobs en `jobs/`
- Knowledge graph en `knowledge/`
- Conectores en `src/connectors/`
- **Estado**: Maduro, bien testeado (1100+ tests en CI, py 3.11–3.13)

### Capa 2: Agent Engine (ya existe — extender)

- Orquestador, intent, tools, QA, deliver
- Writeback safety plane con HMAC approvals
- Guided Execution (nunca dead-end)
- MCP Server read-only (33 tools)
- Perfiles cliente (filesystem)
- **Gap**: Single-tenant, sync, sin cola

### Capa 3: Agency Gateway (NUEVA — construir)

**Componentes:**

#### 3.1 Job Queue
- Redis + Celery o ARQ (async)
- Cola por cliente con prioridad
- Timeout por tool (T1: 30s, T2: 120s, T3: humano)
- Retry con backoff exponencial
- Dead letter queue para fallos

#### 3.2 Tenant Isolation
- PostgreSQL o SQLite persistente
- `tenants` table (id, name, api_key_hash, plan, created_at)
- `jobs` table (id, tenant_id, tool, status, result, created_at)
- `usage` table (tenant_id, tool, tokens_used, cost, timestamp)
- `approvals` table (tenant_id, changeset_id, status, approved_at)

#### 3.3 RBAC por Tool
- `tenant_tools` table (tenant_id, tool_name, allowed, quota)
- Middleware que verifica `tenant → tool → allowed` antes de ejecutar
- Plan tiers: Basic (10 tools), Pro (25 tools), Enterprise (40 tools)

#### 3.4 SLA Tracking
- `sla_metrics` table (tenant_id, tool, p50, p95, p99, error_rate)
- Alertas cuando p95 > threshold (configurable por tool)
- Dashboard de SLA por cliente

#### 3.5 Usage Metering
- Log de cada tool call: tenant, tool, tokens_in, tokens_out, cost, duration
- Agregación diaria para billing
- Export a Stripe/InvoiceNinja

### Capa 4: Operator Dashboard (NUEVA — construir)

**Componentes:**

#### 4.1 Client Management
- Crear/editar/deshabilitar clientes
- Asignar tools por plan
- Ver uso y SLA por cliente

#### 4.2 Writeback Approvals
- Cola de approvals pendientes (irreversible writes)
- Aprobar/rechazar con justificación
- Historial de approvals

#### 4.3 Usage & Billing
- Dashboard de consumo por cliente
- Export a CSV/JSON para facturación
- Integración con Stripe (futuro)

#### 4.4 SLA Dashboard
- Métricas en tiempo real por tool
- Alertas de SLA breach
- Historial de uptime

---

## Tests Serios a Probar (antes de construir)

### 1. Aislamiento Multi-Tenant (CRÍTICO)

```python
# tests/test_tenant_isolation.py

def test_tenant_a_cannot_see_tenant_b_jobs():
    """Tenant A no puede ver jobs de Tenant B"""

def test_tenant_a_cannot_approve_tenant_b_writebacks():
    """Tenant A no puede aprobar writebacks de Tenant B"""

def test_tenant_a_rate_limit_does_not_affect_tenant_b():
    """Rate limit de Tenant A no afecta a Tenant B"""
```

### 2. RBAC por Tool (CRÍTICO)

```python
# tests/test_rbac.py

def test_basic_tenant_cannot_call_enterprise_tool():
    """Tenant con plan Basic no puede llamar tool Enterprise"""

def test_revoked_tool_access_returns_403():
    """Acceso revocado a tool retorna 403"""

def test_tool_quota_enforcement():
    """Quota de tool se aplica por tenant"""
```

### 3. Cola de Trabajos (CRÍTICO)

```python
# tests/test_job_queue.py

def test_concurrent_jobs_different_tenants():
    """Jobs de diferentes tenants corren en paralelo"""

def test_job_timeout_per_tier():
    """T1 timeout 30s, T2 timeout 120s, T3 requiere humano"""

def test_dead_letter_queue():
    """Jobs fallidos van a dead letter queue"""

def test_retry_with_backoff():
    """Retry con backoff exponencial"""
```

### 4. SLA Tracking (IMPORTANTE)

```python
# tests/test_sla_tracking.py

def test_sla_metrics_collected():
    """Métricas SLA se recolectan por tool y tenant"""

def test_sla_breach_alert():
    """Alerta cuando p95 > threshold"""

def test_sla_report_generation():
    """Reporte SLA se genera por tenant"""
```

### 5. Usage Metering (IMPORTANTE)

```python
# tests/test_usage_metering.py

def test_usage_logged_per_tool_call():
    """Cada tool call registra uso"""

def test_daily_aggregation():
    """Agregación diaria funciona correctamente"""

def test_usage_export_csv():
    """Export a CSV funciona"""
```

### 6. Writeback con Multi-Tenant (CRÍTICO)

```python
# tests/test_writeback_multi_tenant.py

def test_writeback_approval_per_tenant():
    """Approval de writeback es por tenant"""

def test_writeback_rollback_per_tenant():
    """Rollback de writeback es por tenant"""

def test_writeback_audit_trail_per_tenant():
    """Audit trail de writeback es por tenant"""
```

### 7. End-to-End (CRÍTICO)

```python
# tests/test_e2e_agency.py

def test_full_job_lifecycle():
    """Job completo: submit → queue → execute → deliver → meter"""

def test_multi_client_concurrent():
    """Múltiples clientes concurrentes"""

def test_failover_on_tool_error():
    """Failover cuando tool falla"""
```

---

## Orden de Construcción (fase por fase)

### Fase 1: Base (2-3 semanas)

1. `src/tenant.py` — Modelo de tenant (PostgreSQL o SQLite)
2. `src/job_queue.py` — Cola de trabajos (Redis + Celery o ARQ)
3. `src/rbac.py` — RBAC por tool
4. `src/metering.py` — Usage logging
5. Tests de aislamiento y RBAC

### Fase 2: Gateway (2-3 semanas)

1. `webapp/api_gateway.py` — Middleware de tenant/rate-limit/SLA
2. `webapp/usage_api.py` — API de usage y SLA
3. Integración con orquestador existente
4. Tests end-to-end

### Fase 3: Dashboard (3-4 semanas)

1. `webapp/dashboard/` — UI de operador
2. Client management CRUD
3. Writeback approval queue
4. Usage & SLA dashboard

### Fase 4: Billing (1-2 semanas)

1. Integración con Stripe
2. Export de usage para facturación
3. Alertas de quota

---

## Riesgos Técnicos

| Riesgo | Mitigación |
|---|---|
| SQLite no escala para multi-tenant | Migrar a PostgreSQL en Fase 1 |
| Redis single point of failure | Redis Sentinel o managed (Upstash, Redis Cloud) |
| Worker OOM (512MB Fly.io) | Subir a 1GB o mover a Railway/Render |
| Writeback HMAC key rotation | `LINCHPIN_APPROVAL_SECRET` ya soporta rotación |
| Perfil cliente en filesystem | Migrar a PostgreSQL en Fase 1 |

---

## Métricas de Éxito

- **P95 latency**: < 30s para T1 tools, < 120s para T2
- **Error rate**: < 1% por tool
- **Tenant isolation**: 0 cross-tenant data leaks (testeados)
- **SLA uptime**: 99.5% por cliente
- **Usage accuracy**: 100% de tool calls registrados

---

*Plan generado el 2026-07-17. Basado en análisis completo del repo Kern (supply-chain-optimization).*

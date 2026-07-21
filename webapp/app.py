"""FastAPI backend for the Inventory Planner — a thin layer over the engine.

All numbers come from src/ (forecasting, policies, constraints). The frontend is
a single static page; this app exposes the portfolio computation and serves it.

Run:
    py -m uvicorn webapp.app:app --reload      # from the repo root
    # then open http://localhost:8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path

# Make `src` importable no matter where uvicorn is launched from.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd  # noqa: E402
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Query, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from jobs.price_monitor import accept_observation  # noqa: E402
from jobs.scheduled_jobs import PRODUCTION_JOB_IDS, production_registry  # noqa: E402
from scm_agent import Orchestrator  # noqa: E402
from scm_agent.autonomy import DEFAULT_PATH as AUTONOMY_DEFAULT_PATH  # noqa: E402
from scm_agent.autonomy import (  # noqa: E402
    STATUS_AUTO_EXECUTED,
    STATUS_PENDING,
    AutonomyLedger,
    AutonomyRecord,
    acknowledge_pending,
)
from scm_agent.autonomy_promotion import (  # noqa: E402
    PromotionLedger,
    PromotionRecord,
    approve_promotion,
    reject_promotion,
)
from scm_agent.event_intent import DEFAULT_ROUTING_PATH as EVENT_ROUTING_DEFAULT_PATH  # noqa: E402
from scm_agent.events import DEFAULT_PATH as EVENTS_DEFAULT_PATH  # noqa: E402
from scm_agent.events import Event, EventLedger  # noqa: E402
from scm_agent.monitors import EVENT_COMPETITOR_PRICE_MOVE, run_all_monitors  # noqa: E402
from src.constraints import InventoryItem, allocate_under_budget  # noqa: E402
from src.forecasting import ForecastResult, forecast_demand  # noqa: E402
from src.mcp_keys import DEFAULT_PATH as MCP_KEYS_DEFAULT_PATH  # noqa: E402
from src.mcp_keys import McpKeyStore  # noqa: E402
from src.policies import continuous_review_sq, periodic_review_rs  # noqa: E402
from src.pricing_intel.acquire.watcher import ChangeDetectionWebhookError, parse_changedetection_webhook  # noqa: E402
from src.pricing_intel.ledger import DEFAULT_BASE_PATH as PRICE_LEDGER_DEFAULT_PATH  # noqa: E402
from src.pricing_intel.ledger import PriceLedger  # noqa: E402
from src.pricing_intel.match.sku_map import DEFAULT_BASE_PATH as SKU_MAP_DEFAULT_PATH  # noqa: E402
from src.pricing_intel.match.sku_map import SkuMap  # noqa: E402
from src.sources import CsvDemandSource  # noqa: E402
from src.state.store import DEFAULT_BASE_PATH as STATE_STORE_DEFAULT_PATH  # noqa: E402
from src.state.store import StateStore  # noqa: E402
from warehouse.generator import generate_layout  # noqa: E402
from warehouse.html_export import to_html  # noqa: E402
from warehouse.qa import validate as validate_layout  # noqa: E402
from webapp import demo_price_scan, demo_scan, observability, security  # noqa: E402
from webapp.decisions import router as decisions_router  # noqa: E402
from webapp.mcp_auth import McpKeyAuthMiddleware  # noqa: E402
from webapp.mcp_server import build_mcp_server  # noqa: E402
from webapp.offers import OFFERS, get_offer  # noqa: E402
from webapp.one_plan_page import render_one_plan_html  # noqa: E402
from webapp.operator_profile import get_operator_profile  # noqa: E402
from webapp.paquetes_page import render_index_html, render_offer_html  # noqa: E402
from webapp.pricing_page import render_pricing_html  # noqa: E402
from webapp.pricing_quote import router as pricing_quote_router  # noqa: E402
from webapp.stocky_alternative_page import render_stocky_alternative_html  # noqa: E402
from webapp.tower_page import T1_DISPLAY_LIMIT, render_tower_html  # noqa: E402

DATA_FILE = Path(
    os.environ.get("LINCHPIN_PORTFOLIO_DATA_FILE", "").strip()
    or (_REPO_ROOT / "data" / "sample_demand_portfolio.csv")
)
SAMPLE_STOCK_FILE = _REPO_ROOT / "data" / "sample_stock_snapshot.csv"
# Lead mini-reports + follow-up drafts (operator-facing; gitignored). On a cloud
# deploy point this at the persistent volume (e.g. /data/leads) or it is ephemeral.
LEAD_REPORTS_DIR = Path(
    os.environ.get("LINCHPIN_LEAD_REPORTS_DIR", "").strip()
    or (_REPO_ROOT / "deliverables" / "leads")
)
STATIC_DIR = Path(__file__).resolve().parent / "static"
OPERATOR_DOCS_DIR = _REPO_ROOT / "documentation" / "operator"
PAQUETES_DOCS_DIR = _REPO_ROOT / "documentation" / "paquetes"
JOBS_OUTPUT_DIR = _REPO_ROOT / "webapp" / "_jobs_output"
JOBS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LEADS_DIR = _REPO_ROOT / "webapp" / "_leads"
LEADS_DIR.mkdir(parents=True, exist_ok=True)
LEADS_FILE = LEADS_DIR / "leads.jsonl"  # one JSON object per captured demo lead (PII; gitignored)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # cap /api/jobs uploads at 25 MB
JOBS_TTL_SECONDS = 3600  # per-job output dirs older than this are swept on the next request
PERIODS_PER_YEAR = 52.0
MAX_LEAD_PERIODS = 52.0

_ORCHESTRATOR: Orchestrator | None = None
_MCP_KEY_STORE: McpKeyStore | None = None
MCP_KEYS_PATH = os.environ.get("LINCHPIN_MCP_KEYS_PATH", "").strip() or MCP_KEYS_DEFAULT_PATH
# Both already honor their own LINCHPIN_EVENTS_PATH/LINCHPIN_AUTONOMY_PATH env
# vars via scm_agent.events.DEFAULT_PATH / scm_agent.autonomy.DEFAULT_PATH -
# referenced here (not re-read from os.environ) so there is one source of
# truth. Named as module attributes (not inlined at each ledger-constructing
# call site) so tests can monkeypatch them the same way MCP_KEYS_PATH already
# is - a bare EventLedger()/AutonomyLedger() call binds its default `path`
# argument once at import time, which a test's monkeypatch.setenv(...) run
# AFTER that import can never retroactively change.
EVENTS_LEDGER_PATH = EVENTS_DEFAULT_PATH
AUTONOMY_LEDGER_PATH = AUTONOMY_DEFAULT_PATH
# Same monkeypatch-friendly convention, for the one real file
# api_approve_promotion() mutates (Linchpin 3.0 PR-9) -- config/event_routing.yaml.
EVENT_ROUTING_PATH = EVENT_ROUTING_DEFAULT_PATH
# Same monkeypatch-friendly convention, for POST /api/watch (Linchpin 3.0
# PR-15) -- the PRODUCTION price ledger + sku_map, not demo_price_scan.py's
# own per-request isolated ledger (a real watcher webhook's observations are
# real continuous-monitoring data, meant to persist).
PRICE_LEDGER_PATH = PRICE_LEDGER_DEFAULT_PATH
SKU_MAP_PATH = SKU_MAP_DEFAULT_PATH
# Same monkeypatch-friendly convention, for POST /api/jobs/run-scheduled's
# run_all_monitors() call -- the PRODUCTION src.state store (src/state/store.py's
# own default_store() singleton would work too, but a fresh-per-call instance
# here matches this file's own EventLedger/PriceLedger/SkuMap convention, and
# is what makes this path monkeypatchable in tests).
STATE_STORE_PATH = STATE_STORE_DEFAULT_PATH


def _get_orchestrator() -> Orchestrator:
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        # Client profiles are DISABLED on this surface by default: /api/jobs and the
        # MCP mount are multi-tenant, and `client` here is a caller-typed display
        # label, not an authenticated identity — honoring it for profile lookup would
        # let any caller pull another client's real cost parameters by naming them.
        # A local single-operator deployment can opt in via LINCHPIN_CLIENTS_ROOT.
        clients_root = os.environ.get("LINCHPIN_CLIENTS_ROOT", "").strip() or None
        _ORCHESTRATOR = Orchestrator(clients_root=clients_root)
    return _ORCHESTRATOR


def _get_mcp_key_store() -> McpKeyStore:
    """Lazy singleton, same pattern as _get_orchestrator(). Looked up fresh by
    McpKeyAuthMiddleware on every request (not baked in at mount time), so tests
    can monkeypatch this function to swap in an in-memory store."""
    global _MCP_KEY_STORE
    if _MCP_KEY_STORE is None:
        _MCP_KEY_STORE = McpKeyStore(MCP_KEYS_PATH)
    return _MCP_KEY_STORE


def _get_event_ledger() -> EventLedger:
    """A FRESH connection per call, unlike _get_mcp_key_store()'s cached
    singleton: sqlite3 connections default to check_same_thread=True and
    EventLedger/AutonomyLedger were not built with McpKeyStore's
    check_same_thread=False + Lock pairing, so a request-scoped open/close
    (matching jobs/digest_job.py's own_ledger convention) is the safe
    lifecycle here, not a persisted cross-request object. Still indirected
    through this function (reading EVENTS_LEDGER_PATH, not a bare
    EventLedger()) so tests can monkeypatch either the path or this function
    itself, matching _get_mcp_key_store()'s "swap via monkeypatch" precedent."""
    return EventLedger(EVENTS_LEDGER_PATH)


def _get_autonomy_ledger() -> AutonomyLedger:
    """See _get_event_ledger()'s docstring -- same fresh-connection-per-call
    reasoning applies here."""
    return AutonomyLedger(AUTONOMY_LEDGER_PATH)


def _get_price_ledger() -> PriceLedger:
    """A FRESH connection per call -- see ``_get_event_ledger()``'s docstring
    for why (sqlite3's ``check_same_thread=True`` default)."""
    return PriceLedger(PRICE_LEDGER_PATH)


def _get_sku_map() -> SkuMap:
    """See ``_get_event_ledger()``'s docstring -- same fresh-connection-per-call
    reasoning applies here."""
    return SkuMap(SKU_MAP_PATH)


def _get_state_store() -> StateStore:
    """See ``_get_event_ledger()``'s docstring -- same fresh-connection-per-call
    reasoning applies here. Used only by POST /api/jobs/run-scheduled's
    ``run_all_monitors()`` call: the Control Tower's inventory monitors
    (rop_breach, stockout_projected, ...) read whatever ``src.state`` domains
    happen to be populated -- an empty store (nothing written yet) makes them
    silently contribute zero events, same as everywhere else in this
    codebase (``run_all_monitors``'s own documented "empty Tower on day one
    is expected" contract)."""
    return StateStore(STATE_STORE_PATH)


def _get_promotion_ledger() -> PromotionLedger:
    """See _get_event_ledger()'s docstring -- same fresh-connection-per-call
    reasoning applies here. Shares AUTONOMY_LEDGER_PATH with
    _get_autonomy_ledger() -- scm_agent.autonomy_promotion.PromotionLedger's
    own default path is the SAME scm_agent.autonomy.DEFAULT_PATH (see that
    module's docstring: one autonomy.sqlite3, two tables)."""
    return PromotionLedger(AUTONOMY_LEDGER_PATH)


class SafeJSONResponse(JSONResponse):
    """Reject non-finite floats at serialization — never emit invalid JSON."""

    def render(self, content: object) -> bytes:
        return json.dumps(content, allow_nan=False, separators=(",", ":")).encode("utf-8")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Runs the mounted MCP sub-app's own lifespan (its session manager's task
    group) inside this app's lifespan.

    Mounting a sub-app with `app.mount()` does NOT propagate ASGI lifespan
    events into it - only the top-level app receives `lifespan.startup` from
    the server (uvicorn). Without this, FastMCP's `streamable_http_app()` never
    runs `session_manager.run()`, and every real tool call 500s with "Task
    group is not initialized" the moment a client gets past auth - the auth
    gate itself (`webapp/mcp_auth.py`, tested in `tests/test_mcp_mount.py`)
    still returns clean 401s, so this gap doesn't show up there. `_mcp_asgi_app`
    is looked up by name (module global) rather than a closure over a value,
    since it doesn't exist yet at this point in the module - it's built after
    `app` below - but this function only runs at server startup, long after
    module load finishes.
    """
    async with _mcp_asgi_app.router.lifespan_context(_mcp_asgi_app):
        yield


# Initialize error tracking (Sentry) as early as possible -- a no-op unless the
# operator has set SENTRY_DSN (webapp/observability.py). Called before the app is
# built so its FastAPI integration instruments every route from the first request.
observability.init_observability()

app = FastAPI(
    title="Inventory Planner", version="1.0.0", default_response_class=SafeJSONResponse, lifespan=_lifespan
)

# Gate deliverable downloads behind LINCHPIN_API_KEY when configured (no-op otherwise).
# Registered BEFORE security_headers_middleware so the latter stays the outermost
# layer and still runs (via its post-call_next setdefault calls) even when this
# short-circuits with an early 401 - Starlette's innermost-added-middleware-first
# dispatch order means the reverse registration would strip hardening headers off
# every 401 this middleware returns (caught by code review, verified live).
app.middleware("http")(security.jobs_output_auth_middleware)
# Always-on hardening headers (+ path-aware CSP). CORS is opt-in via env allowlist.
app.middleware("http")(security.security_headers_middleware)
if security.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=security.CORS_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

# Structured per-request access log (+ X-Request-ID). Registered last so it wraps
# the others and records the final status/duration.
app.middleware("http")(observability.request_log_middleware)
if observability.should_configure_logging():
    observability.configure_logging()

# Fail loud on an unsecured production boot; refuse outright if REQUIRE_SECURE.
_PROD_WARNINGS = security.production_warnings()
for _w in _PROD_WARNINGS:
    logging.getLogger("linchpin.security").warning("production hardening: %s", _w)
if _PROD_WARNINGS and security.REQUIRE_SECURE:
    raise RuntimeError("LINCHPIN_REQUIRE_SECURE is set but: " + "; ".join(_PROD_WARNINGS))

# Decision-support guardrail calculators (the human-facing Guided Execution Layer).
app.include_router(decisions_router)

# GMV-band commercial-pricing quote adapter (webapp/pricing_quote.py, Task 3 of
# the GMV-band GTM plan) - GET /api/pricing-quote, a thin read-only wrapper over
# src/commercial_pricing.py. Stateless, no auth required (same public-read
# posture as GET /paquetes; nothing here is sensitive or mutating).
app.include_router(pricing_quote_router)

# Read-only MCP server (Phase A go-to-market: sell analysis-only access to other
# AI agents). Shares this process's orchestrator (avoids loading the knowledge
# graph twice) but is gated by its OWN per-client key store, not LINCHPIN_API_KEY -
# see webapp/mcp_auth.py. No writeback tool (e.g. odoo_replenishment) is ever
# exposed here; see webapp/mcp_tool_specs.py for the exact exposed surface.
_mcp_asgi_app = build_mcp_server(_get_orchestrator()).streamable_http_app()
# A lambda, not the bare function: `_get_mcp_key_store` must be looked up by NAME
# in this module's globals on every call (late binding), not captured by value
# here at mount time - otherwise tests monkeypatching `app_module._get_mcp_key_store`
# would never reach the already-constructed middleware instance below.
_mcp_asgi_app.add_middleware(McpKeyAuthMiddleware, key_store_getter=lambda: _get_mcp_key_store())
app.mount("/mcp", _mcp_asgi_app)


def _reject_nonfinite(token: str) -> float:
    raise ValueError(f"non-finite JSON token: {token}")


@dataclass(frozen=True)
class SkuForecast:
    """Per-SKU data that does NOT depend on the sliders — computed once."""

    product_id: str
    forecast: ForecastResult
    unit_cost: float
    lead_periods: float
    history: list[float]


# ---- forecasts are slider-independent → compute once and cache per method ----

_FORECASTS: dict[str, list[SkuForecast]] = {}
_VALID_FORECAST_METHODS = frozenset({"auto", "auto_modern", "auto_ets", "tsb", "ses", "croston"})


def _load_forecasts(method: str = "auto") -> list[SkuForecast]:
    if method not in _VALID_FORECAST_METHODS:
        raise ValueError(f"unknown forecast method: {method!r}")
    if method not in _FORECASTS:
        source = CsvDemandSource(str(DATA_FILE), periods_per_year=PERIODS_PER_YEAR)
        out: list[SkuForecast] = []
        for pid in source.list_products():
            series = source.demand_series(pid)
            meta = source.metadata(pid)
            out.append(
                SkuForecast(
                    product_id=pid,
                    forecast=forecast_demand(series, method=method),
                    unit_cost=meta.mean_unit_cost,
                    lead_periods=meta.lead_time_periods,
                    history=[float(x) for x in series],
                )
            )
        _FORECASTS[method] = out
    return _FORECASTS[method]


def _status(forecast: ForecastResult) -> dict[str, str]:
    if forecast.is_intermittent:
        return {"key": "review", "label": "review"}
    if abs(forecast.bias) >= 2:
        return {"key": "risk", "label": "high bias"}
    return {"key": "ok", "label": "on track"}


def _sku_payload(
    sf: SkuForecast,
    *,
    service_level: float,
    order_cost: float,
    holding_rate: float,
    lead: float,
) -> dict:
    fc = sf.forecast
    inputs = fc.to_engine_inputs(periods_per_year=PERIODS_PER_YEAR)
    holding_cost = max(holding_rate * sf.unit_cost, 1e-6)

    if fc.is_intermittent:
        pol = periodic_review_rs(
            annual_demand=inputs["annual_demand"],
            mean_demand_per_period=inputs["mean_demand_per_period"],
            demand_std_per_period=inputs["demand_std_per_period"],
            holding_cost_per_unit=holding_cost,
            fixed_order_cost=order_cost,
            lead_time_periods=lead,
            review_period=1.0,
            cycle_service_level=service_level,
        )
        kind = "(R, S)"
        order_quantity = None
    else:
        pol = continuous_review_sq(
            annual_demand=inputs["annual_demand"],
            mean_demand_per_period=inputs["mean_demand_per_period"],
            demand_std_per_period=inputs["demand_std_per_period"],
            holding_cost_per_unit=holding_cost,
            fixed_order_cost=order_cost,
            lead_time_periods=lead,
            cycle_service_level=service_level,
        )
        kind = "(s, Q)"
        order_quantity = pol.order_quantity

    ss = pol.safety_stock.safety_stock
    cycle_units = pol.expected_cycle_stock
    cycle_investment = cycle_units * sf.unit_cost
    ss_investment = ss * sf.unit_cost
    # Reorder line for the chart/stat: mu*L + safety, on the lead-time-only risk
    # (matches the design). For (s,Q) this equals pol.reorder_point; for (R,S) it
    # stays distinct from order-up-to S = mu*(L+R) + safety.
    risk_reorder = inputs["mean_demand_per_period"] * lead + ss

    return {
        "id": sf.product_id,
        "method": fc.method,
        "intermittent": fc.is_intermittent,
        "forecast": fc.forecast,
        "demand_mean": fc.demand_mean,
        "demand_std": fc.demand_std,
        "error_std": fc.error_std,
        "bias": fc.bias,
        "mae": fc.mae,
        "unit_cost": sf.unit_cost,
        "lead_periods": lead,
        "policy_kind": kind,
        "order_quantity": order_quantity,
        "order_up_to": pol.order_up_to_level,
        "reorder_point": risk_reorder,
        "safety_stock": ss,
        "z_factor": pol.safety_stock.service_level_factor,
        "cycle_units": cycle_units,
        "cycle_investment": cycle_investment,
        "ss_investment": ss_investment,
        "investment": cycle_investment + ss_investment,
        "status": _status(fc),
        "history": sf.history,
    }


def compute_portfolio(
    *,
    service_level: float,
    order_cost: float,
    holding_rate: float,
    budget: float,
    lead_overrides: dict[str, float],
    forecast_method: str = "auto",
) -> dict:
    forecasts = _load_forecasts(forecast_method)
    skus = [
        _sku_payload(
            sf,
            service_level=service_level,
            order_cost=order_cost,
            holding_rate=holding_rate,
            lead=lead_overrides.get(sf.product_id, sf.lead_periods),
        )
        for sf in forecasts
    ]

    # Budget allocation via the real constraints engine. Map cycle stock onto an
    # equivalent order_quantity so InventoryItem.cycle_investment matches exactly.
    items = [
        InventoryItem(
            product_id=s["id"],
            order_quantity=2.0 * s["cycle_units"],
            safety_stock=s["safety_stock"],
            unit_cost=s["unit_cost"],
        )
        for s in skus
    ]
    plan = allocate_under_budget(items, budget)
    cycle_floor = sum(it.cycle_investment for it in items)
    ss_total = sum(it.safety_investment for it in items)

    n_risk = sum(1 for s in skus if s["status"]["key"] == "risk")
    n_intermittent = sum(1 for s in skus if s["intermittent"])

    return {
        "params": {
            "service_level": service_level,
            "order_cost": order_cost,
            "holding_rate": holding_rate,
            "budget": budget,
            "periods_per_year": PERIODS_PER_YEAR,
            "forecast_method": forecast_method,
        },
        "skus": skus,
        "totals": {
            "requested": plan.requested_investment,
            "cycle_floor": cycle_floor,
            "ss_total": ss_total,
            "scale": plan.safety_stock_scale,
            "final": plan.final_investment,
            "feasible": plan.feasible,
            "headroom": budget - plan.requested_investment,
            "n_risk": n_risk,
            "n_intermittent": n_intermittent,
            "n_skus": len(skus),
        },
    }


@app.get("/api/portfolio", dependencies=[Depends(security.rate_limit)])
def api_portfolio(
    service_level: float = Query(0.95, gt=0.0, lt=1.0),
    order_cost: float = Query(80.0, gt=0.0),
    holding_rate: float = Query(0.22, gt=0.0, le=2.0),
    budget: float = Query(44000.0, ge=0.0),
    forecast_method: str = Query(
        "auto",
        description="Forecast engine: auto (AutoETS/TSB when [forecast] installed), "
        "auto_modern, auto_ets, tsb, ses, croston",
    ),
    lead_overrides: str | None = Query(None, description="JSON object {sku: lead_periods}"),
) -> dict:
    if forecast_method not in _VALID_FORECAST_METHODS:
        raise HTTPException(
            status_code=400,
            detail=f"forecast_method must be one of: {sorted(_VALID_FORECAST_METHODS)}",
        )
    overrides: dict[str, float] = {}
    if lead_overrides:
        try:
            raw = json.loads(lead_overrides, parse_constant=_reject_nonfinite)
            if not isinstance(raw, dict):
                raise ValueError("not an object")
            for key, value in raw.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("values must be numbers")
                lead = float(value)
                if not math.isfinite(lead) or not (0 < lead <= MAX_LEAD_PERIODS):
                    raise ValueError("lead out of range")
                overrides[str(key)] = lead
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"lead_overrides must be a JSON object of finite numbers in (0, {MAX_LEAD_PERIODS:g}]",
            ) from exc

    return compute_portfolio(
        service_level=service_level,
        order_cost=order_cost,
        holding_rate=holding_rate,
        budget=budget,
        lead_overrides=overrides,
        forecast_method=forecast_method,
    )


@app.get("/api/health")
def api_health() -> dict:
    return {"ok": True, "skus": len(_load_forecasts())}


@app.post("/api/leads", dependencies=[Depends(security.rate_limit)])
async def api_leads(email: str = Form(...), source: str = Form("demo")) -> dict:
    """Capture a demo lead: validate the email and append it to a JSONL store.

    The store lives under webapp/_leads/ (gitignored — it holds PII) and is never
    versioned. No API key is required: this is the public demo's email gate.
    """
    addr = email.strip().lower()
    if len(addr) > 254 or not EMAIL_RE.match(addr):
        raise HTTPException(status_code=400, detail="invalid email")
    clean_source = re.sub(r"[^\w.\-]", "", source)[:40] or "demo"
    record = {
        "email": addr,
        "source": clean_source,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with LEADS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"ok": True}


def _prune_old_jobs(now: float | None = None) -> None:
    """Best-effort sweep: drop per-job output dirs older than JOBS_TTL_SECONDS.

    Called at the start of each /api/jobs request so generated deliverables and
    uploads do not accumulate forever. Failures are swallowed (cleanup is opportunistic).
    """
    cutoff = (now if now is not None else time.time()) - JOBS_TTL_SECONDS
    try:
        entries = list(JOBS_OUTPUT_DIR.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def _safe_upload_basename(filename: str) -> str:
    """Reduce a client-supplied filename to a bare basename, raising 400 if it
    doesn't survive the reduction (empty, '.', '..') — never trust the
    client-supplied filename for anything beyond this. Called from the async
    handler BEFORE the upload-size check, so an invalid filename is rejected
    with the same precedence as before this endpoint's asyncio.to_thread split
    (filename validity is a 400 regardless of how large the accompanying body is).
    """
    raw_name = filename.replace("\\", "/")
    safe_name = os.path.basename(raw_name)
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(status_code=400, detail="invalid upload filename")
    return safe_name


def _run_job_sync(
    brief: str,
    client: str,
    job_type: str | None,
    parsed_params: dict,
    use_sample: bool,
    safe_filename: str | None,
    file_bytes: bytes | None,
) -> dict:
    """The blocking half of /api/jobs: temp-dir staging, Orchestrator.run(), and
    building the response dict (download-URL map + the serialized `guided`
    outcome). Offloaded via asyncio.to_thread (see api_jobs below) so a real
    analysis (pandas/Excel work, CPU-bound) never blocks the event loop - with
    WEB_CONCURRENCY=1 in production, running this inline used to stall every
    other request, including /api/health, for the run's duration.

    ``safe_filename`` is already basename-validated by _safe_upload_basename
    (called from the async handler) — the containment check below is
    defense-in-depth against a future caller that skips that step, not the
    primary guard.
    """
    _prune_old_jobs()

    job_dir = Path(tempfile.mkdtemp(dir=JOBS_OUTPUT_DIR))
    data_path: str | None = None
    if safe_filename and file_bytes is not None:
        upload = job_dir / safe_filename
        if upload.resolve().parent != job_dir.resolve():
            raise HTTPException(status_code=400, detail="invalid upload filename")
        upload.write_bytes(file_bytes)
        data_path = str(upload)

    # Demo path: no upload, but the visitor asked to try the bundled sample dataset.
    if data_path is None and use_sample:
        data_path = str(DATA_FILE)

    result = _get_orchestrator().run(
        brief, data_path=data_path, overrides=parsed_params,
        job_type=job_type or None, client=client, out_dir=job_dir,
    )

    download_urls: dict[str, str] = {}
    for name, path in result.deliverables.items():
        try:
            rel = Path(path).resolve().relative_to(JOBS_OUTPUT_DIR.resolve())
            download_urls[name] = "/jobs-output/" + rel.as_posix()
        except ValueError:
            pass  # path outside JOBS_OUTPUT_DIR — skip download link, keep deliverable entry

    return {
        "status": result.status,
        "tool": result.tool,
        "confidence": result.confidence,
        "summary": result.summary,
        "deliverables": result.deliverables,
        "download_urls": download_urls,
        "qa_issues": result.qa_issues,
        "clarifications": result.clarifications,
        "citations": result.citations,
        "kb_warnings": result.kb_warnings,
        # The never-unprotected contract, machine-readable: ranked options, a
        # prepared handoff, or an escalation (route_to/sla/reason) - whichever
        # the tool produced. Orchestrator.run() always attaches one (falling
        # back to a generic one derived from `status` when a tool supplies
        # none), so this is never null for a real orchestrator result. Frozen
        # dataclasses all the way down (src/guided.py) -> plain, JSON-safe dicts.
        "guided": asdict(result.guided) if result.guided is not None else None,
    }


@app.post("/api/jobs", dependencies=[Depends(security.rate_limit), Depends(security.require_api_key)])
async def api_jobs(
    brief: str = Form(...),
    client: str = Form("Client"),
    job_type: str | None = Form(None),
    params: str = Form("{}"),
    use_sample: bool = Form(False),
    file: UploadFile | None = File(None),
) -> dict:
    try:
        parsed_params = json.loads(params) if params else {}
        if not isinstance(parsed_params, dict):
            raise ValueError("params must be a JSON object")
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid params JSON: {exc}") from exc

    # Sanitize the client-supplied label before it lands in report copy/headings.
    client = re.sub(r"[^\w\s.,\-]", "", client)[:100].strip() or "Client"

    # Genuine async I/O stays on the event loop; only the CPU-bound orchestrator
    # run (+ its filesystem staging) moves to a thread below.
    safe_filename: str | None = None
    file_bytes: bytes | None = None
    if file is not None and file.filename:
        safe_filename = _safe_upload_basename(file.filename)  # 400 before the size check below
        file_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"upload exceeds {MAX_UPLOAD_BYTES} bytes")

    return await asyncio.to_thread(
        _run_job_sync, brief, client, job_type, parsed_params, use_sample, safe_filename, file_bytes,
    )


MAX_LEAD_DIRS = 5000  # bounds LEAD_REPORTS_DIR when LINCHPIN_RATE_LIMIT is left at its off-by-default 0


def _prune_excess_lead_dirs(limit: int = MAX_LEAD_DIRS) -> None:
    """Cap LEAD_REPORTS_DIR at `limit` lead directories, evicting the oldest.

    Unlike JOBS_OUTPUT_DIR, lead directories are the funnel's durable artifact
    (an operator reviews them later) so they are deliberately NOT TTL-purged --
    but /api/demo-scan is unauthenticated and rate limiting is OFF by default
    (LINCHPIN_RATE_LIMIT=0), so an unbounded lead store is a trivial scripted
    disk-exhaustion vector (a fresh email per request, forever). This is a
    best-effort count cap, not a substitute for setting LINCHPIN_RATE_LIMIT in
    production -- see SECURITY.md.
    """
    try:
        entries = [e for e in LEAD_REPORTS_DIR.iterdir() if e.is_dir()]
    except OSError:
        return
    if len(entries) <= limit:
        return
    entries.sort(key=lambda e: e.stat().st_mtime)
    for stale in entries[: len(entries) - limit]:
        shutil.rmtree(stale, ignore_errors=True)


@app.post("/api/demo-scan", dependencies=[Depends(security.rate_limit)])
async def api_demo_scan(
    email: str = Form(...),
    use_sample: bool = Form(False),
    file: UploadFile | None = File(None),
) -> dict:
    """The /demo funnel: one stock CSV -> the Diagnostico's teaser numbers.

    Public like /api/leads (the demo IS the lead magnet), rate-limited, and the
    upload path enforces the same SECURITY.md controls as /api/jobs: 25 MB cap,
    basename-only filenames pinned to an isolated per-request tempdir under
    JOBS_OUTPUT_DIR, TTL-purged. Lead artifacts (mini-report + follow-up DRAFT,
    never auto-sent) are written under LEAD_REPORTS_DIR only when QA passes --
    the raw upload itself is never copied there.
    """
    addr = email.strip().lower()
    if len(addr) > 254 or not EMAIL_RE.match(addr):
        raise HTTPException(status_code=400, detail="invalid email")

    _prune_old_jobs()

    import tempfile

    if file is not None and file.filename:
        raw_name = (file.filename or "upload").replace("\\", "/")
        safe_name = os.path.basename(raw_name)
        if not safe_name or safe_name in (".", ".."):
            raise HTTPException(status_code=400, detail="invalid upload filename")
        scan_dir = Path(tempfile.mkdtemp(dir=JOBS_OUTPUT_DIR))
        upload = scan_dir / safe_name
        if upload.resolve().parent != scan_dir.resolve():
            raise HTTPException(status_code=400, detail="invalid upload filename")
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"upload exceeds {MAX_UPLOAD_BYTES} bytes")
        try:
            upload.write_bytes(data)
        except OSError as exc:
            # A filename that's syntactically fine for os.path.basename() can
            # still be rejected by the underlying filesystem (Windows: <>:"|?*,
            # reserved names; any OS: an overlong path) - a 400, not a crash.
            raise HTTPException(status_code=400, detail="invalid upload filename") from exc
        data_path, dataset_label = upload, safe_name
    elif use_sample:
        data_path, dataset_label = SAMPLE_STOCK_FILE, "sample_stock_snapshot.csv"
    else:
        raise HTTPException(status_code=400, detail="sube un CSV de stock o marca use_sample")

    try:
        df = pd.read_csv(data_path)
    except Exception as exc:  # pandas raises several parse/encoding error types
        raise HTTPException(status_code=400, detail=f"no se pudo leer el CSV: {exc}") from exc
    try:
        result = demo_scan.run_demo_scan(df)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"columnas requeridas: {demo_scan.REQUIRED_COLUMNS_HINT} ({exc})",
        ) from exc

    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if result.ok:
        # QA passed -> persist the operator's follow-up artifacts for this lead.
        _prune_excess_lead_dirs()
        lead_dir = LEAD_REPORTS_DIR / demo_scan.safe_lead_dirname(addr)
        if lead_dir.resolve().parent != LEAD_REPORTS_DIR.resolve():
            raise HTTPException(status_code=400, detail="invalid email")
        lead_dir.mkdir(parents=True, exist_ok=True)
        (lead_dir / "mini_report.md").write_text(
            demo_scan.render_mini_report(result, email=addr, dataset_label=dataset_label, ts=ts),
            encoding="utf-8",
        )
        (lead_dir / "followup_email_draft.md").write_text(
            demo_scan.render_followup_email(result, email=addr, dataset_label=dataset_label),
            encoding="utf-8",
        )

    # Telemetry line ALWAYS (feeds funnel metrics); artifacts only on QA pass.
    record = {
        "email": addr,
        "source": "demo-scan",
        "ts": ts,
        "dataset": dataset_label,
        "status": "ok" if result.ok else "qa_failed",
        "result": result.headline if result.ok else None,
    }
    with LEADS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    if not result.ok:
        return {"status": "qa_failed", "qa_issues": list(result.qa_issues), "dataset": dataset_label}
    return {
        "status": "ok",
        "headline": result.headline,
        "findings": list(result.findings),
        "cta_url": demo_scan.CTA_PATH,
        "dataset": dataset_label,
    }


@app.post("/api/demo-price-scan", dependencies=[Depends(security.rate_limit)])
async def api_demo_price_scan(
    email: str = Form(...),
    urls: str = Form(...),
    product_id: str = Form("Product"),
    our_price: float | None = Form(None),
) -> dict:
    """The /demo Pricing funnel: N competitor URLs -> a teaser (non-
    quarantined, partial) price-position matrix (plan section 9's lead
    magnet). Public like /api/demo-scan (the demo IS the lead magnet),
    rate-limited. SSRF-safe by construction: every URL routes through
    jobs.price_intelligence's own require_approved_site allowlist gate
    (see webapp/demo_price_scan.py's module docstring) -- an unapproved
    domain is skipped, never fetched. A fresh, isolated ledger is used per
    request (webapp/demo_price_scan.run_demo_price_scan's own contract),
    never the production one."""
    addr = email.strip().lower()
    if len(addr) > 254 or not EMAIL_RE.match(addr):
        raise HTTPException(status_code=400, detail="invalid email")

    url_list = [u for u in re.split(r"[\s,]+", urls.strip()) if u]
    if not url_list:
        raise HTTPException(status_code=400, detail="submit at least one competitor URL")

    _prune_old_jobs()
    scan_ledger_dir = Path(tempfile.mkdtemp(dir=JOBS_OUTPUT_DIR))
    try:
        result = demo_price_scan.run_demo_price_scan(
            url_list, product_id=product_id.strip() or "Product", our_price=our_price,
            ledger_base_path=scan_ledger_dir,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        shutil.rmtree(scan_ledger_dir, ignore_errors=True)

    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if result.ok:
        _prune_excess_lead_dirs()
        lead_dir = LEAD_REPORTS_DIR / demo_scan.safe_lead_dirname(addr)
        if lead_dir.resolve().parent != LEAD_REPORTS_DIR.resolve():
            raise HTTPException(status_code=400, detail="invalid email")
        lead_dir.mkdir(parents=True, exist_ok=True)
        (lead_dir / "price_scan_mini_report.md").write_text(
            demo_price_scan.render_mini_report(result, email=addr, product_id=product_id, ts=ts),
            encoding="utf-8",
        )
        (lead_dir / "price_scan_followup_email_draft.md").write_text(
            demo_price_scan.render_followup_email(result, email=addr, product_id=product_id),
            encoding="utf-8",
        )

    record = {
        "email": addr, "source": "demo-price-scan", "ts": ts, "dataset": product_id,
        "status": "ok" if result.ok else "qa_failed",
        "result": result.headline,
    }
    with LEADS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    if not result.ok:
        return {"status": "qa_failed", "headline": result.headline}
    return {
        "status": "ok",
        "headline": result.headline,
        "teaser_rows": result.teaser_rows,
        "cta_url": demo_price_scan.CTA_PATH,
    }


@app.post("/api/watch", dependencies=[Depends(security.rate_limit), Depends(security.require_api_key)])
async def api_watch(payload: dict = Body(...)) -> dict:
    """L2 acquisition receiver: a changedetection.io webhook POST (Linchpin
    3.0 PR-15, plan S6.1/S8) -- see
    ``src.pricing_intel.acquire.watcher``'s module docstring for the exact
    JSON body contract an operator configures on their own,
    separately-deployed changedetection.io instance's notification
    settings.

    Gated behind ``LINCHPIN_API_KEY`` (a no-op when unset, matching every
    other mutating endpoint -- POST /api/jobs, POST /api/approvals/{id}):
    an operator adds an ``X-API-Key: <value>`` custom header to
    changedetection.io's notification URL so only their own instance can
    post here. Also rate-limited.

    Resolves ``matched_product_id`` via a ``sku_map`` reverse lookup
    (``SkuMap.latest_confirmed_for_competitor_ref``) BEFORE the sanity gate
    -- an observation for a competitor URL Kern has never confirmed a match
    for still gets sanity-gated and ledgered (``matched_product_id=None`` is
    a legitimate state, see ``watcher.py``'s docstring), just with no sku
    attached to the resulting Event(s).

    Runs the SAME sanity gate (``src.pricing_intel.sanity``) every other
    acquisition tier goes through before landing in the SAME production
    ``PriceLedger``, and emits price_move/competitor_oos/promo_detected/
    new_competitor_listing through the SAME ``scm_agent.events.EventLedger``
    the scheduled L0 cycle (``jobs.price_monitor.run_price_monitor_cycle``)
    uses -- one ledger, one event stream, regardless of acquisition tier.

    A malformed/incomplete payload is a 400 (``ChangeDetectionWebhookError``)
    -- loud and actionable for whoever is debugging their changedetection.io
    notification template, never a silent 200 that drops the observation.
    """
    try:
        candidate = parse_changedetection_webhook(payload)
    except ChangeDetectionWebhookError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sku_map = _get_sku_map()
    try:
        match = sku_map.latest_confirmed_for_competitor_ref(candidate.competitor_sku_ref, candidate.site)
    finally:
        sku_map.close()
    if match is not None:
        candidate = replace(candidate, matched_product_id=match.our_product_id)

    ledger = _get_price_ledger()
    event_ledger = _get_event_ledger()
    try:
        outcome = accept_observation(candidate, ledger=ledger, event_ledger=event_ledger, detect_new_listing=True)
    finally:
        ledger.close()
        event_ledger.close()

    return {
        "status": outcome.status,
        "reason": outcome.reason,
        "site": candidate.site,
        "competitor_sku_ref": candidate.competitor_sku_ref,
        "matched_product_id": candidate.matched_product_id,
        "events": [e.type for e in outcome.events],
    }


# ---- POST /api/jobs/run-scheduled: cron-triggered scheduler wiring ----------
#
# jobs.scheduler.JobRegistry.run_once() is Golden Rule 9's ("todo componente
# continuo degrada a batch") batch-degradation entry point: PRICE_MONITOR_JOB
# and PRICE_WATCH_JOB were fully built and tested (jobs/price_monitor.py,
# jobs/price_watch.py) but nothing in production ever called them --
# jobs.scheduler.default_registry() ships empty, and a real
# BackgroundScheduler (the "tower" extra) was explicitly rejected for this
# deploy (fly.toml's WEB_CONCURRENCY=1 on a 512MB VM already OOM-killed once
# at 2 workers; a persistent background thread doing periodic network
# scraping inside the same process serving all web/API/MCP traffic is too
# risky). This endpoint is the other half of Golden Rule 9: an EXTERNAL cron
# (a GitHub Actions scheduled workflow, added separately) POSTs here, and
# each call runs both jobs synchronously, once, then returns -- no scheduler
# loop, no background thread, no sleeping, ever started by this process.


def _cycle_report_summary(report: object) -> dict:
    """Duck-typed summary shared by ``jobs.price_monitor.PriceMonitorCycleReport``
    and ``jobs.price_watch.PriceWatchCycleReport`` -- both expose the
    identical ``outcomes``/``pairs_checked``/``summary`` contract (see
    ``PriceWatchCycleReport``'s own docstring: "Mirrors
    jobs.price_monitor.PriceMonitorCycleReport's exact shape"), so one small
    helper serves both instead of two near-identical copies."""
    by_status: dict[str, int] = {}
    for outcome in report.outcomes:
        by_status[outcome.status] = by_status.get(outcome.status, 0) + 1
    return {"pairs_checked": report.pairs_checked, "by_status": by_status, "summary": report.summary}


def _run_scheduled_jobs_sync() -> dict:
    """The blocking half of POST /api/jobs/run-scheduled -- offloaded via
    asyncio.to_thread by the async handler below (same reasoning as
    _run_job_sync: this makes REAL outbound HTTP calls, MercadoLibre's API
    plus competitor PDP fetches, which must never block the single
    WEB_CONCURRENCY=1 event loop for the run's duration).

    Runs PRICE_MONITOR_JOB and PRICE_WATCH_JOB via
    ``production_registry().run_once(job_id)`` -- ONE call per job id (not a
    single ``run_once()`` covering both), so one job's own failure (a
    network error, a site compliance refusal raised as an exception) is
    caught and reported back without ever hiding or aborting the other
    job's result -- golden rule 14, "ningun cap silencioso", applied to
    endpoint-level failures too.

    Every price_move/competitor_oos/promo_detected Event either cycle
    emitted (``report.events`` -- see ``PriceMonitorCycleReport``/
    ``PriceWatchCycleReport``'s own ``events`` property) is collected and
    fed into ``scm_agent.monitors.run_all_monitors(price_signal_events=...)``
    -- previously this parameter was never populated by any production
    caller, so ``competitor_price_move_monitor`` never actually ran outside
    a test. ``ledger=_get_event_ledger()`` is the SAME production
    EventLedger POST /api/watch and GET /api/events already read/write (see
    that getter's docstring) -- a promoted ``competitor_price_move`` Tower
    event lands there immediately, never a second, invented event store.

    A job's own exception is reported back only as ``type(exc).__name__``
    (fix round 1) -- never ``str(exc)``. Unlike ``/api/watch``'s
    ``ChangeDetectionWebhookError`` (a controlled, caller-facing validation
    message), an exception raised here can be a raw network/infra/library
    error whose message may embed a local filesystem path
    (``data/*.sqlite3``), a competitor URL, or other internal detail --
    reflecting it verbatim into a 200 body would leak that to any caller
    (in the shipped default config, ANY unauthenticated caller; even when
    gated, it lands in the external cron's own logs). The full exception
    (message + traceback) is still logged server-side via ``logging`` for
    whoever operates the deploy to actually debug it -- this only stops it
    from being echoed back over HTTP, matching this file's own
    ``/api/jobs``-generic-500 / ``/api/watch``-controlled-400-only
    convention for what is safe to reflect.
    """
    registry = production_registry()
    jobs_summary: dict[str, dict] = {}
    price_signal_events: list[Event] = []

    for job_id in PRODUCTION_JOB_IDS:
        try:
            report = registry.run_once(job_id)[job_id]
        except Exception as exc:  # a job's own failure must never hide the other job's result
            logging.getLogger("linchpin.jobs").exception("scheduled job %r failed", job_id)
            jobs_summary[job_id] = {"status": "error", "error": type(exc).__name__}
            continue
        jobs_summary[job_id] = {"status": "ok", **_cycle_report_summary(report)}
        price_signal_events.extend(report.events)

    event_ledger = _get_event_ledger()
    state_store = _get_state_store()
    try:
        promoted = run_all_monitors(ledger=event_ledger, store=state_store, price_signal_events=price_signal_events)
    finally:
        event_ledger.close()
        state_store.close()

    competitor_price_move_count = sum(1 for e in promoted if e.type == EVENT_COMPETITOR_PRICE_MOVE)
    any_job_error = any(v["status"] == "error" for v in jobs_summary.values())
    return {
        "status": "error" if any_job_error else "ok",
        "jobs": jobs_summary,
        "tower_events_promoted": len(promoted),
        "competitor_price_move_events_promoted": competitor_price_move_count,
    }


# ---- run-scheduled throttle / concurrency guard / dedicated executor --------
#
# Fix round 1 (adversarial + spec review). Before this, the endpoint offloaded
# straight onto asyncio.to_thread's SHARED default executor with NO guard at
# all: in the shipped default config (LINCHPIN_API_KEY and LINCHPIN_RATE_LIMIT
# both unset) ANY unauthenticated caller could loop POST /api/jobs/run-scheduled
# to drive unbounded REAL outbound crawling of MercadoLibre + approved
# competitor sites -- server-IP/UA bans, thread-pool + CPU exhaustion on the
# single WEB_CONCURRENCY=1 512MB worker. Two overlapping calls (a retried cron
# tick, a monitoring probe double-firing) could also land on two DIFFERENT
# shared-pool worker threads and hit default_ledger()/default_sku_map()'s
# cached sqlite3 connections (opened WITHOUT check_same_thread=False -- see
# src/pricing_intel/ledger.py / src/pricing_intel/match/sku_map.py) from a
# thread that never opened them, raising sqlite3.ProgrammingError that this
# file's own per-job try/except then silently turned into a job "error" (the
# cycle looked like it ran, actually did nothing).
#
# Three cooperating, entirely in-process guards close this -- none of them
# depend on the external cron's own cadence being correctly configured:
#
# 1. SCHEDULED_JOBS_MIN_INTERVAL_SECONDS -- a hard floor between the START of
#    one real run and the next. A caller (or attacker) looping this endpoint
#    gets a cheap 429 with Retry-After, checked on the event loop BEFORE the
#    lock/executor/any network I/O are ever touched.
# 2. _SCHEDULED_JOBS_LOCK -- a plain, non-reentrant threading.Lock, acquired
#    non-blocking. A second call that arrives while a run is still in flight
#    (an overlapping retry, a double-firing probe) gets an immediate 409,
#    never queued -- there is NEVER more than one real cycle running at a
#    time, so production_registry()'s lazy-singleton init (independently
#    hardened with its own lock too, see jobs/scheduled_jobs.py) can't race
#    either. The lock is released by the WORKER thread itself once
#    _run_scheduled_jobs_sync actually returns (success OR exception) -- NOT
#    by the async handler on a timeout -- so a genuinely wedged run (e.g. a
#    hung socket read with no library-level timeout) keeps rejecting new
#    calls with 409 instead of ever dispatching a second, overlapping real
#    run. Honest trade-off: Python cannot forcibly cancel a running thread,
#    so a truly stuck job stays stuck until the process restarts, but it can
#    never double the outbound traffic while stuck.
# 3. _SCHEDULED_JOBS_EXECUTOR -- a DEDICATED ThreadPoolExecutor with exactly
#    one worker (NOT asyncio.to_thread's shared default pool, which also
#    backs /api/jobs and /api/demo-scan). Every real run of this endpoint
#    therefore always executes on the SAME OS thread for the life of the
#    process -- default_ledger()/default_sku_map()'s cached sqlite3
#    connections get bound once, by this endpoint, to that one thread, and
#    are never touched from a second thread by a LATER call to this same
#    endpoint. It also means a slow run-scheduled call can never starve
#    /api/jobs or /api/demo-scan's shared pool of workers.
#    SCHEDULED_JOBS_TIMEOUT_SECONDS bounds how long the CALLER waits (via
#    asyncio.wait_for) -- the caller always gets an answer within that
#    window, even when the underlying thread does not.


def _positive_int_env(name: str, default: int) -> int:
    """Same convention as ``webapp.security._int_env``, duplicated locally so
    this module doesn't reach into that module's private helper. Falls back
    to ``default`` on anything unparsable or <= 0 -- a zero/negative interval
    or timeout would silently defeat the guard it configures."""
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


SCHEDULED_JOBS_MIN_INTERVAL_SECONDS = _positive_int_env("LINCHPIN_SCHEDULED_MIN_INTERVAL_SECONDS", 300)
SCHEDULED_JOBS_TIMEOUT_SECONDS = _positive_int_env("LINCHPIN_SCHEDULED_JOB_TIMEOUT_SECONDS", 900)

_SCHEDULED_JOBS_LOCK = threading.Lock()
_SCHEDULED_JOBS_LAST_STARTED_AT: float | None = None  # time.monotonic() of the last ACCEPTED run's start
_SCHEDULED_JOBS_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scheduled-jobs")


@app.post(
    "/api/jobs/run-scheduled",
    dependencies=[Depends(security.rate_limit), Depends(security.require_api_key)],
)
async def api_run_scheduled_jobs() -> dict:
    """The cron-triggered entry point for ``jobs.scheduler.JobRegistry`` (F0,
    PR-3) -- see ``jobs/scheduled_jobs.py``'s module docstring for the full
    "why an HTTP endpoint, not a BackgroundScheduler" rationale. An external
    scheduler (a GitHub Actions cron workflow, added separately) is expected
    to POST here on a cadence -- see ``jobs.price_monitor.DEFAULT_CADENCE_HOURS``
    / ``jobs.price_watch.DEFAULT_CADENCE_HOURS`` (4h each) for the intended
    pace.

    Gated the same as every other mutating endpoint in this file
    (require_api_key + rate_limit, as FastAPI ``dependencies=`` -- both run
    BEFORE this handler's body, so an unauthenticated caller who
    guesses/brute-forces this path never triggers any network activity or
    cost). On top of that, this endpoint is ALSO self-throttling in-process
    (fix round 1, see the comment block above): a minimum-interval floor
    (429 + Retry-After) and a non-blocking concurrency lock (409) reject a
    looping/overlapping caller before any outbound HTTP or job work happens
    -- safety no longer depends entirely on the external cron's own cadence
    being correctly configured.

    See :func:`_run_scheduled_jobs_sync` for the actual work; this handler
    offloads it to a DEDICATED single-worker executor (never the shared
    ``asyncio.to_thread`` pool /api/jobs and /api/demo-scan use) so a real
    run's outbound HTTP calls never block the single WEB_CONCURRENCY=1 event
    loop, never starve those other endpoints' shared pool, and always land
    on the same OS thread run after run.
    """
    global _SCHEDULED_JOBS_LAST_STARTED_AT

    now = time.monotonic()
    last_started_at = _SCHEDULED_JOBS_LAST_STARTED_AT
    if last_started_at is not None:
        elapsed = now - last_started_at
        if elapsed < SCHEDULED_JOBS_MIN_INTERVAL_SECONDS:
            retry_after = max(1, int(SCHEDULED_JOBS_MIN_INTERVAL_SECONDS - elapsed))
            raise HTTPException(
                status_code=429,
                detail=(
                    f"run-scheduled called too soon; minimum interval is "
                    f"{SCHEDULED_JOBS_MIN_INTERVAL_SECONDS}s between runs"
                ),
                headers={"Retry-After": str(retry_after)},
            )

    if not _SCHEDULED_JOBS_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a scheduled job run is already in progress")

    _SCHEDULED_JOBS_LAST_STARTED_AT = now  # only accepted (lock-held) runs count toward the interval floor

    def _run_and_release() -> dict:
        try:
            return _run_scheduled_jobs_sync()
        finally:
            _SCHEDULED_JOBS_LOCK.release()

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_SCHEDULED_JOBS_EXECUTOR, _run_and_release),
            timeout=SCHEDULED_JOBS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        # The lock is intentionally NOT released here -- see guard #2 above.
        raise HTTPException(
            status_code=504,
            detail=(
                f"scheduled job run exceeded the {SCHEDULED_JOBS_TIMEOUT_SECONDS}s timeout; "
                "it continues running in the background and will release its lock when it finishes"
            ),
        ) from None


_METRICS_LABEL_RE = re.compile(r"[^\w.\- ]")
_METRICS_LABEL_MAX_LEN = 60
_METRICS_MAX_BUCKETS = 25  # beyond this many distinct labels, fold the rest into "other"


def _metrics_label(value: object) -> str:
    """A caller-controlled field (source/status/dataset) reduced to a safe,
    length-capped bucket label - never a non-string/unhashable value (which
    would crash a dict-keying aggregation), and never PII-shaped text
    surviving verbatim (an uploaded filename that happens to be an email
    address, e.g., loses its '@' and most punctuation here), unlike a plain
    `.get(...) or "unknown"` on unvalidated JSONL content."""
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    cleaned = _METRICS_LABEL_RE.sub("", value).strip()
    return (cleaned or "unknown")[:_METRICS_LABEL_MAX_LEN]


def _metrics_bump(bucket: dict[str, int], label: str) -> None:
    """Increment ``bucket[label]``, folding a label beyond ``_METRICS_MAX_BUCKETS``
    distinct keys into "other" - caller-controlled labels (an arbitrary
    upload filename or a scripted /api/leads "source" value) must not let an
    attacker inflate the response with unbounded, ever-growing keys."""
    if label not in bucket and len(bucket) >= _METRICS_MAX_BUCKETS:
        label = "other"
    bucket[label] = bucket.get(label, 0) + 1


@app.get("/api/metrics", dependencies=[Depends(security.rate_limit), Depends(security.require_api_key)])
def api_metrics() -> dict:
    """Aggregate, PII-free counts from the lead-capture telemetry (leads.jsonl) -
    internal tooling for the operator (E8), not for public consumption: gated
    behind LINCHPIN_API_KEY, same as POST /api/jobs (a no-op when unset, so this
    doesn't force auth in local/dev use). Never returns a raw email or any other
    per-lead identifying value - only counts and small, sanitized, count-capped
    labeled buckets (see _metrics_label/_metrics_bump) - leads.jsonl's "source"
    and "dataset" fields are caller-controlled (a scripted /api/leads POST, or
    an uploaded file's own name on /api/demo-scan) and must never be trusted to
    already be safe, short, or non-PII-shaped by the time they reach this
    endpoint. leads.jsonl is the only operational telemetry stream in the
    codebase; there is nothing (yet) to report about commercial-package runs,
    which aren't logged anywhere. A line that is malformed JSON, or valid JSON
    that isn't an object, is skipped rather than crashing the request."""
    total = 0
    emails: set[str] = set()
    by_source: dict[str, int] = {}
    demo_scan_status: dict[str, int] = {}
    demo_scan_dataset: dict[str, int] = {}
    if LEADS_FILE.exists():
        for line in LEADS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            total += 1
            email = rec.get("email")
            if isinstance(email, str) and email.strip():
                emails.add(email.strip().lower())
            source = _metrics_label(rec.get("source"))
            _metrics_bump(by_source, source)
            if source == "demo-scan":
                _metrics_bump(demo_scan_status, _metrics_label(rec.get("status")))
                _metrics_bump(demo_scan_dataset, _metrics_label(rec.get("dataset")))
    return {
        "leads": {
            "total_captures": total,
            "unique_emails": len(emails),
            "by_source": by_source,
        },
        "demo_scan": {
            "total_runs": by_source.get("demo-scan", 0),
            "by_status": demo_scan_status,
            "by_dataset": demo_scan_dataset,
        },
    }


# ---- Control Tower (Linchpin 3.0 PR-7, plan S5): GET /api/events + ----------
# ---- POST /api/approvals/{id} + GET /tower ----------------------------------

_EVENTS_DEFAULT_LIMIT = 100
_EVENTS_MAX_LIMIT = 500
_APPROVED_BY_RE = re.compile(r"[^\w\s.,@\-]")
_APPROVED_BY_MAX_LEN = 80


def _event_to_dict(event: Event) -> dict:
    d = asdict(event)
    d["ts"] = event.ts.isoformat()
    return d


def _autonomy_record_to_dict(record: AutonomyRecord) -> dict:
    d = asdict(record)
    d["created_at"] = record.created_at.isoformat()
    d["acknowledged_at"] = record.acknowledged_at.isoformat() if record.acknowledged_at else None
    d["expires_at"] = record.expires_at.isoformat() if record.expires_at else None
    return d


def _promotion_record_to_dict(record: PromotionRecord) -> dict:
    d = asdict(record)
    d["created_at"] = record.created_at.isoformat()
    d["resolved_at"] = record.resolved_at.isoformat() if record.resolved_at else None
    return d


@app.get("/api/events", dependencies=[Depends(security.rate_limit)])
def api_events(
    limit: int = Query(_EVENTS_DEFAULT_LIMIT, ge=1, le=_EVENTS_MAX_LIMIT),
    event_type: str | None = Query(None, description="Filter to one Event.type, e.g. stock_below_rop"),
) -> dict:
    """Recent Control Tower events (scm_agent.events.EventLedger) -- the most
    recent `limit` rows (optionally filtered to one type), oldest-first.
    Windowed (EventLedger.list_recent), never an unbounded dump of an
    ever-growing table. Powers the /tower page's "eventos de hoy" feed
    (client-side fetch) and is read-only, same auth level as /api/portfolio."""
    ledger = _get_event_ledger()
    try:
        recent = ledger.list_recent(event_type=event_type, limit=limit)
    finally:
        ledger.close()
    return {"events": [_event_to_dict(e) for e in recent], "count": len(recent)}


@app.post(
    "/api/approvals/{approval_id}",
    dependencies=[Depends(security.rate_limit), Depends(security.require_api_key)],
)
def api_approve_pending(
    approval_id: str,
    approved_by: str = Query("operator", description="Who is completing this approval (audit trail)"),
) -> dict:
    """The one-click T2 approval endpoint: complete a pending
    scm_agent.autonomy.AutonomyRecord via acknowledge_pending() -- the SAME
    accept/approve function PR-6's autonomy.py built specifically for this
    endpoint (see its own docstring), not a second, parallel approval
    mechanism.

    Gated by require_api_key (this mutates state, same auth level as
    POST /api/jobs) AND rate_limit. An unknown id (never issued, or from a
    different LINCHPIN_AUTONOMY_PATH) is a 404; an id that is not currently
    pending (already acknowledged, or was never a T2 row) is a 409 -- both
    loud, actionable failures, never a silent no-op.

    Note: today this only completes an ANALYSIS-tier T2 item (the path
    enforce_analysis_tier()/handle_event_tiered() actually wire into
    AutonomyLedger). enforce_writeback_tier()'s T2 HANDOFF -- staging a real
    src.writeback.Changeset for the few writeback-capable tools -- is not
    yet persisted anywhere id-addressable (AutonomyRecord carries no
    changeset reference, and no caller threads one through the ledger), so
    there is nothing yet for this endpoint to approve()+apply() against for
    that path. Wiring that up (giving AutonomyRecord an optional serialized
    Changeset, and calling src.writeback.approve()+apply() here with a real
    900s TTL Approval when one is present) is left to a future PR rather
    than inventing a second, ad hoc changeset store here.
    """
    clean_by = _APPROVED_BY_RE.sub("", approved_by)[:_APPROVED_BY_MAX_LEN].strip() or "operator"
    ledger = _get_autonomy_ledger()
    try:
        try:
            outcome = acknowledge_pending(ledger, approval_id, clean_by)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown approval id: {approval_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        record = ledger.get(approval_id)
    finally:
        ledger.close()
    return {
        "status": outcome.status,
        "summary": outcome.summary,
        "record": _autonomy_record_to_dict(record) if record is not None else None,
    }


@app.post(
    "/api/promotions/{promotion_id}/approve",
    dependencies=[Depends(security.rate_limit), Depends(security.require_api_key)],
)
def api_approve_promotion(
    promotion_id: str,
    approved_by: str = Query("operator", description="Who is approving this promotion (audit trail)"),
) -> dict:
    """The human sign-off Golden Rule 11 requires for a T2->T1 autonomy
    promotion (Linchpin 3.0 PR-9): complete a PENDING
    scm_agent.autonomy_promotion.PromotionRecord via approve_promotion() --
    the SAME function PR-9 built specifically for this endpoint, not a
    second, parallel approval mechanism. This is what actually mutates
    config/event_routing.yaml's autonomy_tier for subsequent events of that
    type.

    Gated the same as POST /api/approvals/{id} (require_api_key + rate_limit
    -- this mutates a config file on disk, not just an in-memory ledger row).
    An unknown id is a 404; an id that is not currently pending, OR whose
    proposal's expected from_tier no longer matches the file's current tier
    (config moved since the proposal was created), is a 409 -- all loud,
    actionable failures, never a silent no-op.
    """
    clean_by = _APPROVED_BY_RE.sub("", approved_by)[:_APPROVED_BY_MAX_LEN].strip() or "operator"
    ledger = _get_promotion_ledger()
    try:
        try:
            outcome = approve_promotion(ledger, promotion_id, clean_by, routing_path=EVENT_ROUTING_PATH)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown promotion id: {promotion_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        record = ledger.get(promotion_id)
    finally:
        ledger.close()
    return {
        "status": outcome.status,
        "summary": outcome.summary,
        "record": _promotion_record_to_dict(record) if record is not None else None,
    }


@app.post(
    "/api/promotions/{promotion_id}/reject",
    dependencies=[Depends(security.rate_limit), Depends(security.require_api_key)],
)
def api_reject_promotion(
    promotion_id: str,
    rejected_by: str = Query("operator", description="Who is rejecting this promotion (audit trail)"),
) -> dict:
    """Reject a PENDING T2->T1 autonomy promotion (Linchpin 3.0 PR-9) --
    config/event_routing.yaml is never touched. Same auth level and 404/409
    error-code conventions as the approve endpoint above."""
    clean_by = _APPROVED_BY_RE.sub("", rejected_by)[:_APPROVED_BY_MAX_LEN].strip() or "operator"
    ledger = _get_promotion_ledger()
    try:
        try:
            record = reject_promotion(ledger, promotion_id, clean_by)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown promotion id: {promotion_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        ledger.close()
    return {"status": "rejected", "record": _promotion_record_to_dict(record)}


@app.get("/tower")
def tower_page() -> HTMLResponse:
    """The Control Tower dashboard tab (Linchpin 3.0 PR-7, plan S5): T1
    auto-executed actions and T2 pending approvals are rendered server-side
    from the real AutonomyLedger at request time; "eventos de hoy" is
    populated client-side via GET /api/events; pending T2->T1 autonomy
    promotions (Linchpin 3.0 PR-9) are rendered server-side from the real
    PromotionLedger; the A4 per-tool reliability section is a clearly-labeled
    placeholder until a future PR wires it into this page
    (src/verify/backtest.py + src/verify/reliability.py already exist as of
    PR-8, but nothing here reads them yet)."""
    autonomy_ledger = _get_autonomy_ledger()
    try:
        records = autonomy_ledger.list_all()
    finally:
        autonomy_ledger.close()
    t1 = [r for r in records if r.status == STATUS_AUTO_EXECUTED][-T1_DISPLAY_LIMIT:]
    # A lapsed T2 record (approval window closed) is dropped from the actionable
    # queue even before a sweep flips it to STATUS_EXPIRED -- otherwise the page
    # would render a live "Aprobar" button that can only 409. is_expired() uses
    # the current time; a legacy row with no deadline (NULL) is never expired.
    t2 = [r for r in records if r.status == STATUS_PENDING and not r.is_expired()]

    promotion_ledger = _get_promotion_ledger()
    try:
        promotions = promotion_ledger.list_pending()
    finally:
        promotion_ledger.close()

    return HTMLResponse(render_tower_html(t1_records=t1, t2_records=t2, promotion_records=promotions))


@app.get("/pricing")
def pricing_page() -> HTMLResponse:
    """The Pricing dashboard tab (Linchpin 3.0 PR-13, plan sections 6.11/9):
    position matrix summary, freshness and quarantine rate. No persisted
    "last run" store exists yet (a PriceIntelReport lives only for one
    jobs.price_intelligence.run() call, via the CLI or the registered agent
    tool) -- this route renders the honest empty state, the exact precedent
    /tower already sets for its own not-yet-wired A4 section (webapp/
    pricing_page.py's own docstring). No number is ever fabricated here."""
    return HTMLResponse(render_pricing_html())


def _warehouse_params(
    building_w: float, building_d: float, height: float, levels: int,
    modules: int, aisle_width: float, docks: int, gates: int, yard_depth: float,
) -> dict:
    return {
        "building": {"width_m": building_w, "depth_m": building_d, "height_m": height, "levels": levels},
        "racks": {"modules": modules, "aisle_width_m": aisle_width},
        "docks": {"count": docks, "face": "south"},
        "gates": {"count": gates},
        "yard_depth_m": yard_depth,
    }


@app.get("/api/warehouse")
def api_warehouse(
    building_w: float = Query(80.0, gt=0, le=1000),
    building_d: float = Query(80.0, gt=0, le=1000),
    height: float = Query(12.0, gt=0, le=100),
    levels: int = Query(4, ge=1, le=20),
    modules: int = Query(6, ge=1, le=500),
    aisle_width: float = Query(3.5, gt=0, le=20),
    docks: int = Query(8, ge=1, le=500),
    gates: int = Query(2, ge=1, le=100),
    yard_depth: float = Query(40.0, ge=0, le=500),
) -> dict:
    params = _warehouse_params(building_w, building_d, height, levels, modules, aisle_width, docks, gates, yard_depth)
    layout = generate_layout(params)
    issues = validate_layout(layout)
    if issues:
        raise HTTPException(status_code=400, detail={"qa_issues": issues})
    return layout.to_dict()


@app.get("/warehouse")
def warehouse_page(
    building_w: float = Query(80.0, gt=0, le=1000),
    building_d: float = Query(80.0, gt=0, le=1000),
    height: float = Query(12.0, gt=0, le=100),
    levels: int = Query(4, ge=1, le=20),
    modules: int = Query(6, ge=1, le=500),
    aisle_width: float = Query(3.5, gt=0, le=20),
    docks: int = Query(8, ge=1, le=500),
    gates: int = Query(2, ge=1, le=100),
    yard_depth: float = Query(40.0, ge=0, le=500),
) -> HTMLResponse:
    params = _warehouse_params(building_w, building_d, height, levels, modules, aisle_width, docks, gates, yard_depth)
    return HTMLResponse(to_html(generate_layout(params)))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/console")
def console() -> FileResponse:
    """The live agent console — a thin UI over POST /api/jobs."""
    return FileResponse(STATIC_DIR / "prototype" / "index.html")


@app.get("/demo")
def demo_page() -> FileResponse:
    """Lead-gated self-serve demo: capture an email, then upload data or use the
    bundled sample dataset and see what the engine recommends."""
    return FileResponse(STATIC_DIR / "demo" / "index.html")


@app.get("/operator")
def operator_portfolio() -> FileResponse:
    """The Operator Portfolio — renders documentation/operator/*.md as a web page."""
    return FileResponse(STATIC_DIR / "operator" / "index.html")


@app.get("/decisiones")
def decisiones_page() -> FileResponse:
    """The decision-support page — friendly guardrail calculators for the operator."""
    return FileResponse(STATIC_DIR / "decisiones" / "index.html")


@app.get("/paquetes")
def paquetes_index() -> HTMLResponse:
    """The 7-package sales grid — structured data from webapp/offers.py, CTAs
    degrade to mailto when Stripe/Calendly env vars are not configured."""
    return HTMLResponse(render_index_html(OFFERS, get_operator_profile()))


@app.get("/paquetes/{slug}")
def paquetes_offer(slug: str) -> HTMLResponse:
    """One-pager for a single package: fetches its real documentation/paquetes/*.md
    client-side (mounted at /paquetes-docs) and renders it with marked.js — same
    proven pattern as /operator, no server-side markdown dependency needed."""
    offer = get_offer(slug)
    if offer is None:
        raise HTTPException(status_code=404, detail="unknown package")
    return HTMLResponse(render_offer_html(offer, get_operator_profile()))


@app.get("/stocky-alternative")
def stocky_alternative_page() -> HTMLResponse:
    """SEO/conversion landing page for the "stocky alternative" search wave --
    Shopify delisted Stocky from the App Store Feb 2026 and shuts it down
    2026-08-31. Points at the two packages that actually replace Stocky's
    forecasting/reorder-point/PO-suggestion job (see
    webapp/stocky_alternative_page.py's module docstring)."""
    offer_starter = get_offer("starter-fundamentos")
    offer_diagnostico = get_offer("diagnostico-arranque")
    assert offer_starter is not None and offer_diagnostico is not None, (
        "starter-fundamentos/diagnostico-arranque must exist in webapp.offers.OFFERS"
    )
    return HTMLResponse(render_stocky_alternative_html(offer_starter, offer_diagnostico))


@app.get("/one-plan")
def one_plan_page() -> HTMLResponse:
    """English AU/NZ agency landing page. Positions Kern as a fractional
    planning team that works demand, stock, purchasing and pricing as a single
    plan. CTAs into the SAME two real offers the stocky page uses -- no new
    pricing is authored on the page (see webapp/one_plan_page.py's docstring for
    the banned-words + fractional-team-economics guardrails)."""
    offer_starter = get_offer("starter-fundamentos")
    offer_diagnostico = get_offer("diagnostico-arranque")
    assert offer_starter is not None and offer_diagnostico is not None, (
        "starter-fundamentos/diagnostico-arranque must exist in webapp.offers.OFFERS"
    )
    return HTMLResponse(render_one_plan_html(offer_starter, offer_diagnostico))


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
# Read-only markdown source for the Operator Portfolio page (single source of truth).
app.mount("/operator-docs", StaticFiles(directory=str(OPERATOR_DOCS_DIR)), name="operator-docs")
# Read-only markdown source for the sales one-pagers (single source of truth).
app.mount("/paquetes-docs", StaticFiles(directory=str(PAQUETES_DOCS_DIR)), name="paquetes-docs")
app.mount("/jobs-output", StaticFiles(directory=str(JOBS_OUTPUT_DIR)), name="jobs-output")

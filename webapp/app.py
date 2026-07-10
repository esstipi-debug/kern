"""FastAPI backend for the Inventory Planner — a thin layer over the engine.

All numbers come from src/ (forecasting, policies, constraints). The frontend is
a single static page; this app exposes the portfolio computation and serves it.

Run:
    py -m uvicorn webapp.app:app --reload      # from the repo root
    # then open http://localhost:8000
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

# Make `src` importable no matter where uvicorn is launched from.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from scm_agent import Orchestrator  # noqa: E402
from src.constraints import InventoryItem, allocate_under_budget  # noqa: E402
from src.forecasting import ForecastResult, forecast_demand  # noqa: E402
from src.mcp_keys import DEFAULT_PATH as MCP_KEYS_DEFAULT_PATH  # noqa: E402
from src.mcp_keys import McpKeyStore  # noqa: E402
from src.policies import continuous_review_sq, periodic_review_rs  # noqa: E402
from src.sources import CsvDemandSource  # noqa: E402
from warehouse.generator import generate_layout  # noqa: E402
from warehouse.html_export import to_html  # noqa: E402
from warehouse.qa import validate as validate_layout  # noqa: E402
from webapp import observability, security  # noqa: E402
from webapp.decisions import router as decisions_router  # noqa: E402
from webapp.mcp_auth import McpKeyAuthMiddleware  # noqa: E402
from webapp.mcp_server import build_mcp_server  # noqa: E402
from webapp.offers import OFFERS, get_offer  # noqa: E402
from webapp.operator_profile import get_operator_profile  # noqa: E402
from webapp.paquetes_page import render_index_html, render_offer_html  # noqa: E402

DATA_FILE = _REPO_ROOT / "data" / "sample_demand_portfolio.csv"
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

    _prune_old_jobs()

    import tempfile

    job_dir = Path(tempfile.mkdtemp(dir=JOBS_OUTPUT_DIR))
    data_path: str | None = None
    if file is not None and file.filename:
        # Never trust the client-supplied filename: reduce to a bare basename and
        # pin the write inside the per-job dir (blocks path traversal / absolute writes).
        raw_name = (file.filename or "upload").replace("\\", "/")
        safe_name = os.path.basename(raw_name)
        if not safe_name or safe_name in (".", ".."):
            raise HTTPException(status_code=400, detail="invalid upload filename")
        upload = job_dir / safe_name
        if upload.resolve().parent != job_dir.resolve():
            raise HTTPException(status_code=400, detail="invalid upload filename")
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"upload exceeds {MAX_UPLOAD_BYTES} bytes")
        upload.write_bytes(data)
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
    }


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


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
# Read-only markdown source for the Operator Portfolio page (single source of truth).
app.mount("/operator-docs", StaticFiles(directory=str(OPERATOR_DOCS_DIR)), name="operator-docs")
# Read-only markdown source for the sales one-pagers (single source of truth).
app.mount("/paquetes-docs", StaticFiles(directory=str(PAQUETES_DOCS_DIR)), name="paquetes-docs")
app.mount("/jobs-output", StaticFiles(directory=str(JOBS_OUTPUT_DIR)), name="jobs-output")

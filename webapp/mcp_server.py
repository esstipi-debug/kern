"""Linchpin's read-only MCP server (Phase A go-to-market — see linchpin-monetization-plan).

Exposes 8 of the 34 registered agent tools to remote MCP clients (other AI
agents, via Streamable HTTP): analysis only, no writeback. `odoo_replenishment`
and every other tool that mutates a client's system of record is deliberately
NOT exposed here — that stays for direct clients only, gated by the audited
writeback safety plane (`src/writeback.py`), not by anything in this file.

Bridge design: an MCP tool call carries data inline (JSON rows), but every
underlying job's `prepare()` reads a CSV path (`Orchestrator.run(data_path=...)`).
`_run_analysis_tool` is the one shared seam that writes `rows` to a throwaway
temp CSV, calls the SAME orchestrator entry point `webapp/app.py`'s
`POST /api/jobs` uses, and reads back the markdown report `deliver()` already
writes — reusing the existing client-ready deliverable formatting instead of
inventing a second serialization path. `job_type` is passed explicitly, so the
orchestrator's brief-based intent classifier is bypassed entirely (confidence=1.0,
see `scm_agent/intent.py::classify`'s `job_type_override` path) — each tool call
deterministically hits the tool it names, no NLU guessing.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field, field_validator

from scm_agent import Orchestrator

SERVER_NAME = "linchpin_mcp"

# FastMCP auto-enables DNS-rebinding Host-header checking whenever it's built
# with `host="127.0.0.1"` (the default), but its own auto-allowlist only
# covers localhost/127.0.0.1/::1 - so a deployed instance would 421 every
# single real request regardless of a valid API key, since the client's Host
# header is the deploy's real hostname, not "127.0.0.1". The per-client
# X-API-Key gate (webapp/mcp_auth.py) already authenticates every request to
# this mount, so Host-based DNS-rebinding protection is redundant defense in
# depth here, not the primary control - but it still needs to actually allow
# the real deploy host rather than silently blocking everyone. Comma-separated,
# e.g. "linchpin.fly.dev,linchpin.example.com"; unset -> localhost-only (safe
# local-dev default, matches FastMCP's own auto-enable behavior).
_EXTRA_ALLOWED_HOSTS = [h.strip() for h in os.environ.get("LINCHPIN_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]

_READ_ONLY_ANALYSIS_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


class LinchpinAnalysisInput(BaseModel):
    """Shared input shape for every read-only analysis tool on this server."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    rows: list[dict[str, Any]] = Field(
        ...,
        description=(
            "Tabular input data, one dict per row (like CSV rows as JSON objects). "
            "Column names are matched flexibly (case/spacing-tolerant aliases), but "
            "see each tool's own description for the canonical columns it expects."
        ),
        min_length=1,
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional tool-specific parameters (e.g. service_level, cost_ratio) - see the tool description for what it reads.",
    )
    client_label: str = Field(
        default="MCP client",
        description="Cosmetic label shown in the generated report's client field.",
        max_length=100,
    )

    @field_validator("client_label")
    @classmethod
    def _non_empty_label(cls, v: str) -> str:
        return v or "MCP client"


def _run_analysis_tool_sync(orchestrator: Orchestrator, job_type: str, params: LinchpinAnalysisInput) -> str:
    """Blocking implementation: rows -> temp CSV -> orchestrator.run() -> JSON string.

    Runs off the event loop (see the `asyncio.to_thread` callers below) since both
    the CSV write and the actual analysis are CPU/disk-bound, not network I/O.
    """
    with tempfile.TemporaryDirectory(prefix="linchpin_mcp_") as tmp:
        tmp_dir = Path(tmp)
        data_path = tmp_dir / "input.csv"
        try:
            pd.DataFrame(params.rows).to_csv(data_path, index=False)
        except (ValueError, TypeError) as exc:
            return json.dumps({"status": "error", "summary": f"Could not read rows as tabular data: {exc}"})

        result = orchestrator.run(
            f"MCP analysis request ({job_type})",
            data_path=str(data_path),
            overrides=params.params,
            job_type=job_type,
            client=params.client_label,
            out_dir=tmp_dir / "out",
        )

        report_markdown = None
        for name, path in result.deliverables.items():
            if name == "report" or str(path).lower().endswith(".md"):
                try:
                    report_markdown = Path(path).read_text(encoding="utf-8")
                except OSError:
                    pass
                break

        response = {
            "status": result.status,
            "tool": result.tool,
            "confidence": result.confidence,
            "summary": result.summary,
            "qa_issues": result.qa_issues,
            "clarifications": result.clarifications,
            "citations": result.citations,
            "report_markdown": report_markdown,
        }
        return json.dumps(response, indent=2, default=str)


def build_mcp_server(orchestrator: Orchestrator | None = None) -> FastMCP:
    """Construct the MCP server. Pass an existing ``Orchestrator`` (e.g. from
    ``webapp/app.py``'s own singleton) to avoid loading the knowledge graph twice
    when this is mounted into the same process; a fresh one is built otherwise
    (used by tests and standalone runs)."""
    orch = orchestrator if orchestrator is not None else Orchestrator()
    # streamable_http_path="/": FastMCP's own default ("/mcp") is meant for
    # standalone use. Mounted under "/mcp" in webapp/app.py, that default would
    # make the real route "/mcp/mcp" - one path segment more than the documented
    # client URL (docs/MCP_SERVER.md: POST .../mcp/) and every existing test.
    # Rooting the sub-app at "/" makes the mount's own path the whole story.
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", *_EXTRA_ALLOWED_HOSTS],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    )
    mcp = FastMCP(SERVER_NAME, streamable_http_path="/", transport_security=transport_security)

    async def _run(job_type: str, params: LinchpinAnalysisInput) -> str:
        return await asyncio.to_thread(_run_analysis_tool_sync, orch, job_type, params)

    @mcp.tool(
        name="linchpin_inventory_optimize",
        annotations={"title": "Optimize Inventory Policy", **_READ_ONLY_ANALYSIS_ANNOTATIONS},
    )
    async def linchpin_inventory_optimize(params: LinchpinAnalysisInput) -> str:
        """Forecast demand and recommend (s,Q)/(R,S) reorder policies + a budget-fit
        inventory plan, per SKU.

        Rows need columns: date, product_id, quantity (required); unit_cost,
        lead_time_days (optional, improve the plan when present). One row per
        SKU-period, e.g. {"date": "2026-01-01", "product_id": "SKU-A", "quantity": 42,
        "unit_cost": 12.5, "lead_time_days": 7}.

        Useful params: service_level (0-1, default 0.95), holding_rate (default 0.25),
        order_cost (default 75.0), budget (optional spend cap), periods_per_year
        (default 52.0).

        Returns: JSON with status ("ok"/"needs_data"/"needs_clarification"/"qa_failed"/
        "error"), summary, confidence, citations, and report_markdown (the full
        client-ready analysis as markdown) when status is "ok".
        """
        return await _run("inventory_optimization", params)

    @mcp.tool(
        name="linchpin_classify_abc_xyz",
        annotations={"title": "ABC-XYZ Classify SKUs", **_READ_ONLY_ANALYSIS_ANNOTATIONS},
    )
    async def linchpin_classify_abc_xyz(params: LinchpinAnalysisInput) -> str:
        """Classify SKUs into the 9-cell ABC (value) x XYZ (demand variability) matrix
        and assign a review policy + service-level target per cell.

        Rows need columns: product_id, quantity (demand history), unit_cost. One row
        per SKU-period, e.g. {"product_id": "SKU-A", "quantity": 42, "unit_cost": 12.5}.

        Useful params: abc_thresholds (default [0.80, 0.95], cumulative value share
        cut points), cv_cuts (default [0.5, 1.0], coefficient-of-variation cut points).

        Returns: JSON with status, summary, confidence, citations, and report_markdown
        (per-cell counts, value share, and the policy table) when status is "ok".
        """
        return await _run("abc_xyz", params)

    @mcp.tool(
        name="linchpin_newsvendor_order_quantity",
        annotations={"title": "Newsvendor Order Quantity", **_READ_ONLY_ANALYSIS_ANNOTATIONS},
    )
    async def linchpin_newsvendor_order_quantity(params: LinchpinAnalysisInput) -> str:
        """Set the profit-maximizing one-shot order quantity per SKU for perishable,
        seasonal, or spare-part demand (the critical-ratio newsvendor model).

        Rows need columns: product_id, a mean-demand column, price, unit_cost. A
        demand-std column and salvage/goodwill columns are optional but sharpen the
        result, e.g. {"product_id": "SKU-A", "mean_demand": 100, "std_demand": 20,
        "price": 30, "unit_cost": 12, "salvage_value": 4}.

        Returns: JSON with status, summary, confidence, citations, and report_markdown
        (critical ratio, order quantity, expected profit, implied service level per
        SKU) when status is "ok".
        """
        return await _run("newsvendor", params)

    @mcp.tool(
        name="linchpin_forecast_demand",
        annotations={"title": "Forecast Demand & Method Fit", **_READ_ONLY_ANALYSIS_ANNOTATIONS},
    )
    async def linchpin_forecast_demand(params: LinchpinAnalysisInput) -> str:
        """Segment SKUs by forecastability, auto-select and backtest the matching
        forecasting method (SES/Croston, or AutoETS/TSB when installed), and quantify
        forecast value-add versus a naive baseline.

        Rows need columns: product_id, a quantity column, and a period column (date
        or sequential period index). One row per SKU-period, e.g. {"product_id":
        "SKU-A", "period": "2026-W01", "quantity": 42}.

        Useful params: holdout_fraction (default 0.25, share of history held out for
        backtesting), min_backtest_periods (default 4).

        Returns: JSON with status, summary, confidence, citations, and report_markdown
        (per-SKU forecastability class, chosen method, and accuracy vs. naive) when
        status is "ok".
        """
        return await _run("forecast", params)

    @mcp.tool(
        name="linchpin_financial_kpis",
        annotations={"title": "Inventory Financial KPIs", **_READ_ONLY_ANALYSIS_ANNOTATIONS},
    )
    async def linchpin_financial_kpis(params: LinchpinAnalysisInput) -> str:
        """Roll up the per-SKU finance pack: inventory turns, DIO, GMROI, sell-through,
        inventory-to-sales, cash-to-cash, and flag the weakest-GMROI SKUs.

        Rows need columns: product_id, a COGS column, an inventory-value column. Margin,
        units-sold, units-on-hand, and net-sales columns are optional but improve
        coverage, e.g. {"product_id": "SKU-A", "cogs": 500, "inventory_value": 1200,
        "units_sold": 40, "units_on_hand": 15}.

        Useful params: dso_days, dpo_days, dio_days (working-capital cash-cycle inputs).

        Returns: JSON with status, summary, confidence, citations, and report_markdown
        (the KPI table + weakest-GMROI SKUs) when status is "ok".
        """
        return await _run("financial_kpis", params)

    @mcp.tool(
        name="linchpin_price_optimize",
        annotations={"title": "Optimize Price per SKU", **_READ_ONLY_ANALYSIS_ANNOTATIONS},
    )
    async def linchpin_price_optimize(params: LinchpinAnalysisInput) -> str:
        """Estimate per-SKU price elasticity from a price/quantity history and
        recommend a margin-maximizing price.

        Rows need columns: product_id, a price column, a quantity-sold column, one row
        per SKU-period (price changes over time are what identify elasticity), e.g.
        {"product_id": "SKU-A", "date": "2026-01-01", "price": 29.99, "quantity": 120}.

        Useful params: cost_ratio (default 0.6, used to impute unit cost when not
        directly observable - lowers confidence on the affected SKUs).

        Returns: JSON with status, summary, confidence, citations, and report_markdown
        (elasticity, recommended price, and confidence per SKU - inelastic or
        insufficient-data SKUs are flagged, not silently priced) when status is "ok".
        """
        return await _run("pricing", params)

    @mcp.tool(
        name="linchpin_audit_data_quality",
        annotations={"title": "Audit Product Master Data Quality", **_READ_ONLY_ANALYSIS_ANNOTATIONS},
    )
    async def linchpin_audit_data_quality(params: LinchpinAnalysisInput) -> str:
        """Audit a product master for duplicate SKUs (shared GTIN or fuzzy name match),
        invalid GTIN/UPC check digits, and completeness gaps, then rank remediation.

        Rows need columns: product_id, a product-name column. GTIN and unit-cost
        columns are optional but sharpen the audit, e.g. {"product_id": "SKU-A",
        "name": "Widget 12mm", "gtin": "012345678905"}.

        Useful params: name_threshold (default 90.0, fuzzy-match similarity cutoff
        0-100 for flagging near-duplicate names).

        Returns: JSON with status, summary, confidence, citations, and report_markdown
        (quality score + a ranked clean-up plan) when status is "ok".
        """
        return await _run("data_quality", params)

    @mcp.tool(
        name="linchpin_whatif_sensitivity",
        annotations={"title": "What-If Sensitivity Sweep", **_READ_ONLY_ANALYSIS_ANNOTATIONS},
    )
    async def linchpin_whatif_sensitivity(params: LinchpinAnalysisInput) -> str:
        """Sweep a planning assumption (demand, holding cost, lead time, ...) over a
        low/high band against the inventory policy's cost, ranking drivers by impact
        (a tornado chart's underlying data) and bounding the optimistic/pessimistic case.

        Rows need columns: a driver-name column, low and high band columns, plus a
        base-value/unit column, e.g. {"driver": "demand", "low": -0.15, "high": 0.20,
        "base_value": 100, "unit": "units/period"}.

        Useful params: metric (default "annual_cost"), budget_pct (default 0.10),
        maximize (default false).

        Returns: JSON with status, summary, confidence, citations, and report_markdown
        (drivers ranked by impact + the optimistic/pessimistic bound) when status is "ok".
        """
        return await _run("whatif", params)

    return mcp


if __name__ == "__main__":
    build_mcp_server().run(transport="streamable_http")

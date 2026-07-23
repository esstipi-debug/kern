"""Kern's read-only MCP server (Phase A go-to-market — see linchpin-monetization-plan).

Exposes 33 of the 41 registered agent tools to remote MCP clients (other AI
agents, via Streamable HTTP): analysis only, no writeback. `odoo_replenishment`
and `excel_replenishment` mutate a client's system of record and are
deliberately NOT exposed here — that stays for direct clients only, gated by the
audited writeback safety plane (`src/writeback.py`), not by anything in this
file. `leadership_chain` and `warehouse_layout` are also off this surface: they
don't consume tabular rows, so the bridge below has nothing to feed them; `repricing` needs TWO tabular inputs (price/quantity history plus a competitor position table), which the single-rows bridge cannot carry.

The tool surface itself lives in `webapp/mcp_tool_specs.py` (one spec per tool:
name, job_type, title, calling contract) — this module just registers each spec
against the shared bridge.

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
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field, field_validator

from scm_agent import Orchestrator
from webapp.mcp_tool_specs import TOOL_SPECS, MCPToolSpec

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


class KernAnalysisInput(BaseModel):
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


def _run_analysis_tool_sync(orchestrator: Orchestrator, job_type: str, params: KernAnalysisInput) -> str:
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
            # The never-unprotected contract, machine-readable - parity with
            # POST /api/jobs (webapp/app.py). An MCP client is itself an agent:
            # it needs the ranked options / prepared handoff / escalation
            # (route_to/sla/reason), not just the human-readable report, to know
            # what a human still has to decide. Orchestrator.run() always
            # attaches a GuidedOutcome (falling back to a generic one derived
            # from `status`), so this is only null for a non-orchestrator result.
            # Frozen dataclasses all the way down (src/guided.py) -> plain,
            # JSON-safe dicts.
            "guided": asdict(result.guided) if result.guided is not None else None,
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

    async def _run(job_type: str, params: KernAnalysisInput) -> str:
        return await asyncio.to_thread(_run_analysis_tool_sync, orch, job_type, params)

    def _register(spec: MCPToolSpec) -> None:
        # A fresh closure per spec (late-binding-safe); FastMCP reads the tool's
        # description from __doc__ and its input schema from the signature, which
        # is the same shared KernAnalysisInput shape for every tool.
        async def _tool(params: KernAnalysisInput) -> str:
            return await _run(spec.job_type, params)

        _tool.__name__ = spec.name
        _tool.__doc__ = spec.description
        mcp.tool(
            name=spec.name,
            annotations={"title": spec.title, **_READ_ONLY_ANALYSIS_ANNOTATIONS},
        )(_tool)

    for spec in TOOL_SPECS:
        _register(spec)

    return mcp


if __name__ == "__main__":
    build_mcp_server().run(transport="streamable_http")

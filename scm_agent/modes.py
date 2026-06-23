"""Linchpin operating modes — two role profiles over the same engine.

Research-grounded split (inventory management is a strict subset of supply-chain
management; the inventory role is stock-centric / internal / short-horizon, the
SCM role is end-to-end / external / strategic):

- **Inventory mode** — the brand's own stock: levels, reorder points, safety
  stock, ABC-XYZ, reconciliation/cycle counts, inventory reporting.
- **SCM mode** — the end-to-end superset: + demand & supply planning (S&OP),
  sourcing & supplier performance, landed cost, logistics, cost-to-serve, risk
  & resilience, sustainability, and supply-chain leadership.

A mode scopes (a) which tools the orchestrator may route to, (b) the persona it
presents, and (c) the deliverable / KPI catalogue it offers. Selecting a mode is
one call to `build_registry()` / `orchestrator_for()` — no edits to tools.
"""
from __future__ import annotations

from dataclasses import dataclass

from .orchestrator import Orchestrator
from .registry import ToolRegistry
from .tools import build_default_registry

# Tool keys that belong to the stock-centric Inventory role. SCM is the superset
# (tool_keys=None => every registered tool, including ones added later).
_INVENTORY_TOOL_KEYS = frozenset({"inventory_optimization", "pricing"})


@dataclass(frozen=True)
class Mode:
    """A role profile: a tool surface + persona + deliverable/KPI catalogue."""

    key: str
    label: str
    persona: str
    tool_keys: frozenset[str] | None  # None => all registered tools (superset)
    deliverables: tuple[str, ...]
    kpis: tuple[str, ...]

    def includes(self, tool_key: str) -> bool:
        """Whether this mode exposes a given tool. SCM (None) includes everything."""
        return self.tool_keys is None or tool_key in self.tool_keys


INVENTORY = Mode(
    key="inventory",
    label="Inventory / E-commerce Inventory Specialist",
    persona=(
        "an e-commerce inventory & replenishment specialist who owns the brand's own "
        "stock: levels, reorder points, safety stock, ABC-XYZ classification, "
        "reconciliation / cycle counts, and inventory reporting — not sourcing, "
        "logistics, or network strategy"
    ),
    tool_keys=_INVENTORY_TOOL_KEYS,
    deliverables=(
        "Inventory policy document (targets, reorder points, safety stock, service levels)",
        "Reorder-point & safety-stock model",
        "ABC-XYZ classification + per-segment policy",
        "Stock reconciliation / cycle-count plan (IRA)",
        "Excess & obsolete (E&O) / dead-stock report",
        "Demand forecast package (for replenishment)",
        "Inventory KPI dashboard",
        "Purchase-order / replenishment plan",
    ),
    kpis=(
        "Inventory record accuracy (IRA)",
        "Stockout rate",
        "Fill rate",
        "Inventory turns",
        "Days inventory outstanding (DIO)",
        "Carrying cost",
        "Sell-through",
        "Excess / obsolete value",
    ),
)

SCM = Mode(
    key="scm",
    label="Supply Chain Manager / Consultant",
    persona=(
        "a supply chain manager and consultant who owns the end-to-end flow: "
        "demand & supply planning (S&OP), sourcing & supplier performance, "
        "procurement & landed cost, logistics, inventory strategy, cost-to-serve, "
        "risk & resilience, sustainability, and supply-chain leadership"
    ),
    tool_keys=None,  # superset: every registered tool, including future ones
    deliverables=(
        "Supply chain diagnostic / health assessment",
        "30/60/90-day roadmap tied to KPIs",
        "S&OP / IBP deck + monthly cadence",
        "Demand plan package (forecast value-add, bias)",
        "Supplier scorecard + quarterly business review",
        "Cost-to-serve analysis (by customer / channel / SKU)",
        "Sourcing & landed-cost / supplier-selection study",
        "Network / fulfillment (3PL) study",
        "Working-capital / cash-release plan (cash-to-cash)",
        "Risk & resilience map (single-source, TTR/TTS)",
        "Sustainability / reverse-logistics assessment",
        "Leadership diagnostic (CHAIN model)",
        "Inventory policy + KPI dashboard (inherited from Inventory mode)",
    ),
    kpis=(
        "OTIF / DIFOT",
        "Perfect order rate",
        "Forecast accuracy (WAPE / MAPE) + bias",
        "Cash-to-cash cycle (CCC)",
        "Cost-to-serve / SC cost % of revenue",
        "SCOR Level-1 (reliability, responsiveness, agility, cost, assets)",
        "Inventory turns / DIO",
        "Fill rate / service level",
    ),
)

MODES: dict[str, Mode] = {INVENTORY.key: INVENTORY, SCM.key: SCM}
DEFAULT_MODE = SCM  # the superset — never narrows capability unless asked


def get_mode(name: str | None) -> Mode:
    """Resolve a mode by key (case-insensitive). Unknown / empty -> SCM (superset)."""
    if not name:
        return DEFAULT_MODE
    return MODES.get(name.strip().lower(), DEFAULT_MODE)


def build_registry(mode: Mode, full: ToolRegistry | None = None) -> ToolRegistry:
    """A registry containing only the tools this mode exposes.

    SCM (tool_keys=None) returns the full registry unchanged. Inventory returns a
    filtered copy. Tools added later default into SCM only, never silently into
    Inventory — the stock-role surface stays deliberately narrow.
    """
    full = full if full is not None else build_default_registry()
    if mode.tool_keys is None:
        return full
    scoped = ToolRegistry()
    for tool in full.list():
        if mode.includes(tool.key):
            scoped.register(tool)
    return scoped


def orchestrator_for(mode: Mode, **kwargs) -> Orchestrator:
    """An Orchestrator scoped to a mode's tool surface and narrating in its persona
    (provider/knowledge/persona overridable via kwargs)."""
    kwargs.setdefault("persona", mode.persona)
    return Orchestrator(registry=build_registry(mode), **kwargs)

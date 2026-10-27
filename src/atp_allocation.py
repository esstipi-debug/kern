"""Available-to-Promise (ATP) Allocation & Multi-Level BOM Bottleneck Engine.

Implements:
1. Multi-level Bill of Materials (BOM) modeled as a Directed Acyclic Graph (DAG).
2. Topological critical path calculation for Cumulative Lead Time (CLT).
3. Capacity rationing between Tier-1 contractual SLA commitments (penalty-bearing)
   and uncommitted spot/campaign demand.
4. Bottleneck component detection and deterministic order quoting.

Pure, deterministic module. Zero external framework dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class BomEdge:
    """Component requirement relationship."""
    parent_sku: str
    child_sku: str
    quantity_per_parent: float = 1.0


@dataclass(frozen=True)
class Tier1Contract:
    """Contractual commitment with penalties for non-delivery."""
    customer_id: str
    sku: str
    committed_units: float
    unit_price: float
    penalty_per_unit: float


@dataclass(frozen=True)
class AtpQuote:
    """Deterministic order commitment quote."""
    sku: str
    requested_units: float
    immediate_available_units: float
    shortage_units: float
    bottleneck_component: Optional[str]
    cumulative_lead_time_days: int
    is_fulfilled_within_requested_time: bool
    tier1_reserved_units: float
    recommendation: str  # "FULFILL_IMMEDIATE" | "SPLIT_SHIPMENT" | "AWAIT_CLT"


class BomGraph:
    """Directed Acyclic Graph representing assembly hierarchies."""

    def __init__(self) -> None:
        self._lead_times: Dict[str, int] = {}
        self._is_assembly: Dict[str, bool] = {}
        self._children: Dict[str, List[Tuple[str, float]]] = {}  # parent -> [(child, qty)]
        self._parents: Dict[str, List[str]] = {}                 # child -> [parents]

    def add_item(self, sku: str, lead_time_days: int, is_assembly: bool = False) -> None:
        """Register an item with its processing or procurement lead time."""
        if lead_time_days < 0:
            raise ValueError(f"Lead time for {sku} cannot be negative: {lead_time_days}")
        self._lead_times[sku] = lead_time_days
        self._is_assembly[sku] = is_assembly
        if sku not in self._children:
            self._children[sku] = []
        if sku not in self._parents:
            self._parents[sku] = []

    def add_dependency(self, parent_sku: str, child_sku: str, quantity_per_parent: float = 1.0) -> None:
        """Add a child component requirement to a parent assembly."""
        if quantity_per_parent <= 0:
            raise ValueError(f"Quantity per parent must be positive: {quantity_per_parent}")
        if parent_sku not in self._lead_times or child_sku not in self._lead_times:
            raise KeyError(f"Both parent '{parent_sku}' and child '{child_sku}' must be registered before linking.")
        self._is_assembly[parent_sku] = True
        self._children[parent_sku].append((child_sku, quantity_per_parent))
        self._parents[child_sku].append(parent_sku)
        self._validate_no_cycles()

    def _validate_no_cycles(self) -> None:
        """Ensure the BOM is strictly a DAG (no circular dependencies)."""
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def dfs(node: str) -> None:
            visited.add(node)
            rec_stack.add(node)
            for child, _ in self._children.get(node, []):
                if child not in visited:
                    dfs(child)
                elif child in rec_stack:
                    raise ValueError(f"Circular dependency detected in BOM involving '{child}'")
            rec_stack.remove(node)

        for sku in self._lead_times:
            if sku not in visited:
                dfs(sku)

    def cumulative_lead_time(self, sku: str) -> int:
        """Compute the critical path Cumulative Lead Time (CLT) in days.
        
        CLT(leaf) = LeadTime(leaf)
        CLT(assembly) = LeadTime(assembly) + max(CLT(child) for child in children)
        """
        if sku not in self._lead_times:
            raise KeyError(f"Item '{sku}' is not registered in the BOM graph.")

        memo: Dict[str, int] = {}

        def compute_clt(item: str) -> int:
            if item in memo:
                return memo[item]
            base_lt = self._lead_times[item]
            children = self._children.get(item, [])
            if not children:
                memo[item] = base_lt
                return base_lt
            child_max = max(compute_clt(c) for c, _ in children)
            total = base_lt + child_max
            memo[item] = total
            return total

        return compute_clt(sku)

    def explode_leaf_requirements(self, sku: str, parent_quantity: float = 1.0) -> Dict[str, float]:
        """Explode BOM recursively to determine total leaf (purchased) component requirements."""
        if sku not in self._lead_times:
            raise KeyError(f"Item '{sku}' is not registered in the BOM graph.")

        requirements: Dict[str, float] = {}

        def traverse(item: str, multiplier: float) -> None:
            children = self._children.get(item, [])
            if not children:
                requirements[item] = requirements.get(item, 0.0) + multiplier
                return
            for child, child_qty in children:
                traverse(child, multiplier * child_qty)

        traverse(sku, parent_quantity)
        return requirements


def quote_atp_order(
    bom: BomGraph,
    sku: str,
    requested_units: float,
    requested_lead_time_days: int,
    inventory_on_hand: Dict[str, float],
    tier1_contracts: Optional[List[Tier1Contract]] = None,
) -> AtpQuote:
    """Calculate Available-to-Promise (ATP) commitment with capacity rationing.

    1. Ring-fence inventory committed to Tier-1 contractual clients.
    2. Explode the BOM to find the bottleneck component restricting immediate fulfillment.
    3. Determine immediate available quantity without violating Tier-1 reserves.
    4. Compute Cumulative Lead Time (CLT) for the shortage.
    """
    if requested_units <= 0:
        raise ValueError("Requested units must be strictly positive.")
    if requested_lead_time_days < 0:
        raise ValueError("Requested lead time cannot be negative.")

    # Step 1: Calculate Tier-1 reserved inventory per component/SKU
    tier1_reserves: Dict[str, float] = {}
    if tier1_contracts:
        for contract in tier1_contracts:
            tier1_reserves[contract.sku] = tier1_reserves.get(contract.sku, 0.0) + contract.committed_units

    # Step 2: Calculate uncommitted spot stock for all items
    spot_inventory: Dict[str, float] = {}
    for item, on_hand in inventory_on_hand.items():
        reserved = tier1_reserves.get(item, 0.0)
        spot_inventory[item] = max(0.0, on_hand - reserved)

    # Step 3: Check if the SKU itself is an assembly or leaf
    leaf_reqs_per_unit = bom.explode_leaf_requirements(sku, parent_quantity=1.0)
    clt = bom.cumulative_lead_time(sku)

    # If the product itself is in inventory directly:
    direct_spot_stock = spot_inventory.get(sku, 0.0)

    # Check maximum possible assembly from component spot inventory
    max_assemblable = float("inf")
    bottleneck: Optional[str] = None

    if leaf_reqs_per_unit:
        for leaf, req_qty in leaf_reqs_per_unit.items():
            if leaf == sku:
                continue
            avail = spot_inventory.get(leaf, 0.0)
            possible = avail / req_qty
            if possible < max_assemblable:
                max_assemblable = possible
                bottleneck = leaf

    # Immediate available is either direct stock or assemblable stock
    if not bom._children.get(sku, []):
        # Pure leaf product
        immediate_available = min(requested_units, direct_spot_stock)
        if direct_spot_stock < requested_units:
            bottleneck = sku
    else:
        # Assembly product
        immediate_available = min(requested_units, direct_spot_stock + (max_assemblable if max_assemblable != float("inf") else 0.0))

    immediate_available = max(0.0, immediate_available)
    shortage = max(0.0, requested_units - immediate_available)

    # If no shortage, no bottleneck
    if shortage == 0.0:
        bottleneck = None

    # Step 4: Feasibility check against requested lead time
    if shortage == 0.0:
        is_feasible = True
        recommendation = "FULFILL_IMMEDIATE"
    else:
        is_feasible = requested_lead_time_days >= clt
        if immediate_available > 0.0:
            recommendation = "SPLIT_SHIPMENT"
        else:
            recommendation = "AWAIT_CLT"

    return AtpQuote(
        sku=sku,
        requested_units=requested_units,
        immediate_available_units=immediate_available,
        shortage_units=shortage,
        bottleneck_component=bottleneck,
        cumulative_lead_time_days=clt,
        is_fulfilled_within_requested_time=is_feasible,
        tier1_reserved_units=tier1_reserves.get(sku, 0.0),
        recommendation=recommendation,
    )

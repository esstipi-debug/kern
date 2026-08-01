"""Stocky Migration & Offboarding Importer for Kern (Phase 4).

Parses CSV exports from Shopify Stocky (suppliers, purchase orders, reorder points)
and converts them into Kern client parameter profiles and inventory intake structures.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StockySupplier:
    supplier_id: str
    name: str
    contact_email: str | None = None
    lead_time_days: int = 14
    moq: int = 1
    currency: str = "USD"


@dataclass
class StockyPurchaseOrder:
    po_number: str
    supplier_name: str
    status: str  # draft, sent, partial, received, cancelled
    sku: str
    quantity_ordered: int
    quantity_received: int
    cost_per_unit: float
    order_date: str | None = None
    expected_date: str | None = None


@dataclass
class StockyReorderPoint:
    sku: str
    min_reorder_point: int
    max_reorder_point: int
    target_stock: int


@dataclass
class StockyMigrationBatch:
    suppliers: list[StockySupplier] = field(default_factory=list)
    purchase_orders: list[StockyPurchaseOrder] = field(default_factory=list)
    reorder_points: list[StockyReorderPoint] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "suppliers_count": len(self.suppliers),
            "purchase_orders_count": len(self.purchase_orders),
            "reorder_points_count": len(self.reorder_points),
        }


def parse_stocky_suppliers_csv(csv_content: str | io.StringIO) -> list[StockySupplier]:
    """Parse Stocky suppliers export CSV."""
    if isinstance(csv_content, str):
        f = io.StringIO(csv_content)
    else:
        f = csv_content

    reader = csv.DictReader(f)
    suppliers = []
    for row in reader:
        # Normalize column names (Stocky exports use various header titles)
        name = row.get("Supplier Name") or row.get("Name") or row.get("supplier_name", "Unknown")
        supp_id = row.get("ID") or row.get("Supplier ID") or name.lower().replace(" ", "_")
        email = row.get("Email") or row.get("Contact Email")
        lead_time = int(row.get("Lead Time (days)") or row.get("lead_time", 14))
        moq = int(row.get("MOQ") or row.get("Min Order Qty", 1))
        currency = row.get("Currency", "USD")

        suppliers.append(
            StockySupplier(
                supplier_id=str(supp_id),
                name=str(name),
                contact_email=str(email) if email else None,
                lead_time_days=lead_time,
                moq=moq,
                currency=str(currency),
            )
        )
    return suppliers


def parse_stocky_purchase_orders_csv(csv_content: str | io.StringIO) -> list[StockyPurchaseOrder]:
    """Parse Stocky Purchase Orders export CSV."""
    if isinstance(csv_content, str):
        f = io.StringIO(csv_content)
    else:
        f = csv_content

    reader = csv.DictReader(f)
    pos = []
    for row in reader:
        po_num = row.get("PO Number") or row.get("PO #") or row.get("Number", "PO-000")
        supplier = row.get("Supplier") or row.get("Supplier Name", "Unknown")
        status = row.get("Status", "draft").lower()
        sku = row.get("SKU") or row.get("Variant SKU", "")
        qty_ordered = int(row.get("Quantity Ordered") or row.get("Ordered Qty", 0))
        qty_received = int(row.get("Quantity Received") or row.get("Received Qty", 0))
        cost = float(row.get("Cost Price") or row.get("Unit Cost", 0.0))
        order_date = row.get("Created At") or row.get("Order Date")
        expected_date = row.get("Expected At") or row.get("Delivery Date")

        if sku:
            pos.append(
                StockyPurchaseOrder(
                    po_number=str(po_num),
                    supplier_name=str(supplier),
                    status=str(status),
                    sku=str(sku),
                    quantity_ordered=qty_ordered,
                    quantity_received=qty_received,
                    cost_per_unit=cost,
                    order_date=str(order_date) if order_date else None,
                    expected_date=str(expected_date) if expected_date else None,
                )
            )
    return pos


def parse_stocky_reorder_points_csv(csv_content: str | io.StringIO) -> list[StockyReorderPoint]:
    """Parse Stocky reorder points / min-max CSV export."""
    if isinstance(csv_content, str):
        f = io.StringIO(csv_content)
    else:
        f = csv_content

    reader = csv.DictReader(f)
    reorders = []
    for row in reader:
        sku = row.get("SKU") or row.get("Variant SKU", "")
        min_pt = int(row.get("Min Stock") or row.get("Reorder Point", 0))
        max_pt = int(row.get("Max Stock") or row.get("Target Stock", 0))
        target = int(row.get("Target Stock") or max_pt or min_pt)

        if sku:
            reorders.append(
                StockyReorderPoint(
                    sku=str(sku),
                    min_reorder_point=min_pt,
                    max_reorder_point=max_pt,
                    target_stock=target,
                )
            )
    return reorders

"""End-to-end Odoo connector: read products/inventory/sales -> forecast -> safe restock.

Runs against a LIVE Odoo when ODOO_URL / ODOO_DB / ODOO_USERNAME / ODOO_API_KEY are set
(XML-RPC, no extra deps); otherwise it falls back to an in-memory Odoo stand-in so the
whole flow is demonstrable with no credentials. The chain after the connector is identical
to the other backends: demand bridge -> forecast -> stage a restock through the safe-staging
writeback plane (here, as a reversible Odoo reorder-point update).

Usage:
    python examples/run_odoo.py
    python examples/run_odoo.py --cover 10
    # against a real Odoo:
    ODOO_URL=https://my.odoo.com ODOO_DB=mydb ODOO_USERNAME=me ODOO_API_KEY=... \\
        python examples/run_odoo.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobs.odoo_job import politica_min_max, read_on_order, read_unit_steps  # noqa: E402
from nosilo_core import writeback  # noqa: E402
from nosilo_core.writeback_store import SqliteAuditLedger  # noqa: E402
from src.connectors.odoo import OdooConnector, OdooPurchaseUnit, OdooRPC, demo_odoo  # noqa: E402
from src.connectors.replenish import plan_replenishment  # noqa: E402
from src.constraints import PurchasableQty  # noqa: E402
from src.data_paths import data_root  # noqa: E402


def _rpc() -> OdooRPC:
    """A live ``OdooClient`` when ODOO_* env vars are present, else an in-memory stand-in."""
    url, db = os.environ.get("ODOO_URL"), os.environ.get("ODOO_DB")
    user, key = os.environ.get("ODOO_USERNAME"), os.environ.get("ODOO_API_KEY")
    if url and db and user and key:
        from src.connectors.odoo import OdooClient

        print(f"  connecting to live Odoo at {url} (db={db}, user={user})")
        return OdooClient(url, db, user, key)
    print("  no ODOO_* env vars set -> using in-memory Odoo stand-in (demo data)")
    return demo_odoo()


def _ajuste_oc(sku: str, pedido: float, lote: PurchasableQty, compra: OdooPurchaseUnit | None) -> str:
    """Lo que pidio el plan y lo que dice la OC, en una linea en espanol y ASCII.

    ``lote.rule`` sale de `lot_rule` ("moq 50, multiple 1"), el texto que comparten el
    CSV y los residuales; aca solo se traduce para la consola.
    """
    regla = lote.rule.replace("moq ", "minimo ").replace("multiple ", "multiplo ")
    if compra is None:
        return f"{sku}: el plan pidio {pedido:g} -> la OC pide {lote.quantity:g} ({regla})"
    return (
        f"{sku}: el plan pidio {pedido:g} -> la OC pide {lote.quantity:g} {compra.name} "
        f"(= {lote.quantity * compra.stock_per_unit:g} en unidad de stock; {regla})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Odoo connector replenishment loop.")
    parser.add_argument("--cover", type=float, default=8.0,
                        help="ignored by the min/max policy; kept so old command lines still run")
    parser.add_argument("--service-level", type=float, default=None,
                        help="cycle service level for the minimum, e.g. 0.95; without it the "
                             "engine default 0.95 is used AND declared")
    parser.add_argument("--review-days", type=float, default=None,
                        help="days between two Kern runs (R); without it 7 is used AND declared")
    parser.add_argument("--draft-po", action="store_true",
                        help="raise draft purchase orders instead of applying reorder points")
    parser.add_argument("--approved-by", default=None,
                        help="who authorises the write (reorder points OR draft POs); without it the "
                             "run stays a dry-run. Recorded on the writeback audit ledger")
    args = parser.parse_args()
    if args.draft_po and not args.approved_by:
        # Creating POs is TIER_IRREVERSIBLE. There is no unattended path to it by
        # design, so the CLI refuses here rather than inventing an "operator" default
        # that would put an anonymous signature on a real purchase.
        parser.error("--draft-po requires --approved-by (the human authorising the spend)")

    print("\n=== Odoo connector (read -> forecast -> safe restock) ===")
    # Every write leaves a row in a ledger ON DISK (KERN_DATA_DIR, else <repo>/data):
    # who approved, when, and the idempotency key. Process memory would forget it on
    # the next run, and "who touched the client's Odoo" is the first question a
    # partner asks when something looks off. Without --approved-by nothing can be
    # written, so no ledger is opened and a dry run leaves no file behind.
    ledger_path: Path | None = None
    if args.approved_by:
        root = data_root()
        root.mkdir(parents=True, exist_ok=True)
        ledger_path = root / "odoo_writeback.sqlite"
    connector = OdooConnector(
        _rpc(), ledger=SqliteAuditLedger(ledger_path) if ledger_path else None
    )

    products = connector.list_products()
    on_hand = {lvl.sku: lvl.available for lvl in connector.inventory_levels()}
    lead = connector.lead_times()
    print(f"  products : {', '.join(p.sku for p in products)}")
    print(f"  orders   : {len(connector.orders())} confirmed sales order(s)")

    # Contra la POSICION (stock + lo que ya viene), no contra el estante: sin esto
    # la corrida volvia a comprar lo que ya estaba pedido. Si no se puede leer, se
    # dice y las cantidades salen sin descontar (nunca "descontado: cero").
    on_order, netting_note = read_on_order(connector)
    print(f"  En camino: {netting_note}")
    # La unidad de cada producto: lo que se escribe sube a una pieza entera (o a la
    # precision de su unidad). Lo que no se pudo redondear se dice aca, no despues.
    pasos, nota_unidad = read_unit_steps(connector)
    if nota_unidad:
        print(f"  Unidad: {nota_unidad}")
    plan = plan_replenishment(
        connector, cover_periods=args.cover, store=connector, on_order_by_sku=on_order,
        unit_step_by_sku=pasos,
        **politica_min_max(connector, lead, service_level=args.service_level, review_days=args.review_days),
    )
    print("  Supuestos:")
    for supuesto in plan.assumptions:
        print(f"    - {supuesto}")

    print("\n  SKU      estante    plazo  min=max  plan sin redondear  a pedir")
    restock = plan.restock
    for line in plan.lines:
        gap = restock.get(line.sku, 0.0)
        # "n/a" y no 0: un SKU cuyo proveedor no tiene plazo cargado en el ERP no
        # entrega en el acto, que es lo que decia el cero que salia aca antes.
        # Tercer sitio con el mismo default -- el conector y el CSV del entregable
        # eran los otros dos.
        plazo = lead.get(line.sku)
        plazo_txt = "n/a" if plazo is None else f"{plazo:.0f}"
        # Las dos cifras: lo que pidio el plan y lo que se escribe. Antes salia una
        # sola a un decimal y la de la PO se imprimia con .0f -- dos numeros para lo mismo.
        pedido = "-" if line.restock_unrounded is None else f"{line.restock_unrounded:.1f}"
        # n/a en el minimo de un SKU sin dimensionar: un 0 diria "medido, no hace falta".
        tope = "n/a" if line.order_up_to is None else f"{line.order_up_to:g}"
        print(f"  {line.sku:<8} {on_hand.get(line.sku, 0.0):>7.0f}  {plazo_txt:>7}  {tope:>7}  {pedido:>18}  {gap:>7g}")

    print(f"\n  Outcome: {plan.outcome.status} - {plan.outcome.summary}")
    if not restock:
        print("  Nothing to do (all SKUs above target cover).")
        return

    if args.draft_po:
        # Option 2: raise draft purchase orders (RFQs), grouped by each SKU's primary supplier.
        po = connector.create_draft_purchase_orders(restock, approved_by=args.approved_by)
        print(f"\n  Created {po.n_orders} draft PO(s) in Odoo (left unconfirmed for a buyer to review):")
        for po_id, skus in po.purchase_orders.items():
            print(f"    PO {po_id}: {', '.join(skus)}")
        if po.unsourced:
            print(f"  Unsourced (no supplier in Odoo): {', '.join(po.unsourced)}")
        # Todo SKU cuya OC no dice lo mismo que el plan: subio al minimo del proveedor o
        # al paso de la unidad, o va en otra unidad de compra. Antes solo se decia el minimo.
        for sku, lote in sorted(po.lot_adjustments.items()):
            print(f"  {_ajuste_oc(sku, restock[sku], lote, po.purchase_units.get(sku))}")
        print(f"  Audit ledger: {ledger_path}")
        return

    # Option 1 (default): reorder points. Stage is ALWAYS a dry-run; applying needs a
    # named human even though the write is reversible. On a client's Odoo "reversible"
    # is a property of the field, not of the trust: the buyer still wants to know who
    # moved their reorder points, and the ledger row is what answers that.
    if plan.changeset is None:
        return
    print("  Aviso: una regla NUEVA no se deshace con la marcha atras de Kern (queda en Odoo, trigger manual).")
    print(f"  Staged (dry-run): {plan.changeset.summary()}")
    if not args.approved_by:
        print("  Nothing applied: pass --approved-by <name> to write the reorder points.")
        return
    approval = writeback.approve(plan.changeset, args.approved_by)
    result = connector.apply_restock(plan.changeset, approval=approval, auto_apply_reversible=False)
    print(f"  Applied reorder point(s): {result.applied} (audit key {result.audit_id}, approved by {args.approved_by})")
    print(f"  Audit ledger: {ledger_path}")

    again = connector.apply_restock(plan.changeset, approval=approval, auto_apply_reversible=False)
    print(f"  Re-apply same key -> idempotent_skip={again.idempotent_skip} (no double-write)")


if __name__ == "__main__":
    main()

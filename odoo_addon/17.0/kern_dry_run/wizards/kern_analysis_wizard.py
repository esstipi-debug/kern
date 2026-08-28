import logging
from datetime import timedelta

from odoo import _, fields, models
from odoo.exceptions import UserError

# lib/ no importa nada de Odoo por diseno, para que la logica de protocolo se
# pueda probar fuera de un runtime de Odoo (tests/test_odoo_addon_mcp_client.py).
# Es un subpaquete de verdad, asi que se importa relativo: Odoo carga cada addon
# como el paquete odoo.addons.<modulo>, y sus subpaquetes con __init__.py son
# importables con la sintaxis normal de Python. La version anterior insertaba
# lib/ en sys.path global, lo que ensombrece cualquier otro modulo llamado
# mcp_client en la misma instancia -- ver el docstring de lib/__init__.py.
from ..lib.mcp_client import KernMcpClient, KernMcpError
from ..models.res_config_settings import DEFAULT_BASE_URL

_logger = logging.getLogger(__name__)

# Keeps a single HTTP call's JSON payload bounded - this is a dry-run demo,
# not a bulk data pipeline; a real subscription customer's full history goes
# through the direct-client relationship instead (see the module description).
_MAX_ROWS = 5000


class KernAnalysisWizard(models.TransientModel):
    _name = "kern.analysis.wizard"
    _description = "Kern Inventory Analysis (dry run, read-only)"
    # Odoo's default TransientModel vacuum cutoff is 1 hour - too short for a
    # result that took a real (possibly rate-limited) API call to produce and
    # that a user might reasonably want to come back to after switching tabs
    # or getting interrupted. 24h keeps the framework's own cleanup behavior
    # (this is intentionally still a TransientModel, not persisted forever)
    # while giving a realistic window before ir.autovacuum deletes the row.
    _transient_max_hours = 24.0

    history_days = fields.Integer(
        string="History window (days)",
        default=365,
        help="How far back to pull confirmed sales history from.",
    )
    state = fields.Selection([("draft", "Draft"), ("done", "Done")], default="draft")
    result_summary = fields.Char(readonly=True)
    result_markdown = fields.Text(readonly=True)

    def _collect_rows(self):
        cutoff = fields.Date.today() - timedelta(days=self.history_days)
        lines = self.env["sale.order.line"].search(
            [
                ("order_id.state", "=", "sale"),
                ("order_id.date_order", ">=", cutoff),
                ("product_id", "!=", False),
            ],
            limit=_MAX_ROWS,
        )
        if not lines:
            raise UserError(
                _("No confirmed sales orders found in the last %(days)s days - nothing to analyze.")
                % {"days": self.history_days}
            )
        if len(lines) >= _MAX_ROWS:
            _logger.warning(
                "Kern dry-run: sales history truncated at %(limit)s rows (history_days=%(days)s)",
                {"limit": _MAX_ROWS, "days": self.history_days},
            )

        rows = []
        for line in lines:
            product = line.product_id
            order_date = line.order_id.date_order
            supplier = product.seller_ids[:1]
            rows.append(
                {
                    "date": fields.Date.to_string(order_date.date() if order_date else fields.Date.today()),
                    # default_code (the SKU) may be unset on some products - fall
                    # back to the Odoo record id so every row still has a stable,
                    # unique identifier rather than being silently dropped.
                    "product_id": product.default_code or str(product.id),
                    "quantity": line.product_uom_qty,
                    "unit_cost": product.standard_price,
                    "lead_time_days": supplier.delay if supplier else False,
                }
            )
        return rows

    def action_run_analysis(self):
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("kern_dry_run.base_url") or DEFAULT_BASE_URL
        api_key = icp.get_param("kern_dry_run.api_key")
        if not api_key:
            raise UserError(
                _("Set your Kern API key first: Settings > Inventory > Kern. Get one at %s.") % DEFAULT_BASE_URL
            )

        rows = self._collect_rows()
        client = KernMcpClient(base_url, api_key)
        try:
            result = client.call_tool(
                "linchpin_inventory_optimize", rows, client_label=self.env.company.name or "Odoo"
            )
        except KernMcpError as exc:
            raise UserError(str(exc)) from exc

        self.write(
            {
                "result_summary": result.get("summary") or "",
                "result_markdown": result.get("report_markdown") or result.get("summary") or "",
                "state": "done",
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

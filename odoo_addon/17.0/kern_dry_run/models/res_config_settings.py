from odoo import fields, models

DEFAULT_BASE_URL = "https://linchpin.fly.dev"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    kern_base_url = fields.Char(
        string="Kern URL",
        config_parameter="kern_dry_run.base_url",
        default=DEFAULT_BASE_URL,
        help="Base URL of the Kern instance to call. Only change this if you run your own deployment.",
    )
    # config_parameter fields store as plain text in ir.config_parameter - this
    # is Odoo's standard res.config.settings persistence mechanism (the same
    # pattern many core/OCA integrations use for API keys), not something
    # specific to this module. password="True" on the view field (see
    # views/res_config_settings_views.xml) only masks the input widget
    # client-side; it does not encrypt storage. Reading ir.config_parameter
    # already requires base.group_system (Settings/Technical access), which is
    # the accepted boundary here - same as Odoo's own outgoing-mail-server
    # credentials.
    kern_api_key = fields.Char(
        string="Kern API Key",
        config_parameter="kern_dry_run.api_key",
        help="Per-client key issued by Kern (not your Odoo password or any Odoo credential). "
        "Stored as plain text, readable by Settings > Technical admins - same as other API-key "
        "settings in Odoo. Get one at https://linchpin.fly.dev.",
    )

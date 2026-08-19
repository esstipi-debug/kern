{
    "name": "Kern Inventory AI",
    "version": "19.0.1.0.0",
    "category": "Supply Chain/Inventory",
    "summary": "AI inventory policy recommendations from Kern - a free dry run, no data written back",
    "description": """
Kern Inventory AI
======================

Sends your confirmed-sales history to Kern's hosted analysis service and
shows back per-SKU reorder policy recommendations (forecast, safety stock,
reorder point/quantity) right inside Odoo.

**This module requires an external service to run.** For each confirmed sale
order line in the window you choose, this sends: the product's SKU (or
internal ID if no SKU is set), order date, quantity sold, unit cost
(standard_price), your primary supplier's lead time in days, and your
company name - over HTTPS to Kern's cloud (https://linchpin.fly.dev by
default, configurable in Settings) using the API key you enter there. No
stock/on-hand quantities are read or sent, and no data is written back to
Odoo by this module - it only displays a recommendation for you to review.
Get an API key at https://linchpin.fly.dev.

Requires Odoo.sh (private repository) or an on-premise installation. It is not
compatible with Odoo Online (SaaS), which does not accept third-party modules
containing Python files - and this module is Python.

**On Odoo Online you do not need this module to get the same analysis.** Kern
also reads your database from the outside over Odoo's External API, which is
the single integration path that covers Odoo Online, Odoo.sh and on-premise
alike, with no module installed. On Odoo 19 that is the External JSON-2 API
(POST to /json/2, authenticated with an Odoo API key as a bearer token); the
older XML-RPC and JSON-RPC endpoints (/xmlrpc, /xmlrpc/2, /jsonrpc) still work
but are deprecated and scheduled for removal in Odoo 22 (fall 2028). One
caveat worth knowing before you plan around it: Odoo exposes the External API
only on Custom pricing plans - the One App Free and Standard plans do not
include it. Same read-only guarantee either way: Kern never writes to your
database. Ask us at https://linchpin.fly.dev.
""",
    "author": "Kern",
    "website": "https://linchpin.fly.dev",
    "license": "OPL-1",
    "depends": ["base", "stock", "sale"],
    "data": [
        "security/kern_security.xml",
        "security/ir.model.access.csv",
        "views/res_config_settings_views.xml",
        "views/kern_analysis_wizard_views.xml",
        "views/kern_menu.xml",
    ],
    "installable": True,
    "application": True,
    "images": ["static/description/icon.png"],
}

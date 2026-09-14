"""Post-install verification, run inside `odoo shell` against a fresh Odoo 19 DB.

Checks the things a 17->19 port can break SILENTLY. A field that still exists but
means something else is worse than one that errors, so section 5 builds a fixture with
KNOWN values and asserts _collect_rows() returns exactly those - not just "not empty".

The fixture is test scaffolding in a throwaway DB; the module itself still writes
nothing (asserted in section 8).
"""
# ruff: noqa: F821  # `env` is injected by `odoo shell`, not imported.
import traceback

FAIL = []


def check(label, fn):
    try:
        print(f"  PASS  {label}: {fn()}")
    except Exception as exc:
        FAIL.append(label)
        print(f"  FAIL  {label}: {type(exc).__name__}: {exc}")
        traceback.print_exc()


print("\n=== 1. module state ===")
check("module installed", lambda: env['ir.module.module'].search(
    [('name', '=', 'kern_dry_run')]).state)

print("\n=== 2. Odoo 19 res.groups.privilege wiring (the actual breaking change) ===")
def _priv():
    g = env.ref('kern_dry_run.group_kern_user')
    p = env.ref('kern_dry_run.res_groups_privilege_kern')
    assert g.privilege_id == p, "group not attached to its own privilege"
    assert g.privilege_id != env.ref('stock.res_groups_privilege_inventory'), \
        "must NOT share stock's privilege - would make it exclusive with Inventory roles"
    return (f"full_name={g.full_name!r} privilege={p.name!r} "
            f"category={p.category_id.name!r} groups_in_privilege={len(p.group_ids)}")
check("privilege/group", _priv)

print("\n=== 3. menu + ACL ===")
check("menu", lambda: env.ref('kern_dry_run.menu_kern_analysis').complete_name)
check("ACL row", lambda: env['ir.model.access'].search([
    ('group_id', '=', env.ref('kern_dry_run.group_kern_user').id)]).mapped('name'))

print("\n=== 4. settings view actually rendered into stock's Inventory app ===")
def _settings():
    arch = env['res.config.settings'].get_view(
        env.ref('stock.res_config_settings_view_form').id, 'form')['arch']
    assert 'kern_base_url' in arch and 'kern_api_key' in arch, "fields absent from rendered arch"
    assert 'kern_dry_run_setting' in arch, "our <setting> block did not survive the xpath"
    return "both fields and the <setting> block present in the rendered arch"
check("settings arch", _settings)

print("\n=== 5. FIELD SEMANTICS: known fixture -> exact expected row ===")
EXPECTED = {"sku": "KERN-TEST-01", "qty": 7.0, "cost": 12.5, "lead": 9, "date": "2026-03-04"}
def _fixture():
    partner = env['res.partner'].create({'name': 'Kern Test Vendor'})
    tmpl = env['product.template'].create({
        'name': 'Kern Test Widget',
        'default_code': EXPECTED["sku"],
        'is_storable': True,
        'seller_ids': [
            # primary vendor (sequence 1) - seller_ids[:1] must resolve to THIS one
            (0, 0, {'partner_id': partner.id, 'delay': EXPECTED["lead"], 'price': 10.0, 'sequence': 1}),
            # a slower, higher-sequence vendor that must NOT be picked
            (0, 0, {'partner_id': partner.id, 'delay': 99, 'price': 20.0, 'sequence': 50}),
        ],
    })
    prod = tmpl.product_variant_id
    prod.standard_price = EXPECTED["cost"]
    order = env['sale.order'].create({
        'partner_id': partner.id,
        'order_line': [(0, 0, {'product_id': prod.id,
                               'product_uom_qty': EXPECTED["qty"],
                               'price_unit': 33.0})],
    })
    order.action_confirm()
    order.date_order = EXPECTED["date"] + " 10:00:00"
    return prod, order

def _rows():
    prod, order = _fixture()
    assert order.state == 'sale', f"fixture order not confirmed: {order.state}"
    w = env['kern.analysis.wizard'].create({'history_days': 3650})
    rows = [r for r in w._collect_rows() if r['product_id'] == EXPECTED["sku"]]
    assert len(rows) == 1, f"expected exactly 1 row for our SKU, got {len(rows)}"
    r = rows[0]
    errs = []
    if r['quantity'] != EXPECTED["qty"]:
        errs.append(f"quantity {r['quantity']} != {EXPECTED['qty']} (product_uom_qty moved?)")
    if r['unit_cost'] != EXPECTED["cost"]:
        errs.append(f"unit_cost {r['unit_cost']} != {EXPECTED['cost']} (standard_price moved?)")
    if r['lead_time_days'] != EXPECTED["lead"]:
        errs.append(f"lead_time_days {r['lead_time_days']} != {EXPECTED['lead']} "
                    f"(supplierinfo.delay changed meaning, or seller_ids[:1] no longer the primary vendor?)")
    if r['date'] != EXPECTED["date"]:
        errs.append(f"date {r['date']} != {EXPECTED['date']} (date_order moved?)")
    assert not errs, errs
    return f"\n           row == expected exactly: {r}"
check("_collect_rows exact values", _rows)

print("\n=== 6. SSRF guard must still reject private targets (NOT weakened) ===")
def _ssrf():
    import sys
    sys.path.insert(0, '/mnt/extra-addons/kern_dry_run/lib')
    from mcp_client import KernMcpClient, KernMcpError
    got = []
    for bad in ('http://example.com', 'https://localhost', 'https://127.0.0.1',
                'https://169.254.169.254', 'https://10.0.0.1'):
        try:
            KernMcpClient(bad, 'lpk_test')
            got.append(f"{bad} -> NOT BLOCKED")
        except KernMcpError as e:
            got.append(f"{bad} -> blocked ({str(e)[:40]}...)")
    assert all('NOT BLOCKED' not in g for g in got), got
    return "\n           " + "\n           ".join(got)
check("SSRF guard", _ssrf)

print("\n=== 7. end-to-end wizard with the HTTP transport stubbed (no external call) ===")
def _e2e():
    from odoo.addons.kern_dry_run.wizards import kern_analysis_wizard as kw
    env['ir.config_parameter'].sudo().set_param('kern_dry_run.api_key', 'lpk_stub')

    seen = {}

    class _Stub:
        def __init__(self, base_url, api_key, *a, **k):
            seen['base_url'], seen['api_key'] = base_url, api_key

        def call_tool(self, name, rows, params=None, client_label="Odoo"):
            seen['tool'], seen['rows'], seen['label'] = name, len(rows), client_label
            return {"summary": f"stub OK over {len(rows)} rows",
                    "report_markdown": "## Reorder policy\n- KERN-TEST-01: ROP 12, Q 40"}

    real, kw.KernMcpClient = kw.KernMcpClient, _Stub
    try:
        w = env['kern.analysis.wizard'].create({'history_days': 3650})
        act = w.action_run_analysis()
        assert w.state == 'done', w.state
        assert w.result_summary and w.result_markdown, (w.result_summary, w.result_markdown)
        assert seen['tool'] == 'linchpin_inventory_optimize', seen['tool']
        return (f"\n           state={w.state} summary={w.result_summary!r}"
                f"\n           report shown={w.result_markdown.splitlines()[0]!r}"
                f"\n           client got base_url={seen['base_url']!r} label={seen['label']!r}"
                f"\n           action -> view_mode={act['view_mode']!r} target={act['target']!r}")
    finally:
        kw.KernMcpClient = real
check("wizard end to end", _e2e)

print("\n=== 8. module writes NOTHING outside its own transient model ===")
def _readonly():
    src = open('/mnt/extra-addons/kern_dry_run/wizards/kern_analysis_wizard.py').read()
    bad = [ln.strip() for ln in src.splitlines()
           if ('.write(' in ln or '.create(' in ln or '.unlink(' in ln or 'set_param(' in ln)
           and 'self.write' not in ln]
    assert not bad, bad
    return "only self.write() on the transient wizard; no create/unlink/write/set_param on Odoo records"
check("read-only", _readonly)

print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"FAILURES: {FAIL}"))

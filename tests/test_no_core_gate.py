"""No-core CI gate: listing/engine tests stay collectable without nosilo_core."""
from __future__ import annotations

import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from _no_core import depends_on_nosilo_core  # noqa: E402


def test_offers_and_listing_do_not_need_private_core() -> None:
    assert depends_on_nosilo_core(_TESTS / "test_webapp_offers.py") is False
    assert depends_on_nosilo_core(_TESTS / "test_webapp_paquetes_page.py") is False
    assert depends_on_nosilo_core(_TESTS / "test_odoo_addon_listing.py") is False
    assert depends_on_nosilo_core(_TESTS / "test_ci_authenticate_core.py") is False
    # Parent-package walking of src/__init__.py must not drag the engine suite
    # into the ignore list: that file re-exports Vandeput modules, not core.
    assert depends_on_nosilo_core(_TESTS / "test_eoq.py") is False
    assert depends_on_nosilo_core(_TESTS / "test_safety_stock.py") is False


def test_app_import_routes_need_private_core() -> None:
    assert depends_on_nosilo_core(_TESTS / "test_webapp_paquetes_routes.py") is True
    assert depends_on_nosilo_core(_TESTS / "test_stocky_alternative_page.py") is True


# Collection died on these under KERN_CI_NO_CORE=1 when the scanner only
# followed the leaf module and missed parent-package __init__.py / importlib.
_INDIRECT_CORE_TESTS = (
    "test_events.py",
    "test_examples_survive_unscoreable_entities.py",
    "test_knowledge_rag.py",
    "test_knowledge_warnings.py",
    "test_merchant_alerts.py",
    "test_pricing_intel_acquire_base.py",
    "test_pricing_intel_acquire_l1.py",
    "test_pricing_intel_auto_approve.py",
    "test_pricing_intel_events.py",
    "test_pricing_intel_meli_api.py",
    "test_pricing_intel_pdp_fetcher.py",
    "test_pricing_intel_sanity.py",
    "test_pricing_intel_watcher.py",
    "test_retention_events.py",
    "test_run_repricing_cli.py",
    "test_s2p_matching.py",
    "test_s2p_recovery.py",
    "test_simworld_lens.py",
)


def test_parent_package_and_importlib_core_imports_are_detected() -> None:
    for name in _INDIRECT_CORE_TESTS:
        assert depends_on_nosilo_core(_TESTS / name) is True, name

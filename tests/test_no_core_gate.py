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


def test_app_import_routes_need_private_core() -> None:
    assert depends_on_nosilo_core(_TESTS / "test_webapp_paquetes_routes.py") is True
    assert depends_on_nosilo_core(_TESTS / "test_stocky_alternative_page.py") is True

"""Odoo Apps Store listing readiness for kern_dry_run (17.0 and 19.0)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SERIES = ("17.0", "19.0")


def _manifest(series: str) -> dict:
    path = _REPO / "odoo_addon" / series / "kern_dry_run" / "__manifest__.py"
    return ast.literal_eval(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("series", _SERIES)
def test_manifest_has_support_and_no_store_price(series: str) -> None:
    manifest = _manifest(series)
    assert "support" in manifest
    assert "linchpin.fly.dev" in manifest["support"]
    # Free bait: omitting price (or 0) is the Apps Store free listing. Paid
    # retainers are sold outside the Store.
    assert manifest.get("price", 0) in (0, 0.0, None)


@pytest.mark.parametrize("series", _SERIES)
def test_manifest_cover_and_screenshots_exist(series: str) -> None:
    manifest = _manifest(series)
    desc = _REPO / "odoo_addon" / series / "kern_dry_run" / "static" / "description"
    images = manifest["images"]
    assert images[0].endswith("cover.png")
    for rel in images:
        assert (_REPO / "odoo_addon" / series / "kern_dry_run" / rel).is_file()
    assert (desc / "icon.png").is_file()
    index = (desc / "index.html").read_text(encoding="utf-8")
    for name in ("cover.png", "screenshot_wizard.png", "screenshot_result.png"):
        assert name in index

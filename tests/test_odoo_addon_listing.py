"""Odoo Apps Store listing readiness for kern_dry_run (17.0 and 19.0)."""

from __future__ import annotations

import ast
import importlib.util
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SERIES = ("17.0", "19.0")
_MODULE = "kern_dry_run"


def _addon(series: str) -> Path:
    return _REPO / "odoo_addon" / series / _MODULE


def _manifest(series: str) -> dict:
    return ast.literal_eval((_addon(series) / "__manifest__.py").read_text(encoding="utf-8"))


@pytest.mark.parametrize("series", _SERIES)
def test_manifest_has_support_and_no_store_price(series: str) -> None:
    manifest = _manifest(series)
    assert "support" in manifest
    assert "linchpin.fly.dev" in manifest["support"]
    # Free bait: omitting price (or 0) is the Apps Store free listing. Paid
    # retainers are sold outside the Store.
    assert manifest.get("price", 0) in (0, 0.0, None)
    assert manifest["license"] == "OPL-1"
    assert str(manifest["version"]).startswith(f"{series}.")
    assert manifest["name"] == "Kern Inventory AI"


@pytest.mark.parametrize("series", _SERIES)
def test_manifest_cover_and_screenshots_exist(series: str) -> None:
    manifest = _manifest(series)
    desc = _addon(series) / "static" / "description"
    images = manifest["images"]
    assert images[0].endswith("cover.png")
    for rel in images:
        assert (_addon(series) / rel).is_file()
    assert (desc / "icon.png").is_file()
    index = (desc / "index.html").read_text(encoding="utf-8")
    for name in ("cover.png", "screenshot_wizard.png", "screenshot_result.png"):
        assert name in index


@pytest.mark.parametrize("series", _SERIES)
def test_opl1_license_and_copyright_ship_with_the_module(series: str) -> None:
    license_text = (_addon(series) / "LICENSE").read_text(encoding="utf-8")
    copyright_text = (_addon(series) / "COPYRIGHT").read_text(encoding="utf-8")
    assert "Odoo Proprietary License v1.0" in license_text
    assert "kern_dry_run" in copyright_text


def test_both_series_share_the_store_technical_name() -> None:
    # The Store treats two series as the same app only if the directory name
    # matches. A rename on one side is a new listing, not an upgrade.
    assert _addon("17.0").name == _addon("19.0").name == _MODULE


def _load_sync():
    spec = importlib.util.spec_from_file_location(
        "sync_linchpin_odoo_apps",
        _REPO / "scripts" / "sync_linchpin_odoo_apps.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sync_script_puts_the_module_at_the_store_root(tmp_path: Path) -> None:
    sync = _load_sync()
    store = tmp_path / "linchpin-odoo-apps"
    for series in _SERIES:
        dest = sync.stage_series(_REPO, store, series)
        assert dest == store / _MODULE
        assert (dest / "__manifest__.py").is_file()
        assert (dest / "LICENSE").is_file()
        assert (dest / "static" / "description" / "cover.png").is_file()
        assert not (store / "odoo_addon").exists()
        manifest = ast.literal_eval((dest / "__manifest__.py").read_text(encoding="utf-8"))
        assert str(manifest["version"]).startswith(f"{series}.")


def test_sync_script_commits_orphan_series_branches(tmp_path: Path) -> None:
    sync = _load_sync()
    store = tmp_path / "linchpin-odoo-apps"
    rc = sync.main(["--store-dir", str(store)])
    assert rc == 0
    branches = subprocess.check_output(
        ["git", "-C", str(store), "branch", "--format=%(refname:short)"],
        text=True,
    ).split()
    assert "17.0" in branches and "19.0" in branches
    for series in _SERIES:
        tree = subprocess.check_output(
            ["git", "-C", str(store), "ls-tree", "--name-only", series],
            text=True,
        ).split()
        assert _MODULE in tree
        assert "README.md" in tree
        assert "odoo_addon" not in tree
        blob = subprocess.check_output(
            ["git", "-C", str(store), "show", f"{series}:{_MODULE}/__manifest__.py"],
            text=True,
        )
        manifest = ast.literal_eval(blob)
        assert str(manifest["version"]).startswith(f"{series}.")


def test_sync_push_without_the_store_repo_exits_cleanly(tmp_path: Path) -> None:
    sync = _load_sync()
    store = tmp_path / "linchpin-odoo-apps"
    rc = sync.main(
        [
            "--store-dir",
            str(store),
            "--push",
            "--remote",
            str(tmp_path / "no-such-remote.git"),
        ]
    )
    assert rc == 2
    # Staging still happened; only the push is blocked.
    branches = subprocess.check_output(
        ["git", "-C", str(store), "branch", "--format=%(refname:short)"],
        text=True,
    ).split()
    assert "17.0" in branches and "19.0" in branches

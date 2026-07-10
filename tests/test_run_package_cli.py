"""Tests for the Sprint de Liquidacion CLI glue in examples/run_package.py:
fee-param resolution (CLI > client profile > default), the post-liquidation
sales CSV reader, and the annex-writing wire-up around a real package run.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from examples.run_package import (
    _actual_recovery_by_sku,
    _resolve_fee_params,
    _write_liquidation_annexes,
    build_demo_intake,
)
from scm_agent.package_specs import LIQUIDACION
from scm_agent.packages import run_package
from src import client_profile
from src.contingent_fee import DEFAULT_FEE_PCT, DEFAULT_FLOOR


class _Args:
    """Minimal stand-in for argparse.Namespace with only the fields the
    annex-writing helpers read."""

    def __init__(self, client="Demo Client", out="out", fee_pct=None, fee_floor=None, measure=None):
        self.client = client
        self.out = out
        self.fee_pct = fee_pct
        self.fee_floor = fee_floor
        self.measure = measure


# ---- _resolve_fee_params: CLI > profile > default -----------------------------


def test_resolve_fee_params_cli_override_wins_over_everything():
    pct, floor = _resolve_fee_params("Any Client", cli_pct=0.20, cli_floor=999.0)
    assert (pct, floor) == (0.20, 999.0)


def test_resolve_fee_params_falls_back_to_client_profile(tmp_path):
    client_profile.upsert_profile("Acme Liquida", "Acme Liquida", root=tmp_path, contingent_fee_pct=0.20)
    pct, floor = _resolve_fee_params("Acme Liquida", cli_pct=None, cli_floor=None, root=tmp_path)
    assert pct == 0.20
    assert floor == DEFAULT_FLOOR


def test_resolve_fee_params_defaults_when_no_profile_and_no_cli(tmp_path):
    pct, floor = _resolve_fee_params("Nobody On Record", cli_pct=None, cli_floor=None)
    assert pct == DEFAULT_FEE_PCT
    assert floor == DEFAULT_FLOOR


def test_resolve_fee_params_ignores_generic_client_label():
    # "Client" is the generic placeholder label — must never attempt a real
    # profile lookup (see client_profile.is_generic_client_label).
    pct, floor = _resolve_fee_params("Client", cli_pct=None, cli_floor=None)
    assert pct == DEFAULT_FEE_PCT


def test_resolve_fee_params_corrupt_profile_fails_loudly_instead_of_silently_defaulting(tmp_path):
    # Mirrors _resolve_lang's equivalent test (tests/test_run_package_lang_cli.py)
    # -- a corrupt profile.json is a data-integrity problem, not "no profile",
    # and must never silently degrade to the default fee percentage.
    slug = client_profile.slugify_client_id("Acme Corrupto")
    profile_dir = tmp_path / slug
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ValueError, match="corrupt"):
        _resolve_fee_params("Acme Corrupto", cli_pct=None, cli_floor=None, root=tmp_path)


# ---- _actual_recovery_by_sku: post-liquidation sales CSV -> {sku: cash} ------


def test_actual_recovery_by_sku_sums_quantity_times_price(tmp_path):
    csv = tmp_path / "post.csv"
    pd.DataFrame([
        {"product_id": "SKU-1", "quantity": 100, "price": 5.0},
        {"product_id": "SKU-1", "quantity": 50, "price": 4.0},   # a second sale of the same SKU
        {"product_id": "SKU-2", "quantity": 10, "price": 20.0},
    ]).to_csv(csv, index=False)
    by_sku = _actual_recovery_by_sku(csv)
    assert by_sku["SKU-1"] == pytest.approx(100 * 5.0 + 50 * 4.0)
    assert by_sku["SKU-2"] == pytest.approx(200.0)


def test_actual_recovery_by_sku_accepts_common_column_aliases(tmp_path):
    csv = tmp_path / "post.csv"
    pd.DataFrame([{"sku": "SKU-1", "units_sold": 10, "unit_price": 2.5}]).to_csv(csv, index=False)
    by_sku = _actual_recovery_by_sku(csv)
    assert by_sku["SKU-1"] == pytest.approx(25.0)


def test_actual_recovery_by_sku_missing_columns_is_actionable(tmp_path):
    csv = tmp_path / "post.csv"
    pd.DataFrame([{"foo": 1, "bar": 2}]).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="quantity.*price|price.*quantity"):
        _actual_recovery_by_sku(csv)


def test_actual_recovery_by_sku_rejects_unparseable_values_instead_of_zeroing(tmp_path):
    # A garbled cell (e.g. a thousands-separator "1,200" from an Excel export)
    # must be a loud error, not a silent $0 baked into the client's real fee.
    csv = tmp_path / "post.csv"
    pd.DataFrame([
        {"product_id": "SKU-1", "quantity": "1,200", "price": 5.0},
        {"product_id": "SKU-2", "quantity": 10, "price": 3.0},
    ]).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="SKU-1"):
        _actual_recovery_by_sku(csv)


# ---- _write_liquidation_annexes: wired around a real package run -------------


@pytest.fixture(scope="module")
def demo_intake(tmp_path_factory) -> Path:
    return build_demo_intake(tmp_path_factory.mktemp("intake"))


class _NoKnowledge:
    def ground_citations(self, keywords, brief, limit=5):
        return []


def test_write_liquidation_annexes_writes_estimate_only_without_measure(demo_intake, tmp_path):
    out = tmp_path / "out"
    result = run_package(
        LIQUIDACION, demo_intake, out_dir=out, knowledge=_NoKnowledge(), clients_root=None,
    )
    assert result.status == "ok"
    args = _Args(client="Demo Client", out=str(out), measure=None)
    _write_liquidation_annexes(LIQUIDACION, result, args)

    estimate_path = out / "liquidacion" / "estimacion_honorarios.md"
    assert estimate_path.exists()
    assert "NO UNA FACTURA" in estimate_path.read_text(encoding="utf-8")
    assert not (out / "liquidacion" / "anexo_cierre.md").exists()


def test_write_liquidation_annexes_writes_closing_annex_with_measure(demo_intake, tmp_path):
    out = tmp_path / "out"
    result = run_package(
        LIQUIDACION, demo_intake, out_dir=out, knowledge=_NoKnowledge(), clients_root=None,
    )
    assert result.status == "ok"
    liq = next(s for s in result.steps if s.tool_key == "markdown_liquidation")
    planned_skus = [line.product_id for line in liq.report.lines]
    assert planned_skus  # the demo intake's stock.csv has excess/dead SKUs to liquidate

    measure_csv = tmp_path / "post_liquidacion.csv"
    pd.DataFrame(
        [{"product_id": sku, "quantity": 10, "price": 3.0} for sku in planned_skus]
    ).to_csv(measure_csv, index=False)

    args = _Args(client="Demo Client", out=str(out), measure=str(measure_csv))
    _write_liquidation_annexes(LIQUIDACION, result, args)

    closing = out / "liquidacion" / "anexo_cierre.md"
    assert closing.exists()
    text = closing.read_text(encoding="utf-8")
    assert "nunca sobre la estimacion inicial" in text
    for sku in planned_skus:
        assert sku in text


def test_write_liquidation_annexes_raises_on_bad_fee_pct_instead_of_writing_garbage(demo_intake, tmp_path):
    # main() wraps this call in try/except (ValueError, OSError) and prints a
    # friendly message -- this test documents/pins the exception it relies on,
    # so an out-of-range --fee-pct never reads as a silently-wrong annex.
    out = tmp_path / "out"
    result = run_package(
        LIQUIDACION, demo_intake, out_dir=out, knowledge=_NoKnowledge(), clients_root=None,
    )
    assert result.status == "ok"
    args = _Args(client="Demo Client", out=str(out), fee_pct=0.5)
    with pytest.raises(ValueError, match="fee_pct"):
        _write_liquidation_annexes(LIQUIDACION, result, args)
    assert not (out / "liquidacion" / "estimacion_honorarios.md").exists()


def test_write_liquidation_annexes_measure_without_liquidation_step_is_a_noop(tmp_path, capsys):
    class _FakeResult:
        steps = ()

    args = _Args(out=str(tmp_path), measure="does-not-matter.csv")
    _write_liquidation_annexes(LIQUIDACION, _FakeResult(), args)
    assert "se omite" in capsys.readouterr().out
    assert not (tmp_path / "liquidacion").exists()

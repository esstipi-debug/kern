"""Tests for examples/run_repricing.py -- the operator entry point for the
multichannel repricing playbook (closes 3.0-audit finding #6: the playbook was
reachable only from tests before this CLI existed).

The full stage->approve->apply->verify semantics are covered by
tests/test_repricing_job.py; these tests cover the CLI wiring: argument parsing,
the dry-run vs --apply return codes/output, and that each channel's offline
stand-in builds and runs end to end.
"""

from __future__ import annotations

import importlib

import pytest

cli = importlib.import_module("examples.run_repricing")


def test_parse_prices_valid():
    assert cli._parse_prices("SKU-1=18.0, SKU-2=45") == {"SKU-1": 18.0, "SKU-2": 45.0}


@pytest.mark.parametrize("raw", ["SKU-1", "SKU-1=", "=18", "SKU-1=abc", ""])
def test_parse_prices_rejects_bad_input(raw):
    with pytest.raises(SystemExit):
        cli._parse_prices(raw)


@pytest.mark.parametrize("channel", ["shopify", "meli", "odoo"])
def test_dry_run_stages_but_does_not_apply(channel, capsys):
    rc = cli.main(["--channel", channel])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Staged (dry-run" in out
    assert "Dry run. Re-run with --apply" in out
    assert "applied=" not in out  # nothing applied on a dry run


@pytest.mark.parametrize("channel", ["shopify", "meli", "odoo"])
def test_apply_runs_full_cycle(channel, capsys):
    rc = cli.main(["--channel", channel, "--apply", "--approved-by", "Ana"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "applied=True" in out
    assert "verified=True" in out
    assert "Ana" in out


def test_custom_prices_are_used(capsys):
    rc = cli.main(["--channel", "meli", "--prices", "SKU-1=17.5", "--apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "SKU-1=17.5" in out
    assert "applied=True" in out

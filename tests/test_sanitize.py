"""Tests for the CSV/Excel formula-injection guard."""

from __future__ import annotations

import pytest

from src.sanitize import defuse_formula


@pytest.mark.parametrize("payload", ['=cmd|" /C calc"!A0', "+1+1", "-1+1", "@SUM(A1)"])
def test_defuse_formula_prefixes_trigger_chars(payload):
    assert defuse_formula(payload) == "'" + payload


@pytest.mark.parametrize("safe", ["SKU-A", "", "hello world", "1000"])
def test_defuse_formula_leaves_safe_strings_unchanged(safe):
    assert defuse_formula(safe) == safe


@pytest.mark.parametrize("value", [None, 42, 3.14, float("nan")])
def test_defuse_formula_leaves_non_strings_unchanged(value):
    result = defuse_formula(value)
    assert result is value or (isinstance(value, float) and result != result)  # nan != nan


def test_defuse_formula_is_idempotent():
    once = defuse_formula('=cmd|" /C calc"!A0')
    assert defuse_formula(once) == once


@pytest.mark.parametrize(
    "disguised",
    ['\t=cmd|" /C calc"!A0', " =cmd|\" /C calc\"!A0", "\r-boom", "\n+boom", "  \t@boom"],
)
def test_defuse_formula_catches_leading_whitespace_disguised_payload(disguised):
    """A trigger char hidden behind leading whitespace/control chars must still be
    defused - Excel's CSV import strips leading whitespace before deciding whether
    a field starts with a formula trigger, so checking the literal first character
    alone is not enough (this must not depend on an upstream caller having already
    called .strip() first)."""
    result = defuse_formula(disguised)
    assert result.startswith("'")
    assert result == "'" + disguised  # original content preserved, just quoted

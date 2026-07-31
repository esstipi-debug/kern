"""Tests for the pure intake_scan engine: profiles, circuit breakers, verdict."""
from src.intake_scan import UNCLASSIFIED, FileColumns, classify_all, classify_file


def _file(name, columns, coverage=None, n_rows=100):
    cov = coverage or {c: 1.0 for c in columns}
    return FileColumns(filename=name, columns=tuple(columns), coverage=cov, n_rows=n_rows)


def test_file_matching_all_required_columns_gets_its_profile():
    result = classify_file(_file("ventas.csv", ["date", "product_id", "quantity", "unit_cost"]))
    assert result.profile == "ventas"
    assert result.missing == ()


def test_file_missing_one_required_column_is_unclassified_with_the_reason():
    result = classify_file(_file("algo.csv", ["date", "product_id"]))
    assert result.profile is None
    assert result.reason == UNCLASSIFIED
    assert "quantity" in result.missing


def test_ambiguous_file_matching_two_profiles_is_not_guessed():
    """A file carrying every column of two profiles is reported, never resolved."""
    both = ["sku", "product_id", "on_hand", "daily_demand"]
    result = classify_file(_file("mezcla.csv", both))
    assert result.profile is None
    assert "ambiguous" in result.reason


def test_classification_is_deterministic_and_sorted_by_filename():
    files = [_file("z.csv", ["sku"]), _file("a.csv", ["product_id", "cogs", "avg_inventory_value"])]
    first = classify_all(files)
    second = classify_all(list(reversed(files)))
    assert [c.filename for c in first] == ["a.csv", "z.csv"]
    assert first == second

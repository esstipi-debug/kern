"""Unit tests for the shared negative-quantity counter (jobs/quantity_audit.py)."""

import dataclasses

import pandas as pd
import pytest

from jobs.quantity_audit import QuantityAudit, audit_quantities


def _df(quantities, products=None):
    products = products or ["A"] * len(quantities)
    return pd.DataFrame({"product_id": products, "quantity": quantities})


def test_clean_flow_data_is_not_material():
    audit = audit_quantities(_df([10, 20, 30]), quantity_col="quantity", product_col="product_id")
    assert audit.n_negative == 0
    assert audit.negative_fraction == 0.0
    assert audit.is_material is False
    assert audit.issues() == []


def test_flow_below_threshold_is_counted_but_not_material():
    # 1 negative in 200 rows = 0.5%, under the 2% default.
    quantities = [10.0] * 199 + [-5.0]
    audit = audit_quantities(_df(quantities), quantity_col="quantity", product_col="product_id")
    assert audit.n_negative == 1
    assert audit.negative_total == -5.0
    assert round(audit.negative_fraction, 4) == 0.005
    assert audit.is_material is False
    assert audit.issues() == []


def test_flow_above_threshold_is_material():
    # 3 negatives in 60 rows = 5%, over the 2% default.
    quantities = [10.0] * 57 + [-5.0, -5.0, -5.0]
    audit = audit_quantities(_df(quantities), quantity_col="quantity", product_col="product_id")
    assert audit.is_material is True
    assert len(audit.issues()) == 1
    assert "negative" in audit.issues()[0]


def test_flow_net_negative_sku_is_material_even_below_threshold():
    # 1 negative in 200 rows (0.5%, under threshold) but it sinks SKU B's total.
    quantities = [10.0] * 199 + [-500.0]
    products = ["A"] * 199 + ["B"]
    audit = audit_quantities(
        _df(quantities, products), quantity_col="quantity", product_col="product_id"
    )
    assert audit.n_entities_net_negative == 1
    assert audit.is_material is True


def test_level_data_any_negative_is_material():
    # A negative stock LEVEL admits no "return" reading: one is enough.
    quantities = [10.0] * 199 + [-5.0]
    audit = audit_quantities(
        _df(quantities), quantity_col="quantity", product_col="product_id", kind="level"
    )
    assert audit.negative_fraction < 0.02
    assert audit.is_material is True


def test_threshold_is_overridable():
    quantities = [10.0] * 90 + [-5.0] * 10   # 10%
    audit = audit_quantities(
        _df(quantities), quantity_col="quantity", product_col="product_id", threshold=0.25
    )
    assert audit.is_material is False


def test_disclosure_is_written_even_when_immaterial():
    quantities = [10.0] * 199 + [-5.0]
    audit = audit_quantities(_df(quantities), quantity_col="quantity", product_col="product_id")
    text = audit.disclosure()
    assert "1" in text and "quantity" in text
    assert audit.is_material is False


def test_disclosure_is_empty_when_there_are_no_negatives():
    audit = audit_quantities(_df([10, 20]), quantity_col="quantity", product_col="product_id")
    assert audit.disclosure() == ""


def test_non_numeric_values_do_not_crash_and_are_not_counted_negative():
    df = pd.DataFrame({"product_id": ["A", "A"], "quantity": ["abc", -5]})
    audit = audit_quantities(df, quantity_col="quantity", product_col="product_id")
    assert audit.n_negative == 1


def test_empty_frame_is_inert():
    df = pd.DataFrame({"product_id": [], "quantity": []})
    audit = audit_quantities(df, quantity_col="quantity", product_col="product_id")
    assert audit.n_rows == 0
    assert audit.negative_fraction == 0.0
    assert audit.is_material is False


def test_audit_is_frozen():
    audit = QuantityAudit(column="quantity", kind="flow", n_rows=1, n_negative=0,
                          negative_total=0.0, n_entities_net_negative=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        audit.n_negative = 5


def test_audit_does_not_mutate_the_input_frame():
    df = _df([10.0, -5.0])
    before = df.copy()
    audit_quantities(df, quantity_col="quantity", product_col="product_id")
    pd.testing.assert_frame_equal(df, before)

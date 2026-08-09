import pandas as pd

from jobs.column_mapping import map_columns, normalize_dataframe


def test_map_columns_exact():
    df = pd.DataFrame(columns=["date", "product_id", "quantity", "unit_cost", "lead_time_days"])
    mapping, missing = map_columns(df)
    assert mapping["date"] == "date"
    assert mapping["product_id"] == "product_id"
    assert mapping["quantity"] == "quantity"
    assert missing == []


def test_map_columns_fuzzy():
    df = pd.DataFrame(columns=["Fecha", "SKU", "Cantidad", "Precio", "Plazo de entrega"])
    mapping, missing = map_columns(df)
    assert mapping["date"] == "Fecha"
    assert mapping["product_id"] == "SKU"
    assert mapping["quantity"] == "Cantidad"
    assert mapping["unit_cost"] == "Precio"
    assert mapping["lead_time_days"] == "Plazo de entrega"
    assert missing == []


def test_map_columns_missing():
    df = pd.DataFrame(columns=["date", "product_id"])
    mapping, missing = map_columns(df)
    assert "quantity" in missing
    assert "quantity" not in mapping
    try:
        normalize_dataframe(df, mapping)
        assert False, "should raise"
    except ValueError as exc:
        assert "missing required" in str(exc)

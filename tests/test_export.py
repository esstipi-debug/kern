"""Tests for the CSV export layer (Excel / Power BI feed)."""

from dataclasses import dataclass

import pandas as pd

from src.export import _flatten, write_policy_comparison, write_summary_csv


@dataclass(frozen=True)
class _Sample:
    a: int
    b: float


def test_flatten_nested_dict():
    flat = _flatten({"eoq": {"q": 100, "cost": 50}})
    assert flat == {"eoq_q": 100, "eoq_cost": 50}


def test_flatten_dataclass():
    flat = _flatten(_Sample(a=1, b=2.5))
    assert flat == {"a": 1, "b": 2.5}


def test_write_policy_comparison_prefixes_and_optional_blocks():
    row = write_policy_comparison(
        product_id="SKU-A",
        eoq={"q": 100},
        sq={"s": 250},
        rs={"S": 400},
        simulation={"service_level": 0.96},
        extra={"note": "ok"},
    )
    assert row["product_id"] == "SKU-A"
    assert row["eoq_q"] == 100
    assert row["sq_s"] == 250
    assert row["rs_S"] == 400
    assert row["sim_service_level"] == 0.96
    assert row["note"] == "ok"


def test_write_policy_comparison_omits_simulation_when_absent():
    row = write_policy_comparison("SKU-B", {"q": 1}, {"s": 2}, {"S": 3})
    assert not any(k.startswith("sim_") for k in row)


def test_write_summary_csv_roundtrip(tmp_path):
    rows = [{"product_id": "SKU-A", "q": 100}, {"product_id": "SKU-B", "q": 200}]
    out = write_summary_csv(rows, tmp_path / "nested" / "summary.csv")
    assert out.exists()
    df = pd.read_csv(out)
    assert list(df["product_id"]) == ["SKU-A", "SKU-B"]
    assert list(df["q"]) == [100, 200]

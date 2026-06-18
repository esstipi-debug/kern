"""Tests for Power BI dataset export."""

from pathlib import Path

from src.powerbi_export import build_powerbi_dataset


def test_build_powerbi_dataset(tmp_path: Path):
    data = Path("data/sample_demand.csv")
    paths = build_powerbi_dataset(data, tmp_path, simulate=True)
    assert paths.demand_history.exists()
    assert paths.policies.exists()
    assert paths.product_summary.exists()
    assert paths.simulation.exists()

    import pandas as pd

    policies = pd.read_csv(paths.policies)
    assert set(policies["policy"]) >= {"EOQ", "sQ", "RS"}
    assert len(pd.read_csv(paths.product_summary)) >= 1

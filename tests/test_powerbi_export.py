"""Tests for Power BI dataset export."""

from pathlib import Path

import pandas as pd

from src.powerbi_export import build_powerbi_dataset


def test_build_powerbi_dataset(tmp_path: Path):
    data = Path("data/sample_demand.csv")
    paths = build_powerbi_dataset(data, tmp_path, simulate=True)
    assert paths.demand_history.exists()
    assert paths.policies.exists()
    assert paths.product_summary.exists()
    assert paths.simulation.exists()

    policies = pd.read_csv(paths.policies)
    assert set(policies["policy"]) >= {"EOQ", "sQ", "RS"}
    assert len(pd.read_csv(paths.product_summary)) >= 1


def test_build_powerbi_dataset_defuses_formula_injection_in_product_id(tmp_path: Path):
    """A product_id starting with a formula-trigger char (OWASP CSV injection)
    must survive into the CSV as literal text, never as a live formula. Mirrors
    the same source data used elsewhere (data/sample_demand.csv's SKU-A demand),
    just with a malicious product_id, so the run exercises the real pipeline."""
    malicious_id = "=cmd|' /C calc'!A0"
    source = pd.read_csv("data/sample_demand.csv")
    source = source[source["product_id"] == "SKU-A"].copy()
    source["product_id"] = malicious_id
    data_path = tmp_path / "malicious_demand.csv"
    source.to_csv(data_path, index=False)

    out_dir = tmp_path / "out"
    paths = build_powerbi_dataset(data_path, out_dir, product_ids=[malicious_id])

    defused = "'" + malicious_id
    for path in (
        paths.demand_history,
        paths.product_summary,
        paths.policies,
        paths.cost_optimization,
        paths.fill_rate,
        paths.gsm_nodes,
    ):
        col = pd.read_csv(path)["product_id"]
        assert (col == defused).all(), f"{path.name}: expected defused product_id, got {col.tolist()}"

"""Job-layer tests for jobs/hats_job.py; Task 7 appends tool wiring/routing tests."""

from dataclasses import replace

import pandas as pd
import pytest

from jobs import hats_job
from src.guided import EXECUTED, HANDOFF, OPTIONS, as_executed, passed_guided
from src.hats import HAT_KEYS


def _csv(tmp_path, n_weeks=12):
    rows = []
    for sku, (mu, cost, lead) in {"SKU-A": (100, 10.0, 7), "SKU-B": (40, 25.0, 14)}.items():
        for w in range(n_weeks):
            rows.append({
                "date": f"2026-{1 + w // 4:02d}-{1 + 7 * (w % 4):02d}",
                "product_id": sku,
                "quantity": mu + (7 if w % 2 else -7),   # deterministic sigma > 0
                "unit_cost": cost,
                "lead_time_days": lead,
            })
    path = tmp_path / "demand.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


# -- prepare ------------------------------------------------------------------


def test_prepare_builds_sorted_inputs_with_defaults(tmp_path):
    payload = hats_job.prepare(_csv(tmp_path))
    assert [i.sku for i in payload["inputs"]] == ["SKU-A", "SKU-B"]
    a = payload["inputs"][0]
    assert a.mean_weekly == pytest.approx(100.0)
    assert a.std_weekly > 0
    assert a.annual_demand == pytest.approx(52.0 * 100.0)
    assert a.lead_time_weeks == pytest.approx(1.0)
    assert a.price_breaks_assumed is True                 # D8 default
    assert payload["weights"] == {k: 0.25 for k in HAT_KEYS}
    assert payload["config"].wacc == 0.12


def test_prepare_sku_filter(tmp_path):
    payload = hats_job.prepare(_csv(tmp_path), {"sku": "SKU-B"})
    assert [i.sku for i in payload["inputs"]] == ["SKU-B"]


def test_prepare_raises_on_unknown_sku_and_missing_columns(tmp_path):
    with pytest.raises(ValueError):
        hats_job.prepare(_csv(tmp_path), {"sku": "NOPE"})
    bad = tmp_path / "bad.csv"
    pd.DataFrame({"product_id": ["A"], "unit_cost": [1.0]}).to_csv(bad, index=False)
    with pytest.raises(ValueError):
        hats_job.prepare(str(bad))


def test_prepare_rejects_malformed_weights_and_wacc(tmp_path):
    with pytest.raises(ValueError):
        hats_job.prepare(_csv(tmp_path), {"weights": "gerente=1"})
    with pytest.raises(ValueError):
        hats_job.config_from_params({"wacc": 0.5})        # >= h_total (D5)


def test_prepare_injected_price_breaks(tmp_path):
    payload = hats_job.prepare(
        _csv(tmp_path), {"price_breaks": [[0, 10.0], [500, 9.5]]})
    assert all(i.price_breaks_assumed is False for i in payload["inputs"])
    with pytest.raises(ValueError):
        hats_job.prepare(_csv(tmp_path), {"price_breaks": [[0]]})


# -- run_tension (N4) ---------------------------------------------------------


def test_run_tension_emits_protected_options(tmp_path):
    report = hats_job.run_tension(hats_job.prepare(_csv(tmp_path)))
    assert report.n_skus == 2 and len(report.maps) == 2
    assert report.outcome.status == OPTIONS
    assert passed_guided(report.outcome)
    assert len(report.outcome.options) == len(HAT_KEYS) + 1     # 4 ideals + baseline
    assert all(0.0 <= o.score <= 1.0 for o in report.outcome.options)
    assert "eleccion es humana" in report.outcome.summary
    assert "(assumed)" in report.outcome.summary                # D8 default breaks
    assert report.price_breaks_assumed is True


def test_run_tension_without_assumed_label_when_breaks_injected(tmp_path):
    payload = hats_job.prepare(_csv(tmp_path), {"price_breaks": [[0, 10.0], [500, 9.5]]})
    report = hats_job.run_tension(payload)
    assert "(assumed)" not in report.outcome.summary
    assert report.price_breaks_assumed is False


# -- run_settlement (N5) ------------------------------------------------------


def test_run_settlement_emits_protected_handoff(tmp_path):
    report = hats_job.run_settlement(hats_job.prepare(_csv(tmp_path)))
    assert report.n_skus == 2 and len(report.settlements) == 2
    assert report.outcome.status == HANDOFF
    assert passed_guided(report.outcome)
    packet = report.outcome.handoffs[0]
    assert packet.title == "Aplicar plan reconciliado (Q*, SL*)"
    assert packet.steps and "ACTA" in packet.artifact
    assert len(packet.data["plans"]) == 2
    assert any("politica" in r.description for r in report.outcome.residuals)
    assert report.total_value_usd == pytest.approx(
        sum(s.value_vs_baseline_usd for s in report.settlements))


def test_outcomes_are_never_executed(tmp_path):
    payload = hats_job.prepare(_csv(tmp_path))
    assert hats_job.run_tension(payload).outcome.status != EXECUTED
    assert hats_job.run_settlement(payload).outcome.status != EXECUTED


# -- QA gates -----------------------------------------------------------------


def test_verify_tension_passes_and_blocks_executed(tmp_path):
    report = hats_job.run_tension(hats_job.prepare(_csv(tmp_path)))
    assert hats_job.verify_tension(report) == []
    broken = replace(report, outcome=as_executed("nope"))
    assert hats_job.verify_tension(broken)                # non-empty issue list


def test_verify_settlement_passes_and_blocks_executed(tmp_path):
    report = hats_job.run_settlement(hats_job.prepare(_csv(tmp_path)))
    assert hats_job.verify_settlement(report) == []
    broken = replace(report, outcome=as_executed("nope"))
    assert hats_job.verify_settlement(broken)

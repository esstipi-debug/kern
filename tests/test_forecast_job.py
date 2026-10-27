"""Tests for forecast_job causal forecasting integration and strict AIC gating."""

from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from jobs import forecast_job as fj
from src.causal_forecasting import CausalForecastResult, fit_causal_forecast


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    import src.state.store as store_module
    monkeypatch.setattr(
        store_module,
        "default_store",
        lambda: pytest.fail("test resolved default_store"),
    )


def test_forecast_job_causal_accepted_with_strong_promotions():
    # 12 periods: baseline 10, promo adds 20
    np.random.seed(42)
    periods = list(range(12))
    promo = [0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0]
    demand = [10.0 + 20.0 * p + np.random.normal(0, 0.5) for p in promo]

    df = pd.DataFrame({
        "sku": ["PROMO_SKU"] * 12,
        "period": periods,
        "demand": demand,
        "promo": promo,
    })

    # Prepare with next period having a promotion
    payload = fj.prepare_records(df, {"future_covariates": {"promo": 1.0}})
    assert "PROMO_SKU" in payload
    assert "promo" in payload.covariates_by_sku["PROMO_SKU"]

    report = fj.run(payload)
    sku = report.skus[0]

    # Strong promotion must satisfy strict AIC gating (delta_aic <= -2.0)
    assert sku.causal_accepted is True
    assert sku.delta_aic is not None and sku.delta_aic <= -2.0
    assert sku.method == "causal_multivariate_aic"
    # Next period is promo=1.0, forecast should be ~30 (not ~10)
    assert sku.forecast == pytest.approx(30.0, abs=3.0)
    assert report.n_causal_accepted == 1
    assert "promo" in report.causal_covariates


def test_forecast_job_causal_rejected_with_noise_covariate():
    # 12 periods of steady demand around 50, covariate is pure random noise
    np.random.seed(99)
    periods = list(range(12))
    demand = [50.0 + np.random.normal(0, 1.0) for _ in range(12)]
    noise_promo = np.random.uniform(0, 1, 12).tolist()

    df = pd.DataFrame({
        "sku": ["NOISE_SKU"] * 12,
        "period": periods,
        "demand": demand,
        "promo": noise_promo,
    })

    payload = fj.prepare_records(df, {"future_covariates": {"promo": 0.5}})
    report = fj.run(payload)
    sku = report.skus[0]

    # Irrelevant noise fails AIC gating: univariate baseline maintained
    assert sku.causal_accepted is False
    assert sku.method != "causal_multivariate_aic"
    assert report.n_causal_accepted == 0


def test_forecast_job_operational_and_deck_with_causal(tmp_path):
    np.random.seed(42)
    periods = list(range(12))
    promo = [0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0]
    demand = [10.0 + 25.0 * p for p in promo]

    df = pd.DataFrame({
        "sku": ["PROMO_ITEM"] * 12,
        "period": periods,
        "demand": demand,
        "promo": promo,
    })

    payload = fj.prepare_records(df, {"future_covariates": {"promo": 1.0}})
    report = fj.run(payload)

    # Operational CSV
    out = fj.write_operational(report, tmp_path)
    csv_path = Path(out["csv"])
    content = csv_path.read_text(encoding="utf-8")
    assert "causal_accepted" in content
    assert "delta_aic" in content

    # Deck deliverable
    deck = fj.build_deck(report, client="TestClient")
    md = deck.to_markdown()
    assert "Causal Multivariate Forecast" in md
    assert "promo" in md

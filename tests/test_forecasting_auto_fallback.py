"""Tests for graceful fallback when statsforecast is absent."""

from unittest.mock import patch


def test_forecast_modern_falls_back_when_statsforecast_unavailable():
    """Core path must work without the optional [forecast] extra."""
    import src.forecasting_auto as mod

    with patch.object(mod, "statsforecast_available", return_value=False):
        result = mod.forecast_modern([100, 102, 98, 101, 99] * 5, method="auto_modern")
    assert result.method in ("ses", "croston")

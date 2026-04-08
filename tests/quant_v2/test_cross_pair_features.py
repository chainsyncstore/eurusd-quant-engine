"""Tests for cross-pair feature computation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.features.cross_pair import compute


def _make_bars(n: int = 200) -> pd.DataFrame:
    """Create synthetic OHLCV data."""
    np.random.seed(42)
    base_price = 50000.0
    returns = np.random.normal(0.001, 0.02, n)
    closes = base_price * np.cumprod(1 + returns)
    highs = closes * (1 + np.abs(np.random.normal(0.005, 0.01, n)))
    lows = closes * (1 - np.abs(np.random.normal(0.005, 0.01, n)))
    opens = closes * (1 + np.random.normal(0, 0.005, n))
    volumes = np.random.uniform(100, 1000, n)

    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })
    df.index = pd.date_range(start="2024-01-01", periods=n, freq="1h", tz="UTC")
    return df


def test_cross_pair_features_with_btc_returns() -> None:
    """Test compute() with BTC returns injected."""
    df = _make_bars(200)
    # Create synthetic BTC returns (different from symbol returns)
    btc_returns = pd.Series(np.random.normal(0.0005, 0.015, len(df)), index=df.index)

    # Inject BTC returns as column (simulating caller injection)
    df["_btc_returns"] = btc_returns

    result = compute(df)

    # Assert all 4 new columns are present
    assert "btc_return_4h" in result.columns
    assert "btc_divergence_4h" in result.columns
    assert "btc_correlation_24h" in result.columns
    assert "relative_vol_ratio" in result.columns

    # After warmup (120 bars for correlation, 20 for vol), no NaN
    warmup_idx = 130
    for col in ["btc_return_4h", "btc_divergence_4h", "btc_correlation_24h", "relative_vol_ratio"]:
        assert result[col].iloc[warmup_idx:].notna().all(), f"{col} has NaN after warmup"


def test_cross_pair_features_without_btc_returns() -> None:
    """Test compute() without BTC returns (neutral values)."""
    df = _make_bars(200)
    # No _btc_returns column injected

    result = compute(df)

    # Assert all 4 new columns are present
    assert "btc_return_4h" in result.columns
    assert "btc_divergence_4h" in result.columns
    assert "btc_correlation_24h" in result.columns
    assert "relative_vol_ratio" in result.columns

    # Without BTC returns, features should be 0.0 (neutral)
    assert (result["btc_return_4h"] == 0.0).all()
    assert (result["btc_divergence_4h"] == 0.0).all()
    assert (result["btc_correlation_24h"] == 0.0).all()

    # relative_vol_ratio should still be computed (not 0.0)
    assert (result["relative_vol_ratio"].iloc[20:] > 0).all()


def test_cross_pair_features_empty_btc_series() -> None:
    """Test compute() with empty BTC returns series."""
    df = _make_bars(200)
    # Inject empty series (no index alignment)
    df["_btc_returns"] = pd.Series(dtype=float)

    result = compute(df)

    # Should handle gracefully - empty series gets reindexed and filled with 0.0
    # which is effectively the same as neutral values
    assert (result["btc_return_4h"] == 0.0).all()
    assert (result["btc_divergence_4h"] == 0.0).all()
    assert (result["btc_correlation_24h"] == 0.0).all()


def test_relative_vol_ratio_computation() -> None:
    """Test that relative_vol_ratio uses correct window logic."""
    df = _make_bars(200)

    result = compute(df)

    # relative_vol_ratio should be vol_20 / vol_120
    # After warmup (120 bars), it should be reasonable values
    warmup_slice = result.iloc[130:]
    assert (warmup_slice["relative_vol_ratio"] > 0).all()
    assert (warmup_slice["relative_vol_ratio"] < 10).all()  # Sanity bound

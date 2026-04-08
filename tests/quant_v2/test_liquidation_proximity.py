"""Tests for liquidation proximity feature computation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.features.liquidation_proximity import compute


def _make_bars(n: int = 200, with_oi: bool = True, with_funding: bool = True) -> pd.DataFrame:
    """Create synthetic OHLCV (+ OI/funding) data."""
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

    if with_oi:
        df["open_interest"] = np.random.uniform(1000000, 2000000, n)
        df["open_interest_value"] = df["open_interest"] * closes

    if with_funding:
        df["funding_rate"] = np.random.normal(0.0001, 0.0005, n)

    df.index = pd.date_range(start="2024-01-01", periods=n, freq="1h", tz="UTC")
    return df


def test_liquidation_proximity_with_oi_and_funding() -> None:
    """Test compute() with OI and funding columns present."""
    df = _make_bars(200, with_oi=True, with_funding=True)

    result = compute(df)

    # Assert all 3 new columns are present
    assert "oi_funding_pressure" in result.columns
    assert "price_position_24h" in result.columns
    assert "liquidation_cascade_4h" in result.columns

    # After warmup (72 bars for OI norm, 24 for price position), no NaN
    warmup_idx = 80
    for col in ["oi_funding_pressure", "price_position_24h", "liquidation_cascade_4h"]:
        assert result[col].iloc[warmup_idx:].notna().all(), f"{col} has NaN after warmup"

    # price_position_24h should be in [0, 1]
    assert (result["price_position_24h"].iloc[warmup_idx:] >= 0).all()
    assert (result["price_position_24h"].iloc[warmup_idx:] <= 1).all()


def test_liquidation_proximity_without_oi() -> None:
    """Test compute() without OI columns (graceful fallback)."""
    df = _make_bars(200, with_oi=False, with_funding=True)

    result = compute(df)

    # Assert all 3 new columns are present
    assert "oi_funding_pressure" in result.columns
    assert "price_position_24h" in result.columns
    assert "liquidation_cascade_4h" in result.columns

    # Without OI, oi_funding_pressure and liquidation_cascade_4h should be 0.0
    assert (result["oi_funding_pressure"] == 0.0).all()
    assert (result["liquidation_cascade_4h"] == 0.0).all()

    # price_position_24h should still be computed
    warmup_idx = 30
    assert result["price_position_24h"].iloc[warmup_idx:].notna().all()


def test_liquidation_proximity_without_funding() -> None:
    """Test compute() without funding column."""
    df = _make_bars(200, with_oi=True, with_funding=False)

    result = compute(df)

    # oi_funding_pressure uses funding.abs(), so with missing funding it should be 0.0
    # (funding series defaults to 0.0)
    assert (result["oi_funding_pressure"] == 0.0).all()


def test_liquidation_proximity_without_oi_or_funding() -> None:
    """Test compute() with neither OI nor funding columns."""
    df = _make_bars(200, with_oi=False, with_funding=False)

    result = compute(df)

    # Both OI-dependent features should be 0.0
    assert (result["oi_funding_pressure"] == 0.0).all()
    assert (result["liquidation_cascade_4h"] == 0.0).all()

    # price_position_24h should still be computed from close prices
    warmup_idx = 30
    assert result["price_position_24h"].iloc[warmup_idx:].notna().all()

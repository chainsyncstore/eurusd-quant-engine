"""Liquidation proximity features — how close are leveraged positions to wipeout."""

from __future__ import annotations

import pandas as pd


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Add liquidation proximity features.

    Requires columns: close, open_interest (or open_interest_value),
    funding_rate (optional).
    """
    result = df.copy()
    close = pd.to_numeric(result["close"], errors="coerce")

    oi = None
    if "open_interest_value" in result.columns:
        oi = pd.to_numeric(result["open_interest_value"], errors="coerce")
    elif "open_interest" in result.columns:
        oi = pd.to_numeric(result["open_interest"], errors="coerce")

    funding = (
        pd.to_numeric(result["funding_rate"], errors="coerce")
        if "funding_rate" in result.columns
        else pd.Series(0.0, index=result.index)
    )

    # Feature 1: OI-weighted funding (crowded trade pressure)
    # High OI + extreme funding = crowded trade about to unwind
    if oi is not None:
        oi_norm = oi / oi.rolling(72).mean().clip(lower=1e-8)
        result["oi_funding_pressure"] = (oi_norm * funding.abs()).fillna(0.0)
    else:
        result["oi_funding_pressure"] = 0.0

    # Feature 2: Price distance from recent extremes (proxy for liquidation clusters)
    high_24 = close.rolling(24).max()
    low_24 = close.rolling(24).min()
    range_24 = (high_24 - low_24).clip(lower=1e-8)
    result["price_position_24h"] = ((close - low_24) / range_24).fillna(0.5)

    # Feature 3: Liquidation cascade risk — sharp OI drop + price move
    if oi is not None:
        oi_change_4h = oi.pct_change(4).fillna(0.0)
        price_change_4h = close.pct_change(4).fillna(0.0)
        # Large OI drop + large price move = liquidation cascade happened
        result["liquidation_cascade_4h"] = (
            (oi_change_4h.abs() * price_change_4h.abs()).fillna(0.0)
        )
    else:
        result["liquidation_cascade_4h"] = 0.0

    return result

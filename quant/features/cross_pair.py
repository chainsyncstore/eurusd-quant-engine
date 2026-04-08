"""Cross-pair correlation features for multi-symbol awareness."""

from __future__ import annotations

import pandas as pd


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Add cross-pair features to a single-symbol DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Single-symbol OHLCV DataFrame with 'close' column.
        May contain pre-injected '_btc_returns' column (injected by caller).

    Returns
    -------
    pd.DataFrame
        Original df with new columns appended.
    """
    result = df.copy()
    close = pd.to_numeric(result["close"], errors="coerce")
    symbol_returns = close.pct_change()

    # Check for pre-injected BTC returns column
    btc_returns = result.get("_btc_returns")

    # Valid BTC returns: exists, not empty, and has at least some non-NaN values
    has_valid_btc = (
        btc_returns is not None
        and not btc_returns.empty
        and not btc_returns.isna().all()
    )

    if has_valid_btc:
        # Align BTC returns to this symbol's index
        btc_aligned = btc_returns.reindex(result.index, method="ffill").fillna(0.0)

        # Feature 1: BTC return over last 4 bars
        result["btc_return_4h"] = btc_aligned.rolling(4).sum().fillna(0.0)

        # Feature 2: Symbol vs BTC divergence (symbol_return - btc_return, rolling 4h)
        divergence = symbol_returns - btc_aligned
        result["btc_divergence_4h"] = divergence.rolling(4).sum().fillna(0.0)

        # Feature 3: Rolling correlation with BTC (24h window)
        result["btc_correlation_24h"] = (
            symbol_returns.rolling(24).corr(btc_aligned).fillna(0.0)
        )
    else:
        result["btc_return_4h"] = 0.0
        result["btc_divergence_4h"] = 0.0
        result["btc_correlation_24h"] = 0.0

    # Feature 4: Symbol volatility relative to its own 120h baseline
    vol_20 = symbol_returns.rolling(20).std().fillna(0.0)
    vol_120 = symbol_returns.rolling(120).std().fillna(1e-8)
    result["relative_vol_ratio"] = (vol_20 / vol_120.clip(lower=1e-8)).fillna(1.0)

    return result

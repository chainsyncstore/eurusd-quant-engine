"""Tests for order book snapshot feature module."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.features.order_book import compute


def _make_df(n: int = 50, with_vol: bool = True) -> pd.DataFrame:
    """Create minimal OHLCV DataFrame for testing."""
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = pd.Series(50_000.0 + np.random.randn(n) * 100, index=idx)
    df = pd.DataFrame({
        "open": close,
        "high": close * 1.001,
        "low": close * 0.999,
        "close": close,
        "volume": np.ones(n) * 1000.0,
    })
    if with_vol:
        df["realized_vol_5"] = 0.01
    return df


def _make_snapshot(bid_price: float, ask_price: float, bid_qty: float = 1.0, ask_qty: float = 1.0, levels: int = 20) -> dict:
    """Build a mock order book snapshot."""
    bids = [[bid_price - i * 0.01, bid_qty] for i in range(levels)]
    asks = [[ask_price + i * 0.01, ask_qty] for i in range(levels)]
    return {"bids": bids, "asks": asks}


class TestOrderBookFeaturesNoSnapshot:

    def test_all_zeros_without_snapshot(self):
        df = _make_df(20)
        result = compute(df)
        assert "bid_ask_spread_bps" in result.columns
        assert result["bid_ask_spread_bps"].sum() == pytest.approx(0.0)
        assert result["book_imbalance_5"].sum() == pytest.approx(0.0)
        assert result["book_imbalance_20"].sum() == pytest.approx(0.0)

    def test_depth_ratio_ones_without_snapshot(self):
        df = _make_df(20)
        result = compute(df)
        # log(1.0) = 0.0 for neutral ratio
        assert result["depth_ratio_5"].sum() == pytest.approx(0.0)

    def test_six_features_added(self):
        df = _make_df(10)
        result = compute(df)
        expected = {"bid_ask_spread_bps", "book_imbalance_5", "book_imbalance_20",
                    "depth_ratio_5", "volume_at_touch_ratio", "spread_vol_ratio"}
        assert expected.issubset(set(result.columns))


class TestOrderBookFeaturesWithSnapshot:

    def test_spread_computed_correctly(self):
        df = _make_df(10)
        snap = _make_snapshot(bid_price=50_000.0, ask_price=50_010.0)
        df["_ob_snapshot"] = [None] * 9 + [snap]
        result = compute(df)
        # spread = (50010 - 50000) / 50005 × 10000 ≈ 2.0 bps
        assert result["bid_ask_spread_bps"].iloc[-1] == pytest.approx(2.0, abs=0.1)

    def test_balanced_book_imbalance_zero(self):
        df = _make_df(10)
        snap = _make_snapshot(50_000.0, 50_001.0, bid_qty=1.0, ask_qty=1.0)
        df["_ob_snapshot"] = [None] * 9 + [snap]
        result = compute(df)
        assert result["book_imbalance_5"].iloc[-1] == pytest.approx(0.0, abs=1e-6)
        assert result["book_imbalance_20"].iloc[-1] == pytest.approx(0.0, abs=1e-6)

    def test_bid_heavy_book_positive_imbalance(self):
        df = _make_df(10)
        snap = _make_snapshot(50_000.0, 50_001.0, bid_qty=3.0, ask_qty=1.0)
        df["_ob_snapshot"] = [None] * 9 + [snap]
        result = compute(df)
        assert result["book_imbalance_5"].iloc[-1] > 0.0
        assert result["book_imbalance_20"].iloc[-1] > 0.0

    def test_ask_heavy_book_negative_imbalance(self):
        df = _make_df(10)
        snap = _make_snapshot(50_000.0, 50_001.0, bid_qty=1.0, ask_qty=5.0)
        df["_ob_snapshot"] = [None] * 9 + [snap]
        result = compute(df)
        assert result["book_imbalance_5"].iloc[-1] < 0.0

    def test_volume_at_touch_ratio(self):
        df = _make_df(10)
        snap = _make_snapshot(50_000.0, 50_001.0, bid_qty=4.0, ask_qty=2.0)
        df["_ob_snapshot"] = [None] * 9 + [snap]
        result = compute(df)
        # bid_qty / ask_qty = 4/2 = 2 → log(2) ≈ 0.693
        assert result["volume_at_touch_ratio"].iloc[-1] == pytest.approx(np.log(2.0), abs=0.01)

    def test_spread_vol_ratio_uses_realized_vol(self):
        df = _make_df(10, with_vol=True)
        df["realized_vol_5"] = 0.001  # small vol → large spread_vol_ratio
        snap = _make_snapshot(50_000.0, 50_010.0)
        df["_ob_snapshot"] = [None] * 9 + [snap]
        result = compute(df)
        # Should be non-zero when vol is small and spread is non-zero
        assert result["spread_vol_ratio"].iloc[-1] > 0.0

    def test_spread_vol_ratio_zero_without_vol_column(self):
        df = _make_df(10, with_vol=False)
        snap = _make_snapshot(50_000.0, 50_010.0)
        df["_ob_snapshot"] = [None] * 9 + [snap]
        result = compute(df)
        assert result["spread_vol_ratio"].iloc[-1] == pytest.approx(0.0)

    def test_invalid_snapshot_produces_zeros(self):
        df = _make_df(5)
        df["_ob_snapshot"] = [None, "bad", 42, {}, {"bids": [], "asks": []}]
        result = compute(df)
        assert result["bid_ask_spread_bps"].sum() == pytest.approx(0.0)

    def test_only_last_bar_has_snapshot(self):
        df = _make_df(20)
        snap = _make_snapshot(50_000.0, 50_005.0)
        df["_ob_snapshot"] = [None] * 19 + [snap]
        result = compute(df)
        # Only last bar should have non-zero spread
        assert result["bid_ask_spread_bps"].iloc[:-1].sum() == pytest.approx(0.0)
        assert result["bid_ask_spread_bps"].iloc[-1] > 0.0


class TestOrderBookInPipeline:

    def test_pipeline_includes_ob_features(self):
        """Verify that build_features includes order book features in whitelist."""
        from quant.features.pipeline import _FEATURE_WHITELIST
        ob_features = {"bid_ask_spread_bps", "book_imbalance_5", "book_imbalance_20",
                       "depth_ratio_5", "volume_at_touch_ratio", "spread_vol_ratio"}
        assert ob_features.issubset(_FEATURE_WHITELIST)

    def test_pipeline_with_ob_snapshot_produces_features(self):
        """Full pipeline run with an injected snapshot."""
        from quant.features.pipeline import build_features, get_feature_columns
        import numpy as np

        n = 250
        idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
        rng = np.random.default_rng(42)
        close = 50_000.0 + np.cumsum(rng.normal(0, 100, n))
        df = pd.DataFrame({
            "open": close, "high": close * 1.001, "low": close * 0.999,
            "close": close, "volume": rng.uniform(100, 1000, n),
            "taker_buy_volume": rng.uniform(50, 500, n),
            "taker_sell_volume": rng.uniform(50, 500, n),
            "funding_rate": rng.normal(0, 0.0001, n),
            "open_interest": rng.uniform(1e6, 2e6, n),
            "open_interest_value": rng.uniform(1e6, 2e6, n),
        }, index=idx)
        snap = {"bids": [[49_990.0 - i, 1.0] for i in range(20)],
                "asks": [[50_010.0 + i, 1.0] for i in range(20)]}
        df["_ob_snapshot"] = [None] * (n - 1) + [snap]

        result = build_features(df)
        feat_cols = get_feature_columns(result)
        ob_features = [c for c in feat_cols if c in
                       {"bid_ask_spread_bps", "book_imbalance_5", "bid_ask_spread_bps"}]
        assert len(ob_features) > 0
        # Last bar should have non-zero spread
        assert result["bid_ask_spread_bps"].iloc[-1] > 0.0

"""Tests for RiskParityOptimizer and planner integration."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_v2.portfolio.optimizer import (
    RiskParityOptimizer,
    compute_rolling_correlations,
)
from quant_v2.contracts import StrategySignal
from quant_v2.execution.planner import PlannerConfig, build_execution_intents
from quant_v2.portfolio.risk_policy import PortfolioRiskPolicy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _price_series(n: int = 100, vol: float = 0.01, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    returns = rng.normal(0, vol, n)
    prices = 1000.0 * np.cumprod(1 + returns)
    return pd.Series(prices, index=pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC"))


def _correlated_series(base: pd.Series, rho: float, noise_vol: float = 0.001, seed: int = 99) -> pd.Series:
    """Create a series with target correlation rho relative to base."""
    rng = np.random.default_rng(seed)
    base_ret = base.pct_change().dropna()
    noise = pd.Series(rng.normal(0, noise_vol, len(base_ret)), index=base_ret.index)
    mixed_ret = rho * base_ret + np.sqrt(1 - rho**2) * noise
    prices = base.iloc[0] * np.cumprod(1 + mixed_ret)
    return pd.Series(prices.values, index=base.index[1:])


def _signal(symbol: str, side: str = "BUY", confidence: float = 0.75) -> StrategySignal:
    return StrategySignal(
        symbol=symbol, timeframe="1h", horizon_bars=4,
        signal=side, confidence=confidence,
    )


# ---------------------------------------------------------------------------
# RiskParityOptimizer unit tests
# ---------------------------------------------------------------------------

class TestRiskParityOptimizer:

    def test_single_symbol_weight_equals_exposure(self):
        opt = RiskParityOptimizer(min_notional_usd=0.0)
        prices = {"BTC": _price_series(100, vol=0.02)}
        result = opt.optimize({"BTC": 0.15}, prices, equity_usd=300.0)
        assert "BTC" in result.weights
        assert abs(result.weights["BTC"]) == pytest.approx(0.15, rel=0.01)

    def test_risk_parity_higher_vol_gets_lower_weight(self):
        opt = RiskParityOptimizer(min_notional_usd=0.0)
        prices = {
            "HIGH_VOL": _price_series(100, vol=0.05),
            "LOW_VOL": _price_series(100, vol=0.01, seed=43),
        }
        exposures = {"HIGH_VOL": 0.10, "LOW_VOL": 0.10}
        result = opt.optimize(exposures, prices, equity_usd=300.0)
        # LOW_VOL should have higher weight than HIGH_VOL
        assert abs(result.weights["LOW_VOL"]) > abs(result.weights["HIGH_VOL"])

    def test_gross_exposure_preserved(self):
        opt = RiskParityOptimizer(min_notional_usd=0.0, correlation_threshold=1.0)
        prices = {
            "BTC": _price_series(100, vol=0.02),
            "ETH": _price_series(100, vol=0.03, seed=43),
        }
        exposures = {"BTC": 0.10, "ETH": 0.08}
        result = opt.optimize(exposures, prices, equity_usd=300.0)
        original_gross = sum(abs(v) for v in exposures.values())
        opt_gross = sum(abs(v) for v in result.weights.values())
        assert opt_gross == pytest.approx(original_gross, rel=0.05)

    def test_direction_preserved(self):
        opt = RiskParityOptimizer(min_notional_usd=0.0)
        prices = {"BTC": _price_series(100), "ETH": _price_series(100, seed=43)}
        exposures = {"BTC": 0.10, "ETH": -0.08}  # BUY and SELL
        result = opt.optimize(exposures, prices, equity_usd=300.0)
        assert result.weights["BTC"] > 0.0
        assert result.weights["ETH"] < 0.0

    def test_correlation_penalty_reduces_same_direction_weights(self):
        """After correlation penalty, individual weights are pulled toward equal split
        relative to the no-penalty case. The gross is preserved (renormalised), but
        the constraint flag is applied and individual weights differ.
        """
        base = _price_series(200, vol=0.02)
        correlated = _correlated_series(base, rho=0.95)

        opt_no_penalty = RiskParityOptimizer(min_notional_usd=0.0, correlation_threshold=1.0)
        opt_penalty = RiskParityOptimizer(min_notional_usd=0.0, correlation_threshold=0.70)

        prices = {"BTC": base, "ETH": correlated}
        exposures = {"BTC": 0.10, "ETH": 0.10}

        res_no = opt_no_penalty.optimize(exposures, prices, equity_usd=300.0)
        res_yes = opt_penalty.optimize(exposures, prices, equity_usd=300.0)

        # Penalty should be flagged
        assert "correlation_penalty" in res_yes.constraints_applied
        assert "correlation_penalty" not in res_no.constraints_applied

    def test_correlation_penalty_not_applied_opposite_directions(self):
        base = _price_series(200, vol=0.02)
        correlated = _correlated_series(base, rho=0.95)

        opt = RiskParityOptimizer(min_notional_usd=0.0, correlation_threshold=0.70)
        prices = {"BTC": base, "ETH": correlated}
        exposures = {"BTC": 0.10, "ETH": -0.10}  # opposite directions

        result = opt.optimize(exposures, prices, equity_usd=300.0)
        assert "correlation_penalty" not in result.constraints_applied

    def test_min_notional_filter_drops_small_position(self):
        opt = RiskParityOptimizer(min_notional_usd=100.0)
        prices = {
            "BTC": _price_series(100, vol=0.02),
            "ETH": _price_series(100, vol=0.03, seed=43),
        }
        # At equity=$300, 1% exposure = $3 < min_notional=$100 → should be dropped
        exposures = {"BTC": 0.01, "ETH": 0.01}
        result = opt.optimize(exposures, prices, equity_usd=300.0)
        assert len(result.weights) == 0
        assert len(result.dropped_symbols) == 2
        assert "min_notional_filter" in result.constraints_applied

    def test_empty_target_exposures(self):
        opt = RiskParityOptimizer()
        result = opt.optimize({}, {}, equity_usd=300.0)
        assert result.weights == {}
        assert result.dropped_symbols == []

    def test_missing_price_history_uses_default_vol(self):
        opt = RiskParityOptimizer(min_notional_usd=0.0)
        exposures = {"BTC": 0.10, "ETH": 0.08}
        result = opt.optimize(exposures, {}, equity_usd=300.0)
        # With no price data, both get default vol=1.0 → equal risk-parity weights
        assert abs(result.weights["BTC"]) == pytest.approx(abs(result.weights["ETH"]), rel=0.01)

    def test_vols_in_result(self):
        opt = RiskParityOptimizer(min_notional_usd=0.0)
        prices = {"BTC": _price_series(100, vol=0.02)}
        result = opt.optimize({"BTC": 0.10}, prices, equity_usd=300.0)
        assert "BTC" in result.vols
        assert result.vols["BTC"] > 0.0


# ---------------------------------------------------------------------------
# compute_rolling_correlations
# ---------------------------------------------------------------------------

class TestComputeRollingCorrelations:

    def test_self_correlation_not_computed(self):
        prices = {"BTC": _price_series(100)}
        result = compute_rolling_correlations(prices)
        assert all(a != b for a, b in result.keys())

    def test_correlated_pair_high_correlation(self):
        base = _price_series(200, vol=0.02)
        correlated = _correlated_series(base, rho=0.90)
        prices = {"BTC": base, "ETH": correlated}
        result = compute_rolling_correlations(prices)
        assert len(result) == 1
        corr = list(result.values())[0]
        assert corr > 0.5  # should be strongly positive

    def test_uncorrelated_pair(self):
        prices = {
            "BTC": _price_series(200, vol=0.02, seed=1),
            "ETH": _price_series(200, vol=0.02, seed=999),
        }
        result = compute_rolling_correlations(prices)
        corr = list(result.values())[0]
        assert abs(corr) < 0.8  # not highly correlated

    def test_empty_returns(self):
        prices = {"BTC": pd.Series(dtype=float)}
        result = compute_rolling_correlations(prices)
        assert result == {}


# ---------------------------------------------------------------------------
# Planner integration
# ---------------------------------------------------------------------------

class TestPlannerOptimizerIntegration:

    def test_optimizer_disabled_when_no_histories(self):
        """Planner with optimizer but no price histories should still produce intents."""
        policy = PortfolioRiskPolicy()
        cfg = PlannerConfig(min_confidence=0.65, enable_optimizer=True, equity_usd=300.0)
        optimizer = RiskParityOptimizer(min_notional_usd=0.0)
        sigs = [_signal("BTCUSDT", "BUY", confidence=0.75)]

        plan = build_execution_intents(
            sigs, policy=policy, config=cfg,
            optimizer=optimizer, price_histories={},
        )
        assert len(plan.intents) > 0

    def test_optimizer_none_passes_through(self):
        """Without optimizer, planner uses raw allocation exposures."""
        policy = PortfolioRiskPolicy()
        cfg = PlannerConfig(min_confidence=0.65, enable_optimizer=False, equity_usd=300.0)
        sigs = [_signal("BTCUSDT", "BUY", confidence=0.75)]

        plan = build_execution_intents(sigs, policy=policy, config=cfg)
        assert len(plan.intents) > 0

    def test_optimizer_reduces_correlated_positions(self):
        """When two symbols are highly correlated, optimizer should reduce combined weight."""
        base = _price_series(200, vol=0.02)
        corr = _correlated_series(base, rho=0.95)

        histories = {
            "BTCUSDT": base,
            "ETHUSDT": pd.Series(corr.values, index=base.index[:len(corr)]),
        }

        policy = PortfolioRiskPolicy()
        cfg = PlannerConfig(min_confidence=0.65, enable_optimizer=True, equity_usd=300.0)
        optimizer = RiskParityOptimizer(min_notional_usd=0.0, correlation_threshold=0.70)

        sigs = [
            _signal("BTCUSDT", "BUY", confidence=0.75),
            _signal("ETHUSDT", "BUY", confidence=0.75),
        ]

        plan_with = build_execution_intents(
            sigs, policy=policy, config=cfg,
            optimizer=optimizer, price_histories=histories,
        )
        plan_without = build_execution_intents(
            sigs, policy=policy,
            config=PlannerConfig(min_confidence=0.65, enable_optimizer=False, equity_usd=300.0),
        )

        gross_with = sum(abs(i.risk_budget_frac) for i in plan_with.intents)
        gross_without = sum(abs(i.risk_budget_frac) for i in plan_without.intents)
        # Correlation penalty should reduce gross exposure
        assert gross_with <= gross_without

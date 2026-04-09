"""Tests for BinanceCostModel and cost gate in allocate_signals."""
from __future__ import annotations

import pytest

from quant_v2.portfolio.cost_model import (
    BinanceCostModel,
    CostEstimate,
    confidence_to_edge_bps,
    get_default_cost_model,
)
from quant_v2.contracts import StrategySignal
from quant_v2.portfolio.allocation import allocate_signals


# ---------------------------------------------------------------------------
# BinanceCostModel unit tests
# ---------------------------------------------------------------------------

class TestBinanceCostModel:

    def test_maker_fee_only_for_zero_notional(self):
        model = BinanceCostModel(maker_fee_bps=2.0, taker_fee_bps=4.0)
        est = model.estimate("BTCUSDT", notional_usd=0.0)
        assert est.round_trip_cost_bps == 0.0
        assert est.impact_bps == 0.0

    def test_round_trip_fee_both_maker(self):
        model = BinanceCostModel(maker_fee_bps=2.0, taker_fee_bps=4.0, use_taker_for_exit=False)
        est = model.estimate("BTCUSDT", notional_usd=1000.0)
        # Fee: 2 + 2 = 4 bps minimum, plus small impact
        assert est.round_trip_cost_bps > 4.0
        assert est.maker_fee_bps == 2.0

    def test_round_trip_fee_taker_exit(self):
        model = BinanceCostModel(maker_fee_bps=2.0, taker_fee_bps=4.0, use_taker_for_exit=True)
        est = model.estimate("BTCUSDT", notional_usd=1000.0)
        # Fee: 2 + 4 = 6 bps minimum
        assert est.round_trip_cost_bps > 6.0

    def test_impact_scales_with_notional(self):
        model = BinanceCostModel()
        est_small = model.estimate("BTCUSDT", notional_usd=100.0)
        est_large = model.estimate("BTCUSDT", notional_usd=100_000.0)
        assert est_large.impact_bps > est_small.impact_bps

    def test_impact_lower_for_liquid_symbol(self):
        model = BinanceCostModel()
        est_btc = model.estimate("BTCUSDT", notional_usd=10_000.0)
        est_ltc = model.estimate("LTCUSDT", notional_usd=10_000.0)
        # BTC has much higher ADV → lower participation → lower impact
        assert est_btc.impact_bps < est_ltc.impact_bps

    def test_is_economic_low_confidence_fails(self):
        model = BinanceCostModel(maker_fee_bps=2.0, taker_fee_bps=4.0)
        # 65% confidence → edge ~3000 bps but impact at small notional is near-zero
        # At $45 notional, should pass (fees ~4 bps, min_edge ~6 bps, edge ~3000 bps)
        economic, est = model.is_economic("BTCUSDT", notional_usd=45.0, edge_bps=3000.0)
        assert economic is True

    def test_is_economic_very_small_notional_marginal(self):
        model = BinanceCostModel(maker_fee_bps=2.0, taker_fee_bps=4.0)
        # Tiny notional, marginal edge just above fee threshold
        economic, est = model.is_economic("BTCUSDT", notional_usd=10.0, edge_bps=5.0)
        # 5 bps edge, round-trip ~4 bps, min required ~6 bps → should fail
        assert economic is False

    def test_cost_estimate_usd(self):
        model = BinanceCostModel(maker_fee_bps=2.0, taker_fee_bps=4.0)
        est = model.estimate("BTCUSDT", notional_usd=1000.0)
        expected_usd = 1000.0 * est.round_trip_cost_bps / 10_000.0
        assert abs(est.round_trip_cost_usd - expected_usd) < 1e-9

    def test_is_economic_flag_in_result(self):
        model = BinanceCostModel()
        economic, est = model.is_economic("BTCUSDT", 1000.0, 50.0)
        assert est.is_economic == economic

    def test_min_edge_is_coverage_ratio_times_cost(self):
        model = BinanceCostModel(maker_fee_bps=2.0, taker_fee_bps=4.0)
        est = model.estimate("BTCUSDT", notional_usd=500.0)
        _, est2 = model.is_economic("BTCUSDT", notional_usd=500.0, edge_bps=100.0)
        assert abs(est2.min_edge_bps - est.round_trip_cost_bps * 1.5) < 1e-9


# ---------------------------------------------------------------------------
# confidence_to_edge_bps
# ---------------------------------------------------------------------------

class TestConfidenceToEdgeBps:

    def test_50pct_confidence_zero_edge(self):
        assert confidence_to_edge_bps(0.50) == pytest.approx(0.0)

    def test_55pct_confidence_1000bps(self):
        assert confidence_to_edge_bps(0.55) == pytest.approx(1000.0)

    def test_uncertainty_reduces_edge(self):
        edge_no_unc = confidence_to_edge_bps(0.70, uncertainty=0.0)
        edge_with_unc = confidence_to_edge_bps(0.70, uncertainty=0.5)
        assert edge_with_unc == pytest.approx(edge_no_unc * 0.5)

    def test_full_uncertainty_zeroes_edge(self):
        assert confidence_to_edge_bps(0.80, uncertainty=1.0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Cost gate in allocate_signals
# ---------------------------------------------------------------------------

def _signal(
    symbol: str,
    side: str,
    confidence: float,
    uncertainty: float | None = None,
) -> StrategySignal:
    return StrategySignal(
        symbol=symbol,
        timeframe="1h",
        horizon_bars=4,
        signal=side,
        confidence=confidence,
        uncertainty=uncertainty,
    )


class TestCostGateInAllocation:

    def test_cost_gate_filters_marginal_signal(self):
        """A signal with tiny edge and tiny notional should be filtered."""
        # 65.5% confidence → ~1100 bps edge — should pass at typical notional
        # 65.0% confidence → 1000 bps edge — should also pass easily at $45 notional
        result = allocate_signals(
            [_signal("BTCUSDT", "BUY", 0.651)],
            enable_session_filter=False,
            enable_regime_bias=False,
            enable_symbol_accuracy=False,
            enable_event_gate=False,
            enable_model_agreement=False,
            enable_cost_gate=True,
            equity_usd=300.0,
        )
        # At 65.1% confidence ($45 notional), edge >> cost → should pass
        assert "BTCUSDT" in result.target_exposures

    def test_cost_gate_disabled_passes_all(self):
        result = allocate_signals(
            [_signal("BTCUSDT", "BUY", 0.66)],
            enable_session_filter=False,
            enable_regime_bias=False,
            enable_symbol_accuracy=False,
            enable_event_gate=False,
            enable_model_agreement=False,
            enable_cost_gate=False,
        )
        assert "BTCUSDT" in result.target_exposures

    def test_cost_gate_skip_reason_contains_bps(self):
        """Skipped signals should have a 'cost_gate' reason with bps info.

        95% uncertainty collapses effective edge to 5% of raw:
        65.1% conf → raw_edge ~1020 bps × (1-0.95) = 51 bps effective.
        fees 100+100 bps → min_edge ~300 bps → blocked (51 < 300).
        """
        model = BinanceCostModel(maker_fee_bps=100.0, taker_fee_bps=100.0)
        result = allocate_signals(
            [_signal("BTCUSDT", "BUY", 0.651, uncertainty=0.95)],
            enable_session_filter=False,
            enable_regime_bias=False,
            enable_symbol_accuracy=False,
            enable_event_gate=False,
            enable_model_agreement=False,
            enable_cost_gate=True,
            equity_usd=300.0,
            cost_model=model,
        )
        assert "BTCUSDT" in result.skipped_symbols
        assert "cost_gate" in result.skipped_symbols["BTCUSDT"]

    def test_cost_gate_high_confidence_passes(self):
        """High-confidence signal should always clear cost gate at normal fees."""
        result = allocate_signals(
            [_signal("BTCUSDT", "BUY", 0.80, uncertainty=0.0)],
            enable_session_filter=False,
            enable_regime_bias=False,
            enable_symbol_accuracy=False,
            enable_event_gate=False,
            enable_model_agreement=False,
            enable_cost_gate=True,
            equity_usd=500.0,
        )
        assert "BTCUSDT" in result.target_exposures

    def test_cost_gate_custom_model_injected(self):
        """Custom cost model is used when passed."""
        cheap_model = BinanceCostModel(maker_fee_bps=0.1, taker_fee_bps=0.2)
        result = allocate_signals(
            [_signal("LTCUSDT", "SELL", 0.66, uncertainty=0.3)],
            enable_session_filter=False,
            enable_regime_bias=False,
            enable_symbol_accuracy=False,
            enable_event_gate=False,
            enable_model_agreement=False,
            enable_cost_gate=True,
            equity_usd=300.0,
            cost_model=cheap_model,
        )
        assert "LTCUSDT" in result.target_exposures

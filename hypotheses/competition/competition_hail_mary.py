"""
Ultra-aggressive competition hypothesis for maximum profit potential.

Combines volatility breakout + momentum + RSI extremes.
UPDATED: Triggers on ANY valid signal (Momentum Trend or RSI Reversal) to ensure full uptime.
Uses full position sizing on every signal. No regime gating.

WARNING: This is a HIGH RISK strategy for competition use only.
"""

from typing import Any, Dict, List, Optional

import numpy as np

from clock.clock import Clock
from hypotheses.base import Hypothesis, TradeIntent, IntentType
from market.regime import MarketRegime
from state.market_state import MarketState
from state.position_state import PositionState


class CompetitionHailMary(Hypothesis):
    """
    Ultra-aggressive multi-signal hypothesis for competition mode.

    This combines three signal types:
    1. Volatility expansion (impulsive breakout candles)
    2. Momentum confirmation (EMA cross + ROC)
    3. RSI extremes (oversold/overbought reversals)

    UPDATED: Now uses "Always In" Momentum logic + RSI Reversals to ensure
    signals are generated immediately on system start.
    """

    def __init__(
        self,
        # Volatility parameters (aggressive)
        lookback: int = 14,
        atr_mult: float = 1.0,  # Lowered from 1.4
        min_body_ratio: float = 0.35,  # Lowered from 0.6
        # Momentum parameters (aggressive)
        fast_period: int = 5,
        slow_period: int = 13,
        roc_period: int = 3,
        roc_threshold: float = 0.001,  # Lowered from 0.002
        # RSI parameters (aggressive)
        rsi_period: int = 7,
        rsi_oversold: float = 20.0,  # Lowered from 25
        rsi_overbought: float = 80.0,  # Raised from 75
    ):
        self.lookback = lookback
        self.atr_mult = atr_mult
        self.min_body_ratio = min_body_ratio
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.roc_period = roc_period
        self.roc_threshold = roc_threshold
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought

    @property
    def hypothesis_id(self) -> str:
        return "competition_hail_mary"

    @property
    def allowed_regimes(self) -> List[MarketRegime]:
        # No regime gating - trade ALL conditions
        return []

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "lookback": self.lookback,
            "atr_mult": self.atr_mult,
            "min_body_ratio": self.min_body_ratio,
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "roc_period": self.roc_period,
            "roc_threshold": self.roc_threshold,
            "rsi_period": self.rsi_period,
            "rsi_oversold": self.rsi_oversold,
            "rsi_overbought": self.rsi_overbought,
        }

    def _ema(self, data: np.ndarray, period: int) -> np.ndarray:
        """Calculate EMA."""
        alpha = 2 / (period + 1)
        ema = np.zeros_like(data)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]
        return ema

    def _calculate_rsi(self, closes: np.ndarray) -> float:
        """Calculate RSI from close prices (Wilder's Smoothing)."""
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        if len(gains) < self.rsi_period:
            return 50.0

        # Wilder's Smoothing
        avg_gain = gains[:self.rsi_period].mean()
        avg_loss = losses[:self.rsi_period].mean()

        for i in range(self.rsi_period, len(gains)):
            avg_gain = (avg_gain * (self.rsi_period - 1) + gains[i]) / self.rsi_period
            avg_loss = (avg_loss * (self.rsi_period - 1) + losses[i]) / self.rsi_period

        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(
        self,
        market_state: MarketState,
        position_state: PositionState,
        clock: Clock,
    ) -> Optional[TradeIntent]:
        # Required bars: Max of lookback/slow + safety buffer
        required_bars = max(self.lookback, self.slow_period) + 10
        if market_state.bar_count() < required_bars:
            return TradeIntent(type=IntentType.HOLD)

        bars = market_state.recent_bars(required_bars)
        if bars is None or len(bars) < required_bars:
            return TradeIntent(type=IntentType.HOLD)

        closes = np.array([b.close for b in bars])
        highs = np.array([b.high for b in bars])
        lows = np.array([b.low for b in bars])
        opens = np.array([b.open for b in bars])
        
        # --- 1. Momentum (EMA Cross) ---
        fast_ema = self._ema(closes, self.fast_period)
        slow_ema = self._ema(closes, self.slow_period)
        
        # Cross logic for logging
        cross_up = fast_ema[-2] <= slow_ema[-2] and fast_ema[-1] > slow_ema[-1]
        cross_down = fast_ema[-2] >= slow_ema[-2] and fast_ema[-1] < slow_ema[-1]

        # --- 2. Volatility (ATR) ---
        tr1 = highs - lows
        tr2 = np.abs(highs - np.roll(closes, 1))
        tr3 = np.abs(lows - np.roll(closes, 1))
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        atr = tr[-self.lookback:].mean()
        
        current_range = highs[-1] - lows[-1]
        is_expansion = current_range > (atr * self.atr_mult)

        # --- 3. RSI ---
        rsi = self._calculate_rsi(closes)
        
        # --- Signal Logic (Aggressive / Hail Mary) ---
        # 1. Base Signal: Momentum Trend (Always In)
        if fast_ema[-1] > slow_ema[-1]:
            signal = IntentType.BUY
        else:
            signal = IntentType.SELL
            
        # 2. RSI Reversal Overrides (Catch tops/bottoms early)
        # If oversold, BUY regardless of momentum
        if rsi < self.rsi_oversold and closes[-1] > opens[-1]:
             signal = IntentType.BUY
             
        # If overbought, SELL regardless of momentum
        elif rsi > self.rsi_overbought and closes[-1] < opens[-1]:
             signal = IntentType.SELL
            
        # Debug Log for ALL decisions to trace signal generation
        print(f"[HYP_DEBUG] {bars[-1].symbol} SIGNAL {signal.value} | "
              f"RSI={rsi:.1f} Exp={is_expansion} CrossUp={cross_up} "
              f"CrossDn={cross_down} ATR={atr:.4f} Price={closes[-1]:.3f} "
              f"FastEMA={fast_ema[-1]:.2f} SlowEMA={slow_ema[-1]:.2f}")

        return TradeIntent(type=signal, size=1.0)

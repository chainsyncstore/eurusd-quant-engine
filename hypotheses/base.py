"""
Hypothesis definition interface.

All trading strategies must implement this interface.
"""

from abc import ABC, abstractmethod
from enum import Enum
import logging
from typing import Dict, Any, Optional, List
from market.regime import MarketRegime

from clock.clock import Clock
from pydantic import BaseModel, ConfigDict, Field
from state.market_state import MarketState
from state.position_state import PositionState


class IntentType(str, Enum):
    """Type of trade intent."""
    BUY = "BUY"
    SELL = "SELL"
    CLOSE = "CLOSE"
    HOLD = "HOLD"


class TradeIntent(BaseModel):
    """
    Represents a decision made by a hypothesis.
    
    Immutable to ensure data integrity.
    """
    model_config = ConfigDict(frozen=True)
    
    type: IntentType
    size: float = Field(default=1.0, gt=0.0, description="Position size or percentage")
    
    def is_hold(self) -> bool:
        return self.type == IntentType.HOLD


class Hypothesis(ABC):
    """
    Abstract base class for all trading hypotheses.
    
    Design Rules:
    1. Must be stateless between `on_bar` calls (use local state if needed, but prefer functional)
    2. Must output a `TradeIntent`
    3. Cannot access future data (enforced by `MarketState`)
    4. Cannot execute trades directly (must go through queue)
    """

    _signal_logger: logging.Logger | None = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        original_on_bar = getattr(cls, "on_bar", None)
        if not callable(original_on_bar):
            return
        if getattr(original_on_bar, "_diagnostic_wrapped", False):
            return

        def wrapped(self, market_state: MarketState, position_state: PositionState, clock: Clock):
            intent = original_on_bar(self, market_state, position_state, clock)
            self._log_signal_intent(intent, market_state)
            return intent

        wrapped._diagnostic_wrapped = True  # type: ignore[attr-defined]
        cls.on_bar = wrapped  # type: ignore[assignment]
    
    @property
    @abstractmethod
    def hypothesis_id(self) -> str:
        """Unique identifier for this hypothesis."""
        pass
    
    @property
    def allowed_regimes(self) -> List[MarketRegime]:
        """
        List of regimes where this hypothesis is allowed to trade.
        Default: All regimes (None or empty list means all).
        """
        return []

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """Parameters used for this hypothesis (for experiment tracking)."""
        pass

    @abstractmethod
    def on_bar(
        self,
        market_state: MarketState,
        position_state: PositionState,
        clock: Clock
    ) -> Optional[TradeIntent]:
        """
        Called on every new bar.
        
        Args:
            market_state: Read-only view of market history
            position_state: Read-only view of current position
            clock: Current simulation time
            
        Returns:
            TradeIntent or None (implied HOLD)
        """
        pass
    
    def __repr__(self):
        return f"<Hypothesis: {self.hypothesis_id}>"

    def _log_signal_intent(self, intent: Optional[TradeIntent], market_state: MarketState) -> None:
        if not getattr(self, "explain_decisions", False):
            return
        if intent is None or intent.is_hold():
            return
        try:
            bar = market_state.current_bar()
        except Exception:
            return

        direction = "FLAT"
        if intent.type == IntentType.BUY:
            direction = "LONG"
        elif intent.type == IntentType.SELL:
            direction = "SHORT"
        elif intent.type == IntentType.CLOSE:
            direction = "CLOSE"

        confidence = getattr(intent, "confidence", None)
        if confidence is None:
            confidence = intent.size

        logger = getattr(self, "_diagnostic_logger", None)
        if logger is None:
            logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
            setattr(self, "_diagnostic_logger", logger)

        name = getattr(self, "name", getattr(self, "hypothesis_id", self.__class__.__name__))
        timestamp = getattr(bar, "timestamp", None)
        ts_str = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)

        logger.info(
            "hypothesis_signal | name=%s | ts=%s | direction=%s | confidence=%.4f | size=%.4f",
            name,
            ts_str,
            direction,
            confidence,
            intent.size,
        )

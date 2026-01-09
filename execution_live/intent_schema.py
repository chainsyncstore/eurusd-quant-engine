from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

Side = Literal["BUY", "SELL"]
OrderType = Literal["MARKET"]
Mode = Literal["PAPER", "LIVE"]


@dataclass(frozen=True)
class ExecutionIntent:
    intent_id: str
    timestamp: datetime
    symbol: str
    side: Side
    order_type: OrderType
    quantity: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    time_in_force: str
    policy_hash: str
    mode: Mode

"""Event gate — dampens or vetoes signals based on detected news events."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from quant_v2.data.news_client import NewsEvent

logger = logging.getLogger(__name__)

# How recent must news be to affect trading (in hours)
_NEWS_RELEVANCE_WINDOW_HOURS = 4

# Multiplier bands
_EVENT_VETO_MULT = 0.10         # Near-complete dampening for contradicting high-severity
_EVENT_CAUTION_MULT = 0.50      # Moderate dampening for contradicting medium-severity
_EVENT_NEUTRAL_MULT = 1.0       # No effect
_EVENT_CONFIRM_BOOST = 1.0      # No boost (we don't increase on confirming news)


@dataclass(frozen=True)
class EventGateResult:
    """Result of event gate evaluation for a single symbol."""

    symbol: str
    multiplier: float               # Allocation multiplier [0.10, 1.0]
    has_event: bool
    event_title: str = ""
    event_sentiment: str = "neutral"
    event_severity: str = "low"


def evaluate_event_gate(
    symbol: str,
    signal_direction: str,
    events: list[NewsEvent],
    now: datetime | None = None,
) -> EventGateResult:
    """Evaluate whether news events should dampen a signal.

    Logic:
    - If a HIGH severity event CONTRADICTS the signal direction → 0.10× (near-veto)
    - If a MEDIUM severity event CONTRADICTS → 0.50× (caution)
    - If event CONFIRMS signal direction → 1.0× (no boost, just pass-through)
    - If no relevant events → 1.0× (neutral)

    A "bearish" event contradicts a "BUY" signal.
    A "bullish" event contradicts a "SELL" signal.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    cutoff = now - timedelta(hours=_NEWS_RELEVANCE_WINDOW_HOURS)

    # Filter to this symbol's recent events
    relevant = [
        e for e in events
        if e.symbol == symbol
        and e.published_at >= cutoff
        and e.sentiment != "neutral"
    ]

    if not relevant:
        return EventGateResult(
            symbol=symbol, multiplier=_EVENT_NEUTRAL_MULT, has_event=False,
        )

    # Take the highest-severity event
    severity_order = {"high": 3, "medium": 2, "low": 1}
    relevant.sort(key=lambda e: severity_order.get(e.severity, 0), reverse=True)
    top_event = relevant[0]

    # Check contradiction
    contradicts = (
        (signal_direction == "BUY" and top_event.sentiment == "bearish")
        or (signal_direction == "SELL" and top_event.sentiment == "bullish")
    )

    if contradicts:
        if top_event.severity == "high":
            mult = _EVENT_VETO_MULT
        elif top_event.severity == "medium":
            mult = _EVENT_CAUTION_MULT
        else:
            mult = _EVENT_NEUTRAL_MULT
        logger.info(
            "Event gate: %s %s contradicted by %s news '%s' → mult=%.2f",
            signal_direction, symbol, top_event.severity, top_event.title[:60], mult,
        )
    else:
        mult = _EVENT_CONFIRM_BOOST

    return EventGateResult(
        symbol=symbol,
        multiplier=mult,
        has_event=True,
        event_title=top_event.title,
        event_sentiment=top_event.sentiment,
        event_severity=top_event.severity,
    )

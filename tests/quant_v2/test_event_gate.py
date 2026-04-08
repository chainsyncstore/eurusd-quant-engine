"""Tests for the event gate news awareness layer (Phase 2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quant_v2.data.news_client import NewsEvent
from quant_v2.strategy.event_gate import evaluate_event_gate


def _event(
    symbol: str = "BTCUSDT",
    sentiment: str = "bearish",
    severity: str = "high",
    hours_ago: float = 1.0,
    title: str = "Test news event",
    now: datetime | None = None,
) -> NewsEvent:
    """Helper to build a NewsEvent with a published_at relative to *now*."""
    ref = now or datetime.now(timezone.utc)
    return NewsEvent(
        symbol=symbol,
        title=title,
        source="test",
        published_at=ref - timedelta(hours=hours_ago),
        sentiment=sentiment,
        severity=severity,
        url="https://example.com",
    )


class TestEvaluateEventGate:
    """Core event gate evaluation tests."""

    def test_contradicting_high_severity_buy_returns_veto(self) -> None:
        now = datetime.now(timezone.utc)
        events = [_event(sentiment="bearish", severity="high", hours_ago=1.0, now=now)]
        result = evaluate_event_gate("BTCUSDT", "BUY", events, now=now)
        assert result.multiplier == pytest.approx(0.10)
        assert result.has_event is True
        assert result.event_severity == "high"
        assert result.event_sentiment == "bearish"

    def test_contradicting_high_severity_sell_returns_veto(self) -> None:
        now = datetime.now(timezone.utc)
        events = [_event(sentiment="bullish", severity="high", hours_ago=0.5, now=now)]
        result = evaluate_event_gate("BTCUSDT", "SELL", events, now=now)
        assert result.multiplier == pytest.approx(0.10)
        assert result.has_event is True

    def test_contradicting_medium_severity_returns_caution(self) -> None:
        now = datetime.now(timezone.utc)
        events = [_event(sentiment="bearish", severity="medium", hours_ago=2.0, now=now)]
        result = evaluate_event_gate("BTCUSDT", "BUY", events, now=now)
        assert result.multiplier == pytest.approx(0.50)
        assert result.has_event is True
        assert result.event_severity == "medium"

    def test_contradicting_low_severity_returns_neutral(self) -> None:
        now = datetime.now(timezone.utc)
        events = [_event(sentiment="bearish", severity="low", hours_ago=1.0, now=now)]
        result = evaluate_event_gate("BTCUSDT", "BUY", events, now=now)
        assert result.multiplier == pytest.approx(1.0)
        assert result.has_event is True

    def test_confirming_event_returns_neutral(self) -> None:
        now = datetime.now(timezone.utc)
        # Bullish news + BUY = confirming → 1.0
        events = [_event(sentiment="bullish", severity="high", hours_ago=1.0, now=now)]
        result = evaluate_event_gate("BTCUSDT", "BUY", events, now=now)
        assert result.multiplier == pytest.approx(1.0)
        assert result.has_event is True

    def test_confirming_sell_event_returns_neutral(self) -> None:
        now = datetime.now(timezone.utc)
        # Bearish news + SELL = confirming → 1.0
        events = [_event(sentiment="bearish", severity="high", hours_ago=1.0, now=now)]
        result = evaluate_event_gate("BTCUSDT", "SELL", events, now=now)
        assert result.multiplier == pytest.approx(1.0)
        assert result.has_event is True

    def test_no_events_returns_neutral(self) -> None:
        result = evaluate_event_gate("BTCUSDT", "BUY", [])
        assert result.multiplier == pytest.approx(1.0)
        assert result.has_event is False

    def test_old_events_outside_4h_window_are_ignored(self) -> None:
        now = datetime.now(timezone.utc)
        events = [_event(sentiment="bearish", severity="high", hours_ago=5.0, now=now)]
        result = evaluate_event_gate("BTCUSDT", "BUY", events, now=now)
        assert result.multiplier == pytest.approx(1.0)
        assert result.has_event is False

    def test_neutral_sentiment_events_are_ignored(self) -> None:
        now = datetime.now(timezone.utc)
        events = [_event(sentiment="neutral", severity="high", hours_ago=1.0, now=now)]
        result = evaluate_event_gate("BTCUSDT", "BUY", events, now=now)
        assert result.multiplier == pytest.approx(1.0)
        assert result.has_event is False

    def test_wrong_symbol_events_are_ignored(self) -> None:
        now = datetime.now(timezone.utc)
        events = [_event(symbol="ETHUSDT", sentiment="bearish", severity="high", hours_ago=1.0, now=now)]
        result = evaluate_event_gate("BTCUSDT", "BUY", events, now=now)
        assert result.multiplier == pytest.approx(1.0)
        assert result.has_event is False

    def test_highest_severity_event_wins(self) -> None:
        now = datetime.now(timezone.utc)
        events = [
            _event(sentiment="bearish", severity="medium", hours_ago=1.0, now=now),
            _event(sentiment="bearish", severity="high", hours_ago=2.0, now=now),
        ]
        result = evaluate_event_gate("BTCUSDT", "BUY", events, now=now)
        # High severity should win → 0.10
        assert result.multiplier == pytest.approx(0.10)
        assert result.event_severity == "high"

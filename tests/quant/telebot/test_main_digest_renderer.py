"""Tests for _format_cycle_digest renderer in main.py.

Refs: audit_20260423 task P3-2
"""

from __future__ import annotations

from datetime import datetime, timezone

from quant.telebot.main import _format_cycle_digest


def test_format_cycle_digest_contains_symbol_and_proba() -> None:
    """Rendered text contains each top-3 symbol and its probability."""
    payload = {
        "signal": "CYCLE_DIGEST",
        "timestamp": datetime(2026, 4, 24, 7, 0, 0, tzinfo=timezone.utc).isoformat(),
        "top_by_closest_threshold": [
            {"symbol": "XRPUSDT", "probability": 0.549, "buy_th": 0.59, "sell_th": 0.41, "gap_to_buy": 0.041, "gap_to_sell": 0.139},
            {"symbol": "ADAUSDT", "probability": 0.545, "buy_th": 0.59, "sell_th": 0.41, "gap_to_buy": 0.045, "gap_to_sell": 0.135},
            {"symbol": "SOLUSDT", "probability": 0.446, "buy_th": 0.59, "sell_th": 0.41, "gap_to_buy": 0.144, "gap_to_sell": 0.036},
        ],
        "total_decisions": 10,
        "cycle_interval_seconds": 900,
    }

    text = _format_cycle_digest(payload)

    assert "XRPUSDT" in text
    assert "ADAUSDT" in text
    assert "SOLUSDT" in text
    assert "0.549" in text or "0.549" in text.replace("0.549", "0.549")  # Check probability appears
    assert "0.446" in text or "0.446" in text.replace("0.446", "0.446")


def test_format_cycle_digest_under_500_chars() -> None:
    """Digest message stays under 500 characters."""
    payload = {
        "signal": "CYCLE_DIGEST",
        "timestamp": datetime(2026, 4, 24, 7, 0, 0, tzinfo=timezone.utc).isoformat(),
        "top_by_closest_threshold": [
            {"symbol": "XRPUSDT", "probability": 0.549, "buy_th": 0.59, "sell_th": 0.41, "gap_to_buy": 0.041, "gap_to_sell": 0.139},
            {"symbol": "ADAUSDT", "probability": 0.545, "buy_th": 0.59, "sell_th": 0.41, "gap_to_buy": 0.045, "gap_to_sell": 0.135},
            {"symbol": "SOLUSDT", "probability": 0.446, "buy_th": 0.59, "sell_th": 0.41, "gap_to_buy": 0.144, "gap_to_sell": 0.036},
        ],
        "total_decisions": 10,
        "cycle_interval_seconds": 900,
    }

    text = _format_cycle_digest(payload)

    assert len(text) < 500, f"Digest too long: {len(text)} chars"


def test_format_cycle_digest_shows_cycle_time() -> None:
    """Digest includes formatted timestamp."""
    payload = {
        "signal": "CYCLE_DIGEST",
        "timestamp": datetime(2026, 4, 24, 7, 0, 0, tzinfo=timezone.utc).isoformat(),
        "top_by_closest_threshold": [],
        "total_decisions": 5,
        "cycle_interval_seconds": 900,
    }

    text = _format_cycle_digest(payload)

    assert "07:00 UTC" in text


def test_format_cycle_digest_shows_next_cycle_interval() -> None:
    """Digest includes next cycle interval in minutes."""
    payload = {
        "signal": "CYCLE_DIGEST",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "top_by_closest_threshold": [],
        "total_decisions": 5,
        "cycle_interval_seconds": 900,  # 15 minutes
    }

    text = _format_cycle_digest(payload)

    assert "Next cycle in 15m" in text


def test_format_cycle_digest_handles_empty_top_list() -> None:
    """Digest renders correctly when no top symbols provided."""
    payload = {
        "signal": "CYCLE_DIGEST",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "top_by_closest_threshold": [],
        "total_decisions": 0,
        "cycle_interval_seconds": 3600,
    }

    text = _format_cycle_digest(payload)

    assert "No actionable signals this cycle" in text
    assert "🔵 Cycle digest" in text
    assert "Next cycle in 60m" in text


def test_format_cycle_digest_shows_correct_closest_direction() -> None:
    """Digest shows correct closest direction (BUY or SELL) based on gap."""
    # Symbol closer to BUY threshold
    payload_buy = {
        "signal": "CYCLE_DIGEST",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "top_by_closest_threshold": [
            {"symbol": "BTCUSDT", "probability": 0.58, "buy_th": 0.59, "sell_th": 0.41, "gap_to_buy": 0.01, "gap_to_sell": 0.17},
        ],
        "total_decisions": 1,
        "cycle_interval_seconds": 3600,
    }

    text_buy = _format_cycle_digest(payload_buy)
    assert "to BUY" in text_buy

    # Symbol closer to SELL threshold
    payload_sell = {
        "signal": "CYCLE_DIGEST",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "top_by_closest_threshold": [
            {"symbol": "BTCUSDT", "probability": 0.42, "buy_th": 0.59, "sell_th": 0.41, "gap_to_buy": 0.17, "gap_to_sell": 0.01},
        ],
        "total_decisions": 1,
        "cycle_interval_seconds": 3600,
    }

    text_sell = _format_cycle_digest(payload_sell)
    assert "to SELL" in text_sell

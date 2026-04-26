"""Tests for V2SignalManager quiet-hour heartbeat digest.

Refs: audit_20260423 task P3-2
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest

from quant_v2.telebot.signal_manager import V2SignalManager


class _FakeClient:
    def __init__(self, bars: pd.DataFrame) -> None:
        self._bars = bars

    def fetch_historical(self, date_from, date_to, *, symbol: str, interval: str) -> pd.DataFrame:
        _ = (date_from, date_to, symbol, interval)
        return self._bars

    def get_orderbook(self, symbol: str, limit: int = 5) -> dict:
        return {"bids": [[50000.0, 1.0]], "asks": [[50001.0, 1.0]]}


def _sample_bars(*, n: int = 120) -> pd.DataFrame:
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    index = pd.date_range(end=end, periods=n, freq="h", tz="UTC")
    closes = [float(10_000 + i * 20) for i in range(len(index))]
    df = pd.DataFrame({"close": closes}, index=index)
    df["open"] = df["close"] * 0.99
    df["high"] = df["close"] * 1.01
    df["low"] = df["close"] * 0.98
    df["volume"] = 1000.0
    return df


def _make_manager(tmp_path: Path) -> V2SignalManager:
    bars = _sample_bars()
    return V2SignalManager(
        model_dir=tmp_path,
        symbols=("BTCUSDT", "ETHUSDT"),
        loop_interval_seconds=900,
        client_factory=lambda creds, live, symbol, interval: _FakeClient(bars),
    )


@pytest.fixture
def sample_bars() -> pd.DataFrame:
    return _sample_bars()


@pytest.fixture(autouse=True)
def clear_env():
    """Clear the heartbeat env var before each test."""
    old_val = os.environ.pop("BOT_V2_QUIET_HEARTBEAT", None)
    yield
    if old_val is not None:
        os.environ["BOT_V2_QUIET_HEARTBEAT"] = old_val
    else:
        os.environ.pop("BOT_V2_QUIET_HEARTBEAT", None)


async def _noop_loop(*args, **kwargs):
    """No-op coroutine that simulates the loop ending immediately."""
    pass


async def _run_one_cycle(manager: V2SignalManager, on_signal: Any = None) -> None:
    """Run a single _run_cycle via a temporary session without background loop."""
    # Patch _loop to a no-op so no background task runs
    with patch.object(manager, "_loop", _noop_loop):
        await manager.start_session(
            user_id=999,
            creds={"live": False},
            on_signal=on_signal or (lambda p: None),
            execute_orders=False,
        )
    session = manager.sessions[999]
    try:
        await manager._run_cycle(session)
    finally:
        # Cancel the no-op task and clean up
        if session.task:
            session.task.cancel()
            try:
                await session.task
            except asyncio.CancelledError:
                pass
        manager.sessions.pop(999, None)


def test_digest_not_emitted_when_disabled(tmp_path: Path) -> None:
    """Digest is NOT emitted when BOT_V2_QUIET_HEARTBEAT is unset."""
    received: list[dict] = []

    def capture(payload: dict) -> None:
        received.append(payload)

    manager = _make_manager(tmp_path)

    with patch.object(manager.registry, "get_active_version", return_value=None):
        with patch.object(manager, "_build_featured_frame", return_value=None):
            asyncio.run(_run_one_cycle(manager, on_signal=capture))

    # Should only have HOLD signals, no CYCLE_DIGEST
    digests = [p for p in received if p.get("signal") == "CYCLE_DIGEST"]
    assert len(digests) == 0


def test_digest_emitted_when_all_hold_and_enabled(tmp_path: Path) -> None:
    """Digest IS emitted when env=1 and all signals are HOLD."""
    os.environ["BOT_V2_QUIET_HEARTBEAT"] = "1"
    received: list[dict] = []

    def capture(payload: dict) -> None:
        received.append(payload)

    manager = _make_manager(tmp_path)

    with patch.object(manager.registry, "get_active_version", return_value=None):
        with patch.object(manager, "_build_featured_frame", return_value=None):
            asyncio.run(_run_one_cycle(manager, on_signal=capture))

    # Should have one CYCLE_DIGEST at the end
    digests = [p for p in received if p.get("signal") == "CYCLE_DIGEST"]
    assert len(digests) == 1

    digest = digests[0]
    assert "timestamp" in digest
    assert "top_by_closest_threshold" in digest
    assert digest["total_decisions"] == 2  # BTCUSDT and ETHUSDT
    assert digest["cycle_interval_seconds"] == 900


def test_digest_suppressed_when_any_actionable(tmp_path: Path) -> None:
    """Digest is suppressed when any BUY/SELL signal is generated."""
    os.environ["BOT_V2_QUIET_HEARTBEAT"] = "1"
    received: list[dict] = []

    def capture(payload: dict) -> None:
        received.append(payload)

    manager = _make_manager(tmp_path)

    # Mock _build_signal_payload to return a BUY for one symbol
    def mock_build_payload(
        symbol: str,
        bars: pd.DataFrame,
        *,
        btc_returns=None,
        data_quality_flag=False,
        ob_snapshot=None,
    ) -> dict:
        # Return BUY for BTCUSDT, HOLD for others
        signal = "BUY" if symbol == "BTCUSDT" else "HOLD"
        proba = 0.75 if symbol == "BTCUSDT" else 0.5
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "close_price": 50000.0,
            "signal": signal,
            "probability": proba,
            "regime": 1,
            "regime_probability": 0.8,
            "regime_tradeable": signal in ("BUY", "SELL"),
            "threshold": 0.55,
            "reason": "test",
            "horizon": 4,
            "position": {},
            "risk_status": {"can_trade": True},
            "drift_alert": False,
            "execution_anomaly_rate": 0.0,
            "connectivity_error_rate": 0.0,
            "_buy_th": 0.59,
            "_sell_th": 0.41,
        }

    with patch.object(manager.registry, "get_active_version", return_value=None):
        with patch.object(manager, "_build_signal_payload", side_effect=mock_build_payload):
            asyncio.run(_run_one_cycle(manager, on_signal=capture))

    # Should have signals but NO CYCLE_DIGEST (because one was a BUY)
    digests = [p for p in received if p.get("signal") == "CYCLE_DIGEST"]
    assert len(digests) == 0

    # Verify we did get the BUY signal
    buys = [p for p in received if p.get("signal") == "BUY"]
    assert len(buys) == 1


def test_digest_contains_top_3_closest_to_threshold(tmp_path: Path) -> None:
    """Digest contains top 3 symbols sorted by closest to threshold."""
    os.environ["BOT_V2_QUIET_HEARTBEAT"] = "1"
    received: list[dict] = []

    def capture(payload: dict) -> None:
        received.append(payload)

    manager = _make_manager(tmp_path)

    # Create a mock payload with specific probabilities to test sorting
    symbols = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "ADAUSDT", "SOLUSDT"]
    # Define specific probabilities with varying distances to thresholds
    proba_map = {
        "BTCUSDT": 0.549,  # gap 0.041 to BUY (closest)
        "ETHUSDT": 0.545,  # gap 0.045 to BUY
        "XRPUSDT": 0.446,  # gap 0.036 to SELL (actually closest overall)
        "ADAUSDT": 0.52,   # gap 0.07 to BUY
        "SOLUSDT": 0.48,   # gap 0.07 to SELL
    }

    def mock_build_payload(
        symbol: str,
        bars: pd.DataFrame,
        *,
        btc_returns=None,
        data_quality_flag=False,
        ob_snapshot=None,
    ) -> dict:
        proba = proba_map.get(symbol, 0.5)
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "close_price": 50000.0,
            "signal": "HOLD",
            "probability": proba,
            "regime": 1,
            "regime_probability": 0.8,
            "regime_tradeable": False,
            "threshold": 0.59,
            "reason": "test",
            "horizon": 4,
            "position": {},
            "risk_status": {"can_trade": True},
            "drift_alert": False,
            "execution_anomaly_rate": 0.0,
            "connectivity_error_rate": 0.0,
            "_buy_th": 0.59,
            "_sell_th": 0.41,
        }

    # Override symbols for this test
    manager.symbols = tuple(symbols)

    with patch.object(manager.registry, "get_active_version", return_value=None):
        with patch.object(manager, "_build_signal_payload", side_effect=mock_build_payload):
            asyncio.run(_run_one_cycle(manager, on_signal=capture))

    digests = [p for p in received if p.get("signal") == "CYCLE_DIGEST"]
    assert len(digests) == 1

    digest = digests[0]
    top = digest["top_by_closest_threshold"]
    assert len(top) == 3

    # XRP should be first (gap 0.036 to SELL), then BTC (gap 0.041 to BUY), then ETH (gap 0.045)
    assert top[0]["symbol"] == "XRPUSDT"
    assert top[1]["symbol"] == "BTCUSDT"
    assert top[2]["symbol"] == "ETHUSDT"


def test_digest_emit_exception_does_not_crash_cycle(tmp_path: Path, caplog: Any) -> None:
    """Digest emit failure is logged but does not crash the cycle."""
    os.environ["BOT_V2_QUIET_HEARTBEAT"] = "1"

    def failing_on_signal(payload: dict) -> None:
        if payload.get("signal") == "CYCLE_DIGEST":
            raise RuntimeError("digest emit failed")

    manager = _make_manager(tmp_path)

    with patch.object(manager.registry, "get_active_version", return_value=None):
        with patch.object(manager, "_build_featured_frame", return_value=None):
            with caplog.at_level("WARNING", logger="quant_v2.telebot.signal_manager"):
                # Should not raise
                asyncio.run(_run_one_cycle(manager, on_signal=failing_on_signal))

    assert "cycle digest emit failed for user 999" in caplog.text

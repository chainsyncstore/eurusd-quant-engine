"""Tests for the stranded-position flatten safety net (audit_20260423 P0-4).

Also exercises ``sync_paper_position_state`` which fixes the writer-side gap
left by the original P0-2 implementation: without it,
``_SignalSession.paper_entry_timestamps`` and ``last_known_positions`` would
remain empty in production and both safety nets would be dead code.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from quant_v2.telebot.signal_manager import V2SignalManager, _SignalSession


@pytest.fixture
def manager(tmp_path: Path) -> V2SignalManager:
    """V2SignalManager configured for fast deterministic stranded-flatten tests.

    - 2-cycle threshold so tests don't need to loop dozens of times.
    - $50 floor at $10k equity (matches optimizer's 0.5% default).
    """

    return V2SignalManager(
        model_dir=tmp_path,
        symbols=("BTCUSDT",),
        loop_interval_seconds=1,
        max_hold_hours=12,
        min_notional_usd=10.0,
        min_notional_equity_pct=0.005,
        stranded_flatten_cycles=2,
    )


@pytest.fixture
def held_session() -> _SignalSession:
    """Session with a single $30 LTC position at $10k equity (sub-floor)."""

    session = _SignalSession(
        user_id=12345,
        live=False,
        client=MagicMock(),
        on_signal=MagicMock(),
    )
    # 0.4 LTC × $75 close = $30 notional, well below the $50 floor.
    session.paper_entry_timestamps["LTCUSDT"] = datetime.now(timezone.utc) - timedelta(hours=2)
    session.last_known_positions = {"LTCUSDT": 0.4}
    session.last_known_equity_usd = 10_000.0
    return session


def _hold_payload(symbol: str = "LTCUSDT", *, signal: str = "HOLD", price: float = 75.0) -> dict:
    return {
        "symbol": symbol,
        "signal": signal,
        "reason": "model_hold" if signal == "HOLD" else f"model_{signal.lower()}",
        "close_price": price,
    }


class TestStrandedFlatten:
    """Behavioural tests for ``_apply_stranded_position_flatten``."""

    def test_fires_after_threshold_cycles(
        self, manager: V2SignalManager, held_session: _SignalSession
    ) -> None:
        """Threshold = 2: cycle 1 increments, cycle 2 fires the flatten."""

        first = _hold_payload()
        manager._apply_stranded_position_flatten(held_session, first)
        assert first["signal"] == "HOLD"
        assert "stranded_flatten" not in first
        assert held_session.stranded_cycle_counter["LTCUSDT"] == 1

        second = _hold_payload()
        manager._apply_stranded_position_flatten(held_session, second)
        assert second["signal"] == "SELL"
        assert second["stranded_flatten"] is True
        assert "stranded_flatten=$" in second["reason"]
        # Counter is cleared after firing so a re-emit doesn't re-fire.
        assert "LTCUSDT" not in held_session.stranded_cycle_counter

    def test_fires_for_buy_signal_too(
        self, manager: V2SignalManager, held_session: _SignalSession
    ) -> None:
        """The 'stranded BUY' pattern must be covered, not just HOLD."""

        for _ in range(2):
            payload = _hold_payload(signal="BUY")
            manager._apply_stranded_position_flatten(held_session, payload)
        assert payload["signal"] == "SELL"
        assert payload["stranded_flatten"] is True

    def test_resets_when_position_recovers(
        self, manager: V2SignalManager, held_session: _SignalSession
    ) -> None:
        """A cycle where notional climbs back above the floor clears the counter."""

        # Cycle 1: sub-floor → counter = 1.
        manager._apply_stranded_position_flatten(held_session, _hold_payload())
        assert held_session.stranded_cycle_counter["LTCUSDT"] == 1

        # Cycle 2: price rallies → notional = 0.4 × $200 = $80 > $50 floor.
        recovery = _hold_payload(price=200.0)
        manager._apply_stranded_position_flatten(held_session, recovery)
        assert recovery["signal"] == "HOLD"
        assert "stranded_flatten" not in recovery
        assert "LTCUSDT" not in held_session.stranded_cycle_counter

    def test_skips_live_session(
        self, manager: V2SignalManager, held_session: _SignalSession
    ) -> None:
        """Live mode must never trigger paper-only safety nets."""

        held_session.live = True
        for _ in range(5):
            payload = _hold_payload()
            manager._apply_stranded_position_flatten(held_session, payload)
        assert payload["signal"] == "HOLD"
        assert "stranded_flatten" not in payload
        assert held_session.stranded_cycle_counter == {}

    def test_skips_unheld_symbol(
        self, manager: V2SignalManager, held_session: _SignalSession
    ) -> None:
        """No-op when the symbol isn't in ``last_known_positions``."""

        payload = _hold_payload(symbol="DOGEUSDT")
        manager._apply_stranded_position_flatten(held_session, payload)
        assert payload["signal"] == "HOLD"
        assert "stranded_flatten" not in payload
        assert "DOGEUSDT" not in held_session.stranded_cycle_counter

    def test_skips_existing_sell_signal(
        self, manager: V2SignalManager, held_session: _SignalSession
    ) -> None:
        """A SELL signal already closes the position; counter resets."""

        # Pre-populate counter to simulate prior stranded cycles.
        held_session.stranded_cycle_counter["LTCUSDT"] = 1
        payload = _hold_payload(signal="SELL")
        manager._apply_stranded_position_flatten(held_session, payload)
        assert payload["signal"] == "SELL"
        assert "stranded_flatten" not in payload
        assert "LTCUSDT" not in held_session.stranded_cycle_counter

    def test_skips_when_close_price_unavailable(
        self, manager: V2SignalManager, held_session: _SignalSession
    ) -> None:
        """Missing/zero close price must skip without resetting progress."""

        held_session.stranded_cycle_counter["LTCUSDT"] = 1
        payload = _hold_payload(price=0.0)
        manager._apply_stranded_position_flatten(held_session, payload)
        # Counter preserved for the next cycle that has a valid price.
        assert held_session.stranded_cycle_counter["LTCUSDT"] == 1
        assert payload["signal"] == "HOLD"

    def test_disabled_when_threshold_zero(
        self, tmp_path: Path, held_session: _SignalSession
    ) -> None:
        """Setting ``stranded_flatten_cycles=0`` disables the safety net."""

        disabled = V2SignalManager(
            model_dir=tmp_path,
            symbols=("BTCUSDT",),
            loop_interval_seconds=1,
            stranded_flatten_cycles=0,
        )
        for _ in range(5):
            payload = _hold_payload()
            disabled._apply_stranded_position_flatten(held_session, payload)
        assert payload["signal"] == "HOLD"
        assert "stranded_flatten" not in payload


class TestStrandedFlattenConfig:
    """Resolver/env-var coverage for the new config knobs."""

    def test_env_var_overrides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BOT_V2_MIN_NOTIONAL_USD", "25")
        monkeypatch.setenv("BOT_V2_MIN_NOTIONAL_EQUITY_PCT", "0.01")
        monkeypatch.setenv("BOT_V2_STRANDED_FLATTEN_CYCLES", "8")

        manager = V2SignalManager(
            model_dir=tmp_path,
            symbols=("BTCUSDT",),
            loop_interval_seconds=1,
        )
        assert manager.min_notional_usd == 25.0
        assert manager.min_notional_equity_pct == 0.01
        assert manager.stranded_flatten_cycles == 8

    def test_invalid_env_var_falls_back_to_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BOT_V2_STRANDED_FLATTEN_CYCLES", "not-an-int")
        manager = V2SignalManager(
            model_dir=tmp_path,
            symbols=("BTCUSDT",),
            loop_interval_seconds=1,
        )
        assert manager.stranded_flatten_cycles == 4


class TestSyncPaperPositionState:
    """Coverage for the writer-side hook that feeds both safety nets.

    Refs: audit_20260423 P0-2 writer-side gap + P0-4.
    """

    def test_populates_timestamps_and_positions(
        self, manager: V2SignalManager
    ) -> None:
        manager.sessions[42] = _SignalSession(
            user_id=42, live=False, client=MagicMock(), on_signal=MagicMock(),
        )
        ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        manager.sync_paper_position_state(
            42,
            {
                "open_positions": {"BTCUSDT": 0.05},
                "paper_entry_timestamps": {"BTCUSDT": ts},
                "equity_usd": 10_500.0,
                "equity_baseline_usd": 10_000.0,
            },
        )
        session = manager.sessions[42]
        assert session.last_known_positions == {"BTCUSDT": 0.05}
        assert session.last_known_equity_usd == 10_500.0
        assert "BTCUSDT" in session.paper_entry_timestamps
        # Parsed back to a tz-aware datetime.
        assert session.paper_entry_timestamps["BTCUSDT"].tzinfo is not None

    def test_drops_closed_symbols(self, manager: V2SignalManager) -> None:
        session = _SignalSession(
            user_id=42, live=False, client=MagicMock(), on_signal=MagicMock(),
        )
        session.paper_entry_timestamps["LTCUSDT"] = datetime.now(timezone.utc)
        session.stranded_cycle_counter["LTCUSDT"] = 1
        manager.sessions[42] = session

        manager.sync_paper_position_state(
            42,
            {
                "open_positions": {"BTCUSDT": 0.05},  # LTCUSDT closed
                "paper_entry_timestamps": {"BTCUSDT": datetime.now(timezone.utc).isoformat()},
                "equity_usd": 10_000.0,
            },
        )
        assert "LTCUSDT" not in session.paper_entry_timestamps
        assert "LTCUSDT" not in session.stranded_cycle_counter
        assert session.last_known_positions == {"BTCUSDT": 0.05}

    def test_falls_back_to_baseline_equity(self, manager: V2SignalManager) -> None:
        """When the service didn't include equity_usd, fall back to baseline."""

        manager.sessions[42] = _SignalSession(
            user_id=42, live=False, client=MagicMock(), on_signal=MagicMock(),
        )
        manager.sync_paper_position_state(
            42,
            {
                "open_positions": {"BTCUSDT": 0.05},
                "paper_entry_timestamps": {},
                "equity_baseline_usd": 9_500.0,
            },
        )
        assert manager.sessions[42].last_known_equity_usd == 9_500.0

    def test_handles_missing_session_gracefully(self, manager: V2SignalManager) -> None:
        """No exception when sync targets an unknown user."""

        manager.sync_paper_position_state(
            999,
            {"open_positions": {"BTCUSDT": 0.05}, "equity_usd": 10_000.0},
        )

    def test_handles_none_paper_state(self, manager: V2SignalManager) -> None:
        """No exception when paper_state is None (service has no session yet)."""

        manager.sessions[42] = _SignalSession(
            user_id=42, live=False, client=MagicMock(), on_signal=MagicMock(),
        )
        manager.sync_paper_position_state(42, None)
        assert manager.sessions[42].last_known_positions == {}


class TestEmitOrdering:
    """Verify ``_emit`` runs both safety nets and that time-stop wins ties."""

    @pytest.mark.asyncio
    async def test_time_stop_runs_before_stranded_flatten(
        self, manager: V2SignalManager
    ) -> None:
        """When a position is BOTH aged AND sub-floor, time-stop should fire
        first (HOLD→SELL) and stranded-flatten then sees a SELL and skips."""

        session = _SignalSession(
            user_id=42, live=False, client=MagicMock(), on_signal=MagicMock(),
        )
        session.paper_entry_timestamps["LTCUSDT"] = datetime.now(timezone.utc) - timedelta(hours=20)
        session.last_known_positions = {"LTCUSDT": 0.1}
        session.last_known_equity_usd = 10_000.0

        payload = _hold_payload()
        await manager._emit(session, payload)
        assert payload["signal"] == "SELL"
        assert payload.get("time_stop") is True
        # Stranded-flatten observed SELL and bailed out without tagging.
        assert "stranded_flatten" not in payload

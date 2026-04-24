# TASK P0-2 — Add max-hold time-stop for paper positions

> **BEFORE YOU START**: read `scratch/audit_20260423/agent_prompts/00_common_preamble.md` and follow every rule there.

## Context (why this matters)

The audit found a paper LONG BNBUSDT 0.527 @ 640.12 that has been open for >13 hours on user `6268794073` with no exit trigger, because (a) the model never emitted a SELL for BNBUSDT today (20 HOLD / 2 BUY / 0 SELL), and (b) the portfolio optimizer keeps re-endorsing the single-symbol allocation. There is **no time-stop** anywhere in the codebase; a silent HOLD traps the position indefinitely.

Your task is to add an unconditional time-stop that synthesises a flatten/SELL action when a paper position has been held longer than `BOT_V2_MAX_HOLD_HOURS` (default **12**) while the current cycle is HOLD-only.

## Scope

- **Modify**:
  - `quant_v2/telebot/signal_manager.py` (add time-stop logic in the emit path).
  - `quant/telebot/main.py` only if needed to parse a new env var alongside existing `BOT_V2_SIGNAL_LOOP_SECONDS` (mirror that pattern exactly).
- **Add**:
  - Tests under `tests/quant_v2/telebot/test_signal_manager_time_stop.py` (new file, one focused test module).
- **Do not modify** the optimizer, the scorecard, the execution layer, or any storage schema.

## Preconditions

1. Read `quant_v2/telebot/signal_manager.py` end-to-end. Identify:
   - Where per-user sessions store runtime state (the `_SignalSession` dataclass around lines 37-48).
   - Where the loop emits a signal to the on_signal callback (search for `on_signal` invocations inside `_loop`).
   - Where paper entry prices are recorded — search the repo for `paper_entry_prices` to find the write site. The persisted shape is `{symbol: price}` in `session_state_json`; entry **timestamps are not currently stored**.
2. Read `quant/telebot/main.py` lines 83-96 to see the existing env-var pattern for `BOT_V2_SIGNAL_LOOP_SECONDS`.

## Exact Changes

### Step 1 — Env var

Add a new env var `BOT_V2_MAX_HOLD_HOURS` (default `12`). Parse it with the same `try/except ValueError → default` pattern used for `BOT_V2_SIGNAL_LOOP_SECONDS` at `quant/telebot/main.py:89-95`. Store it as a module-level constant `V2_MAX_HOLD_HOURS`. Pass it through to `V2SignalManager.__init__` as a new kwarg `max_hold_hours: int | None = None`, with env fallback inside `V2SignalManager` mirroring `_resolve_loop_interval` at `quant_v2/telebot/signal_manager.py:113-123`.

### Step 2 — Entry timestamp tracking

Extend `_SignalSession` (the dataclass near line 37) with:

```python
    paper_entry_timestamps: dict[str, datetime] = field(default_factory=dict)
```

Ensure `datetime` and `timezone` are imported at the top of the file (they already are per the existing imports at line 10).

Wherever `paper_entry_prices[symbol] = ...` is written (find it via grep of the repo), also write `paper_entry_timestamps[symbol] = datetime.now(timezone.utc)`. Wherever `paper_entry_prices.pop(symbol, None)` or equivalent is written (on position close), also pop the timestamp.

If entry-price bookkeeping lives outside `signal_manager.py` (it may live in the execution service or session_state serialiser), implement the timestamp write in the same place, alongside the price write. Do not duplicate — one source of truth.

### Step 3 — Time-stop emission

In the signal-emit path (the place where a prepared signal payload is passed to `on_signal`), immediately before emission, insert:

```python
self._apply_time_stop(session, payload)
```

Then add the helper method to `V2SignalManager`:

```python
def _apply_time_stop(self, session: _SignalSession, payload: dict[str, Any]) -> None:
    """Upgrade HOLD to SELL when an open paper position exceeds max_hold_hours.

    This is a SAFETY net: the model/optimizer must not silently trap a
    position forever. Fires only on HOLD signals for symbols with an
    existing long position whose entry was more than max_hold_hours ago.
    """
    signal_type = str(payload.get("signal", "HOLD")).upper()
    if signal_type != "HOLD":
        return
    symbol = str(payload.get("symbol", "")).upper()
    if not symbol:
        return
    entry_ts = session.paper_entry_timestamps.get(symbol)
    if entry_ts is None:
        return
    age_hours = (datetime.now(timezone.utc) - entry_ts).total_seconds() / 3600.0
    if age_hours < self.max_hold_hours:
        return
    logger.warning(
        "Time-stop triggered for user %s %s: held %.1fh >= %.1fh. Upgrading HOLD → SELL.",
        session.user_id, symbol, age_hours, self.max_hold_hours,
    )
    payload["signal"] = "SELL"
    payload["reason"] = (
        (payload.get("reason") or "") + f" [time_stop={age_hours:.1f}h]"
    ).strip()
    payload["time_stop"] = True
```

Use `logger.warning` (not info) so the event is visible in production log scans. Do NOT gate this behind the `_attach_native_v2_fields` path — it must run on every payload before emission to act as a safety net.

### Step 4 — Persist + restore entry timestamps

The entry-price map is serialised to `session_state_json` (see `state/quant_bot.db.user_context` column). Find the serialisation site (grep `paper_entry_prices` across the repo) and include `paper_entry_timestamps` alongside, using ISO-8601 UTC strings:

- Serialise: `{sym: ts.isoformat() for sym, ts in session.paper_entry_timestamps.items()}`.
- Deserialise on session restore: parse with `datetime.fromisoformat`; if parsing fails or key missing, fall back to `datetime.now(timezone.utc)` (so an existing stuck position starts the clock from "now" and will eventually time out).

## Tests to Add

Create `tests/quant_v2/telebot/test_signal_manager_time_stop.py` (new file). Use `pytest`, `unittest.mock`, and stdlib only. Add exactly these tests:

1. `test_time_stop_upgrades_hold_to_sell_when_aged` — construct a `V2SignalManager` with `max_hold_hours=12`, a `_SignalSession` with `paper_entry_timestamps={"BNBUSDT": now - 13h}`, call `_apply_time_stop` with a HOLD payload for BNBUSDT. Assert `payload["signal"] == "SELL"`, `payload["time_stop"] is True`, and that `"time_stop=13." in payload["reason"]`.

2. `test_time_stop_noop_for_fresh_position` — same setup but entry is 1h old. Assert `payload["signal"] == "HOLD"` and `"time_stop" not in payload`.

3. `test_time_stop_noop_for_symbol_without_entry` — entry_timestamps does NOT contain the signal's symbol. Assert HOLD preserved unchanged.

4. `test_time_stop_noop_for_buy_and_sell_signals` — even with an aged position, BUY and SELL payloads are untouched (the model already wants to trade; don't override).

5. `test_time_stop_env_var_override` — monkeypatch `BOT_V2_MAX_HOLD_HOURS=4`, instantiate manager without explicit arg, verify `manager.max_hold_hours == 4`.

All tests should be under 50 lines each. Use pytest fixtures for the common session setup.

## Tests to Update

If existing tests in `tests/quant_v2/telebot/` rely on the exact shape of `_SignalSession`, they may need a trivial update to accept the new `paper_entry_timestamps` field (which has a default_factory so usually no change). If any existing test breaks materially, STOP and escalate.

## Definition of Done

- [ ] New env var `BOT_V2_MAX_HOLD_HOURS` wired from `quant/telebot/main.py` → `V2SignalManager.__init__`.
- [ ] `_SignalSession.paper_entry_timestamps` field added and written alongside `paper_entry_prices` at every existing write site.
- [ ] `_apply_time_stop` helper added and called before every `on_signal` emission.
- [ ] Session persistence round-trips entry timestamps via `session_state_json`.
- [ ] All 5 new tests pass.
- [ ] Full `pytest` suite still passes.
- [ ] PR title: `fix(signal_manager): add time-stop safety for stuck paper positions`.
- [ ] PR body must explicitly list every file touched and confirm none are out of scope.

## Common Pitfalls — Do NOT do any of these

- ❌ Applying time-stop to LIVE sessions before paper is proven. This task is **paper only**. Check `session.live` and skip the time-stop if `True`. (Live futures will be addressed in a later task.)
- ❌ Adding time-stop at the optimizer level — the optimizer sees exposures, not session state. Keep it in the signal manager where session context is available.
- ❌ Making `max_hold_hours` a per-user setting. It is a system-wide safety; one env var, one constant.
- ❌ Storing entry timestamps only in memory. They MUST survive a container restart via `session_state_json`, otherwise restarts defeat the safety.
- ❌ Using naïve datetimes. Always `datetime.now(timezone.utc)` and always `.isoformat()` for serialisation.
- ❌ Removing or altering the time-stop log line. Operators grep for `"Time-stop triggered"` in dashboards.

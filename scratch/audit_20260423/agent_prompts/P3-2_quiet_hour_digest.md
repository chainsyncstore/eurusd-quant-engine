# TASK P3-2 — Quiet-hour Telegram heartbeat for all-HOLD cycles

> **BEFORE YOU START**: read `scratch/audit_20260423/agent_prompts/00_common_preamble.md` and follow every rule there.

## Context (why this matters)

At 07:00 UTC today, every symbol returned HOLD. The user saw no Telegram message and assumed the bot was broken — it wasn't, it just had no actionable signal. HOLDs are suppressed by design from the notifier. Without a heartbeat, users cannot distinguish "quiet market" from "outage".

## Scope

- **Modify**: `quant_v2/telebot/signal_manager.py` — at the end of each cycle, after all per-symbol decisions, optionally emit a digest callback to each session.
- **Modify**: `quant/telebot/main.py` (or wherever the Telegram send-message handler for signals lives) — handle the new digest payload and render it as a lightweight message.
- **Add**: tests under `tests/quant_v2/telebot/test_signal_manager_digest.py`.
- **Do not modify** any existing per-signal Telegram formatting, the optimizer, or the scorecard.

## Design

### Gate

- New env var: `BOT_V2_QUIET_HEARTBEAT` (values `1/true/yes/on` = enabled, anything else = disabled). **Default: disabled (`0`)**. We are opt-in so existing user chats do not get noisier overnight.
- Heartbeat fires at most **once per cycle**, and only when:
  - `BOT_V2_QUIET_HEARTBEAT` is enabled, AND
  - the cycle produced **zero** BUY and **zero** SELL payloads for that user (HOLD-only).

### Payload

The digest is a dict emitted via the same `on_signal` callback but with a sentinel `signal == "CYCLE_DIGEST"` (a new value). The main.py handler inspects for this value and formats a short human-readable message:

```
🔵 Cycle digest — 07:00 UTC
  No actionable signals this cycle.
  Closest to threshold:
    • XRPUSDT  HOLD  proba=0.549  (Δ=0.041 to BUY)
    • ADAUSDT  HOLD  proba=0.545  (Δ=0.045 to BUY)
    • SOLUSDT  HOLD  proba=0.446  (Δ=0.036 to SELL)
  Next cycle in 15m.
```

Top-3 closest-to-threshold includes symbol, direction with smallest gap, proba, and Δ to the nearest threshold. Use `buy_th=0.59 sell_th=0.41` from the per-symbol decision records.

### Data source

Inside `V2SignalManager`, accumulate per-cycle decisions in a local list scoped to the cycle coroutine. After the per-user loop completes, compute the digest from the list and emit one payload per session.

## Exact Changes

### File 1 — `quant_v2/telebot/signal_manager.py`

At the top of the per-cycle driver function, initialise:

```python
cycle_decisions: list[dict[str, Any]] = []
```

Whenever a decision is made per symbol (existing code path, after `Signal decision: ...` log), append a small record:

```python
cycle_decisions.append({
    "symbol": symbol,
    "signal": signal_type,
    "probability": proba_up,
    "buy_th": buy_threshold,
    "sell_th": sell_threshold,
})
```

Reuse existing variables for `buy_threshold`/`sell_threshold` — grep the file to find their names (likely local to the decision function).

At the end of the cycle, after per-user emits:

```python
if self._quiet_heartbeat_enabled():
    for session in list(self.sessions.values()):
        user_cycle = [d for d in cycle_decisions if d["signal"] in ("BUY", "SELL")]
        if user_cycle:
            continue  # user already received actionable signals, no digest needed
        digest_payload = self._build_cycle_digest(cycle_decisions)
        try:
            await _maybe_await(session.on_signal(digest_payload))
        except Exception as exc:
            logger.warning("cycle digest emit failed for user %s: %s", session.user_id, exc)
```

(If there's a helper for "maybe_await" in the file already, reuse it. Otherwise, use the existing pattern for calling `on_signal`.)

Add methods:

```python
@staticmethod
def _quiet_heartbeat_enabled() -> bool:
    return os.getenv("BOT_V2_QUIET_HEARTBEAT", "0").strip().lower() in {"1", "true", "yes", "on"}

def _build_cycle_digest(self, decisions: list[dict[str, Any]]) -> dict[str, Any]:
    top = sorted(
        decisions,
        key=lambda d: min(
            abs(d["probability"] - d["buy_th"]),
            abs(d["probability"] - d["sell_th"]),
        ),
    )[:3]
    return {
        "signal": "CYCLE_DIGEST",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "top_by_closest_threshold": [
            {
                "symbol": d["symbol"],
                "probability": float(d["probability"]),
                "buy_th": float(d["buy_th"]),
                "sell_th": float(d["sell_th"]),
                "gap_to_buy": float(d["buy_th"] - d["probability"]),
                "gap_to_sell": float(d["probability"] - d["sell_th"]),
            }
            for d in top
        ],
        "total_decisions": len(decisions),
        "cycle_interval_seconds": self.loop_interval_seconds,
    }
```

### File 2 — Telegram renderer in `quant/telebot/main.py`

Find the existing `on_signal` handler wired into each session (search for `on_signal=` in main.py). Extend that handler to check:

```python
if payload.get("signal") == "CYCLE_DIGEST":
    text = _format_cycle_digest(payload)
    await context.bot.send_message(chat_id=user_id, text=text, parse_mode=None)
    return
```

Add `_format_cycle_digest(payload: dict) -> str` that produces the example text above. Keep it short (< 500 chars), no inline keyboard. Use emoji only if the rest of the file already uses them consistently (inspect the existing signal format).

### File 3 — `.env.example`

If present, add:

```
# Optional heartbeat on cycles where no BUY/SELL fired. 0=off (default), 1=on.
# BOT_V2_QUIET_HEARTBEAT=0
```

## Tests to Add

`tests/quant_v2/telebot/test_signal_manager_digest.py`:

1. `test_digest_not_emitted_when_disabled` — env unset. Run a cycle with all-HOLD. Assert no digest payload was emitted to any session.

2. `test_digest_emitted_when_all_hold_and_enabled` — env=`1`. All-HOLD cycle. Assert exactly one `CYCLE_DIGEST` payload per session.

3. `test_digest_suppressed_when_any_actionable` — env=`1`. Cycle has one BUY. Assert NO digest is emitted (user already got the BUY).

4. `test_digest_contains_top_3_closest_to_threshold` — env=`1`. Supply 5 decisions with varied probas. Assert `top_by_closest_threshold` has length 3 and is sorted by min gap.

5. `test_digest_emit_exception_does_not_crash_cycle` — mock `session.on_signal` to raise on digest. Assert the cycle completes normally and the error is logged.

6. Renderer test in `tests/quant/telebot/test_main_digest_renderer.py`:
   - `test_format_cycle_digest_contains_symbol_and_proba` — feed a known payload, assert the rendered text contains each top-3 symbol and its proba.
   - `test_format_cycle_digest_under_500_chars` — assert `len(text) < 500`.

## Definition of Done

- [ ] Digest is opt-in via `BOT_V2_QUIET_HEARTBEAT`, default off.
- [ ] Digest fires at most once per cycle per session, only on all-HOLD cycles.
- [ ] Digest emit failure never crashes the cycle.
- [ ] Renderer produces a concise (<500 chars) message with top-3 closest-to-threshold symbols.
- [ ] All new tests pass; existing `pytest` suite passes.
- [ ] PR title: `feat(signal_manager): optional quiet-cycle heartbeat digest to Telegram`.

## Common Pitfalls — Do NOT do any of these

- ❌ Making the digest default-on. Existing users did not opt in. Default OFF.
- ❌ Emitting the digest before per-user BUY/SELL signals. Digest is a fallback, it fires last.
- ❌ Including the full list of decisions in the message. 3 symbols max — the audit showed log-line volume is already ~500/hour; keep Telegram signal-to-noise high.
- ❌ Using a new Telegram send path. Reuse the existing `context.bot.send_message` pattern.
- ❌ Persisting digests to disk or DB. Ephemeral, in-memory only.
- ❌ Treating DRIFT_ALERT as "actionable". For digest gating, actionable means BUY or SELL only.

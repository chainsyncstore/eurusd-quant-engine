# TASK P2-1 — Reduce signal-loop cadence from 3600 s to 900 s

> **BEFORE YOU START**: read `scratch/audit_20260423/agent_prompts/00_common_preamble.md` and follow every rule there.

## Context (why this matters)

`V2SignalManager` runs one evaluation per symbol per hour (`loop=3600s`). The audit showed that a paper position like the BNBUSDT long can hold for a full hour before an opposing signal can fire, even when underlying bars move on 5-minute timeframes. Reducing the loop to 15 minutes (900 s) gives the exit path 4× the opportunities to react.

The feature inputs use `anchor_interval=1h` bars, so the underlying model resolution does not change. Only the **polling cadence** and the decision frequency change.

## Scope

This task is **configuration-only**. There are two valid fulfilment paths:

1. **Path A (recommended)**: change the two Python defaults from `3600` → `900`.
2. **Path B (ops-only)**: set `BOT_V2_SIGNAL_LOOP_SECONDS=900` env var on the production container and leave source defaults alone.

You must do **Path A**. Path B is documented here only so you know what the alternative is.

## Exact Changes

### File 1 — `quant/telebot/main.py`

Find around lines 89-95:

```python
try:
    V2_SIGNAL_LOOP_SECONDS = max(
        int((os.getenv("BOT_V2_SIGNAL_LOOP_SECONDS", "3600").strip() or "3600")),
        1,
    )
except ValueError:
    V2_SIGNAL_LOOP_SECONDS = 3600
```

Replace with:

```python
try:
    V2_SIGNAL_LOOP_SECONDS = max(
        int((os.getenv("BOT_V2_SIGNAL_LOOP_SECONDS", "900").strip() or "900")),
        1,
    )
except ValueError:
    V2_SIGNAL_LOOP_SECONDS = 900
```

### File 2 — `quant_v2/telebot/signal_manager.py`

Find around lines 113-123:

```python
@staticmethod
def _resolve_loop_interval(loop_interval_seconds: int | None) -> int:
    if loop_interval_seconds is not None:
        return max(int(loop_interval_seconds), 1)

    raw = os.getenv("BOT_V2_SIGNAL_LOOP_SECONDS", "3600").strip() or "3600"
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 3600
    return max(parsed, 1)
```

Replace with:

```python
@staticmethod
def _resolve_loop_interval(loop_interval_seconds: int | None) -> int:
    if loop_interval_seconds is not None:
        return max(int(loop_interval_seconds), 1)

    raw = os.getenv("BOT_V2_SIGNAL_LOOP_SECONDS", "900").strip() or "900"
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 900
    return max(parsed, 1)
```

### File 3 — `.env.example` or equivalent

If the repo has a `.env.example`, `config/example.env`, or similar documenting env vars: find the `BOT_V2_SIGNAL_LOOP_SECONDS` line and update its commented default from 3600 → 900 along with a note like `# 900s = 15 min; was 3600 pre-audit`. If no such file exists, skip this step.

## Tests to Update

Search for tests that hard-code `3600`:

```bash
grep -rn "3600" tests/ --include="*.py"
```

For each match that references signal-loop cadence (not some unrelated timeout), change the expected default to `900`. If the test sets `BOT_V2_SIGNAL_LOOP_SECONDS` explicitly, leave it alone — that's testing the override path.

## Tests to Add

Add to `tests/quant_v2/telebot/test_signal_manager.py` (create if missing, following existing test-module conventions in `tests/`):

1. `test_default_loop_interval_is_900` — instantiate `V2SignalManager` with `loop_interval_seconds=None`, `BOT_V2_SIGNAL_LOOP_SECONDS` unset (use `monkeypatch.delenv`). Assert `manager.loop_interval_seconds == 900`.

2. `test_env_override_still_works` — monkeypatch `BOT_V2_SIGNAL_LOOP_SECONDS=60`. Assert resolved value is `60`.

3. `test_explicit_kwarg_beats_env` — monkeypatch env to `60`, pass `loop_interval_seconds=120` explicitly. Assert value is `120`.

## Definition of Done

- [ ] Two Python files changed, one test file touched/added.
- [ ] All new tests pass; all existing tests pass (after expected-value updates where valid).
- [ ] PR title: `perf(signal_manager): reduce default loop cadence from 3600s to 900s`.
- [ ] PR body includes:
  - A note on Binance rate-limit impact. Current rate ≈ 10 symbols × 2 users × 1 req/hour = 20/hr. Post-change ≈ 80/hr (one-four-th of a minute). Well under Binance's per-IP 2400 req/min limit.
  - A monitoring checklist for operators: watch for `429` status codes and `httpx.ReadError` spikes for 24h after deploy.

## Common Pitfalls — Do NOT do any of these

- ❌ Lowering below 900 s in this PR. Anything shorter needs a data-load study first.
- ❌ Changing `anchor_interval` or `history_bars`. Those are feature-space settings, not cadence settings.
- ❌ Touching any scheduler/ retrain cadence. Retrain stays at 168h.
- ❌ Hard-coding `900` in more than two places. Single source of truth: env with default.
- ❌ "Optimising" the loop body while you're in there. Out of scope.

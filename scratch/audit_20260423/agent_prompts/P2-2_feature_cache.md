# TASK P2-2 — Per-cycle shared feature/prediction cache (de-dup per-user compute)

> **BEFORE YOU START**: read `scratch/audit_20260423/agent_prompts/00_common_preamble.md` and follow every rule there. Only proceed after P2-1 is merged.

## Context (why this matters)

The audit found every 07:00 UTC cycle re-computed features and model predictions **twice** — once per active user (`6268794073` and `8392916807`). The model output is user-agnostic: features and predictions depend only on `(symbol, anchor_interval, bar_timestamp)`. Allocation and execution differ per user, but the upstream prediction should be cached. With P2-1 dropping cadence to 900 s, compute load quadruples unless we de-dup.

## Scope

- **Modify**: `quant_v2/telebot/signal_manager.py` (add cache, use cache in the per-user loop).
- **Add**: tests in `tests/quant_v2/telebot/test_signal_manager_cache.py`.
- **Do not modify**: features pipeline (`quant/features/pipeline.py`), Binance client, allocator, or execution.

## Design Constraints

1. **Cache key**: `(symbol, anchor_interval, bar_timestamp_utc_iso)`. Use tuple keys; do not concat into strings with separators that could collide.
2. **Cache value**: the computed prediction payload dict (or a frozen copy). Must be safe to share across users — ensure no per-user mutation leaks.
3. **TTL**: entries older than `max(loop_interval_seconds, 600)` seconds are evicted. Simple time-based eviction, no LRU needed.
4. **Memory**: bound total size to `len(self.symbols) × 4` entries (tight; the most recent 4 cycles per symbol). If full, evict oldest by insertion order.
5. **Thread/async safety**: the manager's `_loop` is async; users' sessions each spawn their own `asyncio.Task`. Use `asyncio.Lock` guarding the dict, or (preferred) build the cache per-cycle scoped to the driver task rather than to the whole manager.

**Strongly preferred design**: a **per-cycle cache** scoped inside the driver coroutine (not a long-lived dict on `V2SignalManager`). Rationale: simpler correctness. Every cycle builds a fresh `cycle_cache: dict[tuple, dict]`, passes it into the per-user evaluate function, and lets it garbage-collect when the cycle ends. No TTL, no lock, no eviction policy. This is the target design.

## Preconditions — Code you must read first

1. Open `quant_v2/telebot/signal_manager.py` and find the driver coroutine that iterates symbols and calls per-user logic. Most likely it's `_loop`, but the actual per-symbol compute may live in a helper like `_evaluate_symbol` or `_route_signals`.
2. Identify the function that actually runs the model on a symbol (where `horizon_ensemble` or `full_ensemble` is called). Note its signature.
3. Identify the function that wraps the per-user signal construction (where `_attach_native_v2_fields` is called).

Document in the PR description exactly which two functions you identified. If the code structure is materially different from the above description, STOP and escalate — the shape of the cache depends on the actual call graph.

## Exact Changes (sketch — adapt to actual call graph)

### Pattern A: Single driver invokes symbol-evaluate once per user per cycle

If the driver structure is:

```
_loop:
    for session in sessions:
        for symbol in symbols:
            payload = self._evaluate_symbol(session, symbol, ...)
            await session.on_signal(payload)
```

**Refactor to**:

```
_loop:
    cycle_cache: dict[tuple, dict] = {}
    for session in sessions:
        for symbol in symbols:
            payload = self._evaluate_symbol(session, symbol, cycle_cache=cycle_cache, ...)
            await session.on_signal(payload)
```

Inside `_evaluate_symbol`, near the top after inputs are assembled:

```python
cache_key = (symbol, self.anchor_interval, bar_ts.isoformat())
cached = cycle_cache.get(cache_key) if cycle_cache is not None else None
if cached is not None:
    # Copy to avoid per-user mutation leaking across sessions
    shared_payload = dict(cached)
else:
    # Existing expensive compute path (features, ensemble predict, etc.)
    shared_payload = self._compute_symbol_prediction(symbol, ...)
    if cycle_cache is not None:
        cycle_cache[cache_key] = dict(shared_payload)

# Now fold in per-user fields (live/paper, user_id, session-specific state)
user_payload = self._apply_user_context(shared_payload, session)
return user_payload
```

The refactor may require extracting "everything that is user-agnostic" into `_compute_symbol_prediction` and "everything per-user" into `_apply_user_context`. Do this extraction cleanly with **no behaviour change** when the cache is empty. The tests below will verify behavioural equivalence.

### Pattern B: If the driver is structured per-symbol-first

```
_loop:
    for symbol in symbols:
        shared = self._compute_symbol_prediction(symbol, ...)
        for session in sessions:
            await session.on_signal(self._apply_user_context(shared, session))
```

This is the ideal shape — no cache dict needed, just hoist the compute. If the existing code is already close to this, do the hoist and call it done.

## Tests to Add

Create `tests/quant_v2/telebot/test_signal_manager_cache.py`.

1. `test_cache_dedups_compute_across_users` — construct a `V2SignalManager` with two registered sessions for different `user_id`s. Patch `_compute_symbol_prediction` with a `unittest.mock.MagicMock` that returns a deterministic dict. Drive one full cycle. Assert the mock was called **exactly once per symbol per cycle**, not twice.

2. `test_cache_does_not_leak_per_user_mutations` — two sessions, mutate the payload returned to user A (e.g. set `payload["user_specific"] = "A"`). Verify user B's payload does NOT have that key. This guards against forgetting the `dict(cached)` copy.

3. `test_cache_scoped_to_cycle` — run two cycles back-to-back. Assert the compute mock is called twice per symbol (once per cycle), proving the cache does not persist between cycles.

4. `test_behaviour_unchanged_with_single_user` — with one session, verify the emitted payload is byte-for-byte identical to the pre-refactor behaviour. Use a golden reference: capture output before the refactor in a JSON fixture if necessary, or generate it at test time by bypassing the cache path.

If the existing `_loop` is tested, add an integration-level test that runs one simulated cycle end-to-end with mocked Binance client and asserts the symbols × 1 compute calls.

## Performance Budget

After this change, a single cycle should:
- Call `_compute_symbol_prediction` exactly `len(symbols)` times.
- Call Binance bar/OI/funding endpoints at most once per symbol (not per user).
- Take no more elapsed time than the pre-refactor single-user scenario.

Document timings in PR body by running:

```python
# quick benchmark harness
import time, asyncio
# instantiate manager with 2 sessions, 5 symbols, mocked client
start = time.perf_counter()
await manager._run_one_cycle()
elapsed = time.perf_counter() - start
print(f"elapsed={elapsed*1000:.0f}ms")
```

Paste before/after numbers (target ≥40% reduction for 2-user case).

## Definition of Done

- [ ] Compute path is invoked at most once per `(symbol, cycle)` regardless of number of active users.
- [ ] Per-user fields (live, user_id, scorecard-as-of-user if any) are still applied correctly.
- [ ] All 4 new tests pass; existing `pytest` suite passes.
- [ ] Benchmark numbers included in PR body.
- [ ] PR title: `perf(signal_manager): cache per-cycle predictions to de-dup cross-user compute`.
- [ ] PR body lists the two functions you refactored (Pattern A vs B) and confirms the scope of extracted helpers.

## Common Pitfalls — Do NOT do any of these

- ❌ Introducing a long-lived cache on `V2SignalManager` without eviction. Memory will creep.
- ❌ Forgetting to deep-copy user payloads. The scorecard uses `hit_rate` per symbol which is **user-agnostic**, but future per-user fields must be applied after the copy.
- ❌ Moving the scorecard update out of the shared compute path. Scorecard records predictions once per symbol per cycle — that is correct today and must remain once-per-cycle after the refactor.
- ❌ Caching across cycles. Bar timestamps change; predictions are stale.
- ❌ Using `functools.lru_cache`. Async + kwargs + mutable dict args do not play well with `lru_cache`; use an explicit dict.

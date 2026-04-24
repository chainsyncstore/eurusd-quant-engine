# TASK P3-1 — Persist `active_model_version` to user_context on model hot-swap

> **BEFORE YOU START**: read `scratch/audit_20260423/agent_prompts/00_common_preamble.md` and follow every rule there.

## Context (why this matters)

The audit dumped `state/quant_bot.db` and found `user_context.active_model_version = model_20260413_192904` for both users, while `V2SignalManager` had reloaded `model_20260421_192947` on Apr 21 19:37. The DB field is **stale** because the persistence helper `_persist_user_session_flags` is only called from `/start_demo`, `/start_live`, etc. — never from the hot-swap path. Result: `/stats` and `/lifetime_stats` report the wrong model; PnL attribution is misleading.

## Scope

- **Modify**:
  - `quant_v2/telebot/signal_manager.py` — emit a hook/callback whenever the active model is reloaded from registry.
  - `quant/telebot/main.py` — wire that hook to call the existing `_persist_user_session_flags` for every active session.
- **Add**: tests in `tests/quant_v2/telebot/test_signal_manager_model_rotation.py` or extend existing test file covering model reload.
- **Do NOT modify** the DB schema, the registry code, or the `_persist_user_session_flags` function itself (it already supports the fields we need).

## Preconditions

Read these files end-to-end:

1. `quant/telebot/main.py` lines 395-440 (`_persist_user_session_flags` signature).
2. `quant_v2/telebot/signal_manager.py` — find the method that loads the active model from the registry. Look for `registry.get_active` or similar, and the log line `"Initialized V2SignalManager with model: ..."`. The method name is likely `_reload_active_model`, `_load_active_model`, or embedded in `_loop`.

Document in PR description which method hosts the reload logic.

## Exact Changes

### Step 1 — Add a post-reload callback hook on V2SignalManager

Extend `V2SignalManager.__init__` to accept:

```python
on_model_rotated: Callable[[str, str], None] | None = None
```

Store as `self._on_model_rotated = on_model_rotated`. Add the `Callable` import alongside existing typing imports if not already present (check top of file first).

In the reload method (wherever `self.active_model = load_model(...)` or `self.horizon_ensemble = ...` is assigned, adjacent to the `"Initialized V2SignalManager with model: ..."` log line), after a successful reload, invoke the hook:

```python
if self._on_model_rotated is not None:
    try:
        new_version = <compute from registry or model_dir>
        new_source = <e.g. "registry_active" — match the existing log format>
        self._on_model_rotated(new_version, new_source)
    except Exception as exc:
        logger.warning("on_model_rotated hook failed: %s", exc)
```

**Critical**: the hook call must be inside a try/except. A failing hook must NEVER crash the signal loop. Log at WARNING and continue.

### Step 2 — Wire the hook in `quant/telebot/main.py`

Find where `V2SignalManager(...)` is instantiated (the `_get_v2_signal_manager` helper around line 381 or its callee). After construction, set the hook:

```python
def _on_v2_model_rotated(new_version: str, new_source: str) -> None:
    """Persist rotated model metadata onto every active user_context row."""
    manager = _CURRENT_V2_MANAGER  # or however the singleton is referenced
    if manager is None:
        return
    for user_id in list(manager.sessions.keys()):
        try:
            _persist_user_session_flags(
                user_id,
                active_model_version=new_version,
                active_model_source=new_source,
            )
        except Exception as exc:
            logger.warning(
                "Failed to persist rotated model for user %s: %s", user_id, exc
            )
```

Pass this callback into the `V2SignalManager` constructor via the new `on_model_rotated` kwarg.

If the singleton structure makes capturing the manager reference awkward, use a module-level `_V2_MANAGER_REF: V2SignalManager | None = None` set right after construction, and read it inside `_on_v2_model_rotated`. This keeps the closure simple.

### Step 3 — Preserve existing startup behaviour

When a user runs `/start_demo` / `/start_live`, `_persist_user_session_flags` is already called. Ensure your new path does not double-write on the same cycle (one call from start, one from hook). De-duping is nice-to-have but not required since the helper only commits when values differ (see line 434 `if changed: session.commit()`).

## Tests to Add

Create `tests/quant_v2/telebot/test_signal_manager_model_rotation.py`:

1. `test_hook_fires_on_reload` — construct a manager with a `MagicMock` hook. Trigger the reload method (you may need to mock the registry). Assert the hook was called once with the expected `(version, source)` tuple.

2. `test_hook_not_called_when_reload_is_noop` — if the model has not changed, the hook should not fire. (If the existing reload logic always re-emits on cycle start, note this in the PR description and skip this test.)

3. `test_hook_exception_does_not_propagate` — hook raises `RuntimeError`. Assert the reload method completes normally and the error is logged at WARNING.

4. Integration-level test in `tests/quant/telebot/test_main_model_rotation.py`: run the full wiring (manager + hook + `_persist_user_session_flags`) against an in-memory SQLite. Assert `user_context.active_model_version` is updated after a simulated rotation.

Use `unittest.mock` and the existing SQLAlchemy test helpers if any; don't build new fixtures from scratch.

## Definition of Done

- [ ] `V2SignalManager` accepts an `on_model_rotated` kwarg with a safe default.
- [ ] Hook fires exactly once per successful rotation, with the new version + source.
- [ ] Hook exceptions are swallowed and logged.
- [ ] Main wiring populates `active_model_version` and `active_model_source` on every active session in DB when rotation occurs.
- [ ] 3–4 new tests pass; existing `pytest` suite passes.
- [ ] PR title: `fix(main): persist active_model_version to user_context on model rotation`.
- [ ] PR body lists every file touched and confirms the audit-observed staleness is fixed (include a small "before/after" table showing the DB field updates).

## Common Pitfalls — Do NOT do any of these

- ❌ Altering `user_context` schema, adding columns, or migrating.
- ❌ Making the hook async. `_persist_user_session_flags` is sync SQLAlchemy; do not block the event loop on it for long — but a single row update is fast enough that running it inline in the hook is fine.
- ❌ Firing the hook from inside a tight inner loop (per symbol per user). It must fire once per rotation event, not per signal.
- ❌ Logging the hook's success at INFO level and spamming the log — one WARNING on failure is enough; success is silent.
- ❌ Hard-coding the `"registry_active"` source literal. Read it from the same code that produces the existing `"Initialized V2SignalManager with model: ... (source=registry_active:...)"` log line.

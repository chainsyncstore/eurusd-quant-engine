# TASK P0-3 — Optimizer: synthesise flatten target for held positions with no signal

> **BEFORE YOU START**: read `scratch/audit_20260423/agent_prompts/00_common_preamble.md` and follow every rule there. If task P0-1 has not yet landed, confirm with the reviewer whether to rebase on top of it.

## Context (why this matters)

The audit found that for ~84% of cycles, the portfolio optimizer returns zero weights because the upstream `allocate_signals` produces no `target_exposures` entry for a symbol the user already holds (model emitted HOLD, so the symbol isn't in the target dict at all). The optimizer therefore has **no opportunity to flatten** an existing position. Combined with the HOLD-trap failure mode (addressed by P0-2), this means a long can linger for many cycles even when the model has clearly stopped endorsing it. P0-2 handles the emergency time-stop; P0-3 handles the deliberate "no signal = no position" case at the portfolio layer.

## Scope

- **Modify**: `quant_v2/portfolio/optimizer.py` (add an optional kwarg + flatten synthesis).
- **Modify**: the single call-site of `RiskParityOptimizer.optimize(...)`. Find it via `grep -r "\.optimize(" quant_v2/` or similar. Pass the new argument from there.
- **Add**: tests in `tests/quant_v2/portfolio/test_optimizer.py` (extending the file created or touched in P0-1).
- **Do not modify** the signal manager's time-stop logic, the scorecard, or execution code.

## Exact Changes

### Step 1 — Extend `RiskParityOptimizer.optimize` signature

Open `quant_v2/portfolio/optimizer.py`. Locate `def optimize(` around line 70. The current signature is:

```python
def optimize(
    self,
    target_exposures: dict[str, float],
    price_histories: dict[str, pd.Series],
    equity_usd: float,
) -> OptimizerResult:
```

Add a new keyword-only parameter `current_positions` after `equity_usd`:

```python
def optimize(
    self,
    target_exposures: dict[str, float],
    price_histories: dict[str, pd.Series],
    equity_usd: float,
    *,
    current_positions: dict[str, float] | None = None,
) -> OptimizerResult:
```

The keyword-only marker (`*,`) is required so the optimizer's public API remains backwards-compatible with positional callers.

### Step 2 — Synthesise flatten targets before the main algorithm

Immediately after the docstring of `optimize` and before the existing `if not target_exposures:` guard, add:

```python
# --- Synthesise flatten targets for held positions with no incoming signal ---
# Prevents "silent HOLD traps a position" by ensuring every held symbol
# gets a portfolio decision each cycle. A zero target + nonzero current
# position will be transformed into an explicit flatten by the caller.
augmented_targets = dict(target_exposures)
synthesised_flatten: list[str] = []
if current_positions:
    for sym, pos in current_positions.items():
        if abs(pos) < 1e-12:
            continue
        if sym in augmented_targets:
            continue
        augmented_targets[sym] = 0.0
        synthesised_flatten.append(sym)

target_exposures = augmented_targets
```

Then, after the `OptimizerResult(...)` is constructed at the end of the function, replace its `constraints_applied` tuple to include the new sentinel when relevant. Find the existing final return:

```python
return OptimizerResult(
    weights=signed_weights,
    vols=vols,
    correlations=correlations,
    dropped_symbols=dropped,
    constraints_applied=tuple(dict.fromkeys(constraints)),
)
```

Replace with:

```python
if synthesised_flatten:
    constraints.append("flatten_held_no_signal")

return OptimizerResult(
    weights=signed_weights,
    vols=vols,
    correlations=correlations,
    dropped_symbols=dropped,
    constraints_applied=tuple(dict.fromkeys(constraints)),
)
```

### Step 3 — Handle zero weights downstream

The existing algorithm will produce `raw_weights[sym] ≈ 0.0` for flatten targets because `original_gross` in step 4 (around line 166) is computed as `sum(abs(v) for v in target_exposures.values())` and a `0.0` target contributes nothing. That will then get filtered by the min-notional step. This is **incorrect** for a flatten — we want the flatten signal preserved, not filtered.

Fix this by special-casing flatten in step 4. Locate:

```python
# --- Step 4: Apply directions and scale by original gross exposure ---
original_gross = sum(abs(v) for v in target_exposures.values())
signed_weights: dict[str, float] = {}
for sym in symbols:
    w = raw_weights[sym] * original_gross * directions.get(sym, 1.0)
    signed_weights[sym] = w
```

Replace with:

```python
# --- Step 4: Apply directions and scale by original gross exposure ---
original_gross = sum(abs(v) for v in target_exposures.values())
signed_weights: dict[str, float] = {}
for sym in symbols:
    if sym in synthesised_flatten:
        # Preserve explicit flatten intent (weight 0 means close any open position).
        signed_weights[sym] = 0.0
        continue
    w = raw_weights[sym] * original_gross * directions.get(sym, 1.0)
    signed_weights[sym] = w
```

### Step 4 — Bypass min-notional for flatten targets

Locate step 5 (around line 172 after P0-1 has landed):

```python
# --- Step 5: Minimum notional filter (dynamic: max(base, equity × 0.5%)) ---
effective_min_notional = max(self.min_notional_usd, equity_usd * 0.005)
dropped: list[str] = []
for sym in list(signed_weights.keys()):
    notional = abs(signed_weights[sym]) * equity_usd
    if notional < effective_min_notional:
        dropped.append(sym)
        del signed_weights[sym]
```

Replace the `for` loop body so flatten targets are never dropped:

```python
for sym in list(signed_weights.keys()):
    if sym in synthesised_flatten:
        # Flatten intents bypass notional filter — closing is always allowed.
        continue
    notional = abs(signed_weights[sym]) * equity_usd
    if notional < effective_min_notional:
        dropped.append(sym)
        del signed_weights[sym]
        logger.debug(
            "Min notional filter: dropped %s (notional=%.2f < %.2f)",
            sym, notional, effective_min_notional,
        )
```

Preserve the existing `logger.debug` line unchanged, only add the `if sym in synthesised_flatten: continue` guard at the top of the loop.

### Step 5 — Update the call-site

Find the single call-site of `RiskParityOptimizer.optimize(` in `quant_v2/portfolio/`. It is most likely in `quant_v2/portfolio/allocation.py` or a similar allocator module.

Look in that module for where the caller already has access to current open positions (session state, or a portfolio-tracker singleton). Pass `current_positions=<that dict>` as a keyword argument. **If no such dict is in scope at the call-site**, STOP and escalate — the caller needs plumbing that belongs in a follow-up task, and forcing it here would violate scope.

## Tests to Add

Add to `tests/quant_v2/portfolio/test_optimizer.py`:

1. `test_held_symbol_with_no_signal_is_flattened` — `target_exposures={}`, `current_positions={"BNBUSDT": 0.05}`, asserts `result.weights == {"BNBUSDT": 0.0}` and `"flatten_held_no_signal" in result.constraints_applied`.

2. `test_held_symbol_with_existing_signal_is_untouched` — `target_exposures={"BNBUSDT": -0.08}` (explicit SELL), `current_positions={"BNBUSDT": 0.05}`, asserts `result.weights["BNBUSDT"] < 0` (the explicit SELL wins, flatten is NOT synthesised) and `"flatten_held_no_signal" not in result.constraints_applied`.

3. `test_flatten_bypasses_min_notional_filter` — `equity_usd=10_000`, `target_exposures={}`, `current_positions={"BNBUSDT": 0.001}` (would produce $10 notional — below floor). Assert `"BNBUSDT"` is still in `result.weights` with value `0.0` and NOT in `result.dropped_symbols`.

4. `test_current_positions_none_is_backwards_compatible` — omit `current_positions` entirely. Assert result matches the pre-patch behaviour (use a separately-computed reference by not setting the kwarg). All previous tests must still pass unchanged.

## Tests to Update

If P0-1 landed first, its test file should be extended. If any pre-existing optimizer test breaks, STOP — it means an assumption changed that the reviewer needs to know about.

## Definition of Done

- [ ] `RiskParityOptimizer.optimize` has the new keyword-only `current_positions` parameter, default `None`, backwards-compatible.
- [ ] Synthesised flatten targets receive weight 0.0, bypass min-notional, and mark `constraints_applied` with `"flatten_held_no_signal"`.
- [ ] Explicit signals (BUY or SELL) override synthesis.
- [ ] The single downstream call-site passes `current_positions`.
- [ ] All 4 new tests pass.
- [ ] Full `pytest` suite still passes.
- [ ] PR title: `fix(optimizer): synthesise flatten target for held positions without signal`.

## Common Pitfalls — Do NOT do any of these

- ❌ Making `current_positions` positional. Keyword-only keeps existing callers safe.
- ❌ Synthesising flatten for symbols that DO have a target. The explicit signal must win.
- ❌ Removing the logger.debug in step 5 or changing its format. Ops greps on it.
- ❌ Treating `current_positions[sym] < 0` (short) differently — a held short should also be flattenable; zero target = close. The sign doesn't matter for flatten.
- ❌ Filtering held positions at the `if not target_exposures:` guard at top of function — early-returning when `target_exposures` is empty would skip flatten synthesis. Move that guard **after** the augmentation block.

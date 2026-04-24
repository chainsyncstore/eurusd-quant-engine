# TASK P0-1 — Fix the dynamic `min_notional` floor that drops dampened allocations

> **BEFORE YOU START**: read `scratch/audit_20260423/agent_prompts/00_common_preamble.md` and follow every rule there. The rules in that file override any default behaviours you would otherwise apply.

## Context (why this matters)

A 7-day production audit showed the portfolio optimizer rejected **all** candidate symbols in 84% of 1,208 cycles (`N symbols → 0 after filter`). Root cause: the min-notional floor scales as `equity_usd × 0.02` (2% of equity). At current paper equity ~$9,970, this sets the floor at **$199.40**. When the upstream `SymbolScorecard` dampens exposures to 0.30× Kelly and risk-parity splits weight across ≥2 symbols, each symbol's notional falls below $199 and gets dropped. Only when a single symbol captures the whole dampened allocation does it pass — which is why the user's session has been stuck long on BNBUSDT alone for ~13 hours.

## Scope

- **Modify**: `quant_v2/portfolio/optimizer.py` (one line + one new test).
- **Add**: one regression test in `tests/quant_v2/portfolio/test_optimizer.py` (create the file if it does not exist; respect the `tests/` layout that already mirrors `quant_v2/`).
- **Do not touch** any other file, including other tests that currently pass.

## Exact Change

Open `quant_v2/portfolio/optimizer.py`. You must find this block verbatim around line 172:

```python
        # --- Step 5: Minimum notional filter (dynamic: max(base, equity × 2%)) ---
        effective_min_notional = max(self.min_notional_usd, equity_usd * 0.02)
```

Replace **only** these two lines with:

```python
        # --- Step 5: Minimum notional filter (dynamic: max(base, equity × 0.5%)) ---
        effective_min_notional = max(self.min_notional_usd, equity_usd * 0.005)
```

**Rationale**: 0.5% of equity at $10k = $49.85, which respects Binance's $10 min-notional while allowing dampened multi-symbol allocations to survive. Do NOT remove the equity-scaling entirely — at $1M equity, a $10 floor would be noise.

If the code block above is not present verbatim (e.g. the comment or formula has drifted), STOP and escalate per the preamble's escalation triggers. Do not guess.

## Tests to Add

Create `tests/quant_v2/portfolio/test_optimizer.py` if it does not exist. Add a test named `test_dampened_two_symbol_allocation_survives_min_notional` that:

1. Builds a `RiskParityOptimizer` with default params.
2. Constructs a `price_histories` dict with two synthetic symbols (use `pandas.Series` of 100 bars each with mild noise, e.g. `pd.Series(100 + np.arange(100) * 0.01 + np.random.default_rng(42).standard_normal(100) * 0.05)`).
3. Passes `target_exposures={"FOOUSDT": 0.018, "BARUSDT": 0.018}` (simulating a 0.30× dampened 0.06 Kelly allocation split across 2 names).
4. Passes `equity_usd=10_000.0`.
5. Asserts that `result.weights` contains **both** symbols (not empty, not dropped).
6. Asserts that every returned weight has `abs(w) * equity_usd >= 49.0` (respects new floor).

Also add `test_min_notional_still_drops_uneconomic_positions` that:

1. Uses the same optimizer.
2. Passes `target_exposures={"TINYUSDT": 0.001}` (would yield $10 notional at $10k equity).
3. Asserts `result.weights == {}` and `"TINYUSDT"` is in `result.dropped_symbols`.

Both tests must pass. Use only `numpy`, `pandas`, and `pytest` — all already in the repo's test deps.

## Tests to Update

None. If existing `test_optimizer.py` tests now fail because they hard-coded the old 2% floor, **STOP and escalate** — do not weaken them. The reviewer will decide whether to update them.

## Definition of Done

- [ ] Exactly two lines changed in `quant_v2/portfolio/optimizer.py`, no others.
- [ ] Two new tests pass: `test_dampened_two_symbol_allocation_survives_min_notional`, `test_min_notional_still_drops_uneconomic_positions`.
- [ ] Full `pytest` suite still passes.
- [ ] PR title: `fix(optimizer): reduce min-notional floor from 2% to 0.5% of equity`.
- [ ] PR body includes: before/after value at $10k equity ($199 → $49), link to audit finding, and `git diff --stat` output.

## Common Pitfalls — Do NOT do any of these

- ❌ Changing `self.min_notional_usd` (default $10) — that's the base floor, leave it alone.
- ❌ Removing Step 5 entirely. Min-notional filtering is still needed at high equity.
- ❌ Changing the log message at line 188-193 — observability stability matters.
- ❌ Adding a new constructor parameter or env var. Task is a single-constant change.
- ❌ Renaming `effective_min_notional`. Other callers / log lines may reference it.
- ❌ Reformatting nearby code. Only the two specified lines may change.

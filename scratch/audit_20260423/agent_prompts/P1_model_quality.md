# TASK P1 — Model Quality Fixes (retrain universe, promotion gate, scorecard bands)

> **BEFORE YOU START**: read `scratch/audit_20260423/agent_prompts/00_common_preamble.md` and follow every rule there. This task bundles three closely related model-quality fixes that share context.

## Context (why this matters)

The audit traced the −2.52% paper PnL to three compounding model-quality issues:

1. **Universe mismatch**: `scheduled_retrain.py` trains on 3 symbols (`BTCUSDT` + `ETHUSDT,BNBUSDT` from env default) but `V2SignalManager` scores 9–10 symbols. Out-of-distribution symbols (ADA, DOGE, SOL, LINK, LTC, AVAX, XRP) have scorecard hit-rates 0.30–0.55.

2. **Promotion gate possibly too lax**: the Apr 20 retrain log shows horizon=8h CV accuracy **0.5499** promoted past `RETRAIN_MIN_ACCURACY=0.525`, suggesting single-split (not CV) is the effective gate. Single-split was 0.6295 — very optimistic.

3. **Scorecard bands**: `STRONG_HIT_RATE=0.55` means currently **no** symbol reaches full allocation. Bands may be fine, but need verification once universe is fixed.

## Scope

This task has **three sub-tasks** that MUST be done in the stated order and committed as three separate commits on one branch:

1. **P1-1**: Fix retrain symbol universe default.
2. **P1-4**: Audit and tighten the promotion gate (READ-ONLY investigation + focused fix).
3. **P1-3**: Re-evaluate scorecard bands (**do not change defaults** unless investigation shows they are wrong).

Files potentially touched:
- `quant_v2/research/scheduled_retrain.py`
- `quant_v2/models/trainer.py` (read-only unless gate fix is localised here)
- `quant_v2/telebot/symbol_scorecard.py` (read-only unless P1-3 mandates a change)
- tests under `tests/quant_v2/research/` and `tests/quant_v2/telebot/`

Do NOT modify:
- The signal manager (P0-2 owns it).
- The optimizer (P0-1 / P0-3 own it).
- Any data-fetching or feature-pipeline code.

---

## Sub-task P1-1 — Retrain universe default

### Exact Change

Open `quant_v2/research/scheduled_retrain.py`. Find around line 339:

```python
_extra_sym_raw = os.getenv("RETRAIN_TRAIN_SYMBOLS", "ETHUSDT,BNBUSDT").strip()
extra_symbols = [s.strip() for s in _extra_sym_raw.split(",") if s.strip()] if _extra_sym_raw else []
```

Replace with:

```python
# Default extra symbols track the live signal universe minus the anchor BTCUSDT.
# Override via RETRAIN_TRAIN_SYMBOLS=<comma-separated>.
_universe = [s for s in default_universe_symbols() if s != "BTCUSDT"]
_default_extra_syms = ",".join(_universe) if _universe else "ETHUSDT,BNBUSDT"
_extra_sym_raw = os.getenv("RETRAIN_TRAIN_SYMBOLS", _default_extra_syms).strip()
extra_symbols = [s.strip() for s in _extra_sym_raw.split(",") if s.strip()] if _extra_sym_raw else []
```

`default_universe_symbols` is already imported at line 33 — do NOT add a duplicate import. Verify the import exists before coding.

Also update the docstring at line 15 to reflect the new default:

Before:
```
    RETRAIN_TRAIN_SYMBOLS  – comma-separated extra symbols to include in training (default: ETHUSDT,BNBUSDT)
```

After:
```
    RETRAIN_TRAIN_SYMBOLS  – comma-separated extra symbols to include in training (default: full universe from default_universe_symbols() minus BTCUSDT)
```

### Tests for P1-1

Add `tests/quant_v2/research/test_scheduled_retrain_universe.py`:

1. `test_default_extra_symbols_tracks_universe` — monkeypatch `default_universe_symbols` to return `("BTCUSDT", "ETHUSDT", "ADAUSDT")`. Unset `RETRAIN_TRAIN_SYMBOLS` via `monkeypatch.delenv`. Import or re-import the module (you may need to use `importlib.reload`). Assert the resolved `extra_symbols` list equals `["ETHUSDT", "ADAUSDT"]`.

2. `test_env_override_still_honoured` — set `RETRAIN_TRAIN_SYMBOLS="FOOUSDT,BARUSDT"`. Reload. Assert `extra_symbols == ["FOOUSDT", "BARUSDT"]`.

3. `test_empty_universe_falls_back_to_safe_default` — monkeypatch `default_universe_symbols` to return `()`. Assert the fallback defaults to `["ETHUSDT", "BNBUSDT"]` (the prior safe behaviour).

If the module's computation happens at import-time inside a function rather than at module-level, these tests must call that function directly instead of reloading — adapt to the actual structure.

---

## Sub-task P1-4 — Promotion gate audit

### Investigation (NO code changes yet)

Find where retrain decides whether to promote a model. Likely locations:
- `quant_v2/research/scheduled_retrain.py` — look for calls to `ModelRegistry.promote` or similar.
- `quant_v2/models/trainer.py` — look for accuracy checks near `save_model`.

Grep the repo for: `min_accuracy`, `RETRAIN_MIN_ACCURACY`, `promote`, `single-split`, `CV accuracy`. Read the code carefully.

Determine **exactly** which scalar is compared against `min_accuracy`:
- CV accuracy? or
- single-split accuracy? or
- the max of both?

Write your finding in the PR description under a heading `## P1-4 Investigation Findings`. Include file:line references and the exact conditional.

### Conditional Fix

**Only if** your investigation shows that single-split accuracy is the effective gate while CV accuracy is lower: modify the condition so **both** must meet `min_accuracy`. Use `min(cv_accuracy, single_split_accuracy) >= min_accuracy` as the gate.

**If the investigation shows CV is already the effective gate**: do NOT change code. Document the finding and close this sub-task as "no action needed, audit hypothesis falsified".

**If you cannot determine the current gate logic with certainty**: STOP and escalate.

### Tests for P1-4 (only if a code change was made)

Add a test that feeds `cv_accuracy=0.51`, `single_split_accuracy=0.70`, `min_accuracy=0.525` and asserts the model is NOT promoted. Add an inverse test where both are ≥ 0.525 and promotion proceeds.

---

## Sub-task P1-3 — Scorecard bands (investigation + conservative fix)

### Investigation (NO code changes until findings reviewed)

Read `quant_v2/telebot/symbol_scorecard.py:46-51`:

```python
STRONG_HIT_RATE: float = 0.55
WEAK_HIT_RATE: float = 0.45
MULT_STRONG: float = 1.0
MULT_NEUTRAL: float = 0.60
MULT_WEAK: float = 0.30
```

The audit shows live hit-rates are 0.30–0.55. **After P1-1 lands**, the non-OOD symbols should climb. The question is whether the bands themselves are well-calibrated.

**Do not change these constants in this PR** unless (a) P1-1 has been deployed to production for ≥48h, (b) new scorecard logs show a meaningful number of symbols still stuck at `MULT_WEAK`, and (c) you have an explicit go-ahead from the reviewer.

### Documented Recommendation Only

In the PR description under `## P1-3 Recommendation`, document a proposed post-deployment experiment:

> After P1-1 is live for 48h, sample the scorecard summary per symbol. If ≥50% of symbols have hit_rate ≥ 0.55, current bands are fine. If ≥50% still sit in `[0.45, 0.55)` after universe fix, propose lowering `STRONG_HIT_RATE` to 0.52 in a follow-up PR, with a backtest on the past 30d of resolved predictions to confirm it does not degrade PnL.

That's the entire P1-3 deliverable for now — no code change.

### P1-3 Test (optional, informational)

You may add a test that asserts the band constants have not drifted from the audit-known values (`STRONG_HIT_RATE == 0.55`, etc.). This is a tripwire, not a functional test, so label it accordingly: `test_scorecard_band_constants_are_stable` with a comment explaining its purpose.

---

## Definition of Done (All Sub-tasks)

- [ ] Three commits on one branch `fix/p1-model-quality`, one per sub-task, in the order P1-1 → P1-4 → P1-3.
- [ ] Commit messages reference the sub-task ID in the body.
- [ ] `default_universe_symbols` import is reused, not duplicated.
- [ ] Three new test modules pass; existing `pytest` suite passes.
- [ ] PR body contains three sections: `## P1-1 Changes`, `## P1-4 Investigation Findings`, `## P1-3 Recommendation`.
- [ ] PR body includes `git diff --stat` and confirms no out-of-scope files touched.

## Common Pitfalls — Do NOT do any of these

- ❌ Changing scorecard constants in this PR. Follow-up only, with production data backing the change.
- ❌ Expanding P1-1 to also modify `HORIZONS = (2, 4, 8)` or the training-period length.
- ❌ "Fixing" the promotion gate before confirming what it currently is. Investigation comes first.
- ❌ Adding retraining symbols that are not in `default_universe_symbols()`. The two universes must stay aligned.
- ❌ Triggering a retrain from your local machine. Retrain happens in production on schedule; your PR just changes the default.

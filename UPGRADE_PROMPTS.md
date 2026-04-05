# Upgrade Prompts — Copy & Paste for Each Phase

> **How to use**: Copy the prompt for the phase you want to implement. Paste it into
> a new conversation with Kimi 2.5 or GPT Codex. The AI will have access to your
> codebase via the IDE. Each prompt is self-contained.
>
> **Execute in order**: Phase 1 → Phase 2 → Phase 3 → Phase 4. Do NOT skip phases.
> After each phase, run the full test suite and deploy before starting the next one.

---

## Phase 1 Prompt

```
I need you to implement Phase 1 of a system upgrade for my crypto trading engine.

READ THE GUIDE FIRST:
Open and read the file BUILD_GUIDE.md in the project root. Read it fully before writing
any code. Pay special attention to:
- Section 1 (Repository Map) — understand where every file lives
- Section 3 (DO NOT TOUCH List) — these files must NOT be modified
- Section 4 (Phase 1) — your implementation spec
- Appendix A (Allocation Multiplier Pattern) — the exact pattern to follow for any new multiplier

WHAT TO BUILD (Phase 1 — Feature Enrichment & Multi-Horizon Ensemble):

1. Create quant/features/cross_pair.py
   - A compute(df) function that adds 4 cross-pair features: btc_return_4h,
     btc_divergence_4h, btc_correlation_24h, relative_vol_ratio
   - It should check for a "_btc_returns" column in the DataFrame (injected by caller)
   - If _btc_returns is missing, fill features with 0.0 (neutral)
   - All new columns must have NO NaN after warmup (use .fillna(0.0))
   - See BUILD_GUIDE.md Section 4A Step 1 for exact code spec

2. Create quant/features/liquidation_proximity.py
   - A compute(df) function that adds 3 features: oi_funding_pressure,
     price_position_24h, liquidation_cascade_4h
   - Must handle missing OI/funding columns gracefully (fill with 0.0)
   - See BUILD_GUIDE.md Section 4A Step 2 for exact code spec

3. Register new features in quant/features/pipeline.py
   - Add imports for cross_pair and liquidation_proximity
   - Add liquidation_proximity to _CRYPTO_MODULES (before crypto_session)
   - Add cross_pair to _CRYPTO_MODULES (after liquidation_proximity, before crypto_session)
   - Add all 7 new feature names to _FEATURE_WHITELIST
   - See BUILD_GUIDE.md Section 4A Step 3

4. Update max_features in quant/config.py ResearchConfig from 90 to 97

5. Inject BTC returns into the pipeline via quant_v2/telebot/signal_manager.py
   - Ensure BTCUSDT is processed first in the symbol loop
   - Cache BTC close returns after processing BTCUSDT
   - Pass btc_returns to _build_featured_frame()
   - Modify _build_featured_frame() to accept and inject btc_returns as _btc_returns column
   - See BUILD_GUIDE.md Section 4A Steps 4

6. Create quant_v2/models/ensemble.py
   - HorizonEnsemble class that loads multiple horizon models (2h, 4h, 8h)
   - from_directory() class method to load from registry artifact dir
   - predict() method that returns weighted (probability, uncertainty) tuple
   - Agreement bonus: if all models agree on direction, reduce uncertainty by 20%
   - See BUILD_GUIDE.md Section 4B Step 2

7. Create quant_v2/research/retrain_pipeline.py
   - run_retrain() function that trains models for horizons 2, 4, 8
   - Uses existing train() and save_model() from quant_v2/models/trainer.py
   - See BUILD_GUIDE.md Section 4B Step 1

8. Wire ensemble into V2SignalManager._predict_with_uncertainty()
   - Add self.horizon_ensemble attribute to __init__
   - Try ensemble first, fall back to single model
   - Load ensemble from registry artifact directory during model refresh
   - See BUILD_GUIDE.md Section 4B Step 3

9. Write tests
   - tests/quant_v2/test_cross_pair_features.py — test compute() with and without BTC returns
   - tests/quant_v2/test_liquidation_proximity.py — test compute() with and without OI
   - tests/quant_v2/test_horizon_ensemble.py — test ensemble predict, agreement bonus,
     graceful fallback, from_directory with empty dir
   - See BUILD_GUIDE.md Section 4C

10. Run full test suite: python -m pytest tests/quant_v2/ -q --tb=short
    All existing 225+ tests must still pass plus your new tests.

RULES:
- Do NOT modify any file in the DO NOT TOUCH list (see BUILD_GUIDE.md Section 3)
- Do NOT delete or weaken any existing test
- Do NOT change the signature of allocate_signals() or StrategySignal for this phase
- Use .fillna(0.0) on all new feature columns to prevent NaN propagation
- All imports must be at the top of the file
- After all tests pass, commit with message: "Phase 1: Cross-pair features + multi-horizon ensemble"
```

---

## Phase 2 Prompt

```
I need you to implement Phase 2 of a system upgrade for my crypto trading engine.
Phase 1 has already been completed and merged.

READ THE GUIDE FIRST:
Open and read the file BUILD_GUIDE.md in the project root. Read it fully before writing
any code. Pay special attention to:
- Section 1 (Repository Map) — understand where every file lives
- Section 3 (DO NOT TOUCH List) — these files must NOT be modified
- Section 5 (Phase 2) — your implementation spec
- Appendix A (Allocation Multiplier Pattern) — THE pattern for adding new multipliers

WHAT TO BUILD (Phase 2 — Event Gate / News Awareness Layer):

1. Create quant_v2/data/news_client.py
   - NewsEvent dataclass with fields: symbol, title, source, published_at, sentiment, severity, url
   - CryptoPanicClient class that fetches news from CryptoPanic free API
   - fetch_recent() method that returns list[NewsEvent]
   - Maps CryptoPanic vote counts to sentiment (bullish/bearish/neutral) and severity (low/medium/high)
   - symbol_to_base_ticker() helper to convert "BTCUSDT" → "BTC"
   - See BUILD_GUIDE.md Section 5 Step 1 for exact code spec

2. Create quant_v2/strategy/event_gate.py
   - EventGateResult dataclass with fields: symbol, multiplier, has_event, event_title, event_sentiment, event_severity
   - evaluate_event_gate() function that checks if news contradicts the signal:
     - HIGH severity contradiction → 0.10× (near-veto)
     - MEDIUM severity contradiction → 0.50× (caution)
     - Confirming or no event → 1.0× (neutral)
   - Only considers events from the last 4 hours
   - See BUILD_GUIDE.md Section 5 Step 2

3. Add event_gate_mult field to StrategySignal in quant_v2/contracts.py
   - Type: float | None = None
   - Validation: must be within [0, 1] if not None
   - Follow the EXACT same pattern as the existing symbol_hit_rate field
   - See BUILD_GUIDE.md Section 5 Step 3

4. Add _event_gate_multiplier() to quant_v2/portfolio/allocation.py
   - Follow the EXACT same pattern as _symbol_accuracy_multiplier()
   - Returns the event_gate_mult value directly (clamped to [0, 1]), or 1.0 if None
   - Add enable_event_gate parameter to allocate_signals() (default True)
   - Wire into the multiplication chain: signed_exposure *= sess_mult * regime_mult * accuracy_mult * event_mult
   - See BUILD_GUIDE.md Section 5 Step 4 and Appendix A

5. Integrate into V2SignalManager in quant_v2/telebot/signal_manager.py
   - Initialize CryptoPanicClient in __init__ (reads CRYPTOPANIC_API_KEY from env)
   - Fetch news ONCE at the start of _run_cycle() — cache for 15 minutes
   - Evaluate event gate in _attach_native_v2_fields() for BUY/SELL signals
   - Populate event_gate_mult in the StrategySignal constructor
   - If no API key is set, the client is None and event_gate_mult stays None (neutral)
   - See BUILD_GUIDE.md Section 5 Step 5

6. Add CRYPTOPANIC_API_KEY to docker-compose.yml environment sections
   - Add to both quant_telegram and quant_execution services
   - Format: - CRYPTOPANIC_API_KEY=${CRYPTOPANIC_API_KEY:-}
   - See BUILD_GUIDE.md Section 5 Step 6

7. Write tests in tests/quant_v2/test_event_gate.py
   - Test contradicting high-severity → 0.10×
   - Test contradicting medium-severity → 0.50×
   - Test confirming event → 1.0×
   - Test no events → 1.0×
   - Test old events (outside 4h window) are ignored
   - Test neutral sentiment events are ignored

8. Add allocation tests in tests/quant_v2/test_portfolio.py
   - Test allocate_signals() with event_gate_mult=0.10 dampens heavily
   - Test event_gate_mult=None is neutral (1.0×)
   - Test enable_event_gate=False ignores the field

9. Run full test suite: python -m pytest tests/quant_v2/ -q --tb=short
   All previous tests must still pass plus your new tests.

RULES:
- Do NOT modify any file in the DO NOT TOUCH list (see BUILD_GUIDE.md Section 3)
- Do NOT delete or weaken any existing test
- The new multiplier MUST follow the exact pattern in Appendix A of BUILD_GUIDE.md
- Mock all HTTP calls in tests — never call CryptoPanic for real in tests
- All imports must be at the top of the file
- After all tests pass, commit with message: "Phase 2: Event gate news awareness layer"
```

---

## Phase 3 Prompt

```
I need you to implement Phase 3 of a system upgrade for my crypto trading engine.
Phases 1 and 2 have already been completed and merged.

READ THE GUIDE FIRST:
Open and read the file BUILD_GUIDE.md in the project root. Read it fully before writing
any code. Pay special attention to:
- Section 1 (Repository Map) — understand where every file lives
- Section 3 (DO NOT TOUCH List) — these files must NOT be modified
- Section 6 (Phase 3) — your implementation spec
- Appendix A (Allocation Multiplier Pattern) — the pattern for new multipliers

WHAT TO BUILD (Phase 3 — Chronos Ensemble & Meta-Learner):

1. Install dependencies
   - Add "chronos-forecasting>=1.0" and "torch>=2.0" to pyproject.toml
   - Update Dockerfile to install torch CPU variant:
     RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
   - This will increase Docker image size significantly

2. Create quant_v2/models/chronos_wrapper.py
   - predict_next_bar_direction(close_series, prediction_length) function
   - Uses amazon/chronos-t5-small model (20M params, CPU-friendly)
   - Lazy-loads the pipeline on first call to avoid import overhead
   - Takes last 256 bars as context
   - Generates 50 forecast samples, computes prob_up as fraction of samples where
     final forecasted price > current price
   - Uncertainty from standard deviation of samples relative to current price
   - Returns (0.5, 1.0) if insufficient data (<32 bars)
   - See BUILD_GUIDE.md Section 6 Step 2

3. Create FullEnsemble class in quant_v2/models/ensemble.py
   - Note: HorizonEnsemble already exists in this file from Phase 1
   - Add FullEnsemble that combines LightGBM (HorizonEnsemble) + Chronos
   - Default weights: LightGBM 0.65, Chronos 0.35
   - predict() takes feature_row + close_series
   - Agreement bonus: if both models agree on direction, reduce uncertainty by 20%
   - Graceful fallback if either model fails
   - See BUILD_GUIDE.md Section 6 Step 3

4. Add model_agreement field to StrategySignal in quant_v2/contracts.py
   - Type: float | None = None (0.0 = disagree, 1.0 = full agree)
   - Validation: must be within [0, 1] if not None
   - Follow exact same pattern as existing fields

5. Add _model_agreement_multiplier() to quant_v2/portfolio/allocation.py
   - agreement >= 0.8 → 1.0× (strong agreement, full allocation)
   - agreement >= 0.5 → 0.85× (mild agreement)
   - agreement < 0.5 → 0.60× (disagreement, dampen)
   - None → 0.85× (neutral, no data)
   - Add enable_model_agreement parameter to allocate_signals() (default True)
   - Wire into chain: signed_exposure *= sess_mult * regime_mult * accuracy_mult * event_mult * agreement_mult
   - See BUILD_GUIDE.md Section 6 Step 6 and Appendix A

6. Wire FullEnsemble into V2SignalManager
   - Modify _predict_with_uncertainty() to accept close_series parameter
   - Try FullEnsemble first if available, fall back to HorizonEnsemble, then single model
   - Update the call site in _build_signal_payload to pass close_series
   - Compute model_agreement from LightGBM vs Chronos direction agreement
   - Populate model_agreement in StrategySignal
   - See BUILD_GUIDE.md Section 6 Step 4

7. Write tests
   - tests/quant_v2/test_chronos_wrapper.py — MOCK the Chronos pipeline, do NOT download
     the actual model. Test valid returns, insufficient data returns (0.5, 1.0)
   - tests/quant_v2/test_full_ensemble.py — test combined prediction, fallback when
     Chronos fails, fallback when LightGBM fails, agreement bonus
   - Add to tests/quant_v2/test_portfolio.py — test model_agreement multiplier bands

8. Run full test suite: python -m pytest tests/quant_v2/ -q --tb=short
   All previous tests must still pass plus your new tests.

CRITICAL RULES:
- Do NOT modify any file in the DO NOT TOUCH list (see BUILD_GUIDE.md Section 3)
- Do NOT delete or weaken any existing test
- ALL Chronos tests MUST use mocks — never download or load the actual model in tests
- The Chronos pipeline must be lazy-loaded (not at import time)
- If CHRONOS import fails at runtime (e.g. torch not installed), catch the error and
  fall back to LightGBM-only. The system must never crash because of Chronos.
- After all tests pass, commit with message: "Phase 3: Chronos ensemble with model agreement multiplier"
```

---

## Phase 4 Prompt

```
I need you to implement Phase 4 of a system upgrade for my crypto trading engine.
Phases 1, 2, and 3 have already been completed and merged.

READ THE GUIDE FIRST:
Open and read the file BUILD_GUIDE.md in the project root. Read it fully before writing
any code. Pay special attention to:
- Section 1 (Repository Map) — understand where every file lives
- Section 3 (DO NOT TOUCH List) — these files must NOT be modified
- Section 7 (Phase 4) — your implementation spec

WHAT TO BUILD (Phase 4 — Execution Upgrade: Limit Orders):

1. Add place_limit_order() to quant/data/binance_client.py
   - Parameters: symbol, side, quantity, price, time_in_force="GTC", reduce_only=False
   - Constructs a LIMIT order via the existing _signed_request() method
   - Uses existing _format_quantity() and _format_price() helpers for lot size compliance
   - See BUILD_GUIDE.md Section 7 Step 1

2. Add get_best_bid_ask() to quant/data/binance_client.py
   - Returns (best_bid, best_ask) tuple from /fapi/v1/ticker/bookTicker
   - Uses existing _public_request() method
   - See BUILD_GUIDE.md Section 7 Step 2

3. Update the LIVE execution adapter to use limit orders
   - For BUY: place limit at best bid price (join the bid)
   - For SELL: place limit at best ask price (join the ask)
   - Paper adapter is NOT modified — it continues to use instant fills
   - See BUILD_GUIDE.md Section 7 Step 3

4. Add partial fill handling
   - After 1 cycle (1 hour), check if any limit orders are partially filled
   - If partially filled: cancel the remaining quantity
   - Log the partial fill event for diagnostics
   - See BUILD_GUIDE.md Section 7 Step 4

5. Write tests
   - Test place_limit_order() constructs correct request parameters
   - Test get_best_bid_ask() parses response correctly
   - Test that BUY orders use bid price and SELL orders use ask price
   - Mock all Binance API calls — never make real HTTP requests in tests
   - See BUILD_GUIDE.md Section 7 Step 5

6. Run full test suite: python -m pytest tests/quant_v2/ -q --tb=short
   All previous tests must still pass plus your new tests.

RULES:
- Do NOT modify any file in the DO NOT TOUCH list (see BUILD_GUIDE.md Section 3)
- Do NOT modify the paper trading adapter — limit orders are ONLY for live execution
- Do NOT delete or weaken any existing test
- Limit order failures should fall back to market orders — never let an order fail silently
- After all tests pass, commit with message: "Phase 4: Limit order execution with partial fill handling"
```

---

## Deployment Prompt (use after each phase)

```
I need you to deploy the latest changes to my AWS EC2 instance.

READ THE GUIDE FIRST:
Open BUILD_GUIDE.md Section 9 (Deployment Workflow) for the exact steps.

DEPLOYMENT DETAILS:
- EC2 host: ubuntu@16.16.122.202
- SSH key: quant-key.pem (in project root)
- Remote project path: /home/ubuntu/quant_bot
- 3 containers: quant_telegram, quant_execution, quant_redis

STEPS:
1. Upload all changed files via SCP to their correct paths under /home/ubuntu/quant_bot/
   Example: scp -i quant-key.pem quant_v2/contracts.py ubuntu@16.16.122.202:/home/ubuntu/quant_bot/quant_v2/contracts.py
   Upload EACH file individually to its correct subdirectory. Do NOT flatten.

2. SSH in and rebuild containers:
   ssh -i quant-key.pem ubuntu@16.16.122.202 "cd /home/ubuntu/quant_bot && sudo docker-compose down && sudo docker-compose up -d --build"

3. Verify all 3 containers are running:
   ssh -i quant-key.pem ubuntu@16.16.122.202 "sudo docker ps"

4. Check logs for errors (must return 0):
   ssh -i quant-key.pem ubuntu@16.16.122.202 "sudo docker logs --tail 50 quant_telegram 2>&1 | grep -ciE 'error|exception|traceback'"

5. If containers are healthy, commit and push:
   git add -A
   git commit -m "<phase commit message>"
   git push origin main

If ANY errors appear in the logs, do NOT push. Show me the errors first.
```

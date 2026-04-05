# Hypothesis Research Engine — System Upgrade Build Guide

> **Purpose**: This document provides exact specifications for implementing four upgrade
> phases to the trading system. It is written so that any competent coding AI (Kimi 2.5,
> GPT Codex, etc.) can execute each phase without making architectural mistakes.
>
> **Golden rule**: The Telegram bot, risk stack, execution infrastructure, and existing
> allocation chain are proven production code. **Do not refactor, rename, or restructure
> them.** All upgrades are *additive* — new files, new fields, new multipliers wired into
> existing integration points.

---

## Table of Contents

1. [Repository Map & Key Files](#1-repository-map--key-files)
2. [Architecture Overview](#2-architecture-overview)
3. [DO NOT TOUCH List](#3-do-not-touch-list)
4. [Phase 1 — Feature Enrichment & Multi-Horizon Ensemble](#4-phase-1--feature-enrichment--multi-horizon-ensemble)
5. [Phase 2 — Event Gate (News Awareness Layer)](#5-phase-2--event-gate-news-awareness-layer)
6. [Phase 3 — Chronos Ensemble & Meta-Learner](#6-phase-3--chronos-ensemble--meta-learner)
7. [Phase 4 — Execution Upgrade (Limit Orders)](#7-phase-4--execution-upgrade-limit-orders)
8. [Testing Standards](#8-testing-standards)
9. [Deployment Workflow](#9-deployment-workflow)
10. [Appendix A — Existing Allocation Multiplier Pattern](#appendix-a--existing-allocation-multiplier-pattern)
11. [Appendix B — Existing Signal Flow (End-to-End)](#appendix-b--existing-signal-flow-end-to-end)

---

## 1. Repository Map & Key Files

```
hypothesis-research-engine/
├── quant/                              # Legacy v1 modules (still used by v2 at runtime)
│   ├── config.py                       # BinanceAPIConfig, ResearchConfig, PathConfig
│   ├── data/
│   │   └── binance_client.py           # BinanceClient — REST API for OHLCV, funding, OI
│   └── features/
│       ├── pipeline.py                 # build_features() — 78-feature whitelist pipeline
│       ├── momentum.py                 # ROC, momentum acceleration
│       ├── volatility.py               # ATR, Bollinger, Parkinson, vol-of-vol
│       ├── candle_geometry.py          # Body/wick ratios, consecutive direction
│       ├── trend.py                    # EMA slopes, cross distances, mean reversion
│       ├── volume.py                   # OBV, VWAP, volume z-score
│       ├── time_encoding.py            # Hour/DOW sin/cos encoding
│       ├── microstructure.py           # Amihud illiquidity, Kyle lambda
│       ├── cross_timeframe.py          # Multi-timeframe trend alignment
│       ├── order_flow.py               # Taker buy ratio, cumulative delta
│       ├── funding_rate.py             # Funding rate z-score, momentum, extremes
│       ├── open_interest.py            # OI ROC, z-score, price divergence
│       ├── liquidation.py              # Liquidation volumes, pressure, flags
│       └── crypto_session.py           # Asia/Europe/US session flags, funding timing
│
├── quant_v2/                           # Production v2 modules
│   ├── config.py                       # _DEFAULT_UNIVERSE (10 symbols), RuntimeProfile
│   ├── contracts.py                    # StrategySignal, ExecutionIntent, OrderPlan, etc.
│   ├── data/
│   │   └── multi_symbol_dataset.py     # fetch_symbol_dataset(), fetch_universe_dataset()
│   ├── models/
│   │   ├── trainer.py                  # TrainedModel dataclass, train(), save/load
│   │   └── predictor.py               # predict_proba(), predict_proba_with_uncertainty()
│   ├── portfolio/
│   │   ├── allocation.py              # allocate_signals() — THE allocation chain
│   │   └── risk_policy.py             # PortfolioRiskPolicy — caps enforcement
│   ├── execution/
│   │   ├── planner.py                 # PlannerConfig, build_execution_intents()
│   │   ├── service.py                 # RoutedExecutionService — session + routing
│   │   ├── main.py                    # Execution engine entry point (Redis consumer)
│   │   └── ...                        # adapters, reconciler, idempotency, WAL, watchdog
│   ├── strategy/
│   │   └── regime.py                  # classify_latest() — GMM regime classification
│   ├── monitoring/
│   │   ├── kill_switch.py             # MonitoringSnapshot, KillSwitchConfig
│   │   └── health_dashboard.py        # Health diagnostics
│   ├── telebot/
│   │   ├── signal_manager.py          # V2SignalManager — THE signal generation loop
│   │   └── symbol_scorecard.py        # SymbolScorecard — prediction accuracy tracker
│   └── research/                      # Offline validation tools
│
├── tests/quant_v2/                    # All v2 tests (225 passing as of latest)
│   ├── test_portfolio.py              # Allocation + risk policy tests
│   ├── test_symbol_scorecard.py       # Scorecard tests
│   ├── test_execution_service.py      # Execution routing tests
│   ├── test_execution_planner.py      # Planner tests
│   └── ...
│
├── docker-compose.yml                 # 3 containers: quant_telegram, quant_execution, quant_redis
├── Dockerfile                         # Multi-stage build with libgomp1
└── pyproject.toml                     # Dependencies
```

### Critical Files You Will Modify

| Phase | Files to MODIFY | Files to CREATE |
|-------|----------------|-----------------|
| 1 | `quant/features/pipeline.py`, `quant_v2/models/trainer.py`, `quant_v2/models/predictor.py`, `quant_v2/telebot/signal_manager.py` | `quant/features/cross_pair.py`, `quant/features/liquidation_proximity.py`, `quant_v2/research/retrain_pipeline.py` |
| 2 | `quant_v2/contracts.py`, `quant_v2/portfolio/allocation.py`, `quant_v2/telebot/signal_manager.py` | `quant_v2/data/news_client.py`, `quant_v2/strategy/event_gate.py` |
| 3 | `quant_v2/models/predictor.py`, `quant_v2/models/trainer.py`, `quant_v2/telebot/signal_manager.py` | `quant_v2/models/chronos_wrapper.py`, `quant_v2/models/ensemble.py` |
| 4 | `quant/data/binance_client.py`, `quant_v2/execution/planner.py` | — |

---

## 2. Architecture Overview

The signal flow at runtime is:

```
V2SignalManager._run_cycle()                     [signal_manager.py]
  │
  ├─ for each symbol in 10-symbol universe:
  │    ├─ Fetch 192 1h bars via BinanceClient     [binance_client.py]
  │    ├─ build_features(bars)                     [pipeline.py]
  │    ├─ classify_latest(close, funding_zscore)   [regime.py]
  │    ├─ _predict_with_uncertainty(feature_row)   [predictor.py]
  │    ├─ Determine BUY/SELL/HOLD/DRIFT_ALERT
  │    ├─ _attach_native_v2_fields() → StrategySignal  [contracts.py]
  │    ├─ Record prediction in SymbolScorecard     [symbol_scorecard.py]
  │    └─ _emit(payload) → on_signal callback
  │
  └─ Evaluate pending scorecard predictions
```

The on_signal callback (in `quant/telebot/main.py`) forwards the signal to:

```
RoutedExecutionService.route_signals()           [service.py]
  ├─ build_execution_intents(signals)             [planner.py]
  │    ├─ allocate_signals()                       [allocation.py]
  │    │    └─ Kelly × session_mult × regime_mult × accuracy_mult
  │    └─ PortfolioRiskPolicy.apply()              [risk_policy.py]
  ├─ intents_to_order_plans()                      [planner.py]
  └─ adapter.execute(order_plans)                  [adapters.py]
```

### The Allocation Multiplier Chain

This is the central pattern you will extend. Currently in `allocation.py`:

```python
signed_exposure = kelly_scale * adjusted_edge
signed_exposure *= sess_mult * regime_mult * accuracy_mult
```

Each multiplier is:
- Computed by a pure function (`_session_multiplier`, `_regime_multiplier`, `_symbol_accuracy_multiplier`)
- Driven by a field on `StrategySignal` (`session_hour_utc`, `momentum_bias`, `symbol_hit_rate`)
- Toggled by a boolean parameter on `allocate_signals()` (`enable_session_filter`, `enable_regime_bias`, `enable_symbol_accuracy`)

**Every new dampening factor you add must follow this exact same pattern.**

---

## 3. DO NOT TOUCH List

These files/modules must NOT be modified unless the upgrade spec explicitly says so:

| File/Module | Reason |
|-------------|--------|
| `quant_v2/execution/service.py` | Production execution routing, WAL, kill-switch integration |
| `quant_v2/execution/main.py` | Redis consumer, watchdog, stale-feed circuit breaker |
| `quant_v2/execution/reconciler.py` | Position reconciliation |
| `quant_v2/execution/adapters.py` | Paper/live adapter implementations |
| `quant_v2/execution/state_wal.py` | Write-ahead log persistence |
| `quant_v2/monitoring/kill_switch.py` | Kill-switch logic |
| `quant_v2/telebot/symbol_scorecard.py` | Recently deployed, working correctly |
| `quant_v2/portfolio/risk_policy.py` | Portfolio caps enforcement |
| `quant/telebot/main.py` | Telegram bot command handlers |
| `docker-compose.yml` | Container orchestration |
| `Dockerfile` | Build configuration |
| All existing tests | Never delete or weaken existing tests |

---

## 4. Phase 1 — Feature Enrichment & Multi-Horizon Ensemble

### 4A. Cross-Pair Features

**Goal**: Each symbol's prediction should know what BTC and the broader market are doing.

#### Step 1: Create `quant/features/cross_pair.py`

```python
"""Cross-pair correlation features for multi-symbol awareness."""

from __future__ import annotations
import pandas as pd


def compute(df: pd.DataFrame, btc_returns: pd.Series | None = None) -> pd.DataFrame:
    """Add cross-pair features to a single-symbol DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Single-symbol OHLCV DataFrame with 'close' column.
    btc_returns : pd.Series | None
        Pre-computed BTC hourly returns aligned to df's index.
        If None, features are filled with 0.0 (neutral).

    Returns
    -------
    pd.DataFrame
        Original df with new columns appended.
    """
    result = df.copy()
    close = pd.to_numeric(result["close"], errors="coerce")
    symbol_returns = close.pct_change()

    if btc_returns is not None and not btc_returns.empty:
        # Align BTC returns to this symbol's index
        btc_aligned = btc_returns.reindex(result.index, method="ffill").fillna(0.0)

        # Feature 1: BTC return over last 4 bars
        result["btc_return_4h"] = btc_aligned.rolling(4).sum().fillna(0.0)

        # Feature 2: Symbol vs BTC divergence (symbol_return - btc_return, rolling 4h)
        divergence = symbol_returns - btc_aligned
        result["btc_divergence_4h"] = divergence.rolling(4).sum().fillna(0.0)

        # Feature 3: Rolling correlation with BTC (24h window)
        result["btc_correlation_24h"] = (
            symbol_returns.rolling(24).corr(btc_aligned).fillna(0.0)
        )
    else:
        result["btc_return_4h"] = 0.0
        result["btc_divergence_4h"] = 0.0
        result["btc_correlation_24h"] = 0.0

    # Feature 4: Symbol volatility relative to its own 120h baseline
    vol_20 = symbol_returns.rolling(20).std().fillna(0.0)
    vol_120 = symbol_returns.rolling(120).std().fillna(1e-8)
    result["relative_vol_ratio"] = (vol_20 / vol_120.clip(lower=1e-8)).fillna(1.0)

    return result
```

**Interface contract**:
- Input: single-symbol DataFrame with `close` column + optional `btc_returns` Series
- Output: same DataFrame with 4 new columns appended
- All new columns must have NO NaN after the warmup period (use `.fillna(0.0)`)

#### Step 2: Create `quant/features/liquidation_proximity.py`

```python
"""Liquidation proximity features — how close are leveraged positions to wipeout."""

from __future__ import annotations
import pandas as pd


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Add liquidation proximity features.

    Requires columns: close, open_interest (or open_interest_value),
    funding_rate (optional).
    """
    result = df.copy()
    close = pd.to_numeric(result["close"], errors="coerce")

    oi = None
    if "open_interest_value" in result.columns:
        oi = pd.to_numeric(result["open_interest_value"], errors="coerce")
    elif "open_interest" in result.columns:
        oi = pd.to_numeric(result["open_interest"], errors="coerce")

    funding = (
        pd.to_numeric(result["funding_rate"], errors="coerce")
        if "funding_rate" in result.columns
        else pd.Series(0.0, index=result.index)
    )

    # Feature 1: OI-weighted funding (crowded trade pressure)
    # High OI + extreme funding = crowded trade about to unwind
    if oi is not None:
        oi_norm = oi / oi.rolling(72).mean().clip(lower=1e-8)
        result["oi_funding_pressure"] = (oi_norm * funding.abs()).fillna(0.0)
    else:
        result["oi_funding_pressure"] = 0.0

    # Feature 2: Price distance from recent extremes (proxy for liquidation clusters)
    high_24 = close.rolling(24).max()
    low_24 = close.rolling(24).min()
    range_24 = (high_24 - low_24).clip(lower=1e-8)
    result["price_position_24h"] = ((close - low_24) / range_24).fillna(0.5)

    # Feature 3: Liquidation cascade risk — sharp OI drop + price move
    if oi is not None:
        oi_change_4h = oi.pct_change(4).fillna(0.0)
        price_change_4h = close.pct_change(4).fillna(0.0)
        # Large OI drop + large price move = liquidation cascade happened
        result["liquidation_cascade_4h"] = (
            (oi_change_4h.abs() * price_change_4h.abs()).fillna(0.0)
        )
    else:
        result["liquidation_cascade_4h"] = 0.0

    return result
```

#### Step 3: Register new features in the pipeline

**File**: `quant/features/pipeline.py`

1. Add imports at the top (after existing imports):
```python
from quant.features import cross_pair, liquidation_proximity  # Phase 1 upgrade
```

2. The `cross_pair` module needs BTC returns passed in. You have two options:

**Option A (simpler, recommended)**: Add `cross_pair` to `_CRYPTO_MODULES` but modify its
`compute()` signature to accept only `df` and internally check for a `_btc_returns` column:

Change `cross_pair.compute()` to look for a pre-injected `_btc_returns` column in the
DataFrame rather than a separate argument. The caller (`V2SignalManager._build_featured_frame`)
will inject BTC returns before calling `build_features()`.

**Option B**: Add `cross_pair` as a special-case call in `build_features()` after the main
module loop, passing `btc_returns` separately.

**Recommendation**: Use Option A. Modify `cross_pair.compute(df)` to:
```python
def compute(df: pd.DataFrame) -> pd.DataFrame:
    btc_returns = df.get("_btc_returns")  # injected by caller, or None
    # ... rest of logic, using btc_returns if available
```

3. Add `liquidation_proximity` to `_CRYPTO_MODULES`:
```python
_CRYPTO_MODULES = [
    order_flow,
    funding_rate,
    open_interest,
    liquidation,
    liquidation_proximity,   # NEW — Phase 1
    cross_pair,              # NEW — Phase 1 (must be last, needs other features)
    crypto_session,
]
```

4. Add all new feature names to `_FEATURE_WHITELIST`:
```python
# Phase 1: Cross-pair features
"btc_return_4h",
"btc_divergence_4h",
"btc_correlation_24h",
"relative_vol_ratio",
# Phase 1: Liquidation proximity features
"oi_funding_pressure",
"price_position_24h",
"liquidation_cascade_4h",
```

5. Update `max_features` in `quant/config.py` `ResearchConfig`:
```python
max_features: int = 97  # was 90, added 7 new features
```

#### Step 4: Inject BTC returns into the feature pipeline

**File**: `quant_v2/telebot/signal_manager.py`

In `_run_cycle()`, BTC bars are already fetched as part of the symbol loop. Cache the BTC
close returns and pass them to `_build_featured_frame()`.

1. At the start of the symbol loop (around line 440), add:
```python
btc_returns: pd.Series | None = None
```

2. Inside the loop, after fetching bars for BTCUSDT, compute and cache:
```python
if symbol == "BTCUSDT":
    btc_close = pd.to_numeric(bars["close"], errors="coerce").dropna()
    btc_returns = btc_close.pct_change()
```

3. Modify `_build_featured_frame()` to accept and inject `btc_returns`:
```python
def _build_featured_frame(
    self, bars: pd.DataFrame, btc_returns: pd.Series | None = None,
) -> pd.DataFrame | None:
    # ... existing frame prep ...
    if btc_returns is not None:
        frame["_btc_returns"] = btc_returns.reindex(frame.index, method="ffill").fillna(0.0)
    featured = build_features(frame)
    return featured if not featured.empty else None
```

4. Update calls to `_build_featured_frame` to pass `btc_returns`.

**IMPORTANT**: BTCUSDT must be processed first in the symbol loop. Either:
- Sort `self.symbols` so BTCUSDT is first, OR
- Do a pre-pass to fetch BTC bars before the main loop

Recommended: sort symbols so BTCUSDT is first. In `__init__`:
```python
# Ensure BTCUSDT is processed first (needed for cross-pair features)
symbols_list = list(symbols)
if "BTCUSDT" in symbols_list:
    symbols_list.remove("BTCUSDT")
    symbols_list.insert(0, "BTCUSDT")
self.symbols = tuple(symbols_list)
```

#### Step 5: Retrain the model

After adding new features, the model must be retrained because `TrainedModel.feature_names`
must include the 7 new columns. The existing training pipeline in `quant_v2/models/trainer.py`
does NOT need modification — it automatically uses whatever feature columns are present
in the training DataFrame. Just ensure:

1. Training data is generated via `build_features()` (which now includes the new modules)
2. The new model is saved via `save_model()` and registered in the model registry
3. The old model is kept as a rollback target via the existing registry pointer mechanism

### 4B. Multi-Horizon Ensemble

**Goal**: Train 3 LightGBM models (2h, 4h, 8h horizons) and combine their probabilities.

#### Step 1: Modify training to produce multiple horizon models

The existing `trainer.py` `train()` function already accepts a `horizon` parameter.
No modification needed — just call it 3 times with different horizons and label sets.

Create `quant_v2/research/retrain_pipeline.py`:

```python
"""Automated walk-forward retrain pipeline for multi-horizon ensemble."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quant.data.binance_client import BinanceClient
from quant.features.pipeline import build_features, get_feature_columns
from quant_v2.config import default_universe_symbols
from quant_v2.data.multi_symbol_dataset import fetch_symbol_dataset
from quant_v2.models.trainer import train, save_model

logger = logging.getLogger(__name__)

HORIZONS = (2, 4, 8)
TRAIN_MONTHS = 6
LABEL_COL_TEMPLATE = "label_{horizon}h"


def _build_labels(df, horizon: int):
    """Binary label: did price go up over the next `horizon` bars?"""
    import pandas as pd
    close = pd.to_numeric(df["close"], errors="coerce")
    future_return = close.shift(-horizon) / close - 1.0
    return (future_return > 0).astype(int)


def run_retrain(
    output_dir: Path,
    symbol: str = "BTCUSDT",
    train_months: int = TRAIN_MONTHS,
) -> dict[int, Path]:
    """Retrain models for all horizons. Returns {horizon: model_path}."""

    client = BinanceClient()
    date_to = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=train_months * 30)

    logger.info("Fetching training data for %s: %s to %s", symbol, date_from, date_to)
    raw = fetch_symbol_dataset(
        symbol, date_from=date_from, date_to=date_to, client=client,
    )
    featured = build_features(raw)
    feature_cols = get_feature_columns(featured)

    model_paths: dict[int, Path] = {}
    for horizon in HORIZONS:
        labels = _build_labels(featured, horizon)
        # Drop rows without labels (last `horizon` rows)
        mask = labels.notna()
        X = featured.loc[mask, feature_cols]
        y = labels.loc[mask]

        if len(X) < 500:
            logger.warning("Insufficient data for horizon=%dh (%d rows), skipping", horizon, len(X))
            continue

        model = train(X, y, horizon=horizon)
        path = output_dir / f"model_{horizon}m.pkl"
        save_model(model, path)
        model_paths[horizon] = path
        logger.info("Trained horizon=%dh: %d samples, saved to %s", horizon, len(X), path)

    return model_paths
```

**Usage**: Run manually or on a cron/scheduler:
```bash
python -c "
from pathlib import Path
from quant_v2.research.retrain_pipeline import run_retrain
run_retrain(Path('models/production_v2/registry/latest/artifacts'))
"
```

#### Step 2: Create `quant_v2/models/ensemble.py`

```python
"""Multi-horizon model ensemble with weighted probability combination."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from quant_v2.models.trainer import TrainedModel, load_model
from quant_v2.models.predictor import predict_proba_with_uncertainty

logger = logging.getLogger(__name__)

# Decay weights: shorter horizon gets more weight
DEFAULT_HORIZON_WEIGHTS = {2: 0.45, 4: 0.35, 8: 0.20}


class HorizonEnsemble:
    """Combine multiple horizon models into a single probability + uncertainty."""

    def __init__(
        self,
        models: dict[int, TrainedModel],
        weights: dict[int, float] | None = None,
    ) -> None:
        self.models = models
        self.weights = weights or DEFAULT_HORIZON_WEIGHTS
        # Normalize weights to sum to 1.0
        total = sum(self.weights.get(h, 0.0) for h in self.models)
        if total > 0:
            self.weights = {h: self.weights.get(h, 0.0) / total for h in self.models}

    @classmethod
    def from_directory(cls, artifact_dir: Path) -> HorizonEnsemble | None:
        """Load all horizon models from a registry artifact directory."""
        models: dict[int, TrainedModel] = {}
        for horizon in (2, 4, 8):
            for suffix in (f"model_{horizon}m.pkl", f"model_{horizon}m.joblib"):
                path = artifact_dir / suffix
                if path.exists():
                    try:
                        models[horizon] = load_model(path)
                    except Exception as e:
                        logger.warning("Failed to load horizon=%d model: %s", horizon, e)
                    break
        if not models:
            return None
        return cls(models)

    def predict(self, X: pd.DataFrame) -> tuple[float, float]:
        """Return weighted ensemble (probability, uncertainty) for one row.

        Falls back gracefully if some horizon models are missing features.
        """
        probas: list[float] = []
        uncertainties: list[float] = []
        weights_used: list[float] = []

        for horizon, model in self.models.items():
            try:
                # Align features: fill missing with 0.0
                missing = set(model.feature_names) - set(X.columns)
                X_aligned = X.copy()
                for col in missing:
                    X_aligned[col] = 0.0
                X_ordered = X_aligned[model.feature_names]

                p, u = predict_proba_with_uncertainty(model, X_ordered)
                probas.append(float(p[0]))
                uncertainties.append(float(u[0]))
                weights_used.append(self.weights.get(horizon, 0.0))
            except Exception as e:
                logger.warning("Horizon=%d prediction failed: %s", horizon, e)
                continue

        if not probas:
            return 0.5, 1.0  # total uncertainty if all models failed

        w = np.array(weights_used)
        w = w / w.sum()
        ensemble_proba = float(np.dot(w, probas))
        ensemble_uncertainty = float(np.dot(w, uncertainties))

        # Agreement bonus: if all models agree on direction, reduce uncertainty
        directions = [1 if p > 0.5 else 0 for p in probas]
        if len(set(directions)) == 1 and len(directions) > 1:
            ensemble_uncertainty *= 0.80  # 20% uncertainty reduction for agreement

        return (
            float(np.clip(ensemble_proba, 0.0, 1.0)),
            float(np.clip(ensemble_uncertainty, 0.0, 1.0)),
        )

    @property
    def horizon_count(self) -> int:
        return len(self.models)
```

#### Step 3: Wire ensemble into `V2SignalManager._predict_with_uncertainty()`

**File**: `quant_v2/telebot/signal_manager.py`

Modify `_predict_with_uncertainty()` (around line 679):

```python
def _predict_with_uncertainty(self, feature_row: pd.DataFrame) -> tuple[float, float]:
    """Run model inference — ensemble if available, single model fallback."""

    # Try ensemble first
    if self.horizon_ensemble is not None:
        return self.horizon_ensemble.predict(feature_row)

    # Single model fallback (existing code)
    model = self.active_model
    if model is None:
        raise RuntimeError("No active model loaded")

    if hasattr(model, "primary_model"):
        from quant_v2.models.predictor import predict_proba_with_uncertainty
        proba_arr, uncertainty_arr = predict_proba_with_uncertainty(model, feature_row)
        return float(proba_arr[0]), float(uncertainty_arr[0])

    if hasattr(model, "raw_model"):
        from quant.models.predictor import predict_proba as legacy_predict_proba
        proba_arr = legacy_predict_proba(model, feature_row)
        proba_up = float(proba_arr[0])
        uncertainty = float(1.0 - abs(2.0 * proba_up - 1.0))
        return proba_up, uncertainty

    raise TypeError(f"Unsupported model type for inference: {type(model)!r}")
```

Add to `__init__`:
```python
self.horizon_ensemble: HorizonEnsemble | None = None
```

In the model loading section of `_run_cycle()` (around line 425), after loading `self.active_model`,
attempt to load the ensemble:
```python
from quant_v2.models.ensemble import HorizonEnsemble

if active_pointer:
    artifact_dir = Path(active_pointer.artifact_dir)
    ensemble = HorizonEnsemble.from_directory(artifact_dir)
    if ensemble is not None and ensemble.horizon_count > 1:
        self.horizon_ensemble = ensemble
        logger.info("Loaded %d-horizon ensemble", ensemble.horizon_count)
    else:
        self.horizon_ensemble = None
```

**IMPORTANT**: If only the single 4h model exists (as today), `HorizonEnsemble.from_directory()`
returns an ensemble with 1 model, which behaves identically to the current single-model
path. This ensures backward compatibility.

### 4C. Tests for Phase 1

Create `tests/quant_v2/test_cross_pair_features.py`:
- Test `cross_pair.compute()` with and without BTC returns
- Assert all 4 new columns are present and have no NaN after warmup
- Assert neutral values (0.0) when btc_returns is None

Create `tests/quant_v2/test_liquidation_proximity.py`:
- Test `liquidation_proximity.compute()` with and without OI/funding columns
- Assert 3 new columns present with no NaN after warmup

Create `tests/quant_v2/test_horizon_ensemble.py`:
- Test `HorizonEnsemble.predict()` with multiple mock models
- Test agreement bonus reduces uncertainty
- Test graceful fallback when one model fails
- Test `from_directory()` with empty directory returns None
- Test single-model ensemble behaves identically to direct prediction

**Run after Phase 1**:
```bash
python -m pytest tests/quant_v2/ -q --tb=short
```
All existing 225 tests must still pass, plus the new Phase 1 tests.

---

## 5. Phase 2 — Event Gate (News Awareness Layer)

### Goal

Detect high-impact news events for traded symbols and apply a dampening/veto multiplier
to the allocation chain.

### Step 1: Create `quant_v2/data/news_client.py`

```python
"""Lightweight news/event fetcher for traded symbols."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

# CryptoPanic API — free tier: 5 requests/minute
# Docs: https://cryptopanic.com/developers/api/
_CRYPTOPANIC_BASE = "https://cryptopanic.com/api/free/v1/posts/"


@dataclass(frozen=True)
class NewsEvent:
    """A single news event relevant to a symbol."""

    symbol: str
    title: str
    source: str
    published_at: datetime
    sentiment: str          # "bullish", "bearish", "neutral"
    severity: str           # "low", "medium", "high"
    url: str = ""


class CryptoPanicClient:
    """Fetch recent crypto news from CryptoPanic API.

    Requires a free API key from https://cryptopanic.com/developers/api/
    Set via environment variable CRYPTOPANIC_API_KEY.
    """

    def __init__(self, api_key: str, timeout: int = 10) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def fetch_recent(
        self,
        symbols: list[str] | None = None,
        max_results: int = 20,
    ) -> list[NewsEvent]:
        """Fetch recent news, optionally filtered by symbol tickers.

        Parameters
        ----------
        symbols : list[str] | None
            E.g. ["BTC", "ETH"]. Pass None for all crypto news.
        max_results : int
            Maximum events to return.

        Returns
        -------
        list[NewsEvent]
            Parsed events sorted by recency.
        """
        params: dict[str, Any] = {
            "auth_token": self._api_key,
            "kind": "news",
            "filter": "important",  # Only important news
            "public": "true",
        }
        if symbols:
            # CryptoPanic uses base tickers (BTC, not BTCUSDT)
            params["currencies"] = ",".join(symbols)

        try:
            resp = requests.get(
                _CRYPTOPANIC_BASE, params=params, timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("CryptoPanic fetch failed: %s", e)
            return []

        events: list[NewsEvent] = []
        for item in (data.get("results") or [])[:max_results]:
            # Map CryptoPanic votes to sentiment
            votes = item.get("votes", {})
            positive = votes.get("positive", 0) + votes.get("liked", 0)
            negative = votes.get("negative", 0) + votes.get("disliked", 0)

            if positive > negative * 2:
                sentiment = "bullish"
            elif negative > positive * 2:
                sentiment = "bearish"
            else:
                sentiment = "neutral"

            # Severity based on vote count
            total_votes = positive + negative
            if total_votes >= 20:
                severity = "high"
            elif total_votes >= 5:
                severity = "medium"
            else:
                severity = "low"

            # Map currencies to USDT pairs
            currencies = item.get("currencies") or []
            for curr in currencies:
                code = curr.get("code", "").upper()
                usdt_symbol = f"{code}USDT"
                events.append(NewsEvent(
                    symbol=usdt_symbol,
                    title=item.get("title", ""),
                    source=item.get("source", {}).get("title", "unknown"),
                    published_at=datetime.fromisoformat(
                        item.get("published_at", "").replace("Z", "+00:00")
                    ) if item.get("published_at") else datetime.now(timezone.utc),
                    sentiment=sentiment,
                    severity=severity,
                    url=item.get("url", ""),
                ))

        return events


def symbol_to_base_ticker(usdt_symbol: str) -> str:
    """Convert 'BTCUSDT' to 'BTC'."""
    return usdt_symbol.replace("USDT", "").replace("BUSD", "")
```

**Environment variable needed**: `CRYPTOPANIC_API_KEY`
- Free tier at https://cryptopanic.com/developers/api/
- Add to `.env` file and `docker-compose.yml` environment section

### Step 2: Create `quant_v2/strategy/event_gate.py`

```python
"""Event gate — dampens or vetoes signals based on detected news events."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from quant_v2.data.news_client import NewsEvent

logger = logging.getLogger(__name__)

# How recent must news be to affect trading (in hours)
_NEWS_RELEVANCE_WINDOW_HOURS = 4

# Multiplier bands
_EVENT_VETO_MULT = 0.10         # Near-complete dampening for contradicting high-severity
_EVENT_CAUTION_MULT = 0.50      # Moderate dampening for contradicting medium-severity
_EVENT_NEUTRAL_MULT = 1.0       # No effect
_EVENT_CONFIRM_BOOST = 1.0      # No boost (we don't increase on confirming news)


@dataclass(frozen=True)
class EventGateResult:
    """Result of event gate evaluation for a single symbol."""

    symbol: str
    multiplier: float               # Allocation multiplier [0.10, 1.0]
    has_event: bool
    event_title: str = ""
    event_sentiment: str = "neutral"
    event_severity: str = "low"


def evaluate_event_gate(
    symbol: str,
    signal_direction: str,
    events: list[NewsEvent],
    now: datetime | None = None,
) -> EventGateResult:
    """Evaluate whether news events should dampen a signal.

    Logic:
    - If a HIGH severity event CONTRADICTS the signal direction → 0.10× (near-veto)
    - If a MEDIUM severity event CONTRADICTS → 0.50× (caution)
    - If event CONFIRMS signal direction → 1.0× (no boost, just pass-through)
    - If no relevant events → 1.0× (neutral)

    A "bearish" event contradicts a "BUY" signal.
    A "bullish" event contradicts a "SELL" signal.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    cutoff = now - timedelta(hours=_NEWS_RELEVANCE_WINDOW_HOURS)

    # Filter to this symbol's recent events
    relevant = [
        e for e in events
        if e.symbol == symbol
        and e.published_at >= cutoff
        and e.sentiment != "neutral"
    ]

    if not relevant:
        return EventGateResult(
            symbol=symbol, multiplier=_EVENT_NEUTRAL_MULT, has_event=False,
        )

    # Take the highest-severity event
    severity_order = {"high": 3, "medium": 2, "low": 1}
    relevant.sort(key=lambda e: severity_order.get(e.severity, 0), reverse=True)
    top_event = relevant[0]

    # Check contradiction
    contradicts = (
        (signal_direction == "BUY" and top_event.sentiment == "bearish")
        or (signal_direction == "SELL" and top_event.sentiment == "bullish")
    )

    if contradicts:
        if top_event.severity == "high":
            mult = _EVENT_VETO_MULT
        elif top_event.severity == "medium":
            mult = _EVENT_CAUTION_MULT
        else:
            mult = _EVENT_NEUTRAL_MULT
        logger.info(
            "Event gate: %s %s contradicted by %s news '%s' → mult=%.2f",
            signal_direction, symbol, top_event.severity, top_event.title[:60], mult,
        )
    else:
        mult = _EVENT_CONFIRM_BOOST

    return EventGateResult(
        symbol=symbol,
        multiplier=mult,
        has_event=True,
        event_title=top_event.title,
        event_sentiment=top_event.sentiment,
        event_severity=top_event.severity,
    )
```

### Step 3: Add `event_gate_mult` field to `StrategySignal`

**File**: `quant_v2/contracts.py`

Add field (after `symbol_hit_rate`):
```python
event_gate_mult: float | None = None
```

Add validation in `__post_init__`:
```python
if self.event_gate_mult is not None and not 0.0 <= self.event_gate_mult <= 1.0:
    raise ValueError("event_gate_mult must be within [0, 1]")
```

### Step 4: Add `_event_gate_multiplier()` to allocation chain

**File**: `quant_v2/portfolio/allocation.py`

Follow the **exact same pattern** as the existing multipliers:

1. Add constants at top:
```python
# ---------------------------------------------------------------------------
# Event gate dampening
# ---------------------------------------------------------------------------
_EVENT_DEFAULT_MULT: float = 1.0  # no event data → neutral
```

2. Add the multiplier function:
```python
def _event_gate_multiplier(event_gate_mult: float | None) -> float:
    """Return the event-gate multiplier, or 1.0 if no event data."""
    if event_gate_mult is None:
        return _EVENT_DEFAULT_MULT
    return max(0.0, min(event_gate_mult, 1.0))
```

3. Add parameter to `allocate_signals()`:
```python
def allocate_signals(
    signals: Iterable[StrategySignal],
    *,
    ...existing params...
    enable_event_gate: bool = True,    # NEW
) -> AllocationDecision:
```

4. Wire into the multiplication chain (after accuracy_mult):
```python
        # --- Event gate ---
        event_mult = 1.0
        if enable_event_gate:
            event_mult = _event_gate_multiplier(signal.event_gate_mult)

        signed_exposure *= sess_mult * regime_mult * accuracy_mult * event_mult
```

### Step 5: Integrate into `V2SignalManager`

**File**: `quant_v2/telebot/signal_manager.py`

1. In `__init__`, initialize the news client:
```python
import os
from quant_v2.data.news_client import CryptoPanicClient, symbol_to_base_ticker

api_key = os.getenv("CRYPTOPANIC_API_KEY", "")
self.news_client: CryptoPanicClient | None = (
    CryptoPanicClient(api_key) if api_key else None
)
self._cached_events: list = []
self._events_fetched_at: datetime | None = None
```

2. In `_run_cycle()`, fetch news ONCE at the start of each cycle (before the symbol loop):
```python
# --- Fetch news events (once per cycle, cached for 15 min) ---
now = datetime.now(timezone.utc)
if (
    self.news_client is not None
    and (
        self._events_fetched_at is None
        or (now - self._events_fetched_at).total_seconds() > 900
    )
):
    base_tickers = [symbol_to_base_ticker(s) for s in self.symbols]
    self._cached_events = self.news_client.fetch_recent(symbols=base_tickers)
    self._events_fetched_at = now
    if self._cached_events:
        logger.info("Event gate: fetched %d news events", len(self._cached_events))
```

3. In `_attach_native_v2_fields()`, evaluate the event gate and populate the field:
```python
from quant_v2.strategy.event_gate import evaluate_event_gate

# --- Event gate evaluation ---
event_gate_mult: float | None = None
if self._cached_events and signal_type in ("BUY", "SELL"):
    gate_result = evaluate_event_gate(
        symbol=symbol,
        signal_direction=signal_type,
        events=self._cached_events,
    )
    if gate_result.has_event:
        event_gate_mult = gate_result.multiplier

# Then pass event_gate_mult= to StrategySignal constructor
```

### Step 6: Environment configuration

**File**: `docker-compose.yml`

Add to both `quant_telegram` and `quant_execution` service environment sections:
```yaml
- CRYPTOPANIC_API_KEY=${CRYPTOPANIC_API_KEY:-}
```

**File**: `.env`
```
CRYPTOPANIC_API_KEY=your_key_here
```

### Step 7: Tests for Phase 2

Create `tests/quant_v2/test_event_gate.py`:
- Test `evaluate_event_gate()` with contradicting high-severity → 0.10×
- Test contradicting medium-severity → 0.50×
- Test confirming event → 1.0×
- Test no events → 1.0×
- Test old events (outside 4h window) are ignored
- Test neutral sentiment events are ignored

Add to `tests/quant_v2/test_portfolio.py`:
- Test `allocate_signals()` with `event_gate_mult=0.10` dampens heavily
- Test `event_gate_mult=None` is neutral (1.0×)
- Test `enable_event_gate=False` ignores the field

**Run**: `python -m pytest tests/quant_v2/ -q --tb=short`

---

## 6. Phase 3 — Chronos Ensemble & Meta-Learner

### Goal

Add Amazon Chronos as a second model family. Combine with LightGBM via a meta-learner.

### Step 1: Install Chronos

Add to `pyproject.toml` dependencies:
```toml
"chronos-forecasting>=1.0",
"torch>=2.0",
```

**WARNING**: Chronos requires PyTorch. This will significantly increase the Docker image size.
Consider adding a build stage in the Dockerfile for torch installation, or using the
`chronos-forecasting[cpu]` variant to avoid GPU dependencies.

Update `Dockerfile` to install torch CPU:
```dockerfile
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
```

### Step 2: Create `quant_v2/models/chronos_wrapper.py`

```python
"""Chronos time-series foundation model wrapper for next-bar prediction."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
import torch

logger = logging.getLogger(__name__)

# Lazy-load to avoid import overhead when Chronos is not used
_pipeline = None


def _get_pipeline():
    """Lazy-load the Chronos pipeline (downloads model on first use)."""
    global _pipeline
    if _pipeline is None:
        from chronos import ChronosPipeline
        _pipeline = ChronosPipeline.from_pretrained(
            "amazon/chronos-t5-small",     # 20M params, fast inference
            device_map="cpu",
            torch_dtype=torch.float32,
        )
    return _pipeline


def predict_next_bar_direction(
    close_series: pd.Series,
    prediction_length: int = 4,
) -> tuple[float, float]:
    """Predict probability of price going up over next `prediction_length` bars.

    Parameters
    ----------
    close_series : pd.Series
        Historical close prices (at least 64 bars recommended).
    prediction_length : int
        Number of bars to forecast ahead.

    Returns
    -------
    tuple[float, float]
        (probability_up, uncertainty) both in [0.0, 1.0]
    """
    if len(close_series) < 32:
        return 0.5, 1.0  # insufficient data

    pipeline = _get_pipeline()

    # Chronos expects a torch tensor of shape (1, context_length)
    context = torch.tensor(
        close_series.values[-256:],  # Use last 256 bars as context
        dtype=torch.float32,
    ).unsqueeze(0)

    # Generate probabilistic forecast (multiple samples)
    with torch.no_grad():
        forecast = pipeline.predict(
            context,
            prediction_length=prediction_length,
            num_samples=50,
        )
    # forecast shape: (1, num_samples, prediction_length)
    samples = forecast[0].numpy()  # (num_samples, prediction_length)

    # Direction: compare final forecasted price to current price
    current_price = float(close_series.iloc[-1])
    final_prices = samples[:, -1]  # last bar of each sample
    prob_up = float(np.mean(final_prices > current_price))

    # Uncertainty from spread of samples
    std_ratio = float(np.std(final_prices) / max(abs(current_price), 1e-8))
    uncertainty = float(np.clip(std_ratio * 10.0, 0.0, 1.0))  # Scale to [0,1]

    return (
        float(np.clip(prob_up, 0.0, 1.0)),
        float(np.clip(uncertainty, 0.0, 1.0)),
    )
```

### Step 3: Add Chronos to the ensemble

Modify `quant_v2/models/ensemble.py` to include a Chronos prediction path:

```python
# Add to HorizonEnsemble or create a new FullEnsemble class:

class FullEnsemble:
    """Combines LightGBM horizon ensemble + Chronos time-series model."""

    def __init__(
        self,
        lgbm_ensemble: HorizonEnsemble | None = None,
        enable_chronos: bool = True,
        lgbm_weight: float = 0.65,
        chronos_weight: float = 0.35,
    ) -> None:
        self.lgbm_ensemble = lgbm_ensemble
        self.enable_chronos = enable_chronos
        self._lgbm_weight = lgbm_weight
        self._chronos_weight = chronos_weight

    def predict(
        self,
        feature_row: pd.DataFrame,
        close_series: pd.Series,
        prediction_length: int = 4,
    ) -> tuple[float, float]:
        """Combined prediction from LightGBM ensemble + Chronos."""

        probas: list[float] = []
        uncertainties: list[float] = []
        weights: list[float] = []

        # LightGBM ensemble
        if self.lgbm_ensemble is not None:
            try:
                p, u = self.lgbm_ensemble.predict(feature_row)
                probas.append(p)
                uncertainties.append(u)
                weights.append(self._lgbm_weight)
            except Exception as e:
                logger.warning("LightGBM ensemble failed: %s", e)

        # Chronos
        if self.enable_chronos:
            try:
                from quant_v2.models.chronos_wrapper import predict_next_bar_direction
                p, u = predict_next_bar_direction(close_series, prediction_length)
                probas.append(p)
                uncertainties.append(u)
                weights.append(self._chronos_weight)
            except Exception as e:
                logger.warning("Chronos prediction failed: %s", e)

        if not probas:
            return 0.5, 1.0

        w = np.array(weights)
        w = w / w.sum()
        final_p = float(np.dot(w, probas))
        final_u = float(np.dot(w, uncertainties))

        # Agreement bonus
        if len(probas) > 1:
            directions = [1 if p > 0.5 else 0 for p in probas]
            if len(set(directions)) == 1:
                final_u *= 0.80

        return (
            float(np.clip(final_p, 0.0, 1.0)),
            float(np.clip(final_u, 0.0, 1.0)),
        )
```

### Step 4: Wire into signal manager

In `_predict_with_uncertainty()`, pass `close_series` to the full ensemble:

```python
def _predict_with_uncertainty(
    self, feature_row: pd.DataFrame, close_series: pd.Series | None = None,
) -> tuple[float, float]:
    # Try full ensemble (LightGBM + Chronos)
    if self.full_ensemble is not None and close_series is not None:
        return self.full_ensemble.predict(
            feature_row, close_series, prediction_length=self.horizon_bars,
        )
    # ... existing fallback code ...
```

Update the call site in `_build_signal_payload()` (around line 621) to pass `close_series`:
```python
proba_up, uncertainty = self._predict_with_uncertainty(feature_row, close_series=close_series)
```

### Step 5: Add `model_agreement` field to `StrategySignal`

**File**: `quant_v2/contracts.py`

```python
model_agreement: float | None = None   # 0.0 = disagree, 1.0 = full agree
```

Validation:
```python
if self.model_agreement is not None and not 0.0 <= self.model_agreement <= 1.0:
    raise ValueError("model_agreement must be within [0, 1]")
```

### Step 6: Add model agreement multiplier to allocation

**File**: `quant_v2/portfolio/allocation.py`

Follow the exact same pattern:

```python
_AGREEMENT_STRONG_THRESHOLD = 0.8
_AGREEMENT_STRONG_MULT = 1.0
_AGREEMENT_NEUTRAL_MULT = 0.85
_AGREEMENT_WEAK_MULT = 0.60

def _model_agreement_multiplier(agreement: float | None) -> float:
    if agreement is None:
        return _AGREEMENT_NEUTRAL_MULT
    if agreement >= _AGREEMENT_STRONG_THRESHOLD:
        return _AGREEMENT_STRONG_MULT
    if agreement >= 0.5:
        return _AGREEMENT_NEUTRAL_MULT
    return _AGREEMENT_WEAK_MULT
```

Wire into chain:
```python
signed_exposure *= sess_mult * regime_mult * accuracy_mult * event_mult * agreement_mult
```

### Step 7: Tests for Phase 3

Create `tests/quant_v2/test_chronos_wrapper.py`:
- Mock the Chronos pipeline (don't require the actual model in tests)
- Test `predict_next_bar_direction()` returns valid (prob, uncertainty) tuple
- Test insufficient data returns (0.5, 1.0)

Create `tests/quant_v2/test_full_ensemble.py`:
- Test `FullEnsemble.predict()` with both sources available
- Test fallback when Chronos fails
- Test fallback when LightGBM fails
- Test agreement bonus

Add to `tests/quant_v2/test_portfolio.py`:
- Test `model_agreement` multiplier in allocation chain

**CRITICAL**: All Chronos tests must use mocks. Do NOT download the actual model in CI/test.

---

## 7. Phase 4 — Execution Upgrade (Limit Orders)

### Goal

Replace market orders with limit orders placed slightly inside the spread.

### Step 1: Add limit order support to `BinanceClient`

**File**: `quant/data/binance_client.py`

The client already has `place_order()`. Add a `place_limit_order()` method:

```python
def place_limit_order(
    self,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    time_in_force: str = "GTC",
    reduce_only: bool = False,
) -> dict[str, Any]:
    """Place a LIMIT order on Binance Futures."""
    params = {
        "symbol": symbol,
        "side": side.upper(),
        "type": "LIMIT",
        "quantity": self._format_quantity(symbol, quantity),
        "price": self._format_price(symbol, price),
        "timeInForce": time_in_force,
    }
    if reduce_only:
        params["reduceOnly"] = "true"
    return self._signed_request("POST", "/fapi/v1/order", params)
```

### Step 2: Add order book price fetching

Add a method to get best bid/ask:

```python
def get_best_bid_ask(self, symbol: str) -> tuple[float, float]:
    """Return (best_bid, best_ask) for a symbol."""
    data = self._public_request("GET", "/fapi/v1/ticker/bookTicker", {"symbol": symbol})
    return float(data["bidPrice"]), float(data["askPrice"])
```

### Step 3: Modify order placement logic

The decision of limit vs. market is made in the execution adapter layer.
**File**: Whichever adapter is used (paper adapter won't change, live adapter needs update).

For live execution, when placing a BUY order:
```python
bid, ask = client.get_best_bid_ask(symbol)
# Place limit at best bid + 1 tick (join the bid)
limit_price = bid
result = client.place_limit_order(symbol, "BUY", quantity, limit_price)
```

For SELL:
```python
bid, ask = client.get_best_bid_ask(symbol)
limit_price = ask
result = client.place_limit_order(symbol, "SELL", quantity, limit_price)
```

### Step 4: Partial fill handling

Add a check in the reconciliation loop to detect partially filled limit orders:
- If an order is partially filled after 1 cycle (1 hour), cancel the remainder
- Log the partial fill for diagnostics

### Step 5: Tests for Phase 4

- Test `place_limit_order()` constructs correct parameters
- Test `get_best_bid_ask()` parses response correctly
- Mock-test that BUY orders use bid price, SELL orders use ask price

**NOTE**: Phase 4 only affects live execution. Paper trading continues to use instant fills.

---

## 8. Testing Standards

### Rules

1. **Never delete or weaken existing tests** — only add new ones
2. Every new public function must have at least one test
3. Every new multiplier in the allocation chain must have:
   - A test confirming dampening works
   - A test confirming None/neutral returns 1.0×
   - A test confirming the `enable_*` toggle disables it
4. Use `pytest.approx()` for float comparisons
5. Mock external APIs (Binance, CryptoPanic, Chronos) — never make real HTTP calls in tests
6. Run full suite after each phase: `python -m pytest tests/quant_v2/ -q --tb=short`

### Test file naming

```
tests/quant_v2/test_cross_pair_features.py
tests/quant_v2/test_liquidation_proximity.py
tests/quant_v2/test_horizon_ensemble.py
tests/quant_v2/test_event_gate.py
tests/quant_v2/test_chronos_wrapper.py
tests/quant_v2/test_full_ensemble.py
```

---

## 9. Deployment Workflow

After each phase passes all tests locally:

### 1. Upload changed files to AWS

```bash
# From project root on local machine
scp -i quant-key.pem <file> ubuntu@16.16.122.202:/home/ubuntu/quant_bot/<same_relative_path>
```

**IMPORTANT**: Upload each file to its correct subdirectory path. Do NOT scp multiple files
to a flat directory. Upload one file at a time:

```bash
scp -i quant-key.pem quant/features/cross_pair.py ubuntu@16.16.122.202:/home/ubuntu/quant_bot/quant/features/cross_pair.py
scp -i quant-key.pem quant_v2/contracts.py ubuntu@16.16.122.202:/home/ubuntu/quant_bot/quant_v2/contracts.py
```

### 2. Rebuild and restart containers

```bash
ssh -i quant-key.pem ubuntu@16.16.122.202 \
  "cd /home/ubuntu/quant_bot && sudo docker-compose down && sudo docker-compose up -d --build"
```

### 3. Verify containers are running

```bash
ssh -i quant-key.pem ubuntu@16.16.122.202 "sudo docker ps"
```

All 3 containers must show "Up":
- `quant_telegram`
- `quant_execution`
- `quant_redis`

### 4. Check logs for errors

```bash
ssh -i quant-key.pem ubuntu@16.16.122.202 \
  "sudo docker logs --tail 50 quant_telegram 2>&1 | grep -iE 'error|exception|traceback'"
```

Must return 0 results.

### 5. Git commit and push

```bash
git add -A
git commit -m "<descriptive message for the phase>"
git push origin main
```

---

## Appendix A — Existing Allocation Multiplier Pattern

Every multiplier follows this exact pattern. Use it as a template for new ones.

### 1. Field on `StrategySignal` (in `quant_v2/contracts.py`)

```python
@dataclass(frozen=True)
class StrategySignal:
    ...
    my_new_field: float | None = None   # None = no data, neutral

    def __post_init__(self) -> None:
        ...
        if self.my_new_field is not None and not 0.0 <= self.my_new_field <= 1.0:
            raise ValueError("my_new_field must be within [0, 1]")
```

### 2. Multiplier function (in `quant_v2/portfolio/allocation.py`)

```python
def _my_new_multiplier(value: float | None) -> float:
    """Return allocation multiplier, or 1.0 (neutral) when no data."""
    if value is None:
        return 1.0
    # ... threshold logic ...
    return multiplier
```

### 3. Toggle parameter on `allocate_signals()`

```python
def allocate_signals(
    ...
    enable_my_new_thing: bool = True,
) -> AllocationDecision:
```

### 4. Wire into the chain

```python
        my_mult = 1.0
        if enable_my_new_thing:
            my_mult = _my_new_multiplier(signal.my_new_field)

        signed_exposure *= sess_mult * regime_mult * accuracy_mult * my_mult
```

### 5. Populate in `_attach_native_v2_fields()` (in `signal_manager.py`)

```python
native_signal = StrategySignal(
    ...
    my_new_field=computed_value,
)
```

### 6. Tests

```python
def test_my_new_multiplier_dampens() -> None:
    signal = StrategySignal(..., my_new_field=0.2)  # low value
    decision = allocate_signals([signal], enable_my_new_thing=True, ...)
    # Assert reduced exposure

def test_my_new_multiplier_none_is_neutral() -> None:
    signal = StrategySignal(..., my_new_field=None)
    decision = allocate_signals([signal], enable_my_new_thing=True, ...)
    # Assert full exposure (same as without the feature)
```

---

## Appendix B — Existing Signal Flow (End-to-End)

```
┌──────────────────────────────────────────────────────────────────────┐
│ V2SignalManager._run_cycle()     [runs every 1 hour]                │
│                                                                      │
│  1. Load/refresh ML model from registry                              │
│  2. For each symbol in universe (10 symbols):                        │
│     a. Fetch 192 1h bars from Binance                                │
│     b. build_features(bars) → 78-column featured DataFrame           │
│     c. classify_latest() → regime state (1-5)                        │
│     d. _predict_with_uncertainty(last_row) → (proba_up, uncertainty) │
│     e. Apply regime-scaled thresholds → BUY/SELL/HOLD/DRIFT_ALERT   │
│     f. _attach_native_v2_fields() builds:                            │
│        - StrategySignal (confidence, uncertainty, session_hour,      │
│          momentum_bias, atr_pct, symbol_hit_rate)                    │
│        - MonitoringSnapshot                                          │
│     g. Record prediction in SymbolScorecard                          │
│     h. Emit signal via on_signal callback                            │
│  3. Evaluate pending scorecard predictions with cycle_prices         │
│                                                                      │
│  ──── Signal emitted to Telegram bot ────                            │
│                                                                      │
│  on_signal callback in quant/telebot/main.py:                        │
│     → Formats and sends Telegram message to user                     │
│     → Calls RoutedExecutionService.route_signals()                   │
│                                                                      │
│  ──── Execution pipeline ────                                        │
│                                                                      │
│  RoutedExecutionService.route_signals():                             │
│     1. build_execution_intents(signals):                             │
│        a. allocate_signals() → Kelly × sess × regime × accuracy      │
│        b. PortfolioRiskPolicy.apply() → cap enforcement              │
│     2. intents_to_order_plans() → quantity-based orders               │
│     3. adapter.execute(orders) → paper fills or Binance API           │
│     4. Update portfolio state, WAL, reconcile                        │
│     5. Kill-switch evaluation                                        │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Summary of Phases

| Phase | What | New Files | Key Risk | Rollback |
|-------|------|-----------|----------|----------|
| 1 | Cross-pair features + multi-horizon ensemble | 4 new files | Model must be retrained with new features | Revert to single model via registry rollback |
| 2 | Event gate (news dampening) | 2 new files | CryptoPanic API downtime | `enable_event_gate=False` or no API key = disabled |
| 3 | Chronos ensemble | 2 new files | Docker image size increase, torch dependency | `enable_chronos=False` on FullEnsemble |
| 4 | Limit orders | 0 new files | Partial fills, unfilled orders | Fall back to market orders |

**Execute in order. Do not skip phases. Run full test suite after each.**

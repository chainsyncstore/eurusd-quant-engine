# Weekly Log Audit — hypothesis-research-engine

**Host**: `ubuntu@13.48.85.88` | **Window**: 2026-04-16 10:06 → 2026-04-23 10:06 UTC (7d) | **Run at**: 2026-04-23 10:40 UTC

## Executive Summary

| # | Symptom | Verdict | Root Cause |
|---|---|---|---|
| 1 | No Telegram signal at 08:00 WAT (07:00 UTC) today | **Confirmed (benign)** | 100% HOLD cycle — every symbol's model probability fell inside the no-trade band (buy_th 0.59 / sell_th 0.41). No crash, no network error, no stale feed. HOLD is suppressed from Telegram by design. |
| 2 | Long position held >1h | **Confirmed (systemic)** | Paper LONG **BNBUSDT 0.527 @ 640.12** (notional ≈ $337). Portfolio optimizer has been degenerate since 22:00 UTC Apr 22 — passing only BNBUSDT (lowest-vol 0.0052). Today, BNBUSDT has produced **20 HOLD / 2 BUY / 0 SELL**, so nothing triggers an exit. Hourly loop (`loop=3600s`) slows reaction further. |
| 3 | Underperformance (−$251.53 vs $10,000 baseline, −2.52%) | **Confirmed (structural)** | Compound of: (a) scorecard hit-rates 0.30–0.55 on every symbol → 0.30–0.60× Kelly dampening, (b) model retrain universe mismatch — only **3 symbols** (BTC/ETH/BNB) trained but **9 symbols** scored → domain shift, (c) portfolio optimizer rejects all symbols in **84% of 1,208 cycles** (`N → 0`). Net effect: tiny positions, sparse entries, stuck exits. |

**No crashes, kill-switches, watchdog flattens, stale-feed events, drift alerts, or maintenance-resume states were triggered in the last 7 days.** The bot is stable — but the *trading logic* is over-filtered and the *model generalisation* is weak.

---

## Infrastructure Snapshot

- **Containers**: `quant_telegram` (up 8d, restarted Apr 14), `quant_execution` (up 10d), `quant_retrain` (up 10d), `quant_redis` (up 10d). **All 4 running, RestartCount=0 across the board.**
- **Host**: t3.medium, up 14d, load avg ~0.00. Memory 3.8 GB total, 523 MB free, 1.8 GB buff/cache — fine.
- **Disk**: `/` at **91% used** (27 GB / 29 GB, 2.9 GB free). `docker system df` shows **19.86 GB reclaimable docker images** (out of 19.91 GB). **Near-term outage risk.**
- **Env** (both telegram + execution): `BOT_EXECUTION_BACKEND=v2`, `BOT_V2_ALLOW_LIVE_EXECUTION=1`, `BOT_ENABLE_CHRONOS=1`, `BOT_V2_ENFORCE_GO_NO_GO=true`, `BOT_V2_LIVE_GO_NO_GO=true`.
- **DB WAL**: `/home/ubuntu/quant_bot/state/quant_bot.db` 12 KB but **`.db-wal` is 3.97 MB** (last write 09:23 UTC today). WAL is active and not checkpointing; consider `PRAGMA wal_checkpoint(TRUNCATE)`.

---

## Section 1 — Log Volumes & Timeline

| Container | Lines (168h) | First | Last | Notes |
|---|---|---|---|---|
| `quant_telegram` | **87,682** | 2026-04-16 10:06 | 2026-04-23 10:06 | Active, ~500 lines/hour consistently |
| `quant_execution` | **0** | n/a | n/a | **Silent since boot 2026-04-13 09:57 (10 startup lines, then nothing)** |
| `quant_retrain` | 129 | 2026-04-20 10:00 | 2026-04-20 10:01 | Retrained once (Apr 20), then `Sleeping 168h` |
| `quant_redis` | 0 | n/a | n/a | Normal (file-based log unused) |

### Break #1 — `quant_execution` container is a zombie

The dedicated execution container has produced **0 bytes of output since 2026-04-13 09:57:34**. Only startup messages exist:

```
2026-04-13 09:57:34 [quant_v2.execution.watchdog] INFO: Watchdog started (interval=5.0s, stale_threshold=120.0s)
2026-04-13 09:57:34 [quant_v2.execution.redis_bus] INFO: Stream consumer started (stream:cmd:exec)
2026-04-13 09:57:34 [__main__] INFO: Reconciliation loop started (interval=30s)
```

After that: **nothing**. No reconciliation output, no watchdog heartbeat prints, no command consumer activity. Meanwhile, the telegram container runs its own in-process execution (`Shadow v2 routed N order(s) for user ...`).

**Interpretation**: `quant_execution` is subscribed to Redis stream `stream:cmd:exec` but **the telegram bot is not publishing commands to that stream**. It's running `InMemoryExecutionService` / shadow routing locally and bypassing the execution engine entirely. The container is paying RAM/CPU for no work.

**Recommended fix**: Either (a) point telegram → Redis stream bus to actually use `quant_execution` for its durability/WAL features, or (b) remove the `execution_engine` service from `docker-compose.yml` to reclaim resources.

---

## Section 2 — Error Catalogue (7d)

Only **14 log lines** matched any error pattern across 7 days. All benign and transient:

| Class | Count | First | Last | Notes |
|---|---|---|---|---|
| `Traceback` | 6 | Apr 17 13:45 | Apr 22 18:45 | Two clustered incidents (3 tracebacks each) |
| `NetworkError` | 4 | Apr 17 13:45 | Apr 22 18:45 | All are `telegram.error.NetworkError: httpx.ReadError` during `getUpdates` polling — retried successfully |
| `httpx.ReadError` | 4 | same | same | Same two incidents |
| `Exception` / `ERROR` | 2+2 | same | same | Same incidents, logged at ERROR severity by the update-dispatcher |

**Zero** instances of: `CRITICAL`, `kill_switch`, `watchdog_flatten`, `stale_feed`, `circuit_breaker`, `DRIFT_ALERT`, `maintenance_resume`, `libgomp`, `ModuleNotFoundError`, `Conflict: terminated by other getUpdates`.

---

## Section 3 — Signal Activity Overview

### Daily counts (entire log window)

| Date (UTC) | BUY | SELL | HOLD | DRIFT |
|---|---:|---:|---:|---:|
| 2026-04-16 | 124 | 86 | 140 | 0 |
| 2026-04-17 | 332 | 34 | 287 | 0 |
| 2026-04-18 | 521 | 16 | 206 | 0 |
| 2026-04-19 | 596 | 41 | 180 | 0 |
| 2026-04-20 | 253 | 191 | 280 | 0 |
| 2026-04-21 | 233 | 144 | 293 | 0 |
| 2026-04-22 | 160 | 89 | 320 | 0 |
| 2026-04-23 (partial) | 58 | 34 | 161 | 0 |

Trend: **BUY/SELL count drops and HOLD rises every day after Apr 20 10:00** — the day the retrain ran and activated `model_20260420_100034`. The prior model (through Apr 19) was emitting ~600 BUYs/day; since the retrain, BUY volume fell ~60%.

### Hourly (last 48h excerpt)

```
hour            lines BUY  SELL HOLD DRIFT err
2026-04-22T09     486   0    0   20     0   0   ← no actionable
2026-04-22T10     495   0    5   16     0   0
2026-04-22T11     510   0   12   12     0   0
2026-04-23T06     498   0    3   18     0   0
2026-04-23T07     501   1    0   19     0   0   ← ★ 08:00 WAT silence
2026-04-23T08     510   0    9   14     0   0
2026-04-23T09     504   3    3   16     0   0
```

**Important**: the log-line volume is a flat ~500/hour regardless of signal output — the bot *is running* at 08:00 WAT, it's just that the model is returning HOLD.

---

## Section 4 — Symptom 1: 08:00 WAT Silence

**Window: 2026-04-23 07:00:00 → 07:59:59 UTC (08:00–08:59 WAT)**

Every `Signal decision` line in that hour was HOLD. Representative excerpt:

```
07:00:20 Signal decision: BNBUSDT HOLD proba=0.49?  buy_th=0.59 sell_th=0.41 regime=3 risk=0.5
07:00:20 Scorecard dampening BNBUSDT: hit_rate=0.41, mult=0.30
07:00:22 Signal decision: SOLUSDT HOLD proba=0.4109 buy_th=0.59 sell_th=0.41 regime=3 risk=0.5
07:00:23 Signal decision: SOLUSDT HOLD proba=0.4459
07:00:24 Signal decision: XRPUSDT HOLD proba=0.5419
07:00:25 Signal decision: XRPUSDT HOLD proba=0.5489
07:00:27 Signal decision: ADAUSDT HOLD proba=0.5455
07:00:28 Signal decision: ADAUSDT HOLD proba=0.5105
07:00:29 Signal decision: DOGEUSDT HOLD proba=0.4763
07:00:31 Signal decision: DOGEUSDT HOLD proba=0.5162
...
```

**Every symbol's calibrated probability in the 07:00 cycle landed in `[0.41, 0.59]` — the no-trade band.** HOLD signals are by design not forwarded to Telegram.

### Why this happened

1. **Narrow no-trade band is wide relative to today's model output.** The distance from 0.5 to the nearest threshold is 0.09. Today's probas cluster around 0.41–0.55 — barely any edge. This is normal for a low-volatility morning in crypto.
2. **Scorecard dampening lowered confidence.** Every symbol has `hit_rate ≤ 0.55`, so the effective trade-size multiplier is 0.30× or 0.60×. This is not the *reason* probas were in the band, but it explains why even a borderline 0.59 BUY doesn't light the board.
3. **3,600-second (hourly) cycle.** Between 06:00 and 08:00 UTC the model runs only once at 07:00 — a single HOLD cycle is all the user experiences in the full hour.

### Recurrence check

Morning-window BUY counts for each day (06–08 UTC):

| Date | BUY | SELL | Observation |
|---|---:|---:|---|
| Apr 17 | 27 | 7 | normal |
| Apr 18 | 59 | 0 | bullish |
| Apr 19 | 70 | 4 | bullish |
| Apr 20 | 26 | 26 | balanced (retrain day) |
| Apr 21 | 21 | 15 | balanced |
| Apr 22 | 20 | 16 | balanced |
| **Apr 23** | **1** | **12** | **heavily bearish + silent** |

Today's BUY silence is the outlier, but not pathological — just a very bearish model-output morning on top of already-narrow edges.

**Verdict**: Not a system break. If the user expects signals even on HOLD-only cycles, add a periodic "market tone" digest (e.g. `/status` on a 60-min heartbeat, or a low-confidence `DRIFT_ALERT`-style notification when no symbol clears the band).

---

## Section 5 — Symptom 2: Stuck BNBUSDT Long

**State from `state/quant_bot.db` at 09:23 UTC today**:

```
user_id:              6268794073
live_mode:            1
strategy_profile:     core_v2
active_model_version: model_20260413_192904   ← STALE (see Break #2)
active_model_source:  registry_active
cumulative_pnl:       -251.53
equity_usd:           9,969.87  (baseline 10,000)
session_state_json:
  equity_baseline_usd: 9972.27
  open_positions:      { BNBUSDT: 0.527013744 }
  paper_entry_prices:  { BNBUSDT: 640.12 }
```

**Position is PAPER (not live)** — confirmed by `paper_entry_prices` column and zero Binance private-API calls (`/fapi/v1/order`) in the 7-day logs.

### Why it's stuck

**BNBUSDT signal decisions today (so far)**: 20 HOLD, 2 BUY, 0 SELL. With 0 SELL emitted, there is no signal to close the long. Meanwhile the **portfolio optimizer has collapsed into a BNBUSDT-only regime** since 22:00 UTC Apr 22:

```
2026-04-22 22:36  Optimizer: 1 symbols → 1 after filter | vols={'BNBUSDT': '0.0052'}
2026-04-22 23:00  Optimizer: 1 symbols → 1 after filter | vols={'BNBUSDT': '0.0052'}
... (≈50 consecutive identical cycles) ...
2026-04-23 10:00  Optimizer: 1 symbols → 1 after filter | vols={'BNBUSDT': '0.0052'}
```

BNBUSDT's realised volatility (0.0052) is lower than every other symbol (0.0056–0.0096). The optimizer's volatility/correlation filter is preferring the lowest-vol symbol, and is **the only symbol passing** through in every recent cycle.

### Portfolio optimizer filter stats (7d, 1,208 cycles)

| Input → Output | Count | % |
|---|---:|---:|
| N → 0 symbols (all rejected) | **1,017** | **84.2%** |
| N → 1 | 157 | 13.0% |
| N → 2 | 26 | 2.2% |
| N → 3 | 2 | 0.2% |
| N → 4 | 2 | 0.2% |

84% of cycles pass *no* symbols. The filter is far too strict for the current model-output distribution.

### Recommendations for the stuck long

- **Immediate**: issue `/stop` then `/start_demo` on the affected user, or run admin `/reset_demo` to reset paper state. (Out of scope for this read-only audit — no action taken.)
- **Short-term**: Add a **max-hold time-stop** or an **unconditional flatten rule** when optimizer returns the same single symbol for N consecutive cycles with no opposing signal. Alternatively, loosen the vol/correlation filter when portfolio contains an open position with no exit candidate.
- **Medium-term**: Reduce `V2SignalManager` loop from 3,600 s to 900 s (15 min) or 300 s (5 min). Hourly is too slow to manage intraday positions.

---

## Section 6 — Symptom 3: Underperformance (−$251 / −2.52% on paper)

The loss is a direct consequence of four stacked filters, each of which is individually defensible but compound badly:

### 6.1 Scorecard accuracy dampening

Every symbol's rolling hit-rate is in the 0.30–0.55 band, triggering the `0.30×` or `0.60×` Kelly multiplier:

| Symbol | hit_rate (today) | mult |
|---|---:|---:|
| BTCUSDT | 0.41 | 0.30× |
| BNBUSDT | 0.40–0.47 | 0.30–0.60× |
| ETHUSDT | 0.47–0.51 | 0.60× |
| LTCUSDT | 0.36–0.42 | 0.30× |
| LINKUSDT | 0.42–0.52 | 0.30–0.60× |
| DOGEUSDT | 0.51–0.55 | 0.60× (borderline) |
| AVAXUSDT | 0.50–0.54 | 0.60× |
| SOLUSDT | 0.30–0.33 | 0.30× |
| XRPUSDT | 0.51–0.54 | 0.60× |
| ADAUSDT | 0.30–0.52 | 0.30–0.60× |

**No symbol is at or above 0.55.** The system is running in permanent "the model is close to random" mode and correctly down-sizing. But if no symbol ever recovers above 0.55, effective allocation is always 30–60% of Kelly, which caps upside in winning cycles while the losing paper positions (like BNB) still bleed full size.

### 6.2 Retrain universe mismatch (the single biggest lever)

`quant_retrain` container on 2026-04-20 10:00 logged:

```
Retrain: fetching 6 months of data for symbols=['BTCUSDT', 'ETHUSDT', 'BNBUSDT']...
Retrain horizon=2h: CV accuracy=0.5780, single-split=0.6175
Retrain horizon=4h: CV accuracy=0.5616, single-split=0.6173
Retrain horizon=8h: CV accuracy=0.5499, single-split=0.6295
Retrain: promoted model_20260420_100034 as active model (3 horizons)
Next retrain in 168 hours. Sleeping...
```

**Training universe = 3 symbols. Signal universe = ≥9 symbols (BTC, ETH, BNB, XRP, ADA, DOGE, SOL, LINK, LTC, AVAX).** The model is extrapolating to 6 symbols it has never seen during training. This explains why the scorecard hit-rates are below 0.50 on ADA, DOGE, SOL, LINK, LTC — they are genuinely out-of-distribution for this model.

Retrain CV accuracy (0.55–0.58 on the 3 training symbols) is also marginal. On OOD symbols, expected accuracy is lower still.

### 6.3 Portfolio optimizer filter over-rejection

See §5: 84% of cycles have every candidate rejected. The scoring pipeline successfully finds trades, then the allocation stage throws them away.

### 6.4 Hourly signal loop

`loop=3600s` means only one decision per symbol per hour. A position like the BNB long can hold for a full cycle before any opposing signal can fire. At 5-min crypto bar granularity, this is 12× under-sampled.

### 6.5 Notional contribution

- 10,000 baseline → 9,969.87 now = **−$30.13** *realised*
- **−$251.53** *cumulative paper_pnl* column → the session has accumulated −$251 over its lifetime; equity is held up by the current BNB mark price (position is flat-to-slightly-up on-paper vs. entry 640.12).
- Net effect: lots of small paper losses across many entry/exit cycles.

---

## Section 7 — Other Findings

### Break #2 — `user_context.active_model_version` is stale

DB says `active_model_version = model_20260413_192904` (Apr 13), but the telegram container logs show V2SignalManager re-initialised at:

```
2026-04-20 12:20  Initialized V2SignalManager with model: model_20260420_100034 (source=registry_active, loop=3600s)
2026-04-21 19:37  Initialized V2SignalManager with model: model_20260421_192947 (source=registry_active, loop=3600s)
```

The registry's active pointer was updated, the runtime loaded the new models, but the per-user `user_context.active_model_version` was not refreshed. This is an **audit/observability bug**: `/stats` and `/lifetime_stats` will report a stale model ID to the user. Functional impact is cosmetic, but for traceability of PnL attribution it matters.

### Break #3 — Retrain cadence anomaly

- `quant_retrain` log has exactly **1 retrain on Apr 20 10:00** (then `Sleeping 168h`).
- But a new model `model_20260421_192947` was activated on Apr 21 19:37, and the registry's `active.json` was last modified Apr 21 19:31.
- That's **33 hours** after the scheduled retrain, not 168 hours. So either (a) someone ran a manual retrain outside the scheduler, or (b) the scheduler rolled early. No evidence of a second retrain in the retrain container's log window.

### Break #4 — No `open_positions` SQLite table

The design suggests there should be a normalized `open_positions` table, but only `users` and `user_context` exist. Open positions are serialized into the JSON `session_state_json` column. This is legal but makes queryability poor. Not a bug per se, just a data-model observation.

### Break #5 — Signal cycles generate per-symbol decisions twice

Each symbol's `Signal decision` appears **twice per cycle** (e.g., `XRPUSDT HOLD proba=0.4823` then `XRPUSDT HOLD proba=0.4553` 1 second later). Root cause likely is the two-user fan-out (`user 6268794073` and `user 8392916807`), each running its own pipeline. This doubles Binance API load and compute cost but is not a correctness issue.

### Break #6 — Disk pressure (91% used, 2.9 GB free)

Docker system has 19.86 GB of reclaimable images (99% of 19.91 GB). No action taken in this audit, but a `docker image prune -a -f` during a maintenance window would recover ~19 GB.

### Break #7 — Event gate fires very rarely

Only 2 instances in 7d: `BUY ETHUSDT contradicted by high news` and `SELL XRPUSDT contradicted by low news`. Not a problem — just a note that the news-event filter is functional but seldom triggered.

---

## Section 8 — Recommended Fixes (Ranked)

### P0 — Prevent stuck paper position

- **Add unconditional time-stop** (e.g. 8h max-hold for paper longs when no opposing signal has fired in N cycles). Small upstream change in `quant_v2/portfolio/allocation.py` or `quant_v2/telebot/signal_manager.py`.
- **Loosen optimizer filter** when there is an existing open position that has no candidate exit: allow the filter to pass the *opposing* side of the held position without vol/correlation rejection.

### P1 — Fix model universe mismatch

- Change `quant_v2/research/scheduled_retrain.py` to train on the **full trading universe** (9–10 symbols), not the hard-coded 3. This alone should significantly lift out-of-sample hit-rates above 0.50 for SOL/ADA/DOGE/LINK/LTC/AVAX.
- Alternatively, restrict **live signal symbols** to only the 3 retrained symbols until the retrain config is fixed.

### P1 — Tighten signal-loop cadence

- Reduce `V2SignalManager` loop from 3,600 s → 900 s. Crypto exit signals need sub-hour reaction.

### P2 — Observability fixes

- Sync `user_context.active_model_version` with runtime `V2SignalManager.model_path` (set inside session start / model reload).
- **Drop `quant_execution` container** from `docker-compose.yml` OR wire telegram to actually publish to `stream:cmd:exec`. Right now the container is idle and misleading.
- Add a Telegram "no-signal" digest on HOLD-only cycles so users don't mistake a quiet hour for an outage. Include top-3 closest-to-threshold probas to give context.

### P2 — Data-model hygiene

- Persist `open_positions` to a normalized table (not just JSON) for queryability/crash resilience.
- Add SQLite `PRAGMA wal_checkpoint(TRUNCATE)` on bot shutdown to prevent WAL bloat.

### P3 — Infra hygiene

- **Disk**: schedule `docker image prune -a -f` at deploy time or weekly cron. Currently 91% full is a **near-term outage risk**.
- Consider upgrading instance disk from 29 GB to 50 GB+ since Chronos/LightGBM images are ≥10 GB each.
- Deduplicate per-user signal computation — two users re-compute identical features against Binance. Cache the features by symbol+bar_ts per cycle.

---

## Appendix A — Key Artifact Paths

All audit artifacts live under `scratch/audit_20260423/` (gitignored):

- `infra_health.txt` — docker ps / df / free / env / state dir
- `audit_out/` — first-pass distilled summaries
  - `A_timerange.txt`, `B_error_count.txt`, `C_error_samples.txt`, `D_signals_per_day.txt`, `E_hourly_last48h.txt`, `F_morning_window.txt`, `G_telegram_tail.txt`, `H_retrain_tail.txt`, `I_today_all.txt`, `J_hold_reasons.txt`, `K_symbols_buy_sell.txt`, `L_positions_tail.txt`, `N_v2_diagnostics.txt`, `O_data_fetch.txt`, `Q_watchdog.txt`, `R_retrain.txt`
- `audit_out2/` — second-pass forensics
  - `A1_today_decisions.txt`, `A2_yesterday_evening.txt`, `B1_signal_decisions_today.txt`, `B2_decisions_by_symbol.txt`, `C_scorecard_dampen_all.txt`, `D_order_events.txt`, `E_bnb_timestamps.txt`, `E2_bnb_tail.txt`, `K_filter_outcomes.txt`, `M_0608_today.txt`, `O_db.txt` (SQLite dump)

## Appendix B — Quick Verification Commands

Rerun on host with:

```bash
# on 13.48.85.88
bash /tmp/remote_infra.sh
bash /tmp/remote_logs.sh
bash /tmp/remote_analyze.sh
bash /tmp/remote_dig.sh
```

Reproducible offline from the tarballs `audit_out.tar.gz` (108 KB) and `audit_out2.tar.gz` (31 KB).

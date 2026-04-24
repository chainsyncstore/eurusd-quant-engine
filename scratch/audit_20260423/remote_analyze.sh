#!/bin/bash
set +e
IN=/tmp/audit_logs
OUT=/tmp/audit_out
rm -rf "$OUT"
mkdir -p "$OUT"
TG="$IN/quant_telegram.stderr.log"
EX="$IN/quant_execution.log"
RT="$IN/quant_retrain.stderr.log"

echo "=== A. Log volume & time-range ==="
for f in "$TG" "$EX" "$RT"; do
  echo "--- $f ---"
  wc -l "$f" 2>/dev/null
  head -1 "$f" 2>/dev/null | cut -c1-120
  tail -1 "$f" 2>/dev/null | cut -c1-120
done > "$OUT/A_timerange.txt"

echo "=== B. Error/Traceback counts (telegram) ==="
grep -cE 'Traceback|ERROR|CRITICAL|Exception|OSError|ConnectionError|ReadTimeout|NetworkError|httpx\.ReadError|Conflict|terminated by other getUpdates|kill_switch|watchdog_flatten|stale_feed|circuit_breaker|libgomp|ImportError|ModuleNotFoundError|maintenance_resume|DRIFT_ALERT' "$TG" > "$OUT/B_error_count.txt"
echo "--- distinct error classes (telegram) ---" >> "$OUT/B_error_count.txt"
grep -oE 'Traceback|ERROR|CRITICAL|Exception|OSError|ConnectionError|ReadTimeout|NetworkError|httpx\.ReadError|Conflict|terminated by other getUpdates|kill_switch|watchdog_flatten|stale_feed|circuit_breaker|libgomp|ImportError|ModuleNotFoundError|maintenance_resume|DRIFT_ALERT' "$TG" | sort | uniq -c | sort -rn >> "$OUT/B_error_count.txt"

echo "=== C. Telegram log: sample of each error class (first 5 each) ==="
for pat in 'Traceback' 'ERROR' 'CRITICAL' 'Exception' 'NetworkError' 'httpx.ReadError' 'Conflict' 'kill_switch' 'watchdog_flatten' 'stale_feed' 'DRIFT_ALERT' 'maintenance_resume'; do
  echo "--- [$pat] ---" >> "$OUT/C_error_samples.txt"
  grep -n -F "$pat" "$TG" | head -5 >> "$OUT/C_error_samples.txt"
done

echo "=== D. Signal activity counts per day (telegram) ==="
# Count BUY/SELL/HOLD/DRIFT_ALERT-containing lines per UTC day (timestamps at line start)
awk '
  {
    # capture date YYYY-MM-DD from first token (docker --timestamps: 2026-04-20T10:01:02.3Z)
    ts=$1; d=substr(ts,1,10);
    if ($0 ~ /BUY/) buy[d]++;
    if ($0 ~ /SELL/) sell[d]++;
    if ($0 ~ /HOLD/) hold[d]++;
    if ($0 ~ /DRIFT_ALERT/) drift[d]++;
    if ($0 ~ /route_signals|route-signals|Route signals|route signals|signals routed|signal routed/) routed[d]++;
    if ($0 ~ /paper_trade|PAPER TRADE|paper trade/) paper[d]++;
    if ($0 ~ /live_order|LIVE ORDER|live order/) live[d]++;
    days[d]=1
  }
  END {
    printf "%-12s %8s %8s %8s %8s %8s %8s %8s\n","date","BUY","SELL","HOLD","DRIFT","route","paper","live";
    n=0; for (d in days){ ds[n++]=d }
    # simple sort
    for (i=0;i<n;i++){ for(j=i+1;j<n;j++){ if (ds[i]>ds[j]){t=ds[i];ds[i]=ds[j];ds[j]=t} } }
    for (i=0;i<n;i++){ d=ds[i]; printf "%-12s %8d %8d %8d %8d %8d %8d %8d\n", d, buy[d]+0, sell[d]+0, hold[d]+0, drift[d]+0, routed[d]+0, paper[d]+0, live[d]+0 }
  }
' "$TG" > "$OUT/D_signals_per_day.txt"

echo "=== E. Hourly signal activity last 48h (telegram) ==="
awk '
  {
    ts=$1; h=substr(ts,1,13); # YYYY-MM-DDTHH
    total[h]++;
    if ($0 ~ /BUY/) buy[h]++;
    if ($0 ~ /SELL/) sell[h]++;
    if ($0 ~ /HOLD/) hold[h]++;
    if ($0 ~ /DRIFT_ALERT/) drift[h]++;
    if ($0 ~ /paper_trade|PAPER TRADE|paper trade/) paper[h]++;
    if ($0 ~ /Traceback|ERROR|CRITICAL|Exception|NetworkError/) err[h]++;
  }
  END {
    printf "%-14s %8s %6s %6s %6s %6s %6s %6s\n","hour","lines","BUY","SELL","HOLD","DRIFT","paper","err";
    n=0; for (h in total){ hs[n++]=h }
    for (i=0;i<n;i++){ for(j=i+1;j<n;j++){ if (hs[i]>hs[j]){t=hs[i];hs[i]=hs[j];hs[j]=t} } }
    start = (n>48)?n-48:0;
    for (i=start;i<n;i++){ h=hs[i]; printf "%-14s %8d %6d %6d %6d %6d %6d %6d\n", h, total[h], buy[h]+0, sell[h]+0, hold[h]+0, drift[h]+0, paper[h]+0, err[h]+0 }
  }
' "$TG" > "$OUT/E_hourly_last48h.txt"

echo "=== F. 06:30-08:30 UTC each day (telegram) — line counts per hour ==="
awk '
  {
    ts=$1; d=substr(ts,1,10); h=substr(ts,12,2);
    if (h=="06" || h=="07" || h=="08") {
      key=d" "h; cnt[key]++;
      if ($0 ~ /BUY/) buy[key]++;
      if ($0 ~ /SELL/) sell[key]++;
      if ($0 ~ /HOLD/) hold[key]++;
      if ($0 ~ /DRIFT_ALERT/) drift[key]++;
      if ($0 ~ /Traceback|ERROR|CRITICAL|Exception|NetworkError/) err[key]++;
    }
  }
  END {
    printf "%-14s %8s %6s %6s %6s %6s %6s\n","date hr","lines","BUY","SELL","HOLD","DRIFT","err";
    n=0; for (k in cnt){ ks[n++]=k }
    for (i=0;i<n;i++){ for(j=i+1;j<n;j++){ if (ks[i]>ks[j]){t=ks[i];ks[i]=ks[j];ks[j]=t} } }
    for (i=0;i<n;i++){ k=ks[i]; printf "%-14s %8d %6d %6d %6d %6d %6d\n", k, cnt[k], buy[k]+0, sell[k]+0, hold[k]+0, drift[k]+0, err[k]+0 }
  }
' "$TG" > "$OUT/F_morning_window.txt"

echo "=== G. Last 500 lines of telegram log (tail) ==="
tail -500 "$TG" > "$OUT/G_telegram_tail.txt"

echo "=== H. Last 200 lines of retrain log ==="
tail -200 "$RT" > "$OUT/H_retrain_tail.txt"

echo "=== I. Today (2026-04-23) UTC all hours (telegram) ==="
grep '^2026-04-23' "$TG" > "$OUT/I_today_all.txt"
wc -l "$OUT/I_today_all.txt"

echo "=== J. HOLD reason classification (telegram, last 48h) ==="
awk '$1 >= "2026-04-21"' "$TG" | grep -oE 'regime[= ][0-9]+|prob[= ][0-9.]+|threshold[= ][0-9.]+|dedup|cooldown|rebalance_deadband|skipped_by_filter|min_notional|min_qty|canary|kelly[= ][0-9.]+|symbol_hit_rate[= ][0-9.]+|accuracy_mult[= ][0-9.]+' | sort | uniq -c | sort -rn | head -80 > "$OUT/J_hold_reasons.txt"

echo "=== K. Symbols mentioned with BUY/SELL in last 48h ==="
awk '$1 >= "2026-04-21"' "$TG" | grep -E 'BUY|SELL' | grep -oE '[A-Z]{2,6}(USDT|USD|/USDT|/USD)' | sort | uniq -c | sort -rn > "$OUT/K_symbols_buy_sell.txt"

echo "=== L. Positions / portfolio / equity snapshots (telegram, last 48h) ==="
awk '$1 >= "2026-04-21"' "$TG" | grep -iE 'portfolio|position|snapshot|equity|unrealized|realized|PnL' | tail -120 > "$OUT/L_positions_tail.txt"

echo "=== M. Session start/stop events (full 7d) ==="
grep -niE 'start_demo|start_live|/stop|stop_session|session started|session stopped|start_session|session resumed|continue_demo|continue_live|prepare_update|update_complete' "$TG" | tail -80 > "$OUT/M_session_events.txt"

echo "=== N. Signal source manager / V2SignalManager / bridge diagnostics ==="
grep -niE 'V2SignalManager|v2_signal_manager|signal_source|routed_execution|execution_service|execution_backend|RoutedExecution|InMemoryExecution|bridge|v2_memory' "$TG" | tail -100 > "$OUT/N_v2_diagnostics.txt"

echo "=== O. Data-fetch / klines / funding / binance errors ==="
grep -niE 'binance|cryptocompare|klines|funding_rate|open_interest|fetch|rate_limit|429|timeout|retry|backoff' "$TG" | tail -120 > "$OUT/O_data_fetch.txt"

echo "=== P. Model registry events ==="
grep -niE 'model_registry|ModelRegistry|active_version|active_model|resolve_model|model_rollback|primary_model|raw_model' "$TG" | tail -60 > "$OUT/P_model_registry.txt"

echo "=== Q. Kill switch / watchdog / drift / stale feed ==="
grep -niE 'kill_switch|watchdog|stale_feed|heartbeat|circuit_breaker|DRIFT_ALERT|drift' "$TG" | tail -80 > "$OUT/Q_watchdog.txt"

echo "=== R. Retrain scheduler events ==="
grep -niE 'retrain|training complete|training failed|min_accuracy|new_active|registry_update' "$RT" | tail -120 > "$OUT/R_retrain.txt"

ls -la "$OUT"
tar czf /tmp/audit_out.tar.gz -C /tmp audit_out
ls -la /tmp/audit_out.tar.gz

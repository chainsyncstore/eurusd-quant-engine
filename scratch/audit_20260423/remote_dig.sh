#!/bin/bash
set +e
IN=/tmp/audit_logs
OUT=/tmp/audit_out2
rm -rf "$OUT"; mkdir -p "$OUT"
TG="$IN/quant_telegram.stderr.log"

echo "=== A1. Signal decisions today with full context (unique per symbol last few cycles) ==="
grep -E 'Signal decision|Scorecard dampening|allocation|Optimizer|Generated signal|StrategySignal|_route_signals|route_signals|notifier|Final allocation|after allocation|execute_order|submit_order|paper fill|live fill|PAPER|submitted|rebalance|position opened|position closed|open_position|close_position|portfolio_snapshot|PnL|unrealized' "$TG" | grep '^2026-04-23' | tail -200 > "$OUT/A1_today_decisions.txt"

echo "=== A2. Signal decisions yesterday evening (Apr 22 18:00+) ==="
grep -E 'Signal decision|Scorecard dampening|allocation|Optimizer|Generated signal|StrategySignal|notifier|execute_order|submit_order|paper fill|live fill|rebalance|position' "$TG" | awk '$1 >= "2026-04-22T18" && $1 < "2026-04-23T00"' | tail -200 > "$OUT/A2_yesterday_evening.txt"

echo "=== B1. All Signal decision lines today ==="
grep 'Signal decision' "$TG" | grep '^2026-04-23' > "$OUT/B1_signal_decisions_today.txt"
wc -l "$OUT/B1_signal_decisions_today.txt"

echo "=== B2. Signal decision count by symbol + action (today) ==="
awk '/Signal decision/{print}' "$TG" | grep '^2026-04-23' | grep -oE 'Signal decision: [A-Z]+ [A-Z]+' | sort | uniq -c | sort -rn > "$OUT/B2_decisions_by_symbol.txt"

echo "=== B3. HOLD reason classification (today) ==="
awk '/Signal decision/{print}' "$TG" | grep '^2026-04-23' | grep -oE 'regime=[0-9]|proba=[0-9.]+|buy_th=[0-9.]+|sell_th=[0-9.]+|risk=[0-9.]+' | sort | uniq -c | sort -rn | head -80 > "$OUT/B3_signal_params_today.txt"

echo "=== C. Scorecard dampening stats (all 7d) ==="
grep 'Scorecard dampening' "$TG" | grep -oE '[A-Z]+USDT: hit_rate=[0-9.]+, mult=[0-9.]+' | sort | uniq -c | sort -rn | head -40 > "$OUT/C_scorecard_dampen_all.txt"
echo "--- today ---" >> "$OUT/C_scorecard_dampen_all.txt"
grep 'Scorecard dampening' "$TG" | grep '^2026-04-23' | grep -oE '[A-Z]+USDT: hit_rate=[0-9.]+, mult=[0-9.]+' | sort | uniq -c | sort -rn >> "$OUT/C_scorecard_dampen_all.txt"

echo "=== D. Order / execution / trade events (last 7d) ==="
grep -iE 'order|trade|fill|executed|rebalance|open_position|close_position|portfolio_snapshot' "$TG" | grep -vE 'httpx|getUpdates' | tail -200 > "$OUT/D_order_events.txt"

echo "=== E. First BNB appearance each day (stuck long hypothesis) ==="
grep -E 'BNBUSDT|BNB/USDT' "$TG" | grep -oE '^[0-9T:.Z-]+' | cut -c1-16 | sort -u > "$OUT/E_bnb_timestamps.txt"
echo "--- first 20 ---" >> "$OUT/E_bnb_timestamps.txt"
head -20 /tmp/audit_out2/E_bnb_timestamps.txt 2>/dev/null
echo "--- last 20 BNB lines ---"
grep -E 'BNBUSDT|BNB/USDT' "$TG" | tail -40 > "$OUT/E2_bnb_tail.txt"

echo "=== F. Session start/stop (broader regex) ==="
grep -iE 'start_demo|start_live|/stop|stop_session|session started|session stopped|session resumed|_start_session|_stop_session|_cmd_start|_cmd_stop|/continue_demo|/continue_live|maintenance' "$TG" | tail -60 > "$OUT/F_session_events_v2.txt"

echo "=== G. Full startup sequence (first 100 lines of telegram) ==="
head -100 "$TG" > "$OUT/G_startup.txt"

echo "=== H. Binance private account/orders calls if any ==="
grep -iE '/fapi/v1/order|/api/v3/order|fapi/v1/account|futures|position_side|positionAmt|NEW_ORDER|TRADE|orderId' "$TG" | tail -120 > "$OUT/H_binance_orders.txt"

echo "=== I. Hourly cadence of signal cycles (look for gap at 7-8 UTC today) ==="
grep -E 'Initialized V2SignalManager|_route_signals|Cycle complete|Signal loop|signal_manager.*cycle|signal_manager.*sleep|sleep_until' "$TG" | tail -100 > "$OUT/I_cycles.txt"

echo "=== J. All optimizer filter OUTPUT pattern (7d) ==="
grep 'Optimizer:' "$TG" | awk '{print substr($1,1,16)"\t"$0}' | awk -F'\t' '{a[$1]++} END{for(k in a) print k, a[k]}' | sort | tail -40 > "$OUT/J_optimizer_hourly.txt"

echo "=== K. All 'after filter' distinct outcomes (7d) ==="
grep 'after filter' "$TG" | grep -oE '[0-9]+ symbols → [0-9]+' | sort | uniq -c | sort -rn > "$OUT/K_filter_outcomes.txt"

echo "=== L. Allocation output events (look for allocate_signals, kelly, notional, qty) ==="
grep -iE 'allocate_signals|kelly|notional|target_qty|allocate.*BUY|allocate.*SELL|size=|qty=|weight=|effective_risk|canary.*risk|risk_cap' "$TG" | grep -vE 'httpx' | tail -120 > "$OUT/L_allocation.txt"

echo "=== M. 06-08 UTC today LINE-BY-LINE (for 08:00 WAT silence) ==="
grep -E '^2026-04-23T0[678]' "$TG" | grep -vE 'httpx.*getUpdates' | head -400 > "$OUT/M_0608_today.txt"
wc -l "$OUT/M_0608_today.txt"

echo "=== N. Check if notifier sent any BUY/SELL alert today ==="
grep -iE 'sendMessage|send_message|notifier|alert|signal.*message|Telegram.*sent' "$TG" | grep -vE 'getUpdates' | tail -120 > "$OUT/N_notifier.txt"

echo "=== O. DB contents (state) ==="
if command -v sqlite3 >/dev/null 2>&1; then
  echo '---tables---'
  sqlite3 /home/ubuntu/quant_bot/state/quant_bot.db '.tables' 2>/dev/null
  echo '---user_context---'
  sqlite3 /home/ubuntu/quant_bot/state/quant_bot.db 'select * from user_context;' 2>/dev/null | head -20
  echo '---open_positions---'
  sqlite3 /home/ubuntu/quant_bot/state/quant_bot.db 'select * from open_positions;' 2>/dev/null | head -40
  echo '---snapshots/signals tables---'
  sqlite3 /home/ubuntu/quant_bot/state/quant_bot.db "select name from sqlite_master where type='table';" 2>/dev/null
else
  echo 'sqlite3 not installed'
fi > "$OUT/O_db.txt" 2>&1

ls -la "$OUT"
tar czf /tmp/audit_out2.tar.gz -C /tmp audit_out2
ls -la /tmp/audit_out2.tar.gz

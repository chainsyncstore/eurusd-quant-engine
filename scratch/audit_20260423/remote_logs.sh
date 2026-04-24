#!/bin/bash
set +e
OUT=/tmp/audit_logs
rm -rf "$OUT"
mkdir -p "$OUT"
for c in quant_telegram quant_execution quant_retrain quant_redis; do
  echo "==> pulling $c"
  sudo docker logs --since 168h --timestamps "$c" > "$OUT/${c}.log" 2> "$OUT/${c}.stderr.log"
  wc -l "$OUT/${c}.log" "$OUT/${c}.stderr.log"
done
# Also snapshot the state DB (read-only copy)
cp /home/ubuntu/quant_bot/state/quant_bot.db "$OUT/quant_bot.db" 2>/dev/null
cp /home/ubuntu/quant_bot/state/quant_bot.db-wal "$OUT/quant_bot.db-wal" 2>/dev/null
cp /home/ubuntu/quant_bot/state/quant_bot.db-shm "$OUT/quant_bot.db-shm" 2>/dev/null
# Snapshot signal log if present
cp /home/ubuntu/quant_bot/signal_log.json "$OUT/signal_log.json" 2>/dev/null
# active.json
cp /home/ubuntu/quant_bot/models/production/registry/active.json "$OUT/active.json" 2>/dev/null
ls -la "$OUT"
# tarball
tar czf /tmp/audit_logs.tar.gz -C /tmp audit_logs
ls -la /tmp/audit_logs.tar.gz

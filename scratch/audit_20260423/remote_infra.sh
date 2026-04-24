#!/bin/bash
set +e
echo "===docker ps -a==="
sudo docker ps -a
echo
echo "===df -h /==="
df -h /
echo
echo "===free -m==="
free -m
echo
echo "===uptime==="
uptime
echo
echo "===docker system df==="
sudo docker system df
echo
echo "===container state==="
for c in quant_telegram quant_execution quant_retrain quant_redis; do
  printf '%s: ' "$c"
  sudo docker inspect --format 'RestartCount={{.RestartCount}} StartedAt={{.State.StartedAt}} FinishedAt={{.State.FinishedAt}} Status={{.State.Status}} ExitCode={{.State.ExitCode}}' "$c" 2>/dev/null || echo NOT_FOUND
done
echo
echo "===env for execution==="
sudo docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' quant_execution 2>/dev/null | grep -E '^(BOT_|REDIS_|CRYPTO|RETRAIN_)' | sort
echo
echo "===env for telegram==="
sudo docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' quant_telegram 2>/dev/null | grep -E '^(BOT_|REDIS_|CRYPTO|RETRAIN_|TELEGRAM|ADMIN)' | sort
echo
echo "===env for retrain==="
sudo docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' quant_retrain 2>/dev/null | grep -E '^(BOT_|REDIS_|RETRAIN_)' | sort
echo
echo "===state dir==="
ls -la /home/ubuntu/quant_bot/state/ 2>/dev/null
echo
echo "===models dir==="
ls -la /home/ubuntu/quant_bot/models/production/registry/ 2>/dev/null | head -40

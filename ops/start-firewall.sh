#!/usr/bin/env bash
# 拉起防火墙服务（tmux 常驻，幂等）：http://127.0.0.1:18210
# 用法：bash ops/start-firewall.sh          # 起/重启服务
#       bash ops/start-firewall.sh stop     # 停服务
#
# 注意（踩过的坑）：PYTHONPATH 必须写成 tmux 命令的**内联前缀**，
# 不要用 `export` 再另起命令 —— tmux 不继承外层 export 的环境变量。
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
SESSION=firewall
PORT="${FIREWALL_PORT:-18210}"
EXPORT_PATH="PYTHONPATH=$ROOT/apps/firewall/src"

if [ "${1:-start}" = "stop" ]; then
  tmux has-session -t "$SESSION" 2>/dev/null && tmux kill-session -t "$SESSION" && echo "已停 $SESSION" || echo "$SESSION 未在运行"
  exit 0
fi

tmux has-session -t "$SESSION" 2>/dev/null && tmux kill-session -t "$SESSION"
tmux new-session -d -s "$SESSION" -c "$ROOT" \
  "$EXPORT_PATH .venv/bin/uvicorn firewall.main:app --host 127.0.0.1 --port $PORT"

for _ in $(seq 1 20); do
  curl -s --noproxy '*' -o /dev/null --max-time 1 "http://127.0.0.1:$PORT/health" && break
  sleep 1
done

echo "firewall : http://127.0.0.1:$PORT/health"
echo "策略表   : ${FIREWALL_POLICY:-$ROOT/apps/firewall/policies/default.yaml}"
curl -s --noproxy '*' --max-time 3 "http://127.0.0.1:$PORT/health" || echo "（服务未就绪，用 tmux attach -t $SESSION 看日志）"
echo

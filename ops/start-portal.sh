#!/usr/bin/env bash
# 拉起平台后端 + 前端托管（tmux 常驻，幂等）。前端由后端托管：http://127.0.0.1:18100/app/
# 用法：bash ops/start-portal.sh          # 起服务
#       bash ops/start-portal.sh stop     # 停服务
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
SESSION=portal-api
PORT="${PORTAL_PORT:-18100}"
EXPORT_PATH="PYTHONPATH=$ROOT/apps/portal-api/src:$ROOT/packages/dip-contracts/src:$ROOT/packages/dip-core/src:$ROOT/packages/dip-agent/src:$ROOT/integrations/lineage-client/src"

if [ "${1:-start}" = "stop" ]; then
  tmux has-session -t "$SESSION" 2>/dev/null && tmux kill-session -t "$SESSION" && echo "已停 $SESSION" || echo "$SESSION 未在运行"
  exit 0
fi

# 内核（血缘服务）不在就拉起来
if ! curl -s --noproxy '*' -o /dev/null --max-time 2 http://127.0.0.1:18080/health; then
  echo "内核未运行，尝试拉起…"
  [ -x /usr/local/bin/start-lineage-api.sh ] && bash /usr/local/bin/start-lineage-api.sh >/dev/null 2>&1
  sleep 3
fi

tmux has-session -t "$SESSION" 2>/dev/null && tmux kill-session -t "$SESSION"
tmux new-session -d -s "$SESSION" -c "$ROOT" \
  "$EXPORT_PATH .venv/bin/uvicorn portal_api.main:app --host 127.0.0.1 --port $PORT"

for _ in $(seq 1 20); do
  curl -s --noproxy '*' -o /dev/null --max-time 1 "http://127.0.0.1:$PORT/health" && break
  sleep 1
done

echo "portal-api : http://127.0.0.1:$PORT/api/health"
echo "前端 + 后端: http://127.0.0.1:$PORT/app/    ← 打开这个提问就有反馈"
curl -s --noproxy '*' --max-time 3 "http://127.0.0.1:$PORT/api/health" || echo "（服务未就绪，用 tmux attach -t $SESSION 看日志）"
echo

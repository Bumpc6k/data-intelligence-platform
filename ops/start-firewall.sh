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
POLICY="${FIREWALL_POLICY:-$ROOT/apps/firewall/policies/default.yaml}"
TTL="${FIREWALL_TOKEN_TTL_SECONDS:-300}"
# 环境变量必须**内联进 tmux 的命令行**：tmux 开新会话用的是它自己那个服务器进程的环境，
# 不继承调用方 export 出来的变量。只 export 不内联的话，FIREWALL_POLICY / FIREWALL_TOKEN_TTL_SECONDS
# 会被**静默忽略**（写错也不报错，看起来一切正常）——这正是我们要避免的失效方式。
INLINE="PYTHONPATH=$ROOT/apps/firewall/src FIREWALL_POLICY=$POLICY FIREWALL_TOKEN_TTL_SECONDS=$TTL"

if [ "${1:-start}" = "stop" ]; then
  tmux has-session -t "$SESSION" 2>/dev/null && tmux kill-session -t "$SESSION" && echo "已停 $SESSION" || echo "$SESSION 未在运行"
  exit 0
fi

tmux has-session -t "$SESSION" 2>/dev/null && tmux kill-session -t "$SESSION"
tmux new-session -d -s "$SESSION" -c "$ROOT" \
  "$INLINE .venv/bin/uvicorn firewall.main:app --host 127.0.0.1 --port $PORT"

for _ in $(seq 1 20); do
  curl -s --noproxy '*' -o /dev/null --max-time 1 "http://127.0.0.1:$PORT/health" && break
  sleep 1
done

echo "firewall : http://127.0.0.1:$PORT/health"
echo "策略表   : $POLICY"
echo "令牌时效 : $TTL 秒"
curl -s --noproxy '*' --max-time 3 "http://127.0.0.1:$PORT/health" || echo "（服务未就绪，用 tmux attach -t $SESSION 看日志）"
echo

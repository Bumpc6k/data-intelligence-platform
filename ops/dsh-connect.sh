#!/usr/bin/env bash
# 把血缘 skill 接进 dsh 壳（工作项 M4-02 / Issue #18）
#
# 在 **WSL 里**跑（skill 用仓库的 Linux venv）：
#
#     bash ops/dsh-connect.sh            # 起 skill（HTTP）+ 写 dsh 配置 + 自检
#     bash ops/dsh-connect.sh --check    # 只自检，不起东西、不改配置
#
# 它做三件事，每件都打印实际结果：
#   [1/3] 起 skill 的 HTTP 传输（:18360/mcp），日志 /tmp/dip-skill.log
#   [2/3] 把 ops/dsh/cordis.patch.mcp.yml 并进 $DSH_HOME/profiles/web/cordis.patch.yml
#   [3/3] 用真 MCP 客户端握手 + tools/list + 一次真调用（**不靠"端口通了"就说成功**）
#
# 边界：不改 dsh 源码、不动 dsh 的 cordis.yml（只写它的 patch 层）。

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
export LANG=C.UTF-8 LC_ALL=C.UTF-8
unset HTTPS_PROXY HTTP_PROXY ALL_PROXY

SKILL_PORT="${DIP_SKILL_PORT:-18360}"
SKILL_URL="http://127.0.0.1:${SKILL_PORT}/mcp"
# 注意：本脚本在 **WSL** 里跑 → 路径是 /mnt/d/...（Git Bash 的 /d/... 在这里不认）
DSH_HOME_DIR="${DSH_HOME:-/mnt/d/Projects/_dsh-probe/dsh-home}"
PATCH="$DSH_HOME_DIR/profiles/web/cordis.patch.yml"
TEMPLATE="$ROOT/ops/dsh/cordis.patch.mcp.yml"
LOG="/tmp/dip-skill.log"

# 本仓库的既有特性（见 notes/mcp-skill.md §1）：pip install -e . 不会装这些包，
# 它们靠 pytest 的 pythonpath 挂载 —— 所以**任何非 pytest 入口都要自己给 PYTHONPATH**。
export PYTHONPATH="$ROOT:$ROOT/packages/dip-contracts/src:$ROOT/packages/dip-skills/src:$ROOT/packages/dip-lineage-skill/src:$ROOT/integrations/lineage-client/src"
QUESTION="${1:-ads.ads_产销存月报 的产量怎么来的？}"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && { CHECK_ONLY=1; QUESTION="ads.ads_产销存月报 的产量怎么来的？"; }

echo "=================================================================="
echo "dsh 接入血缘 skill    $(date '+%Y-%m-%d %H:%M:%S')"
echo "  skill 端点：$SKILL_URL"
echo "  DSH_HOME  ：$DSH_HOME_DIR"
echo "=================================================================="

if [ "$CHECK_ONLY" -eq 0 ]; then
  echo
  echo "[1/3] 起 skill（HTTP 传输，日志 $LOG）"
  if curl -s --noproxy '*' -o /dev/null --max-time 2 "http://127.0.0.1:${SKILL_PORT}/mcp"; then
    echo "  端口已有服务在听，复用"
  else
    # **必须 setsid 脱离会话**：本脚本是被 `wsl.exe -d ... -- bash ops/dsh-connect.sh` 这样叫起来的，
    # 那条 wsl.exe 命令一返回，会话里的子进程会被 WSL 收走 —— 实测 nohup 单独用不够，
    # 服务会在几分钟后无声消失（表现是外壳报 "fetch failed"，很难查）。
    LINEAGE_BASE="${LINEAGE_BASE:-http://127.0.0.1:18080}" \
      setsid nohup .venv/bin/python -m dip_lineage_skill --transport http --port "$SKILL_PORT" >"$LOG" 2>&1 < /dev/null &
    disown 2>/dev/null || true
    for _ in $(seq 1 20); do
      sleep 1
      curl -s --noproxy '*' -o /dev/null --max-time 2 "http://127.0.0.1:${SKILL_PORT}/mcp" && break
    done
    echo "  已拉起（PID $(pgrep -f "dip_lineage_skill --transport http" | head -1)）"
  fi
  echo "  日志尾部："; tail -3 "$LOG" 2>/dev/null | sed 's/^/    /'

  echo
  echo "[2/3] 写 dsh 配置（只动 patch 层；**每个 profile 都要写**）"
  echo "      注意：dsh 的 profile 是按入口分的 —— web 一个、headless 一个，配置不共享。"
  shopt -s nullglob
  patches=("$DSH_HOME_DIR"/profiles/*/cordis.patch.yml)
  shopt -u nullglob
  if [ "${#patches[@]}" -eq 0 ]; then
    echo "  ⚠️  没找到任何 $DSH_HOME_DIR/profiles/*/cordis.patch.yml"
    echo "      先各跑一次 dsh（web 与 headless），让它生成 profile 目录"
    exit 1
  fi
  for PATCH in "${patches[@]}"; do
    echo "  --- $PATCH"
    cp "$PATCH" "$PATCH.bak-$(date +%H%M%S)"
    python3 - "$PATCH" "$TEMPLATE" <<'PY'
import pathlib
import sys

BEGIN = "# >>> dip-lineage-mcp"
END = "# <<< dip-lineage-mcp"

patch_path, template_path = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
raw = patch_path.read_text(encoding="utf-8").splitlines()

# dsh 生成的占位文件正文可能是 `[]`（空数组）。直接往后追加列表项会变成**两个 YAML 文档**，
# dsh 会在启动时报 "document separator is expected"。所以先把这种空数组行去掉。
kept = [line for line in raw if line.strip() != "[]"]

# 幂等：把自己上一次写的标记块整段删掉，再重新追加（不碰用户其它内容）
out: list[str] = []
inside = False
for line in kept:
    if line.strip().startswith(BEGIN):
        inside = True
        continue
    if line.strip().startswith(END):
        inside = False
        continue
    if not inside:
        out.append(line)

block = [
    line for line in template_path.read_text(encoding="utf-8").splitlines()
    if line.strip() == "" or not line.lstrip().startswith("#") or line.strip().startswith((BEGIN, END))
]
while block and not block[0].strip():
    block.pop(0)
while block and not block[-1].strip():
    block.pop()

text = "\n".join(out).rstrip("\n") + "\n\n" + "\n".join(block) + "\n"
patch_path.write_text(text, encoding="utf-8")
print(f"      已并入 mcp-lineage 条目（{len(block)} 行），备份在同目录 .bak-*")
PY
    grep -vE "^\s*#" "$PATCH" | grep -vE "^\s*$" | grep -A 12 "mcp-lineage" | sed 's/^/      /'
  done
fi

echo
echo "[3/3] 用真 MCP 客户端自检（握手 → tools/list → 真调用）"
.venv/bin/python - "$SKILL_URL" "$QUESTION" <<'PY'
import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

URL, QUESTION = sys.argv[1], sys.argv[2]


async def main() -> int:
    async with create_mcp_http_client(timeout=30.0) as http_client:
        async with streamable_http_client(URL, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = [t.name for t in (await session.list_tools()).tools]
                print(f"  ✓ 握手成功；tools/list → {tools}")
                result = await session.call_tool(tools[0], {"question": QUESTION})
                payload = json.loads(result.content[0].text)
                receipt = payload["receipt"]
                print(f"  ✓ 真调用：endpoint={receipt['endpoint']} ms={receipt['ms']} "
                      f"ok={receipt['ok']} evidence_count={receipt['evidence_count']}")
                print(f"    主体={payload['subject']} 涉及表={len(payload.get('tables') or [])} 张")
                if not receipt["ok"]:
                    print(f"    ⚠️  失败回执（这是如实返回，不是崩了）：{receipt['error']}")
                return 0 if receipt["ok"] else 1


sys.exit(asyncio.run(main()))
PY
STATUS=$?
echo
if [ "$STATUS" -eq 0 ]; then
  echo "✅ skill 侧就绪。接下来在 **Git Bash** 里起壳："
else
  echo "⚠️  skill 侧自检没全绿（见上面的实际返回）。壳仍能起，只是这个工具会报错 ——"
  echo "    失败路径要的就是这种「明确提示」，别把它当成阻塞。接着在 **Git Bash** 里起壳："
fi
cat <<'EOS'

    export PATH="/c/Program Files/nodejs:/c/Users/Administrator/AppData/Roaming/npm:$PATH"
    export DSH_HOME=D:/Projects/_dsh-probe/dsh-home    # 原生路径！/d/... 会被 node 当成相对当前盘
    set -a; . /d/Projects/data-intelligence-platform/.env; set +a    # DEEPSEEK_API_KEY
    unset HTTPS_PROXY HTTP_PROXY
    cd /d/Projects/_dsh-probe/dsh && pnpm dsh web --no-open
    # 复制打印出来的 http://127.0.0.1:3080/?token=... 到浏览器，选工作区，然后问：
    #   ads.ads_产销存月报 的产量怎么来的？用 mcp__lineage__lineage_analyze 工具查血缘
EOS

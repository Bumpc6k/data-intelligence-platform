#!/usr/bin/env bash
# M1 垂直切片：一条命令走完验收问题（工作项 M1-06 / Issue #6）
#
#     bash ops/demo-m1.sh                                    # 默认旗舰问题
#     bash ops/demo-m1.sh "dwd.dwd_订单明细 从哪来？"          # 自定义问题
#
# 它做什么（每一步都打印**实际返回**；任何一步失败都指明卡在哪、返回了什么）：
#   [1/5] 内核 :18080       血缘与口径的唯一来源（没起就拉起）
#   [2/5] 模型网关 :18200   薄层、配置化（没起就拉起）
#   [3/5] 血缘 skill（MCP） 以 stdio 起进程，本脚本内嵌的驱动扮演"外壳"去调它
#   [4/5] 三条凭证          血缘（经 skill）/ 字段词表 / 口径公式（后两条直连内核）
#   [5/5] 出口事实校验      模型输出必须全部来自回执白名单，越界即拦
#
# 产物：docs/evidence/m1-<时间戳>.txt（问题 + 答案 + 3 条凭证 + 回执耗时）
#
# 边界：**不做**全栈一键（那是 M4-03）；本脚本只跑 M1 这一条链。
#
# 关于 dsh（对话外壳）：M1 的"外壳"角色由内嵌驱动扮演。若本机已构建 dsh
# （设 DSH_HOME 指向它），脚本会额外把它拉起来并打印地址；没有也不影响本链跑通。

set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"
UVICORN="$ROOT/.venv/bin/uvicorn"

QUESTION="${1:-ads.ads_产销存月报 的产量怎么来的？}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$ROOT/docs/evidence/m1-${STAMP}.txt"

KERNEL_DIR="${LINEAGE_KERNEL_DIR:-/root/projects/sql-lineage-mvp}"
KERNEL_URL="${KERNEL_BASE_URL:-http://127.0.0.1:18080}"
GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:18200}"
GATEWAY_PORT="${GATEWAY_PORT:-18200}"

export PYTHONPATH="$ROOT/packages/dip-contracts/src:$ROOT/packages/dip-skills/src:$ROOT/packages/dip-lineage-skill/src:$ROOT/integrations/lineage-client/src:$ROOT/apps/model-gateway/src:$ROOT/packages/dip-core/src"

mkdir -p "$ROOT/docs/evidence"

# 所有输出同时进证据文件与屏幕
exec > >(tee "$OUT") 2>&1

fail() {
  echo
  echo "❌ 卡在第 $1 步：$2"
  echo "   实际返回：$3"
  echo "   证据（含到失败为止的输出）已写入：$OUT"
  exit 1
}

echo "=================================================================="
echo "M1 垂直切片演示    $(date '+%Y-%m-%d %H:%M:%S')"
echo "问题：$QUESTION"
echo "=================================================================="

# ---------------------------------------------------------------- [1/5] 内核
echo
echo "[1/5] 数据智能内核（$KERNEL_URL）"
KERNEL_HEALTH="$(curl -sS --noproxy '*' --max-time 3 "$KERNEL_URL/health" 2>&1)"
if echo "$KERNEL_HEALTH" | grep -q '"success": *true'; then
  echo "  已在运行"
else
  echo "  未运行，尝试拉起（$KERNEL_DIR）…"
  if [ ! -d "$KERNEL_DIR" ]; then
    fail 1 "内核目录不存在：$KERNEL_DIR" "$KERNEL_HEALTH"
  fi
  # 注意：不能走 ops/start-lineage-api.sh —— 它 export PYTHONPATH 后调 tmux，
  # 而 tmux 不继承 PYTHONPATH（实测 show-environment 报 unknown），内核会以
  # ModuleNotFoundError 静默退出。这里显式内联 PYTHONPATH 并用 nohup。
  (
    cd "$KERNEL_DIR" &&
      PYTHONPATH="$KERNEL_DIR:$KERNEL_DIR/apps/lineage-api:$KERNEL_DIR/packages/lineage-core" \
        nohup .venv/bin/python -m lineage.serve.api_server --host 127.0.0.1 --port 18080 \
        >/tmp/lineage-api.log 2>&1 &
  )
  for _ in $(seq 1 20); do
    sleep 1
    KERNEL_HEALTH="$(curl -sS --noproxy '*' --max-time 2 "$KERNEL_URL/health" 2>&1)"
    echo "$KERNEL_HEALTH" | grep -q '"success": *true' && break
  done
  echo "$KERNEL_HEALTH" | grep -q '"success": *true' || fail 1 "内核起不来（看 /tmp/lineage-api.log）" "$KERNEL_HEALTH"
  echo "  已拉起"
fi
echo "  内核口径条数：$(echo "$KERNEL_HEALTH" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("kb_metrics"))' 2>/dev/null || echo '?')"

# ---------------------------------------------------------------- [2/5] 网关
echo
echo "[2/5] 模型网关（$GATEWAY_URL）"
GATEWAY_HEALTH="$(curl -sS --noproxy '*' --max-time 3 "$GATEWAY_URL/health" 2>&1)"
if echo "$GATEWAY_HEALTH" | grep -q '"ok": *true'; then
  echo "  已在运行"
else
  echo "  未运行，尝试拉起…"
  [ -f "$ROOT/.env" ] || fail 2 "缺少 $ROOT/.env（先 cp .env.example .env 并填 DEEPSEEK_API_KEY）" "(no .env)"
  (
    set -a && . "$ROOT/.env" && set +a
    cd "$ROOT"
    PYTHONPATH="$ROOT/apps/model-gateway/src:$ROOT/packages/dip-core/src" \
      nohup "$UVICORN" model_gateway.main:app --host 127.0.0.1 --port "$GATEWAY_PORT" \
      >/tmp/model-gateway.log 2>&1 &
  )
  for _ in $(seq 1 20); do
    sleep 1
    GATEWAY_HEALTH="$(curl -sS --noproxy '*' --max-time 2 "$GATEWAY_URL/health" 2>&1)"
    echo "$GATEWAY_HEALTH" | grep -q '"ok": *true' && break
  done
  echo "$GATEWAY_HEALTH" | grep -q '"ok": *true' || fail 2 "网关起不来（看 /tmp/model-gateway.log）" "$GATEWAY_HEALTH"
  echo "  已拉起"
fi
echo "  网关配置：$GATEWAY_HEALTH"

# ------------------------------------------------------- [3/5] 外壳（dsh，可选）
echo
echo "[3/5] 对话外壳"
if [ -n "${DSH_HOME:-}" ] && [ -d "${DSH_HOME:-/nonexistent}" ]; then
  echo "  检测到 dsh（$DSH_HOME），按 dsh-setup.md 的方式启动由它负责；本演示不代启（避免起两套）。"
else
  echo "  未配置 DSH_HOME —— 本链的「外壳」由内嵌驱动扮演（MCP stdio 直连 skill）。"
  echo "  这不影响 M1 验收：验收要的是「经 MCP 调到我们的 skill 并带回执」。"
fi

# ------------------------------------------------ [4/5][5/5] 驱动：skill + 网关 + 校验
echo
echo "[4/5] 经 MCP 调 skill 取凭证；[5/5] 出口事实校验"
echo "------------------------------------------------------------------"

KERNEL_URL="$KERNEL_URL" GATEWAY_URL="$GATEWAY_URL" ROOT="$ROOT" PY="$PY" QUESTION="$QUESTION" \
  "$PY" - <<'PYDRIVER'
import json
import os
import sys

ROOT = os.environ["ROOT"]
sys.path.insert(0, f"{ROOT}/packages/dip-contracts/src")
sys.path.insert(0, f"{ROOT}/packages/dip-skills/src")
sys.path.insert(0, f"{ROOT}/packages/dip-lineage-skill/src")
sys.path.insert(0, f"{ROOT}/integrations/lineage-client/src")

import anyio  # noqa: E402
import httpx  # noqa: E402
from dip_contracts.guards import NUMBER_RE, TABLE_RE, Whitelist, check_answer, normalize_number  # noqa: E402
from lineage_client import LineageClient  # noqa: E402
from mcp import ClientSession, StdioServerParameters, stdio_client  # noqa: E402

QUESTION = os.environ["QUESTION"]
KERNEL_URL = os.environ["KERNEL_URL"]
GATEWAY_URL = os.environ["GATEWAY_URL"]
TIMEOUT = 120.0


def die(step, message, actual=""):
    print(f"\n❌ 卡在第 {step} 步：{message}")
    if actual:
        print(f"   实际返回：{actual}")
    sys.exit(3)


async def call_skill(question: str) -> dict:
    """经 MCP stdio 调血缘 skill —— 这就是"外壳调我们的 skill"这一步。"""
    env = dict(os.environ)
    env["LINEAGE_BASE"] = KERNEL_URL
    env["PYTHONPATH"] = os.pathsep.join(
        [
            ROOT,
            f"{ROOT}/packages/dip-contracts/src",
            f"{ROOT}/packages/dip-skills/src",
            f"{ROOT}/packages/dip-lineage-skill/src",
            f"{ROOT}/integrations/lineage-client/src",
        ]
    )
    params = StdioServerParameters(command=sys.executable, args=["-m", "dip_lineage_skill"], env=env, cwd=ROOT)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=TIMEOUT) as session:
            await session.initialize()
            tools = [t.name for t in (await session.list_tools()).tools]
            print(f"  ✓ MCP 握手成功；tools/list → {tools}")
            result = await session.call_tool(tools[0], {"question": question})
            return json.loads(result.content[0].text)


def kb_evidence(keyword: str) -> tuple[dict, str]:
    """另外两类凭证：字段词表 + 口径公式（直连内核只读端点）。"""
    with LineageClient(KERNEL_URL) as client:
        search = client.search(keyword)
        metric = client.metric(keyword)
    return (search.data, metric.data), f"{search.endpoint} / {metric.endpoint}"


def _walk(node, keys: set[str], out: set[str]) -> None:
    """递归收集 JSON 里指定键名下的字符串值（键名取自内核真实响应，不自造）。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys and isinstance(value, str) and value:
                out.add(value)
            _walk(value, keys, out)
    elif isinstance(node, list):
        for item in node:
            _walk(item, keys, out)


TABLE_KEYS = {"table_name", "target_table", "source_table", "start_table"}
FIELD_KEYS = {"column_name", "target_column", "source_column", "field_name"}
COUNT_KEYS = {"upstream_count", "edge_count", "column_lineage_count"}


def collect_identifiers(*payloads: dict) -> tuple[set[str], set[str]]:
    tables: set[str] = set()
    fields: set[str] = set()
    for payload in payloads:
        _walk(payload, TABLE_KEYS, tables)
        _walk(payload, FIELD_KEYS, fields)
    return tables, fields


def collect_counts(payload: dict) -> set[str]:
    counts: set[str] = set()

    def visit(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in COUNT_KEYS and isinstance(value, int):
                    counts.add(normalize_number(str(value)))
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(payload)
    return counts


def main() -> int:
    print(f"  问题：{QUESTION}")

    outcome = anyio.run(call_skill, QUESTION)
    receipt = outcome["receipt"]
    if not receipt.get("ok"):
        die(4, f"skill 调用失败（回执 ok=false）：{receipt.get('error')}", json.dumps(receipt, ensure_ascii=False))
    print(f"  ✓ skill 回执：endpoint={receipt['endpoint']} ms={receipt['ms']} "
          f"attempts={receipt['attempts']} evidence_count={receipt['evidence_count']}")

    (search_data, metric_data), endpoints = kb_evidence("产量")
    metrics = (search_data.get("groups", {}) or {}).get("metrics") or []
    metric_hits = metric_data.get("metrics") or metric_data.get("items") or []

    # ---- 三条凭证 ----
    evidence = [
        {
            "kind": "血缘图",
            "endpoint": receipt["endpoint"],
            "source": "经 MCP skill（lineage_analyze）",
            "summary": f"上游 {outcome['lineage'].get('upstream_count')} 张表 / "
                       f"{outcome['lineage'].get('edge_count')} 条边；涉及 {len(outcome['tables'])} 张表",
            "ref": "graph:" + (outcome["tables"][0] if outcome["tables"] else QUESTION),
        },
        {
            "kind": "字段词表",
            "endpoint": endpoints.split(" / ")[0],
            "source": "内核客户端直连（只读）",
            "summary": f"命中 {len(metrics)} 条口径相关词条",
            "ref": f"kb_search:产量",
        },
        {
            "kind": "口径公式",
            "endpoint": endpoints.split(" / ")[-1],
            "source": "内核客户端直连（只读）",
            "summary": f"命中 {len(metric_hits)} 条口径定义",
            "ref": "metric:产量",
        },
    ]
    for i, item in enumerate(evidence, 1):
        print(f"  凭证 {i}｜{item['kind']:<6} {item['endpoint']:<16} {item['summary']}")

    # ---- 白名单 ----
    # 原则：**喂给模型的每一个事实，都必须先白名单化**。否则模型照我说的写，
    # 出口校验把它当"编造"拦下 —— 那不是模型的错，是驱动喂了没登记的事实。
    # （第一版就踩了这个：把"命中 20 条词条"写进事实块却没登记 20，于是被拦。）
    tables, fields = collect_identifiers(outcome["lineage"], search_data, metric_data)
    tables.update(outcome["tables"])
    numbers = collect_counts(outcome["lineage"])

    # 事实块本身先成形（**不含端点名/内部 ref**：那些是给运维看的元数据，不是给模型的事实；
    # 端点名长得像 snake_case 字段，喂进去会让模型照抄出一堆"编造字段"）
    facts = "\n".join(f"- {e['kind']}：{e['summary']}" for e in evidence)

    # 事实块里出现的表名与数字，也来自内核，一并登记
    numbers.update(normalize_number(match.group(0)) for match in NUMBER_RE.finditer(facts))
    tables.update(match.group(0) for match in TABLE_RE.finditer(facts))

    whitelist = Whitelist(
        tables=frozenset(name for name in tables if name),
        fields=frozenset(name for name in fields if name),
        numbers=frozenset(numbers),
    )
    print(f"  白名单：{len(whitelist.tables)} 张表 / {len(whitelist.fields)} 个字段 / "
          f"{len(whitelist.numbers)} 个数字")

    # ---- 让模型基于凭证作答（只给它这些事实）----
    allowed = "、".join(sorted(whitelist.tables)[:12])
    prompt = (
        "你只能使用下面给定的表名、字段名与数字，不得引入任何其它表名/字段/数字。"
        "用 2 句话回答用户的问题。\n\n"
        f"用户问题：{QUESTION}\n\n可用事实：\n{facts}\n\n可引用的表：{allowed}\n"
    )
    body = {"messages": [{"role": "user", "content": prompt}], "temperature": 0.2}
    try:
        response = httpx.post(f"{GATEWAY_URL}/v1/chat/completions", json=body, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        die(5, f"模型网关不可达：{exc.__class__.__name__}", str(exc))
    if response.status_code != 200:
        die(5, f"模型网关返回 HTTP {response.status_code}", response.text[:500])
    gateway_json = response.json()
    answer = (gateway_json.get("choices") or [{}])[0].get("message", {}).get("content", "")
    usage = gateway_json.get("usage") or {}
    print(f"  ✓ 网关返回：HTTP 200  tokens={usage.get('total_tokens')}")

    # ---- [5/5] 出口事实校验 ----
    verdict = check_answer(answer, whitelist)
    print(f"  ✓ 出口校验：ok={verdict.ok} checked={verdict.checked}  {verdict.reason}")
    if not verdict.ok:
        print("\n答案全文：")
        print(answer)
        die(5, "模型输出越界（编造了回执里没有的东西），已拦下", json.dumps(
            [v.model_dump() for v in verdict.violations], ensure_ascii=False))

    # ---- 结论块 ----
    print()
    print("=" * 66)
    print("M1 验收结论")
    print("=" * 66)
    print(f"问题：{QUESTION}")
    print()
    print("答案：")
    print(f"  {answer.strip()}")
    print()
    print("凭证（3 条）：")
    for i, item in enumerate(evidence, 1):
        print(f"  {i}. [{item['kind']}] {item['summary']}")
        print(f"     来源端点：{item['endpoint']}    {item['ref']}")
        print(f"     取数方式：{item['source']}")
    print()
    print("回执与耗时：")
    print(f"  skill 回执：endpoint={receipt['endpoint']}  耗时={receipt['ms']}ms  "
          f"尝试次数={receipt['attempts']}  证据条数={receipt['evidence_count']}  "
          f"usable={receipt.get('usable')}")
    print(f"  模型网关：tokens={usage.get('total_tokens')}（prompt {usage.get('prompt_tokens')} / "
          f"completion {usage.get('completion_tokens')}）")
    print(f"  出口校验：通过（{verdict.checked} 项事实全部来自回执）")
    print()
    print("✅ M1 垂直切片跑通：经 MCP 调 skill + 带凭证 + 回执 + 出口校验")
    return 0


sys.exit(main())
PYDRIVER

DRIVER_STATUS=$?
if [ "$DRIVER_STATUS" -ne 0 ]; then
  echo
  echo "❌ 驱动以状态码 $DRIVER_STATUS 退出（见上面的实际返回）。证据：$OUT"
  exit "$DRIVER_STATUS"
fi

echo
echo "证据已写入：$OUT"

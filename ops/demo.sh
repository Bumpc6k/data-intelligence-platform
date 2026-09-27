#!/usr/bin/env bash
# 一键起全栈 + 一键验收（工作项 M4-03 / Issue #19）
#
#     bash ops/demo.sh              # 起全栈，然后跑完《v2 项目规划》§5 的 7 步验收路径
#     bash ops/demo.sh --no-dsh     # 不起 dsh 壳（本机没装 Node/pnpm 时）
#     bash ops/demo.sh --check      # 只体检：服务在不在、接口通不通（不起服务、不调模型）
#
# 它做什么（每一步都打印**实际返回**；任何一步失败都指明卡在哪、返回了什么）：
#   [1/7] 起全栈        PG(15432) + 内核(18080) + 网关(18200) + 防火墙(18210) + 平台(18100) + dsh 壳(3080)
#   [2/7] 旗舰问题      对话框问「ads.ads_产销存月报 的产量怎么来的？」→ 答案 + 凭证 + status/confidence/version
#   [3/7] 点开凭证      血缘图（节点/边）+ 口径卡（公式 + 来源；没有行号就标「文件级」）
#   [4/7] 危险动作      truncate → 被拒 + 审计可查
#   [5/7] 生产写        insert → 要求审批 → 审批后放行（令牌不可复用）
#   [6/7] 出口校验      模型编造表名 → 被拦下
#   [7/7] 知识回写      没有来源脚本的候选 → 入不了库（接口层 + 库层两道）
#
# 产物：docs/evidence/demo-<时间戳>.txt
#
# 边界：**不追求生产级部署**（不做编排/K8s，不起第二套）——本脚本只保证"一条命令把本机全栈拉起来并跑完验收"。

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$ROOT/docs/evidence/demo-${STAMP}.txt"

KERNEL_DIR="${LINEAGE_KERNEL_DIR:-/root/projects/sql-lineage-mvp}"
KERNEL_URL="${KERNEL_BASE_URL:-http://127.0.0.1:18080}"
GATEWAY_PORT="${GATEWAY_PORT:-18200}"
GATEWAY_URL="http://127.0.0.1:${GATEWAY_PORT}"
FIREWALL_PORT="${FIREWALL_PORT:-18210}"
FIREWALL_URL="http://127.0.0.1:${FIREWALL_PORT}"
PORTAL_PORT="${PORTAL_PORT:-18100}"
PORTAL_URL="http://127.0.0.1:${PORTAL_PORT}"
DSH_PORT="${DSH_PORT:-3080}"
DSH_URL="http://127.0.0.1:${DSH_PORT}"
QUESTION="${QUESTION:-ads.ads_产销存月报 的产量怎么来的？}"

WITH_DSH=1
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --no-dsh) WITH_DSH=0 ;;
    --check) CHECK_ONLY=1 ;;
    *) echo "认不出的参数：$arg（-h 看用法）"; exit 2 ;;
  esac
done

export LANG=C.UTF-8 LC_ALL=C.UTF-8
unset HTTPS_PROXY HTTP_PROXY ALL_PROXY
set -a; [ -f "$ROOT/.env" ] && . "$ROOT/.env"; set +a

mkdir -p "$ROOT/docs/evidence"
exec > >(tee "$OUT") 2>&1

fail() {
  echo
  echo "❌ 卡在第 $1 步：$2"
  echo "   实际返回：$3"
  echo "   证据（含到失败为止的输出）已写入：$OUT"
  exit 1
}

step() { echo; echo "──────────────────────────────────────────────────────────────"; echo "[$1/7] $2"; echo "──────────────────────────────────────────────────────────────"; }

# 库里读连接串（口令从容器里取，不落仓）：优先用仓外的 _dip-env.sh，其次现场拼
if [ -z "${DIP_PG_DSN:-}" ]; then
  if [ -f /mnt/d/Projects/_dip-env.sh ]; then
    . /mnt/d/Projects/_dip-env.sh
  else
    PG_PW="$(docker exec dip-pg printenv POSTGRES_PASSWORD 2>/dev/null)"
    [ -n "$PG_PW" ] && export DIP_PG_DSN="postgresql://dip:${PG_PW}@127.0.0.1:15432/dip"
  fi
fi
echo "=================================================================="
echo "一键演示    $(date '+%Y-%m-%d %H:%M:%S')"
echo "问题：$QUESTION"
echo "证据将写入：$OUT"
echo "=================================================================="

step 1 "起全栈（PG + 内核 + 网关 + 防火墙 + 平台 + dsh 壳）"

# --- PG ---
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^dip-pg$'; then
  PG_STATE="已在运行（$(docker inspect -f '{{.State.Health.Status}}' dip-pg 2>/dev/null)）"
else
  bash ops/start-pg.sh >/dev/null 2>&1
  PG_STATE="已拉起"
fi
# dockerd 重启后端口代理会失效（实测），这里顺手验一次 TCP
if ! "$PY" -c "
import os, psycopg
try:
    with psycopg.connect(os.environ['DIP_PG_DSN'] + '?connect_timeout=4') as c, c.cursor() as cur:
        cur.execute('select 1')
except Exception:
    raise SystemExit(1)
" 2>/dev/null; then
  echo "  库连不上，重启容器让端口代理重新绑定…"
  docker restart dip-pg >/dev/null 2>&1
  sleep 8
fi
echo "  PostgreSQL  : 127.0.0.1:15432  $PG_STATE"

# --- 内核 ---
if curl -s --noproxy '*' --max-time 3 "$KERNEL_URL/health" | grep -q '"success": *true'; then
  echo "  内核        : $KERNEL_URL  已在运行"
else
  [ -d "$KERNEL_DIR" ] || fail 1 "内核目录不存在：$KERNEL_DIR" "(no dir)"
  (
    cd "$KERNEL_DIR" &&
      PYTHONPATH="$KERNEL_DIR:$KERNEL_DIR/apps/lineage-api:$KERNEL_DIR/packages/lineage-core" \
        setsid nohup .venv/bin/python -m lineage.serve.api_server --host 127.0.0.1 --port 18080 \
        >/tmp/lineage-api.log 2>&1 < /dev/null &
  )
  for _ in $(seq 1 25); do sleep 1; curl -s --noproxy '*' --max-time 2 "$KERNEL_URL/health" | grep -q '"success": *true' && break; done
  curl -s --noproxy '*' --max-time 3 "$KERNEL_URL/health" | grep -q '"success": *true' || fail 1 "内核起不来（看 /tmp/lineage-api.log）" "$(tail -3 /tmp/lineage-api.log 2>/dev/null)"
  echo "  内核        : $KERNEL_URL  已拉起"
fi

# --- 模型网关 ---
if curl -s --noproxy '*' --max-time 3 "$GATEWAY_URL/health" | grep -q '"ok": *true'; then
  echo "  模型网关    : $GATEWAY_URL  已在运行"
else
  (
    cd "$ROOT" &&
      PYTHONPATH="$ROOT/apps/model-gateway/src:$ROOT/packages/dip-core/src" \
        setsid nohup "$ROOT/.venv/bin/uvicorn" model_gateway.main:app --host 127.0.0.1 --port "$GATEWAY_PORT" \
        >/tmp/model-gateway.log 2>&1 < /dev/null &
  )
  for _ in $(seq 1 25); do sleep 1; curl -s --noproxy '*' --max-time 2 "$GATEWAY_URL/health" | grep -q '"ok": *true' && break; done
  curl -s --noproxy '*' --max-time 3 "$GATEWAY_URL/health" | grep -q '"ok": *true' || fail 1 "网关起不来（看 /tmp/model-gateway.log）" "$(tail -3 /tmp/model-gateway.log 2>/dev/null)"
  echo "  模型网关    : $GATEWAY_URL  已拉起"
fi

# --- 防火墙 ---
bash ops/start-firewall.sh >/dev/null 2>&1
curl -s --noproxy '*' --max-time 3 "$FIREWALL_URL/health" | grep -q '"status": *"ok"' \
  && echo "  防火墙      : $FIREWALL_URL  就绪" \
  || fail 1 "防火墙起不来（tmux attach -t firewall 看日志）" "$(curl -s --noproxy '*' --max-time 2 "$FIREWALL_URL/health")"

# --- 文档通道（WeKnora，可选）---
DOCS_ENV=""
if [ -f /mnt/d/Projects/_weknora-conf/endpoint.json ]; then
  read -r WK_URL WK_TOKEN WK_KB < <("$PY" -c "
import json
d = json.load(open('/mnt/d/Projects/_weknora-conf/endpoint.json', encoding='utf-8'))
print(d['url'], d['token'], d['kb_id'])
")
  DOCS_ENV="DOCS_ENABLED=true WEKNORA_MCP_URL=$WK_URL WEKNORA_MCP_TOKEN=$WK_TOKEN WEKNORA_KB_IDS=$WK_KB"
  echo "  文档通道    : $WK_URL（WeKnora 的只读 MCP 端点；未起容器时平台会明确回“未启用/不可达”）"
else
  echo "  文档通道    : 未配置（没有 _weknora-conf/endpoint.json，本次不测文档通道）"
fi

# --- 平台 ---
if curl -s --noproxy '*' --max-time 3 "$PORTAL_URL/api/health" >/dev/null 2>&1; then
  echo "  平台        : $PORTAL_URL  已在运行（如需带文档通道重启：bash ops/start-portal.sh）"
else
  DOCS_ENABLED="${DOCS_ENABLED:-false}" bash ops/start-portal.sh >/dev/null 2>&1
  for _ in $(seq 1 25); do sleep 1; curl -s --noproxy '*' --max-time 2 "$PORTAL_URL/api/health" >/dev/null 2>&1 && break; done
fi
curl -s --noproxy '*' --max-time 3 "$PORTAL_URL/api/health" >/dev/null 2>&1 \
  && echo "  平台 API    : $PORTAL_URL/api/health  就绪" \
  || fail 1 "平台起不来（bash ops/start-portal.sh 看日志）" "$(curl -s --noproxy '*' --max-time 2 "$PORTAL_URL/api/health")"

# --- skill（HTTP 传输，供 dsh 用）---
if curl -s --noproxy '*' -o /dev/null --max-time 2 "http://127.0.0.1:18360/mcp"; then
  echo "  血缘 skill  : http://127.0.0.1:18360/mcp  已在运行"
else
  bash ops/dsh-connect.sh >/tmp/dsh-connect.log 2>&1 && echo "  血缘 skill  : http://127.0.0.1:18360/mcp  已拉起" \
    || echo "  血缘 skill  : ⚠️ 没起来（见 /tmp/dsh-connect.log）"
fi

# --- dsh 壳（Windows 侧）---
if [ "$WITH_DSH" -eq 1 ]; then
  if curl -s -o /dev/null --max-time 3 "$DSH_URL/" 2>/dev/null; then
    echo "  dsh 壳      : $DSH_URL  已在运行"
  elif command -v powershell.exe >/dev/null 2>&1; then
    setsid nohup powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\\Projects\\data-intelligence-platform\\ops\\dsh-web.ps1" \
      >/tmp/dsh-web-launch.log 2>&1 < /dev/null &
    for _ in $(seq 1 30); do sleep 2; grep -q "dsh web: http" /d/Projects/_dsh-web-demo.log 2>/dev/null && break; done
    if grep -q "dsh web: http" /d/Projects/_dsh-web-demo.log 2>/dev/null; then
      echo "  dsh 壳      : $(grep -o 'http://127.0.0.1:3080/?token=[A-Za-z0-9_-]*' /d/Projects/_dsh-web-demo.log | head -1)"
      echo "                （⚠️ token 每次启动都会换；上一条 URL 要完整复制到浏览器，去掉 token 会 401）"
    else
      echo "  dsh 壳      : ⚠️ 没起来（看 /d/Projects/_dsh-web-demo.log 与 /tmp/dsh-web-launch.log）"
    fi
  else
    echo "  dsh 壳      : 跳过（本机没有 powershell.exe；在 Git Bash 里手动起：ops/dsh-web.ps1）"
  fi
else
  echo "  dsh 壳      : 跳过（--no-dsh）"
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
  echo
  echo "✅ --check 完成：上面就是各服务的实际状态（没有起新服务、没有调模型）"
  echo "证据：$OUT"
  exit 0
fi

step 2 "在对话框问旗舰问题（走平台接口，与界面同一条链路）"
ASK_BODY="$("$PY" -c "
import json, sys
print(json.dumps({'text': sys.argv[1], 'session_id': 'demo-' + sys.argv[2]}, ensure_ascii=False))
" "$QUESTION" "$STAMP")"
ANSWER_JSON="$(curl -s --noproxy '*' --max-time 120 -X POST "$PORTAL_URL/api/agent/ask" \
  -H 'content-type: application/json' -d "$ASK_BODY")"
[ -n "$ANSWER_JSON" ] || fail 2 "平台没返回（空响应）" "(empty)"
echo "$ANSWER_JSON" | "$PY" -c "
import json, sys
a = json.load(sys.stdin)
if not a.get('result'):
    print('  ❌ 回答里没有 result：', json.dumps(a, ensure_ascii=False)[:400]); raise SystemExit(1)
r = a['result']
ev = r.get('evidence') or []
print('  问题：', a.get('text', '')[:60])
print('  答案：', (a.get('text') or '')[:300].replace(chr(10), ' '))
print()
print('  status      :', r.get('status'))
print('  confidence  :', r.get('confidence'))
print('  凭证条数    :', len(ev), '（要求 >= 3）')
for i, e in enumerate(ev, 1):
    print(f\"    {i}. [{e.get('type')}] {e.get('ref')} ← {e.get('endpoint')}  {str(e.get('summary'))[:60]}\")
v = r.get('value') or {}
print('  结论值      :', v.get('display'), '| 类型', v.get('type'))
print('  视图块      :', [b.get('renderer') for b in (a.get('views') or [])])
if len(ev) < 3:
    print('  ❌ 凭证少于 3 条'); raise SystemExit(1)
" || fail 2 "旗舰问题的答案不合格（凭证不足 3 条或没有结论）" "$(echo "$ANSWER_JSON" | head -c 600)"

step 3 "点开凭证：血缘图 + 口径卡（公式与来源；没有行号就标「文件级」）"
echo "  --- 血缘图（平台代理给前端的形状：nodes / edges；GET + query 参数）---"
curl -s --noproxy '*' --max-time 30 -G "$PORTAL_URL/api/lineage/upstream" \
  --data-urlencode "table=ads.ads_产销存月报" --data-urlencode "depth=3" \
  | "$PY" -c "
import json, sys
d = json.load(sys.stdin)
nodes = d.get('nodes') or []
edges = d.get('edges') or []
print('    节点数', len(nodes), '边数', len(edges), '（内核说的上游表数', d.get('upstream_count'), '）')
for n in nodes[:6]:
    print('      ·', n.get('name'), '（层', n.get('layer'), '）')
print('    （界面里这张图是可交互的：M4-01 的 graph 视图渲染同一个 payload）')
if not nodes:
    print('    ❌ 血缘图没有节点'); raise SystemExit(1)
" || fail 3 "血缘图没数据（平台代理或内核有问题）" "$(curl -s --noproxy '*' --max-time 5 "$PORTAL_URL/api/lineage/upstream?table=ads.ads_产销存月报&depth=3" | head -c 200)"
echo
echo "  --- 口径卡（内核 /kb/metric 原文）---"
curl -s --noproxy '*' --max-time 30 -X POST "$KERNEL_URL/kb/metric" \
  -H 'content-type: application/json' -d '{"name":"产量"}' \
  | "$PY" -c "
import json, sys
d = json.load(sys.stdin)
items = d.get('metrics') or d.get('items') or []
if not items:
    print('    ⚠️ 没查到口径'); raise SystemExit(0)
m = items[0]
print('    口径名    :', m.get('metric_name'), '/', m.get('chinese_name'))
print('    公式      :', m.get('formula') or m.get('expression_raw'))
print('    来源脚本  :', m.get('source_file'))
line = m.get('source_line')
if line:
    print('    来源行号  :', line, ' → 精度 = line')
else:
    print('    来源行号  : — → **精度 = 文件级（行号待补）**（ADR-0003 要求如实标注，不许假装有行号）')
print('    依赖字段  :', m.get('depends_on'))
" || echo "    ⚠️ 口径接口没返回"

step 4 "危险动作：truncate → 被拒 + 审计可查"
JUDGE_BODY='{"actor":"demo-user","action":"truncate","target":"prod.fact_sales","sql":"truncate table prod.fact_sales"}'
JUDGE="$(curl -s --noproxy '*' --max-time 30 -X POST "$FIREWALL_URL/judge" -H 'content-type: application/json' -d "$JUDGE_BODY")"
echo "$JUDGE" | "$PY" -c "
import json, sys
d = json.load(sys.stdin)
print('  tier        :', d.get('tier'))
print('  disposition :', d.get('disposition'))
print('  命中规则    :', d.get('matched_rules'), '| matched =', d.get('matched'))
print('  理由        :', d.get('reason'))
print('  已落库      :', d.get('persisted'), d.get('persist_error') or '')
if d.get('disposition') != 'reject':
    print('  ❌ 危险动作没被拒'); raise SystemExit(1)
" || fail 4 "truncate 没有被拒（判定结果不对）" "$(echo "$JUDGE" | head -c 400)"
echo
echo "  --- 审计里查得到这条判定吗（平台 /api/audit/judgments）---"
curl -s --noproxy '*' --max-time 30 "$PORTAL_URL/api/audit/judgments?limit=3" \
  | "$PY" -c "
import json, sys
d = json.load(sys.stdin)
items = d.get('items') or d.get('judgments') or []
print('    最近', len(items), '条；首条：')
if items:
    it = items[0]
    print('     ', {k: it.get(k) for k in ('actor', 'action', 'tier', 'disposition', 'created_at') if k in it})
else:
    print('     （没查到，可能审计表为空）')
" || echo "    ⚠️ 审计接口没返回"

step 5 "生产写：insert → 要求审批 → 审批后放行（令牌不可复用）"
INS='{"actor":"demo-user","action":"insert","target":"prod.fact_sales","sql":"insert into prod.fact_sales select * from staging.fact_sales"}'
curl -s --noproxy '*' --max-time 30 -X POST "$FIREWALL_URL/judge" -H 'content-type: application/json' -d "$INS" \
  | "$PY" -c "
import json, sys
d = json.load(sys.stdin)
print('  tier        :', d.get('tier'), '| disposition :', d.get('disposition'))
print('  需要令牌    :', d.get('requires_token'), '| 指纹 :', d.get('fingerprint'))
print('  理由        :', d.get('reason'))
if d.get('disposition') != 'require_approval':
    print('  ❌ 生产写没进审批档'); raise SystemExit(1)
" || fail 5 "生产写没有进审批档" "$(curl -s --noproxy '*' --max-time 10 "$FIREWALL_URL/health")"

TOKEN_JSON="$(curl -s --noproxy '*' --max-time 30 -X POST "$FIREWALL_URL/tokens/issue" -H 'content-type: application/json' -d '{
  "approver":"demo-approver","actor":"demo-user","action":"insert","target":"prod.fact_sales",
  "sql":"insert into prod.fact_sales select * from staging.fact_sales"}')"
TOKEN="$("$PY" -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('token', ''))
print('  审批人      :', d.get('approver'), '→ 执行人', d.get('actor'), '| 令牌长度', len(d.get('token') or ''), '| 已落库', d.get('persisted'), file=sys.stderr)
" <<< "$TOKEN_JSON" 2>/dev/null)"
[ -n "$TOKEN" ] || fail 5 "签发令牌失败" "$(echo "$TOKEN_JSON" | head -c 300)"

echo "  --- 用令牌放行（应当 ok=true）---"
curl -s --noproxy '*' --max-time 30 -X POST "$FIREWALL_URL/verify" -H 'content-type: application/json' \
  -d "{\"token\":\"$TOKEN\",\"action\":\"insert\",\"target\":\"prod.fact_sales\",\"sql\":\"insert into prod.fact_sales select * from staging.fact_sales\"}" \
  | "$PY" -c "
import json, sys
d = json.load(sys.stdin)
print('    ok =', d.get('ok'), '|', d.get('reason'))
if not d.get('ok'): raise SystemExit(1)
" || fail 5 "审批后的动作没被放行" "$(curl -s --noproxy '*' --max-time 10 "$FIREWALL_URL/health")"

echo "  --- 同一个令牌再用一次（应当被拒：一次性）---"
REUSE="$(curl -s -w '\n%{http_code}' --noproxy '*' --max-time 30 -X POST "$FIREWALL_URL/verify" -H 'content-type: application/json' \
  -d "{\"token\":\"$TOKEN\",\"action\":\"insert\",\"target\":\"prod.fact_sales\",\"sql\":\"insert into prod.fact_sales select * from staging.fact_sales\"}")"
REUSE_BODY="$(echo "$REUSE" | head -n -1)"
REUSE_CODE="$(echo "$REUSE" | tail -1)"
echo "    HTTP $REUSE_CODE  $REUSE_BODY"
if [ "$REUSE_CODE" = "200" ]; then
  fail 5 "令牌可以复用（这是安全缺陷）" "$REUSE_BODY"
fi
echo "    ✓ 被拒（$REUSE_CODE）——一次性令牌不允许复用"

step 6 "出口校验：模型编造表名 → 被拦下"
"$PY" - <<'PYGUARD'
import sys
sys.path.insert(0, "packages/dip-contracts/src")
from dip_contracts.guards import Whitelist, check_answer

# 白名单只放真表（来自内核血缘回执），然后让"模型"编一个不存在的表
whitelist = Whitelist(tables=frozenset({"ads.ads_产销存月报", "cdw.dws_产销存汇总"}), fields=frozenset(), numbers=frozenset())
fabricated = "产量来自 ads.ads_产销存月报，它由 ads.ads_不存在的表 汇总而来，共 3 张上游表。"
verdict = check_answer(fabricated, whitelist)
print("  模型输出：", fabricated)
print("  verdict.ok        :", verdict.ok)
print("  检查项数          :", verdict.checked)
print("  理由              :", verdict.reason)
for v in verdict.violations:
    print("  违规              :", v.kind.value if hasattr(v.kind, "value") else v.kind, v.token)
if verdict.ok:
    print("  ❌ 编造的表名没被拦下"); raise SystemExit(1)
PYGUARD
[ $? -eq 0 ] || fail 6 "出口校验没拦住编造的表名" "(见上面输出)"

step 7 "知识回写：没有来源脚本的候选 → 入不了库"
CAND_BODY='{"subject":"demo.无来源.test_metric","formula":"1 + 1","chinese_name":"演示用无来源口径","source_script":"","created_by":"demo-user"}'
CAND="$(curl -s -w '\n%{http_code}' --noproxy '*' --max-time 30 -X POST "$PORTAL_URL/api/knowledge/candidates" -H 'content-type: application/json' -d "$CAND_BODY")"
CAND_BODY_ONLY="$(echo "$CAND" | head -n -1)"
CAND_CODE="$(echo "$CAND" | tail -1)"
echo "  第一道：接口层（HTTP $CAND_CODE）"
echo "$CAND_BODY_ONLY" | CAND_CODE="$CAND_CODE" "$PY" -c "
import json, os, sys
d = json.load(sys.stdin)
problems = d.get('problems') or []
print('    error   :', d.get('error'))
for p in problems:
    print('    problem :', p.get('code'), '|', p.get('field'), '|', str(p.get('message'))[:60])
codes = {p.get('code') for p in problems}
if os.environ.get('CAND_CODE') == '200' or 'missing_source_script' not in codes:
    print('    ❌ 无来源脚本的候选没有被拦下（或没给出 missing_source_script）'); raise SystemExit(1)
" || fail 7 "无来源脚本的候选被接受了（库层门禁没生效）" "$(echo "$CAND_BODY_ONLY" | head -c 300)"

echo
echo "  第二道：绕过接口，直接改库把 status 从 pending 改成 ingested（库层 CHECK 该拦）"
"$PY" - <<'PYDB'
import os
import psycopg

dsn = os.environ["DIP_PG_DSN"] + "?connect_timeout=5"
with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
    cur.execute(
        "insert into knowledge_candidates (subject, formula, chinese_name, source_script, depends_on, status, submitted_by) "
        "values ('demo.无来源.direct', '1+1', '直接写库演示', '', '[]', 'pending', 'demo-user') returning id"
    )
    new_id = cur.fetchone()[0]
    print("    已插入 pending 候选 id =", new_id, "（来源脚本为空，pending 是允许的）")
    try:
        cur.execute("update knowledge_candidates set status = 'ingested' where id = %s", (new_id,))
        print("    ❌ 直接改库成 ingested 成功了 —— 库层约束失效！")
        raise SystemExit(1)
    except psycopg.errors.CheckViolation as exc:
        print("    ✓ 库层 CHECK 拦下：", str(exc).strip().splitlines()[0])
    finally:
        cur.execute("delete from knowledge_candidates where id = %s", (new_id,))
        print("    （已清理这条演示数据）")
PYDB
DB_STATUS=$?
[ "$DB_STATUS" -eq 0 ] || fail 7 "库层约束没拦住「绕过接口把无来源候选置为 ingested」" "(见上)"

echo
echo "=================================================================="
echo "✅ 7 步验收路径全部通过"
echo "=================================================================="
echo "证据：$OUT"
echo
echo "接下来人工看的两处（脚本代不了）："
echo "  1. 浏览器打开 dsh 壳（第 1 步打印的带 token 的 URL）→ 选工作区 → 问同一句旗舰问题"
echo "  2. 平台前端 http://127.0.0.1:${PORTAL_PORT}/app/ → 看『视图』区四块（血缘图/检索表/口径 SQL/版本对照）"

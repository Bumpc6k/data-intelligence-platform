#!/usr/bin/env bash
# 把提炼报告的候选**按人工抽检结论**入库（走完整审核流：提交 → 审核 → 入库）
#
#     bash ops/ingest-refine.sh                # 读 docs/evidence/refine/refine-report.json 并入库
#     bash ops/ingest-refine.sh --dry-run      # 只看会做什么，不动库
#     bash ops/ingest-refine.sh 路径/报告.json  # 指定报告
#
# 前置：平台在跑（默认 http://127.0.0.1:18100）；库连着（DIP_PG_DSN 或仓外 _dip-env.sh）
#
# 规矩（都是 Issue #16 定的，这里只是执行）：
#   1. **抽检合格前一条都不许入库** —— 本脚本只在仓库所有者明确放行后才跑（放行原话记录在证据里）；
#   2. 报告里 `check.ok == false` 的候选**走 reject**（写明契约层给出的问题），不入库；
#   3. 每一条都走真审核流（提交 → review approve → ingest），不绕过接口直接写库；
#   4. 幂等：已提交过（同 subject + 同公式）的候选跳过并写明原因，重复跑不会灌一堆重复行；
#   5. 前后计数都打出来 —— 入库这件事必须能从数字上看出来。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"
PORTAL_URL="${PORTAL_URL:-http://127.0.0.1:18100}"
REPORT="${1:-$ROOT/docs/evidence/refine/refine-report.json}"
DRY_RUN=0
for arg in "$@"; do [ "$arg" = "--dry-run" ] && DRY_RUN=1; done
[ "$REPORT" = "--dry-run" ] && REPORT="$ROOT/docs/evidence/refine/refine-report.json"

export LANG=C.UTF-8 LC_ALL=C.UTF-8
unset HTTPS_PROXY HTTP_PROXY ALL_PROXY
[ -f /mnt/d/Projects/_dip-env.sh ] && . /mnt/d/Projects/_dip-env.sh

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$ROOT/docs/evidence/refine-ingest-${STAMP}.txt"
mkdir -p "$ROOT/docs/evidence"
exec > >(tee "$OUT") 2>&1

REVIEWER="${REVIEWER:-Bumpc6k}"
INGESTED_BY="${INGESTED_BY:-Bumpc6k}"
REVIEW_REASON="${REVIEW_REASON:-人工抽检通过（仓库所有者 2026-09-27 原话：我已经抽检完成，md可以入库。）}"

echo "=================================================================="
echo "提炼候选入库（按抽检结论）    $(date '+%Y-%m-%d %H:%M:%S')"
echo "  报告：$REPORT"
echo "  平台：$PORTAL_URL"
echo "  放行依据：$REVIEW_REASON"
echo "  模式：$([ "$DRY_RUN" -eq 1 ] && echo '--dry-run（不动库）' || echo '真入库')"
echo "=================================================================="

[ -f "$REPORT" ] || { echo "❌ 报告不存在：$REPORT"; exit 1; }
curl -s --noproxy '*' --max-time 5 "$PORTAL_URL/api/health" >/dev/null 2>&1 || { echo "❌ 平台没在跑：$PORTAL_URL（先 bash ops/start-portal.sh）"; exit 1; }

# 前后计数：**直接问库**（列表接口是按 limit 取的，没有全量 total，别拿它当计数）
count_of() {  # $1 = knowledge_candidates|knowledge_metrics
  "$PY" - "$1" <<'PYCOUNT' 2>/dev/null || echo "?"
import os
import sys

import psycopg

table = sys.argv[1]
dsn = os.environ["DIP_PG_DSN"]
with psycopg.connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
    cur.execute(f"select count(*) from {table}")
    print(cur.fetchone()[0])
PYCOUNT
}
BEFORE_CAND="$(count_of knowledge_candidates)"
BEFORE_METRIC="$(count_of knowledge_metrics)"
echo
echo "入库前：候选池 $BEFORE_CAND 条 / 口径库 $BEFORE_METRIC 条"

echo
echo "=== 逐条处理（提交 → 审核 → 入库；check.ok=false 的走拒绝）==="
SUMMARY="$("$PY" - "$REPORT" "$PORTAL_URL" "$REVIEWER" "$INGESTED_BY" "$REVIEW_REASON" "$DRY_RUN" <<'PYDRIVER'
import json
import sys
import time

import httpx

report_path, portal, reviewer, ingested_by, review_reason = sys.argv[1:6]
dry_run = sys.argv[6] == "1"

report = json.load(open(report_path, encoding="utf-8"))
candidates = report.get("candidates") or []

client = httpx.Client(base_url=portal, timeout=60.0)


def existing(subject: str, formula: str) -> dict | None:
    r = client.get("/api/knowledge/candidates", params={"subject": subject, "limit": 200})
    if r.status_code != 200:
        return None
    for item in r.json().get("items") or []:
        if (item.get("formula") or "").strip() == (formula or "").strip():
            return item
    return None


counts = {"ingested": 0, "rejected": 0, "skipped": 0, "failed": 0}
for i, cand in enumerate(candidates, 1):
    subject = cand.get("subject") or ""
    formula = cand.get("formula") or ""
    check = cand.get("check") or {}
    ok = bool(check.get("ok"))
    problems = check.get("problems") or []
    problems_txt = "；".join(f"{p.get('code')}" for p in problems if isinstance(p, dict)) or "-"
    label = f"[{i:02d}/{len(candidates)}] {subject}"

    if not ok:
        # 契约层已经判不合格 → 期望**连候选池都进不去**（提交就被 400 拒，比"先收下再拒"更严）
        if dry_run:
            print(f"{label} → 会被契约层直接拒收（{problems_txt}）")
            counts["rejected"] += 1
            continue
        body = {
            "kind": "metric", "subject": subject, "chinese_name": cand.get("chinese_name"),
            "formula": formula, "depends_on": cand.get("depends_on") or [],
            "source_script": cand.get("source_script"), "source_line": cand.get("source_line"),
            "note": f"来自提炼报告：{cand.get('reason') or ''}"[:500], "submitted_by": "dip-refine",
        }
        r = client.post("/api/knowledge/candidates", json=body)
        if r.status_code == 400:
            got = "；".join(p.get("code", "") for p in (r.json().get("problems") or []))
            print(f"{label} → 提交即被拒（HTTP 400，{got}）——不合格的候选连候选池都进不去")
            counts["rejected"] += 1
            continue
        if r.status_code != 201:
            print(f"{label} → 提交失败 HTTP {r.status_code}（{r.text[:120]}）")
            counts["failed"] += 1
            continue
        # 万一服务端放它进来了，也要在审核里拒掉（不留 pending 垃圾）
        cid = r.json()["candidate"]["id"]
        rv = client.post(f"/api/knowledge/candidates/{cid}/review", json={
            "decision": "reject", "reviewer": reviewer,
            "reason": f"契约层校验不合格：{problems_txt}（抽检未放行）",
        })
        print(f"{label} → 候选 #{cid} 已拒绝（理由：{problems_txt}）"
              if rv.status_code == 200 else f"{label} → 拒绝失败 HTTP {rv.status_code} {rv.text[:120]}")
        counts["rejected"] += 1
        continue

    already = existing(subject, formula)
    if already is not None:
        print(f"{label} → 跳过（已在候选池 #{already.get('id')}，状态 {already.get('status')}）")
        counts["skipped"] += 1
        continue

    if dry_run:
        print(f"{label} → 会入库（来源 {cand.get('source_script')}，公式 {formula[:40]}）")
        counts["ingested"] += 1
        continue

    body = {
        "kind": "metric", "subject": subject, "chinese_name": cand.get("chinese_name"),
        "formula": formula, "depends_on": cand.get("depends_on") or [],
        "source_script": cand.get("source_script"), "source_line": cand.get("source_line"),
        "note": f"来自提炼报告（模型理由：{cand.get('reason') or ''}）"[:800],
        "submitted_by": "dip-refine",
    }
    r = client.post("/api/knowledge/candidates", json=body)
    if r.status_code != 201:
        print(f"{label} → 提交失败 HTTP {r.status_code}（{r.text[:150]}）")
        counts["failed"] += 1
        continue
    cid = r.json()["candidate"]["id"]

    rv = client.post(f"/api/knowledge/candidates/{cid}/review", json={
        "decision": "approve", "reviewer": reviewer, "reason": review_reason, "worth_keeping": True,
    })
    if rv.status_code != 200:
        print(f"{label} → 候选 #{cid} 审核失败 HTTP {rv.status_code}（{rv.text[:150]}）")
        counts["failed"] += 1
        continue

    ig = client.post(f"/api/knowledge/candidates/{cid}/ingest", json={"ingested_by": ingested_by})
    if ig.status_code != 200:
        print(f"{label} → 候选 #{cid} 入库失败 HTTP {ig.status_code}（{ig.text[:150]}）")
        counts["failed"] += 1
        continue
    metric = (ig.json() or {}).get("metric") or {}
    print(f"{label} → 候选 #{cid} 审核通过并入库（口径版本 v{metric.get('version')}）")
    counts["ingested"] += 1
    time.sleep(0.05)

print()
print("本轮小结：" + "  ".join(f"{k}={v}" for k, v in counts.items()))
if counts["failed"]:
    print("❌ 有失败项，见上面逐条")
    raise SystemExit(1)
PYDRIVER
)"
echo "$SUMMARY"
STATUS=$?

echo
AFTER_CAND="$(count_of knowledge_candidates)"
AFTER_METRIC="$(count_of knowledge_metrics)"
echo "入库后：候选池 $AFTER_CAND 条 / 口径库 $AFTER_METRIC 条"
echo "变化  ：候选池 $((AFTER_CAND - BEFORE_CAND)) / 口径库 $((AFTER_METRIC - BEFORE_METRIC))"
echo
echo "证据：$OUT"
[ "$STATUS" -eq 0 ] || exit "$STATUS"

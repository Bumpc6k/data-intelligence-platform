#!/usr/bin/env python3
"""B2 验收演示：对**真实内核**跑三个用例，打印人话 + 原始回答对象。

用法：
    bash /usr/local/bin/start-lineage-api.sh          # 内核仓库里执行
    PYTHONPATH=packages/dip-contracts/src:packages/dip-core/src:packages/dip-agent/src:integrations/lineage-client/src \\
        .venv/bin/python demos/ask.py [--json]
"""

from __future__ import annotations

import json
import os
import sys

from dip_agent import make_agent
from lineage_client import LineageClient

BASE = os.environ.get("LINEAGE_BASE", "http://127.0.0.1:18080")

CASES: list[tuple[str, str]] = [
    ("用例 1 · 主路径", "ads.ads_产销存月报 的产量怎么来的？"),
    ("用例 2 · 影响面", "改 cdw.dws_产销存汇总 会砸哪些下游？"),
    ("用例 3 · 追问（不带表名）", "那它的上游还有哪些表？"),
    ("反例 · 没给表/字段", "你好"),
]

STATUS_CN = {
    "verified": "已核实",
    "inferred": "推导",
    "candidate": "候选",
    "unresolved": "证据不足",
    "stale": "快照过期",
}


def show(case: str, question: str, answer, *, raw: bool = False) -> None:
    r = answer.result
    print("=" * 92)
    print(f"【{case}】{question}")
    print("-" * 92)
    print(f"回答   : {answer.text}")
    print(f"结论   : {r.status.value}（{STATUS_CN.get(r.status.value, r.status.value)}）"
          f" · 置信度 {r.confidence} · 版本 {r.version or '—'}")
    if r.value:
        print(f"取值   : [{r.value.type}] {r.value.display}")
    if r.source and r.source.file:
        precise = "文件+行号" if r.source.precise else "仅文件级（行号待内核补，见 ADR-0003）"
        print(f"来源   : {r.source.file}（{precise}）")
    print(f"凭证   : {len(r.evidence)} 条")
    for e in r.evidence:
        print(f"         · [{e.type.value}] {e.ref} — {e.summary}")
    print("工具   : " + " / ".join(f"{c.name}({c.ms}ms{'✓' if c.ok else '✗'})" for c in answer.tool_calls))
    if answer.suggestions:
        print("追问   : " + " ｜ ".join(answer.suggestions))
    if raw:
        print("-" * 92)
        print(json.dumps(answer.model_dump(mode="json"), ensure_ascii=False, indent=1)[:2600])


def main() -> int:
    raw = "--json" in sys.argv
    with LineageClient(BASE) as client:
        h = client.health()
        if not h.ok:
            print(f"!! 内核不可用：{h.error}（先跑 bash /usr/local/bin/start-lineage-api.sh）")
            return 1
        agent = make_agent(client)
        print(f"内核 {BASE} 正常（{h.ms}ms）· 模式 rule（无 LLM key 也跑通，ADR-0004）\n")
        for i, (case, q) in enumerate(CASES):
            show(case, q, agent.ask(q), raw=raw and i == 0)
        print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""工具计划（W-114）：把意图变成**有序工具序列**（同组可并发）。

只读、可缓存、幂等优先；步骤数上限 6（超过说明意图没拆清楚，宁可反问）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .entity import Entities, pick_topic
from .intent import Intent, IntentResult

MAX_STEPS = 6


@dataclass(frozen=True)
class PlanStep:
    tool: str  # LineageClient 的方法名
    args: dict = field(default_factory=dict)
    group: int = 0  # 同组可并发
    purpose: str = ""


def plan(intent: IntentResult, entities: Entities, *, depth: int = 3) -> list[PlanStep]:
    """按意图生成计划；复合问句 = 各意图步骤拼接（同组并发）。"""
    steps: list[PlanStep] = []
    table = entities.table
    primary_word = pick_topic(entities)

    for idx, current in enumerate(intent.intents):
        if current is Intent.SCRIPT_ANALYZE and entities.sql:
            steps.append(PlanStep("analyze", {"sql": entities.sql, "dialect": "hive", "depth": depth}, idx,
                                  "解析脚本血缘并生成报告"))

        elif current is Intent.TABLE_LINEAGE and table:
            steps.append(PlanStep("upstream", {"table": table, "depth": depth}, idx, f"取 {table} 的上游链路"))

        elif current is Intent.METRIC_LOOKUP and primary_word:
            if table:
                # ① 该表字段的词表命中（拿中文名/置信度）；② 全局公式口径（公式可能在别的层）
                steps.append(PlanStep("search", {"keyword": f"{table} {primary_word or ''}".strip()}, idx,
                                      "取该表字段的词表命中"))
            steps.append(PlanStep("search", {"keyword": primary_word or table}, idx,
                                  "取全局口径（含公式，可能在上游层）"))

        elif current is Intent.IMPACT and table:
            steps.append(PlanStep("impact", {"table": table}, idx, f"取 {table} 的下游影响面"))

        elif current is Intent.REPORT and table:
            steps.append(PlanStep("upstream", {"table": table, "depth": depth}, idx,
                                  "报告需要脚本输入；先取链路（该表若无脚本，会如实说明）"))

    # 去重（同工具同参数只跑一次）+ 限步
    seen: set[tuple[str, str]] = set()
    unique: list[PlanStep] = []
    for s in steps:
        key = (s.tool, repr(sorted(s.args.items())))
        if key in seen:
            continue
        seen.add(key)
        unique.append(s)
    return unique[:MAX_STEPS]

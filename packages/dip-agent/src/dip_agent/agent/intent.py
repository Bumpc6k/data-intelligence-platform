"""意图判定（W-114，规则模式）——"老会计先答"。

规则表来自《B2 接口设计与评审》§2.1。内核 `/kb/ask` 的 `intent`/`intent_label` 只作**加分信号**，
不单独定案：它的实体抽取不可用，但意图分类本身是对的（实测「产量怎么来的」→ `metric_formula`）。

多意图（复合问句）不在这里拆——`planner` 会把命中的意图按固定顺序拼接；LLM 仅用于自然语言拆句（ADR-0004）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .entity import Entities, keywords


class Intent(str, Enum):
    TABLE_LINEAGE = "table_lineage"  # 这张表的数据从哪来
    METRIC_LOOKUP = "metric_lookup"  # 这个字段/指标怎么算
    IMPACT = "impact"  # 改这个会砸谁
    SCRIPT_ANALYZE = "script_analyze"  # 贴了一段 SQL
    REPORT = "report"  # 给我报告
    UNKNOWN = "unknown"


# 关键词 → 意图 的权重表（命中即加分；同一意图累加）
RULES: dict[Intent, dict[str, int]] = {
    Intent.TABLE_LINEAGE: {"怎么来的": 3, "来源": 2, "上游": 3, "血缘": 3, "链路": 2, "从哪来": 3, "数据流": 2},
    Intent.METRIC_LOOKUP: {"怎么算": 3, "口径": 3, "公式": 3, "定义": 2, "计算": 2, "怎么统计": 3, "含义": 2},
    Intent.IMPACT: {"会砸谁": 4, "砸到": 3, "下游": 3, "影响": 3, "被谁用": 3, "依赖": 2, "影响面": 3},
    Intent.REPORT: {"报告": 3, "导出": 3, "给我看": 2, "打印": 2, "生成报告": 4},
}

# 内核意图标签 → 平台意图（只做"锦上添花"的一票，+1）
KERNEL_INTENT_MAP = {
    "metric_formula": Intent.METRIC_LOOKUP,
    "metric_lookup": Intent.METRIC_LOOKUP,
    "lineage": Intent.TABLE_LINEAGE,
    "upstream": Intent.TABLE_LINEAGE,
    "impact": Intent.IMPACT,
    "report": Intent.REPORT,
    "script_analyze": Intent.SCRIPT_ANALYZE,
}


@dataclass(frozen=True)
class IntentResult:
    intents: list[Intent]
    scores: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    need_clarify: bool = False

    @property
    def primary(self) -> Intent:
        return self.intents[0] if self.intents else Intent.UNKNOWN


def classify(text: str, entities: Entities, kernel_intent: str | None = None) -> IntentResult:
    """按关键词打分；返回**按分数排序**的意图列表（复合问句会有多个）。"""
    if entities.sql:
        return IntentResult([Intent.SCRIPT_ANALYZE], {"script_analyze": 99}, ["输入里含 SQL 语句"])

    scores: dict[Intent, int] = {}
    reasons: list[str] = []
    for intent, table in RULES.items():
        hit = [kw for kw in table if kw in text]
        if hit:
            scores[intent] = sum(table[kw] for kw in hit)
            reasons.append(f"{intent.value}：命中 {('、'.join(hit))}")

    mapped = KERNEL_INTENT_MAP.get((kernel_intent or "").lower())
    if mapped:
        scores[mapped] = scores.get(mapped, 0) + 1
        reasons.append(f"{mapped.value}：内核意图 {kernel_intent} 投一票")

    # 复合意图：问了链路、同时提到字段/业务词（如「ads.ads_产销存月报 的产量怎么来的」）
    # → 既取链路，也取该字段口径。缺这一步就会答成「产量就是上游透传」而漏掉真正的公式。
    def _has_business_word() -> bool:
        from .entity import pick_topic

        return bool(pick_topic(entities))

    if Intent.TABLE_LINEAGE in scores and (entities.columns or _has_business_word()):
        scores[Intent.METRIC_LOOKUP] = max(scores.get(Intent.METRIC_LOOKUP, 0), scores[Intent.TABLE_LINEAGE])
        reasons.append('复合意图：既问链路也问该字段口径')

    # 有表/字段但没命中任何关键词：按"带字段名 → 口径；只带表名 → 血缘"兜底
    if not scores:
        if entities.columns or any(len(w) > 1 for w in entities.words):
            scores[Intent.METRIC_LOOKUP] = 1
            reasons.append("兜底：有字段或业务词 → 口径查询")
        elif entities.tables:
            scores[Intent.TABLE_LINEAGE] = 1
            reasons.append("兜底：只有表名 → 血缘查询")

    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0].value))
    intents = [i for i, _ in ordered]

    need_clarify = not intents or (not entities.tables and not entities.columns and not entities.sql and not entities.words)
    if entities.ambiguity:
        need_clarify = True
    return IntentResult(
        intents=intents,
        scores={i.value: s for i, s in ordered},
        reasons=reasons,
        need_clarify=need_clarify,
    )


def keywords_of(text: str) -> list[str]:
    """给编排层用的小工具（口径检索词）。"""
    return keywords(text)

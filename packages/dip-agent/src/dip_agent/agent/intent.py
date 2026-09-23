"""意图判定（B2 实现，工作项 W-114）。

规则表（《B2 接口设计与评审》§2.1）：血缘 / 口径 / 影响 / 报告 / 综合分析 / 闲聊与未知。
`/kb/ask` 的 `intent`/`intent_label` 只作**加分信号**，不单独定案（它的实体不可用）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    TABLE_LINEAGE = "table_lineage"
    METRIC_LOOKUP = "metric_lookup"
    IMPACT = "impact"
    SCRIPT_ANALYZE = "script_analyze"
    REPORT = "report"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    confidence: float
    reasons: list[str]


def classify(text: str, entities, kernel_intent: str | None = None) -> IntentResult:
    raise NotImplementedError("B2（W-114）：意图判定实现见模块 docstring 的规则表")

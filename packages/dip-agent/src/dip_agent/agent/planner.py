"""工具计划（B2 实现，工作项 W-114）。

把意图变成**有序工具序列**（可并发标记），例如：
  metric_lookup → [search(表+词), search(词, kinds=metrics)]
  复合问句     → 上面两条 + [impact(表)]（LLM 只用于拆句）
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlanStep:
    tool: str
    args: dict = field(default_factory=dict)
    parallel_group: int = 0


def plan(intent, entities, *, max_steps: int = 4) -> list[PlanStep]:
    raise NotImplementedError("B2（W-114）：计划生成实现见模块 docstring")

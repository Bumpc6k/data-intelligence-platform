"""答案成型（B2 实现，工作项 W-103 / W-115）。

规则模式：模板生成文本（同一个 Result 模型，措辞不同）；
LLM 模式：只允许改写措辞与生成建议追问，**不得生成事实**（ADR-0004）。
两模式的 result/value/status/evidence 必须逐字段一致——由 `tests/test_agent_rule_mode.py` 强制（B2 落地）。
"""

from __future__ import annotations

from dip_contracts import Answer, Result


def compose(result: Result, *, mode: str = "rule", suggestions: list[str] | None = None) -> Answer:
    raise NotImplementedError("B2（W-103）：答案成型实现见模块 docstring")

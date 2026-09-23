"""统一结果模型（工作项 W-103）——跨端共享的**唯一**答案结构。

对应《设计说明书》§8 与《B2 接口设计与评审》§4。三条硬约束以代码强制，而不是靠自觉：

1. `evidence` 为空 → 不允许给出结论值（铁律 1：无凭证不发布）。
2. `status` 只能是五个取值；`unresolved` 不得携带 conclusion 值。
3. `confidence` 由证据链的最小值决定（见 `status.derive_confidence`），不取平均。

前端**不得**自行推断 `status`/`confidence`：拿不到就用 `unresolved`，并显示"证据不足"。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Status(str, Enum):
    """结论可信度（界面必须可见，不能只用颜色表达）。"""

    VERIFIED = "verified"  # 来自人工维护的词表 / 脚本推导且来源明确
    INFERRED = "inferred"  # 链路成立但口径未定
    CANDIDATE = "candidate"  # 候选（模糊命中 / 待审核术语）
    UNRESOLVED = "unresolved"  # 证据不足或内核失败
    STALE = "stale"  # 内核知识库快照与平台记录不一致


class EvidenceType(str, Enum):
    LINEAGE = "lineage"
    METRIC = "metric"
    REPORT = "report"
    DICT = "dict"  # 数据字典/术语
    LOG = "log"  # 任务实例日志（后续阶段）


class Source(BaseModel):
    """来源：能落到「文件 + 行号」最好，落不到就明确写文件级（见 ADR-0003）。"""

    kind: Literal["sql_line", "script", "metric", "report", "graph", "kernel"] = "kernel"
    file: str | None = None
    line: int | None = None
    report_id: str | None = None
    detail: str | None = None

    @property
    def precise(self) -> bool:
        return bool(self.file and self.line)


class Evidence(BaseModel):
    type: EvidenceType
    ref: str = Field(description="可点开的引用，如 graph:ads.ads_产销存月报 / metric:产量@v3 / report:rpt_x")
    summary: str
    source: Source | None = None
    endpoint: str | None = Field(default=None, description="产生该证据的内核端点，如 POST /analyze")


class ToolCall(BaseModel):
    """工具步骤条上要显示的东西：真实请求 + 耗时 + 成败（不许美化）。"""

    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    ms: int = 0
    ok: bool = True
    endpoint: str | None = None
    error: str | None = None


class ResultValue(BaseModel):
    type: Literal["formula", "number", "text", "table"]
    display: str = Field(description="给人看的样子（如 产量 = 打码量 + 跳码量 − 重码量）")
    expr: str | None = Field(default=None, description="忠实表达式（如 SUM(dama_qty) + SUM(tiaoma_qty) - SUM(chongma_qty)）")


class Result(BaseModel):
    value: ResultValue | None = None
    confidence: float = 0.0
    status: Status = Status.UNRESOLVED
    evidence: list[Evidence] = Field(default_factory=list)
    source: Source | None = None
    version: str | None = Field(default=None, description="如 kb:1.0.0@2026-09-20；P2 引入平台口径审核表后换成人审版本")

    @model_validator(mode="after")
    def _enforce_no_evidence_no_conclusion(self) -> Result:
        if not self.evidence and self.value is not None:
            raise ValueError("铁律 1：没有证据（evidence 为空）时不允许给出结论值")
        if self.status is Status.UNRESOLVED and self.value is not None:
            raise ValueError("status=unresolved 时不允许携带结论值")
        return self


class Answer(BaseModel):
    """一次问答的完整回答对象——前端渲染的唯一数据源（《设计说明书》§8）。"""

    answer_id: str
    text: str
    result: Result
    suggestions: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    mode: Literal["rule", "llm"] = "rule"
    audit_id: str | None = None

    @model_validator(mode="after")
    def _suggestions_must_be_short(self) -> Answer:
        if len(self.suggestions) > 3:
            raise ValueError("建议追问最多 3 条（界面放不下，也让用户聚焦）")
        return self

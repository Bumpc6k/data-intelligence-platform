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
    #: 这一步的结果用哪个视图渲染（取自 skill 声明的 `renderer`，M4-01 / Issue #17）。
    #: `None` = 这一步没有视图（纯过程型调用），前端不该硬猜一个。
    renderer: str | None = None


#: 视图词汇表 —— 与 `dip_skills.Renderer` 是同一套（契约层不 import skills，靠测试钉住两边一致）。
RENDERERS: tuple[str, ...] = ("table", "graph", "sql", "diff")


class ViewBlock(BaseModel):
    """一块要渲染到前端的视图数据（M4-01 / Issue #17）。

    **前端按 `renderer` 分派到注册表里的视图组件**：宿主不认识具体视图，
    新增一个视图 = 加一个注册文件，不改宿主（验收②）。

    刻意把 `data` 留成自由 dict：视图各自的长相不同（血缘图要 nodes/edges、SQL 视图要脚本文本、
    diff 视图要两版对照），契约层只统一"这是哪一类视图 + 数据给谁 + 出处是哪"，
    不去规定每种视图内部字段 —— 那属于视图自己的事。
    """

    renderer: str = Field(description="视图名，必须来自 RENDERERS")
    title: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    #: 出处（哪个工具/端点带来的），让"这块图是哪来的"可追
    source: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _renderer_must_be_known(self) -> ViewBlock:
        if self.renderer not in RENDERERS:
            raise ValueError(f"未知视图 {self.renderer!r}（只认 {', '.join(RENDERERS)}；见 dip_skills 的 renderer 声明）")
        return self


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


class DocCitation(BaseModel):
    """文档材料必须指得到的出处（M3-04 验收②：引用必须标注来源）。

    形状对齐 WeKnora MCP 检索工具返回的引用字段（`knowledge_id` / `chunk_id` / `excerpt` / `url`），
    但**不依赖**它 —— 换一家文档服务也只改 client，不动这里。
    """

    document_name: str = Field(default="", description="文档名（人认得出的那个）")
    chunk_id: str = Field(default="", description="切片 id（能回到原文那一段）")
    knowledge_id: str = Field(default="", description="文档 id（WeKnora 的 knowledge_id）")
    knowledge_base_id: str = Field(default="", description="知识库 id")
    url: str | None = Field(default=None, description="可点开的原文链接（有就给）")
    position: str | None = Field(default=None, description="切片在原文里的位置（页码/序号，有就给）")

    @property
    def label(self) -> str:
        """给人看的引用标签：`文档名#切片`（没有切片就说清楚没有）。"""
        if self.document_name and self.chunk_id:
            return f"{self.document_name}#{self.chunk_id}"
        return self.document_name or self.chunk_id or "（未标注来源）"


class DocHit(BaseModel):
    """一条文档材料（一条切片）。`text` 是原文摘录，原样保留，不做改写。"""

    text: str = ""
    citation: DocCitation = Field(default_factory=DocCitation)
    score: float | None = None


class Background(BaseModel):
    """文档通道的产出 —— **只能是背景**。

    `usable_for_conclusion` 恒为 `False`，写成字段而不是靠约定：日志、界面、测试都能显式看到
    "这段材料不允许进结论"。有人想改它，得先改这个类，改不了就藏不住。
    """

    hits: list[DocHit] = Field(default_factory=list)
    channel: Literal["documents"] = "documents"
    usable_for_conclusion: Literal[False] = False

    @property
    def citation_labels(self) -> list[str]:
        return [hit.citation.label for hit in self.hits]


class Answer(BaseModel):
    """一次问答的完整回答对象——前端渲染的唯一数据源（《设计说明书》§8）。"""

    answer_id: str
    text: str
    result: Result
    suggestions: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    mode: Literal["rule", "llm"] = "rule"
    audit_id: str | None = None
    #: 文档通道（WeKnora）的背景材料。**只能挂这里**：不进 `result`、更不进 `result.evidence`
    #: （见 `dip_contracts/doc_channel.py` 的「仅背景」铁律与 M3-04 / Issue #15）。
    background: Background | None = None
    #: 要渲染的视图块（M4-01 / #17）。**前端按每块的 `renderer` 分派**，宿主不认识具体视图。
    views: list[ViewBlock] = Field(default_factory=list)

    @model_validator(mode="after")
    def _suggestions_must_be_short(self) -> Answer:
        if len(self.suggestions) > 3:
            raise ValueError("建议追问最多 3 条（界面放不下，也让用户聚焦）")
        return self

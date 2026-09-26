"""血缘分析 skill 的实现（工作项 M1-03）。

包装内核的**只读**端点：

    /upstream   表级血缘（入参：表名）
    /analyze    SQL 血缘解析（入参：SQL）

**依赖的是协议不是实现**：类型上只要求 `dip_contracts.KernelToolkit`，不 import
`lineage_client`。这样测试注入 `FakeKernel` 就能跑，不必起内核（依赖倒置，见
`dip-contracts/src/dip_contracts/kernel.py` 的设计说明）；真实适配器只在进程入口 wiring。

本模块**不做**意图识别与编排（实体识别/计划/多轮）—— 那是 dip-agent 的活，v2 会随
harness 一起迁移；这里只做"从问句里取一个 `库.表`"这一层最小抽取，取不到就明说没查到。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from dip_contracts import KernelToolkit, ToolResult
from dip_skills import SkillReceipt
from pydantic import BaseModel, ConfigDict, Field

SKILL_NAME = "lineage.analyze"
SKILL_VERSION = 1
SKILL_ID = f"{SKILL_NAME}@{SKILL_VERSION}"

# 表名形态：`库.表`。
# **必须 Unicode 感知**：用 ASCII 正则会漏检全部中文标识符，而且测试还会假绿
# （AGENTS.md §8 明确记着这个坑）。str 模式下 `\w` 本身已含 Unicode 单词字符，
# 这里仍显式写出 `\u4e00-\u9fff`，把"必须 Unicode 感知"这个意图钉在代码里、可被评审一眼看到。
TABLE_PATTERN = r"[A-Za-z_][\w\u4e00-\u9fff]*\.[\w\u4e00-\u9fff]+"
_TABLE_RE = re.compile(TABLE_PATTERN)

# 问句里出现这些关键词就按"含 SQL"处理
_SQL_RE = re.compile(r"\b(select|insert\s+overwrite|insert\s+into|with)\b", re.IGNORECASE)

_SQL_SUMMARY_LIMIT = 60


def extract_table(text: str) -> str | None:
    """从一句自然语言里取出 `库.表`；取不到返回 None（**不猜**）。"""
    match = _TABLE_RE.search(text)
    return match.group(0) if match else None


def looks_like_sql(text: str) -> bool:
    """问句里是不是带了 SQL（只看关键词，不做语法校验）。"""
    return bool(_SQL_RE.search(text))


def summarize_sql(sql: str) -> str:
    """SQL 摘要（给回执和界面看），保留首行、超长截断。"""
    first_line = sql.strip().splitlines()[0] if sql.strip() else ""
    if len(first_line) <= _SQL_SUMMARY_LIMIT:
        return first_line
    return f"{first_line[:_SQL_SUMMARY_LIMIT]}…（共 {len(sql)} 字符）"


class LineageOutcome(BaseModel):
    """skill 的返回值：结构化结果 + 回执（Issue #3 验收第 ① 条）。"""

    model_config = ConfigDict(frozen=True)

    receipt: SkillReceipt
    kind: Literal["upstream", "analyze", "none"] = Field(description="这次走的是哪条路径")
    subject: str = Field(description="分析对象：表名，或 SQL 摘要")
    tables: list[str] = Field(default_factory=list, description="涉及的表（起点 + 上游/输入/输出）")
    lineage: dict[str, Any] = Field(
        default_factory=dict,
        description="内核返回的原始结构化数据，**不改写**——任何时候都能回查内核当时到底返回了什么",
    )
    report_url: str | None = Field(default=None, description="内核落盘 HTML 报告的地址（若本次产生了）")
    message: str = Field(default="", description="没查到 / 失败时的说明；成功且有证据时为空")


class LineageSkill:
    """血缘分析 skill。只读：不碰写操作类能力，也不改内核与客户端的接口。"""

    name = SKILL_NAME
    version = SKILL_VERSION
    id = SKILL_ID

    def __init__(self, toolkit: KernelToolkit) -> None:
        self._kernel = toolkit

    # ---------------------------------------------------------------- 对外入口
    def run(
        self,
        *,
        question: str | None = None,
        sql: str | None = None,
        table: str | None = None,
        depth: int = 3,
    ) -> LineageOutcome:
        """跑一次血缘分析。三者优先级：`sql` > `table` > 从 `question` 抽取。"""
        if sql:
            return self._analyze(sql, depth=depth)
        if table:
            return self._upstream(table, depth=depth)
        if question:
            if looks_like_sql(question):
                return self._analyze(question, depth=depth)
            found = extract_table(question)
            if found:
                return self._upstream(found, depth=depth)
            return self._not_found(
                question,
                f"问句里既没有 `库.表` 形式的表名、也没看出 SQL，因此没有调用内核：{question}",
            )
        return self._not_found("", "必须提供 question、table、sql 三者之一")

    # ---------------------------------------------------------------- 两条路径
    def _upstream(self, table: str, *, depth: int) -> LineageOutcome:
        result = self._kernel.upstream(table, depth=depth)
        # 证据条数取内核自己报的上游表数（见录制 fixture 的 upstream 响应）
        return self._to_outcome(
            kind="upstream",
            subject=table,
            result=result,
            evidence_count=_as_count(result.get("upstream_count")),
        )

    def _analyze(self, sql: str, *, depth: int = 3) -> LineageOutcome:
        result = self._kernel.analyze(sql, depth=depth)
        # 证据条数取内核自己报的字段级血缘条数（见录制 fixture 的 analyze 响应）
        return self._to_outcome(
            kind="analyze",
            subject=summarize_sql(sql),
            result=result,
            evidence_count=_as_count(result.get("column_lineage_count")),
        )

    # ---------------------------------------------------------------- 组装回执
    def _to_outcome(self, *, kind: Literal["upstream", "analyze"], subject: str, result: ToolResult, evidence_count: int) -> LineageOutcome:
        # 失败时证据数一律记 0：失败的回执不能支撑任何结论（铁律 2）
        count = evidence_count if result.ok else 0
        receipt = SkillReceipt(
            skill=SKILL_ID,
            endpoint=result.endpoint,
            ms=result.ms,
            evidence_count=count,
            ok=result.ok,
            attempts=result.attempts,
            http_status=result.http_status,
            error=result.error,
        )
        if not result.ok:
            message = result.error or "内核调用失败"
        elif count == 0:
            message = "内核调用成功，但没有返回可引用的证据（上游为空？）"
        else:
            message = ""
        report_url = result.get("report_url")
        return LineageOutcome(
            receipt=receipt,
            kind=kind,
            subject=subject,
            tables=table_names(result),
            lineage=dict(result.data),
            report_url=report_url if isinstance(report_url, str) else None,
            message=message,
        )

    def _not_found(self, subject: str, message: str) -> LineageOutcome:
        """没调用内核的情况也要给回执 —— 回执是"这次凭什么"的交代，不是"调用记录"。"""
        return LineageOutcome(
            receipt=SkillReceipt(skill=SKILL_ID, endpoint="(未调用内核)", ms=0, evidence_count=0, ok=False, error=message),
            kind="none",
            subject=subject,
            message=message,
        )


# -------------------------------------------------------------------- 小工具
def _as_count(value: Any) -> int:
    """把内核报的计数转成非负整数；拿不准就当 0（宁可少报证据，也不虚报）。"""
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 0
    return count if count > 0 else 0


def table_names(result: ToolResult) -> list[str]:
    """从内核响应里取出涉及的表，去重且保持内核给出的顺序。

    字段名按内核**真实响应**（见录制 fixture）取，不自造。
    """
    names: list[str] = []
    start = result.get("start_table")
    if isinstance(start, str) and start:
        names.append(start)
    for key in ("tables", "input_tables", "output_tables"):
        value = result.get(key)
        if isinstance(value, list):
            names.extend(str(item) for item in value)
    ordered: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered

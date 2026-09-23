"""B2 用例测试（W-114/W-103）：不依赖内核服务——用**录制的真实响应**当假内核。

覆盖三个用例 + 两条失败路径 + 上下文回填 + 规则/LLM 一致性（ADR-0004 的强制项）。
"""

from __future__ import annotations

import json
import pathlib

from dip_agent import Agent
from dip_contracts import EvidenceType, Status
from lineage_client import ToolResult

FIXTURE = pathlib.Path(__file__).parents[1] / "integrations/lineage-client/tests/fixtures/kernel-probe-2026-09-23.json"
REAL = json.loads(FIXTURE.read_text(encoding="utf-8"))["endpoints"]


class FakeKernel:
    """把录制的真实响应按方法名回放；同时记录调用参数（用于断言"计划真的调了这些工具"）。"""

    def __init__(self, *, fail_all: bool = False) -> None:
        self.calls: list[tuple[str, object]] = []
        self.fail_all = fail_all

    def _res(self, endpoint: str, key: str) -> ToolResult:
        if self.fail_all:
            return ToolResult(False, endpoint, 3, {}, f"连接失败：内核不可达（{endpoint}）")
        return ToolResult(True, endpoint, 7, REAL[key]["response"])

    def search(self, keyword: str, limit: int = 20) -> ToolResult:
        self.calls.append(("search", keyword))
        return self._res("POST /kb/search", "kb_search")

    def upstream(self, table: str, depth: int = 5, graph: str | None = None) -> ToolResult:
        self.calls.append(("upstream", table))
        return self._res("POST /upstream", "upstream")

    def impact(self, table: str, direction: str = "downstream") -> ToolResult:
        self.calls.append(("impact", table))
        return self._res("POST /impact", "impact")

    def analyze(self, sql: str, dialect: str = "hive", depth: int = 3) -> ToolResult:
        self.calls.append(("analyze", len(sql)))
        return self._res("POST /analyze", "analyze")

    def kb_summary(self) -> ToolResult:
        self.calls.append(("kb_summary", None))
        return self._res("POST /kb/summary", "kb_summary")

    def ask(self, question: str) -> ToolResult:
        self.calls.append(("ask", question))
        return self._res("POST /kb/ask", "kb_ask")


Q1 = "ads.ads_产销存月报 的产量怎么来的？"
Q2 = "改 cdw.dws_产销存汇总 会砸哪些下游？"


# ---------------------------------------------------------------- 用例 1


def test_usecase1_cross_layer_answer():
    """验收主路径：必须答出「公式在 dwd 层 + 本表透传 + 上游链路」三点。"""
    agent = Agent(FakeKernel())
    a = agent.ask(Q1)

    assert a.result.status is Status.VERIFIED, "ads.output_qty 命中人工词表 → verified"
    assert a.result.value is not None
    assert a.result.value.display == "产量 = 打码量 + 跳码量 - 重码量"
    assert a.result.confidence == 0.9, "取证据链最小值（词表 1.0、公式 0.9）"
    assert a.result.version == "kb:1.0.0@2026-09-20"

    # 跨层：公式来自 dwd 层的另一张表，链路证据在上游
    assert any(e.type is EvidenceType.METRIC and "dwd" in (e.summary or "") for e in a.result.evidence)
    assert any(e.type is EvidenceType.LINEAGE and "上游 14 张" in e.summary for e in a.result.evidence)
    assert any(e.ref.startswith("dict:ads.ads_产销存月报.output_qty") for e in a.result.evidence)
    assert "dwd" in a.text, "正文必须点明公式所在层，否则用户会误以为 ads 层自己算的"

    # 计划真的调了这些工具，且参数是表名与业务词
    tools = [c.name for c in a.tool_calls]
    assert tools == ["search", "search", "upstream"]
    assert all(c.ok and c.ms >= 0 and c.endpoint for c in a.tool_calls)
    assert a.suggestions and len(a.suggestions) <= 3
    assert a.mode == "rule"


# ---------------------------------------------------------------- 用例 2


def test_usecase2_impact():
    agent = Agent(FakeKernel())
    a = agent.ask(Q2)
    assert [c.name for c in a.tool_calls] == ["impact"]
    assert any("下游 4 张" in e.summary for e in a.result.evidence)
    assert a.result.status is Status.VERIFIED, "纯图事实（真实脚本生成的图）→ verified；不是推理"
    assert a.result.value is not None and "下游 4 张" in a.result.value.display


# ---------------------------------------------------------------- 用例 3


def test_usecase3_rule_and_llm_modes_agree():
    """ADR-0004：LLM 只改措辞，**事实必须一致**。"""
    rule = Agent(FakeKernel()).ask(Q1, mode="rule")
    llm = Agent(FakeKernel()).ask(Q1, mode="llm")
    assert rule.result.model_dump(mode="json") == llm.result.model_dump(mode="json")
    assert rule.text == llm.text, "P1 的 llm 模式尚未接入模型，文本也应一致（B2 后续只改措辞）"
    assert (rule.mode, llm.mode) == ("rule", "llm")


# ------------------------------------------------------- 失败与澄清路径


def test_no_entity_asks_clarification_without_tools():
    kernel = FakeKernel()
    a = Agent(kernel).ask("你好")
    assert a.result.status is Status.UNRESOLVED
    assert a.result.evidence == [] and a.result.value is None
    assert not any(c[0] in {"search", "upstream", "impact", "analyze"} for c in kernel.calls), "没有实体就不该乱调工具"
    assert "例如" in a.text


def test_ambiguous_table_asks_which_one():
    a = Agent(FakeKernel()).ask("ads.ads_产销存月报 和 cdw.dws_产销存汇总 的口径一样吗？")
    assert a.result.status is Status.UNRESOLVED
    assert "多张表" in a.text and a.result.evidence == []


def test_kernel_failure_never_invents_answer():
    a = Agent(FakeKernel(fail_all=True)).ask(Q1)
    assert a.result.status is Status.UNRESOLVED
    assert a.result.value is None and a.result.evidence == []
    assert "内核调用没有成功" in a.text
    assert any(c.error and "内核不可达" in c.error for c in a.tool_calls), "内核原文要带出来，便于定位问题"


def test_context_backfill_on_followup_question():
    """第二轮追问不带表名时，沿用上一轮的表（否则用户每句都要重复表名）。"""
    kernel = FakeKernel()
    agent = Agent(kernel)
    agent.ask(Q1)
    kernel.calls.clear()
    a = agent.ask("那它的下游有哪些表？")
    assert ("impact", "ads.ads_产销存月报") in kernel.calls
    assert a.result.value is not None


def test_sql_input_goes_to_analyze():
    kernel = FakeKernel()
    a = Agent(kernel).ask("帮我看看这段：insert overwrite table ads.x select * from ods.y")
    assert [c[0] for c in kernel.calls][:1] == ["analyze"]
    assert any(e.type is EvidenceType.REPORT for e in a.result.evidence)

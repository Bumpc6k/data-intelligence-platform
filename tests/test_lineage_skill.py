"""血缘 skill（M1-03 / Issue #3）的测试。

对应验收标准：
- 旗舰问题「ads.ads_产销存月报 的产量怎么来的？」返回结构化结果 + 回执 → `test_flagship_question...`
- 可用录制 fixture 做无内核测试 → 全部用例都注入 `FakeKernel`，一个内核都不起
- `gate` 绿 → 由门禁保证

反例比正例值钱：调用失败、证据为 0、问句里没有表名、优先级冲突，各配了"该怎样"的断言。
另外这里**每个中文用例都是刻意的** —— AGENTS.md §8 记着"用 ASCII 正则会漏检全部中文标识符，
测试还会假绿"，所以专门测了"中文标识符必须命中"以及"ASCII 正则会漏"的对照。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
from typing import Any

import pytest
from dip_contracts import ToolResult
from dip_lineage_skill import (
    SKILL_ID,
    TOOL_NAME,
    LineageSkill,
    build_server,
    extract_table,
    load_declaration,
    looks_like_sql,
)
from dip_lineage_skill.analyze import SKILL_NAME, SKILL_VERSION, TABLE_PATTERN
from mcp import ClientSession, StdioServerParameters, stdio_client

ROOT = pathlib.Path(__file__).parents[1]
FIXTURE = ROOT / "integrations/lineage-client/tests/fixtures/kernel-probe-2026-09-23.json"
ENDPOINTS: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))["endpoints"]

FLAGSHIP_TABLE = "ads.ads_产销存月报"
FLAGSHIP_QUESTION = f"{FLAGSHIP_TABLE} 的产量怎么来的？"

# 最紧的公共子集：MCP 官方 SDK 允许点号，但多家供应商的 function-calling 名字只接受这些字符
NAME_COMMON_SUBSET = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


class FakeKernel:
    """用**录制的真实内核响应**当数据源（Issue #3 验收第 ② 条）。

    只实现 `KernelToolkit` 协议里的两个方法——如果 skill 偷偷调了别的（尤其是写操作类），
    这里会直接 AttributeError，等于把「只做只读」这条边界交给测试执行。
    """

    def __init__(self, *, fail_upstream: bool = False, fail_analyze: bool = False) -> None:
        self.fail_upstream = fail_upstream
        self.fail_analyze = fail_analyze
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def upstream(self, table: str, depth: int = 5, graph: str | None = None) -> ToolResult:
        self.calls.append(("upstream", {"table": table, "depth": depth}))
        if self.fail_upstream:
            # 内核失败的真实形态：HTTP 200，body 里 success=false（见客户端设计说明 §1）
            return ToolResult(
                ok=False,
                endpoint="POST /upstream",
                ms=18,
                data={"success": False, "error": "graph not found"},
                error="graph not found",
                http_status=200,
                attempts=2,
            )
        return ToolResult(ok=True, endpoint="POST /upstream", ms=41, data=dict(ENDPOINTS["upstream"]["response"]))

    def analyze(self, sql: str, dialect: str = "hive", depth: int = 3, mode: str = "sql") -> ToolResult:
        self.calls.append(("analyze", {"sql": sql, "dialect": dialect, "depth": depth}))
        if self.fail_analyze:
            return ToolResult(ok=False, endpoint="POST /analyze", ms=25, data={}, error="内核不可达", attempts=3)
        return ToolResult(ok=True, endpoint="POST /analyze", ms=130, data=dict(ENDPOINTS["analyze"]["response"]))


@pytest.fixture()
def kernel() -> FakeKernel:
    return FakeKernel()


@pytest.fixture()
def skill(kernel: FakeKernel) -> LineageSkill:
    return LineageSkill(kernel)


# ------------------------------------------------------------------ 验收 ① 旗舰问题
def test_flagship_question_returns_structure_and_receipt(skill: LineageSkill, kernel: FakeKernel):
    """「ads.ads_产销存月报 的产量怎么来的？」→ 结构化结果 + 回执。"""
    outcome = skill.run(question=FLAGSHIP_QUESTION)

    assert outcome.kind == "upstream"
    assert outcome.subject == FLAGSHIP_TABLE, "应从中文问句里抽出表名"
    assert kernel.calls == [("upstream", {"table": FLAGSHIP_TABLE, "depth": 3})], "应且只应调一次 /upstream"

    # 结构化结果：涉及的表来自内核原始响应，不改写
    assert outcome.tables[0] == FLAGSHIP_TABLE
    assert outcome.lineage["upstream_count"] == ENDPOINTS["upstream"]["response"]["upstream_count"]
    assert outcome.lineage == ENDPOINTS["upstream"]["response"], "内核原始数据必须原样保留，便于回查"

    # 回执：来源端点 / 耗时 / 证据条数
    assert outcome.receipt.skill == "lineage.analyze@1"
    assert outcome.receipt.endpoint == "POST /upstream"
    assert outcome.receipt.ms == 41
    assert outcome.receipt.evidence_count == ENDPOINTS["upstream"]["response"]["upstream_count"]
    assert outcome.receipt.usable is True


async def test_flagship_question_reaches_the_mcp_tool(kernel: FakeKernel):
    """同一个问题经 MCP 层（真实工具注册 + 真实序列化）也要拿到同样结果。"""
    server = build_server(kernel)
    tools = await server.list_tools()
    result = await server.call_tool(TOOL_NAME, {"question": FLAGSHIP_QUESTION})
    payload = json.loads(result.content[0].text)

    assert [t.name for t in tools] == [TOOL_NAME]
    assert payload["kind"] == "upstream"
    assert payload["subject"] == FLAGSHIP_TABLE
    assert payload["receipt"]["evidence_count"] > 0
    assert payload["receipt"]["endpoint"] == "POST /upstream"


# --------------------------------------------------------------- 中文标识符（重点）
@pytest.mark.parametrize(
    "question,expected",
    [
        (FLAGSHIP_QUESTION, FLAGSHIP_TABLE),
        ("查一下 dwd.dwd_订单明细 的上游", "dwd.dwd_订单明细"),
        ("ads.ads_产销存月报 的产量怎么来的？", "ads.ads_产销存月报"),
        ("ods.t_跳码明细 从哪来", "ods.t_跳码明细"),
        ("看下 dws.dws_用户_日汇总 这条链路", "dws.dws_用户_日汇总"),
    ],
)
def test_chinese_identifiers_must_be_extracted(question, expected):
    """中文标识符必须能取出来 —— 这是本仓库明确记过的坑。"""
    assert extract_table(question) == expected


def test_ascii_only_pattern_silently_truncates_chinese_identifiers():
    """对照实验：ASCII 正则会**悄悄截断**中文标识符，比"取不到"更危险。

    `ads.ads_产销存月报` 在 ASCII 正则下得到的是 `ads.ads_` —— 一个**不存在的假表名**。
    拿它去问内核，轻则查空、重则误导结论，而且不会有任何报错。这正是 AGENTS.md §8
    记的坑（"用 ASCII 正则会漏检全部中文标识符，测试还会假绿"）的真实形态。
    """
    ascii_only = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z0-9_]*")
    truncated = ascii_only.search(FLAGSHIP_QUESTION)
    assert truncated is not None, "ASCII 正则并非取不到，而是取到一半"
    assert truncated.group(0) == "ads.ads_", "它是被截断的假表名"
    assert truncated.group(0) != FLAGSHIP_TABLE

    assert extract_table(FLAGSHIP_QUESTION) == FLAGSHIP_TABLE, "Unicode 感知的正则必须取到完整表名"
    assert "\\u4e00" in TABLE_PATTERN, "模式里必须显式写出中文区间（可被评审一眼看到）"
    assert TABLE_PATTERN.count("\\u4e00") == 2, "库名与表名两侧都要显式含中文区间，缺一侧会半截"


def test_table_pattern_is_unicode_aware_for_other_scripts():
    """不只是中文：\\w 在 str 模式下本身就含 Unicode 单词字符。"""
    assert extract_table("看看 db.tabelle_ü 的血缘") is not None


def test_known_limitation_version_like_tokens_look_like_table_names():
    """**已知局限**（写在 PR 里，不藏）：`v1.2` 这种版本号会被当成表名。

    本 skill 只做最小实体抽取，不做消歧 —— 这里的断言是把现状钉住，
    免得有人以为它已经会消歧了。真正的实体识别/计划属 dip-agent（v2 迁到 skill + harness），
    本项按边界不重复实现。
    """
    assert extract_table("这个方案 v1.2 已经定稿了") == "v1.2"


def test_no_table_like_token_yields_none():
    """反例：句子里没有任何 `库.表` 形态 → 必须返回 None，而不是硬凑一个。"""
    assert extract_table("产量是怎么算出来的？") is None
    assert extract_table("") is None


# ------------------------------------------------------------------ 反例：失败路径
def test_kernel_failure_yields_receipt_with_zero_evidence(kernel: FakeKernel):
    """内核失败：回执必须在、证据数必须是 0、消息必须来自内核原文。"""
    kernel.fail_upstream = True
    outcome = LineageSkill(kernel).run(table=FLAGSHIP_TABLE)

    assert outcome.receipt.ok is False
    assert outcome.receipt.evidence_count == 0, "失败的回执不能带证据（铁律 2）"
    assert outcome.receipt.usable is False
    assert outcome.receipt.error == "graph not found"
    assert outcome.receipt.http_status == 200, "内核失败也是 HTTP 200，真相应由 success/error 表达"
    assert outcome.receipt.attempts == 2
    assert "graph not found" in outcome.message


def test_success_without_evidence_is_not_usable(kernel: FakeKernel):
    """反例：内核成功但上游为空 → 不能拿它支撑结论。"""
    kernel.upstream = lambda table, depth=5, graph=None: ToolResult(
        ok=True, endpoint="POST /upstream", ms=9, data={"upstream_count": 0, "tables": []}
    )
    outcome = LineageSkill(kernel).run(table=FLAGSHIP_TABLE)

    assert outcome.receipt.ok is True
    assert outcome.receipt.evidence_count == 0
    assert outcome.receipt.usable is False
    assert "没有返回可引用的证据" in outcome.message


def test_missing_input_does_not_call_the_kernel(skill: LineageSkill, kernel: FakeKernel):
    """没有任何入参 → 明确拒绝，且**不去猜、不盲调内核**。"""
    outcome = skill.run()

    assert outcome.kind == "none"
    assert outcome.receipt.ok is False
    assert outcome.receipt.evidence_count == 0
    assert outcome.receipt.endpoint == "(未调用内核)"
    assert kernel.calls == [], "拿不准就不该打扰内核"
    assert "必须提供" in outcome.message


def test_question_without_table_or_sql_does_not_call_the_kernel(kernel: FakeKernel):
    """问句里既没有表名也没看出 SQL → 明说没查到，而不是编一个。"""
    outcome = LineageSkill(kernel).run(question="产量是怎么算出来的？")

    assert outcome.kind == "none"
    assert kernel.calls == []
    assert "没有调用内核" in outcome.message
    assert "产量是怎么算出来的？" in outcome.message


# ------------------------------------------------------------------ SQL 路径
def test_sql_path_reports_column_lineage_count(kernel: FakeKernel):
    sql = "insert overwrite table ads.ads_产销存月报 select 1 as output_qty"
    outcome = LineageSkill(kernel).run(sql=sql)

    assert outcome.kind == "analyze"
    assert outcome.receipt.endpoint == "POST /analyze"
    assert outcome.receipt.evidence_count == ENDPOINTS["analyze"]["response"]["column_lineage_count"]
    assert outcome.receipt.evidence_count > 0
    assert FLAGSHIP_TABLE in outcome.lineage["output_tables"]
    assert outcome.report_url == ENDPOINTS["analyze"]["response"]["report_url"], "报告地址要原样带出"
    assert kernel.calls[0][1]["depth"] == 3, "depth 应透传到内核"


def test_sql_in_the_question_is_detected_and_truncated_in_subject(kernel: FakeKernel):
    sql = "select a, b, c from ods.t_跳码明细 where dt = '2026-09-01' " + "and x = 1 " * 20
    outcome = LineageSkill(kernel).run(question=sql)

    assert outcome.kind == "analyze"
    assert looks_like_sql(sql) is True
    assert len(outcome.subject) < len(sql), "subject 是摘要，不该塞整段 SQL"
    assert outcome.subject.endswith("字符）")


@pytest.mark.parametrize(
    "kwargs,expected_kind",
    [
        ({"sql": "select 1 from a.b", "table": "ads.x"}, "analyze"),          # sql 优先
        ({"table": "ads.ads_产销存月报", "question": "看下 dwd.别的表"}, "upstream"),  # table 优先于 question
    ],
)
def test_input_precedence(kernel: FakeKernel, kwargs, expected_kind):
    outcome = LineageSkill(kernel).run(**kwargs)
    assert outcome.kind == expected_kind


# ------------------------------------------------------------------ 边界：只读
def test_skill_only_touches_read_only_endpoints(skill: LineageSkill, kernel: FakeKernel):
    """本 skill 只做只读：调用记录里只能出现 analyze / upstream 两个方法。

    FakeKernel 只实现了协议的这两个方法 —— 一旦实现偷偷调别的（尤其写操作类），
    这里会 AttributeError 而不是"悄悄跑通"。
    """
    skill.run(question=FLAGSHIP_QUESTION)
    skill.run(sql="select 1 from a.b")
    assert {name for name, _ in kernel.calls} <= {"analyze", "upstream"}


def test_declaration_says_read_only():
    declaration = load_declaration()
    assert declaration.side_effect.value == "read", "只读 skill（决定防火墙最低档位）"
    assert declaration.auth_scope == ["lineage:read"]


# ------------------------------------------------------------------ 契约 ↔ 实现一致
def test_declaration_matches_implementation():
    """声明改了实现没改，这里立刻红。"""
    declaration = load_declaration()

    assert declaration.qualified_name == SKILL_ID
    assert (declaration.name, declaration.version) == (SKILL_NAME, SKILL_VERSION)
    assert declaration.idempotent is True
    assert declaration.timeout == 30
    assert declaration.retry.value == "connect_only"
    assert declaration.evidence_kind.value == "lineage"
    assert declaration.renderer.value == "graph"
    assert declaration.budget == 6


def test_mcp_tool_name_is_the_safe_form_of_the_contract_name():
    """契约名带点号，工具名取最紧的公共子集形态；两者可互相推导。"""
    assert SKILL_NAME == "lineage.analyze"
    assert TOOL_NAME == "lineage_analyze"
    assert TOOL_NAME == SKILL_NAME.replace(".", "_")
    assert NAME_COMMON_SUBSET.match(TOOL_NAME), f"工具名必须落在最紧公共子集里：{TOOL_NAME}"
    assert not NAME_COMMON_SUBSET.match(SKILL_NAME), "点号形态确实不在最紧子集里——这正是要转换的原因"


# ------------------------------------------------------------------ 端到端：真起进程
PYTHONPATH = os.pathsep.join(
    str(ROOT / part)
    for part in (
        ".",
        "packages/dip-contracts/src",
        "packages/dip-skills/src",
        "packages/dip-lineage-skill/src",
        "integrations/lineage-client/src",
    )
)


async def test_stdio_round_trip_with_a_real_mcp_client():
    """真起一个进程，用**真实 MCP 客户端**走完 握手 → tools/list → tools/call。

    这是"能被 dsh 这类外壳无差别调用"最接近的证据：协议、传输、序列化三样都是真的，
    只有内核是假的 —— 所以把 LINEAGE_BASE 指向一个必然连不上的端口，让回执如实报错，
    这样这条用例不需要任何外部服务，在 CI 上也能跑。

    反例价值同样在这里：内核连不上时，工具**不能崩、不能抛异常**，必须返回带失败回执的
    正常 JSON —— 外壳才不至于因为一个 skill 失败就整轮挂掉。
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = PYTHONPATH
    env["LINEAGE_BASE"] = "http://127.0.0.1:1"  # 必然连不上：只验协议，不依赖任何服务
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dip_lineage_skill"],
        env=env,
        cwd=str(ROOT),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=30) as session:
            await session.initialize()

            tools = await session.list_tools()
            assert [t.name for t in tools.tools] == [TOOL_NAME]
            assert tools.tools[0].input_schema["type"] == "object", "入参必须是 object 根"

            result = await session.call_tool(TOOL_NAME, {"question": FLAGSHIP_QUESTION})
            payload = json.loads(result.content[0].text)

            assert payload["kind"] == "upstream"
            assert payload["subject"] == FLAGSHIP_TABLE, "中文表名过了协议一趟仍然要保真"
            assert payload["receipt"]["ok"] is False, "内核连不上就该如实报失败"
            assert payload["receipt"]["evidence_count"] == 0
            assert payload["receipt"]["error"], "失败回执必须带原因，不许静默"

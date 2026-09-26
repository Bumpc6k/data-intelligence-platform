"""MCP 服务：把血缘 skill 暴露成一个可被外壳无差别调用的工具（工作项 M1-03）。

**SDK 版本注意**：本机实测装到的是 `mcp` 2.x，其中 `FastMCP` 已改名为 `MCPServer`
（`from mcp.server.mcpserver import MCPServer`）。网上大量示例仍是 v1 的
`from mcp.server.fastmcp import FastMCP`，在 2.x 下直接 ImportError。故 `pyproject.toml`
把版本锁在 `>=2.2,<3`。

**工具名为什么不用契约名的点号形态**：
- MCP 官方 python-sdk 按 SEP-986 允许点号（`^[A-Za-z0-9._-]{1,128}$`）；
- 但外壳往往把 MCP 工具名转发成模型供应商的 function-calling 名字，而 OpenAI / Anthropic /
  DeepSeek / Kimi 等的名字集合是 `[a-zA-Z0-9_-]`（不允许点号），最紧的公共子集为
  `^[a-zA-Z][a-zA-Z0-9_-]{0,63}$`。
- 因此注册用下划线形态 `lineage_analyze`（正好落在最紧子集里），**契约名 `lineage.analyze`
  保持不变**，两者在回执的 `skill` 字段里对得上，溯源不断。
"""

from __future__ import annotations

from dip_contracts import KernelToolkit
from mcp.server.mcpserver import MCPServer

from .analyze import SKILL_ID, SKILL_NAME, SKILL_VERSION, LineageSkill

SERVER_NAME = "dip-lineage-skill"
TOOL_NAME = SKILL_NAME.replace(".", "_")

TOOL_DESCRIPTION = (
    "查一张表的数据血缘（上游有哪些表怎么来的），或解析一段 SQL 的字段级血缘。只读端点，不改任何数据。"
    "合适用它的情况：用户问某张表/某个字段「怎么来的」「上游是什么」「影响面」，或直接给了一段 SQL 要看血缘。"
    f"（契约名 {SKILL_ID}；每次调用都会返回回执：来源端点、耗时、证据条数。）"
)


def build_server(toolkit: KernelToolkit) -> MCPServer:
    """按注入的内核工具构建 MCP 服务。

    入参是 `KernelToolkit` **协议**而不是具体 `LineageClient`：真实进程在 `__main__` 里
    接真实适配器，测试里接 `FakeKernel`，服务代码两者都不用改。
    """
    skill = LineageSkill(toolkit)
    server = MCPServer(
        name=SERVER_NAME,
        version=str(SKILL_VERSION),
        instructions="回答必带回执：先看回执里的证据条数，没有证据就不给结论。",
    )

    @server.tool(name=TOOL_NAME, description=TOOL_DESCRIPTION)
    def lineage_analyze(
        question: str | None = None,
        table: str | None = None,
        sql: str | None = None,
        depth: int = 3,
    ) -> str:
        """返回 JSON：结构化血缘结果 + 回执（来源端点 / 耗时 / 证据条数）。

        question、table、sql 三选一；优先级 sql > table > 从 question 里抽取表名。
        """
        outcome = skill.run(question=question, table=table, sql=sql, depth=depth)
        return outcome.model_dump_json()

    return server

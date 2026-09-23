"""跨仓库契约：工具调用结果 + 内核工具协议。

**为什么放在契约层**：`ToolResult` 是平台与内核之间流通的形状，编排层（dip-agent）需要它，
但不应该为了一个 dataclass 去依赖 `lineage_client`（那是适配器，会拖进 httpx）。
把协议放在这里 → 编排层依赖抽象、适配器实现抽象（依赖倒置），分层守卫也就能真正生效：

    dip_contracts.kernel.KernelToolkit  ←──  dip_agent（只依赖协议）
                ▲
                └──── lineage_client.LineageClient（实现协议，内部用 httpx）

附带好处：测试里用 `FakeKernel` 满足协议即可，不需要起内核。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolResult:
    """一次内核调用的结果——**失败也是结果**（不抛异常，便于记进审计与工具步骤条）。

    `data` 保持内核原始字典不改写，任何时候都能回查"内核当时到底返回了什么"。
    """

    ok: bool
    endpoint: str
    ms: int
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    http_status: int | None = None
    attempts: int = 1

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


@runtime_checkable
class KernelToolkit(Protocol):
    """内核只读能力的协议（实现者：`lineage_client.LineageClient`）。

    注意 `search` 的参数名是平台的叫法（`keyword`），内核真实参数名（`query`）由适配器吸收。
    """

    def analyze(self, sql: str, dialect: str = "hive", depth: int = 3, mode: str = "sql") -> ToolResult: ...
    def upstream(self, table: str, depth: int = 5, graph: str | None = None) -> ToolResult: ...
    def impact(self, table: str, direction: str = "downstream") -> ToolResult: ...
    def search(self, keyword: str, limit: int = 20) -> ToolResult: ...
    def metric(self, name: str) -> ToolResult: ...
    def ask(self, question: str) -> ToolResult: ...
    def kb_summary(self) -> ToolResult: ...
    def make_report(self, payload: dict[str, Any]) -> ToolResult: ...

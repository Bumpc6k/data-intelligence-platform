"""文档通道（M3-04 / Issue #15）：把 WeKnora 当作**只读**的文档来源。

分层：

| 层 | 放什么 | 在哪 |
| --- | --- | --- |
| 契约 | 材料形状 + 「仅背景」铁律（引用必标注、结论只来自结构化通道） | `dip_contracts.doc_channel` |
| 取数 | 跟 WeKnora 的 MCP Server 说话，把结果转成 `DocHit` | 本包 `weknora.py` |
| 编排 | 接口层决定"什么时候问文档通道、怎么挂到答案上" | `portal-api` 的 `routers/doc_channel.py` |

本包**不做判定**（那是契约层的事），也**不写任何东西**：令牌只开 retrieve 组。
"""

from .weknora import (
    LIST_DOCUMENTS_TOOL,
    MCP_TOOL_GROUP_RETRIEVE,
    SEARCH_TOOL,
    DocChannel,
    DocChannelNotConfigured,
    WeknoraMcpChannel,
    channel_from_settings,
    parse_documents_payload,
    parse_search_payload,
    passage_to_hit,
    passages_of,
    payload_of_tool_result,
)

__all__ = [
    "LIST_DOCUMENTS_TOOL",
    "MCP_TOOL_GROUP_RETRIEVE",
    "SEARCH_TOOL",
    "DocChannel",
    "DocChannelNotConfigured",
    "WeknoraMcpChannel",
    "channel_from_settings",
    "parse_documents_payload",
    "parse_search_payload",
    "passage_to_hit",
    "passages_of",
    "payload_of_tool_result",
]

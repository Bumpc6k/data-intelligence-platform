"""WeKnora 文档通道的客户端（工作项 M3-04 / Issue #15）—— 走它**内置的 MCP Server**。

为什么走 MCP 而不是直接打 HTTP 接口：

- Issue 就是这么要求的（"官方 MCP server 接入"）；v0.8.2 起 MCP Server 已内置在 app 里
  （`/mcp/<endpoint_id>`，Streamable HTTP），Python 版 `mcp-server/` 已弃用。
- **只读是天然的**：每个 MCP endpoint 有自己的令牌、知识库范围、限流和**工具分组**。
  我们只开 `retrieve` 组，用 `search_knowledge` / `list_documents`；
  `ingest`（写文档）那一组**不授权**——不是"我们不去调"，是令牌根本没有那个能力。
- 不用自己拼 JSON-RPC：仓库里已经有官方 `mcp` SDK（M1-03 血缘 skill 用的就是它）。

本模块只做两件事：**把 MCP 的返回解析成契约层的 `DocHit`**（纯函数，好测），
以及**发起调用**（在 `WeknoraMcpChannel.search` 里，注入用假实现即可离线测）。
解析刻意写得宽容（不同版本字段名会变），但**来源缺了就缺了**，绝不猜一个文档名填上 ——
`background_of()` 会因此拒收，这正是验收②要的效果。
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable, Sequence
from typing import Any, Protocol, runtime_checkable

from dip_contracts.models import DocCitation, DocHit

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
    "payload_of_tool_result",
    "passages_of",
]

#: 只读的那一组工具（WeKnora 里叫 retrieve；不授权 ingest/chat/wiki）
MCP_TOOL_GROUP_RETRIEVE = "retrieve"
SEARCH_TOOL = "search_knowledge"
LIST_DOCUMENTS_TOOL = "list_documents"

# 字段名容错：不同版本 / 不同工具返回的键名不一样，按优先级取第一个非空的
_TITLE_KEYS = ("knowledge_title", "document_name", "title", "file_name", "file", "name")
_DOC_ID_KEYS = ("knowledge_id", "document_id", "doc_id")
_CHUNK_KEYS = ("chunk_id", "chunk", "segment_id", "id")
_TEXT_KEYS = ("excerpt", "content", "text", "passage", "chunk_content", "chunk")
_SCORE_KEYS = ("score", "similarity", "relevance", "rank_score")
_POSITION_KEYS = ("position", "page", "chunk_index", "index", "seq")
_KB_ID_KEYS = ("knowledge_base_id", "kb_id")
_URL_KEYS = ("url", "link", "source_url")

#: 结果的容器键：`{"results": [...]}` / `{"data": [...]}` / `{"passages": [...]}` 都认
#: （`documents` 是 `list_documents` 那种"清单"形态）
_CONTAINER_KEYS = ("results", "data", "passages", "items", "chunks", "matches", "hits", "documents")


class DocChannelNotConfigured(RuntimeError):
    """文档通道没配（地址/令牌缺）—— 明确报错，不静默返回空结果。"""


@runtime_checkable
class DocChannel(Protocol):
    """文档通道的窄接口：给一句话，回一批**带来源**的材料。

    刻意只有这一个方法 —— 通道该干的事就这一件，其余的（判定、渲染、跟结构化通道合流）
    都在契约层与接口层，那里有测试。
    """

    async def search(self, query: str, *, limit: int | None = None) -> list[DocHit]: ...


# ---------------------------------------------------------------- 解析（纯函数）


def _first_text(payload: dict[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return ""


def _first_float(payload: dict[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


def _as_list(payload: Any) -> list[dict[str, Any]]:
    """把各种"结果容器"拍平成条目列表。认不出就返回空列表（调用方据此报"没搜到"）。"""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in _CONTAINER_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    # 单条结果没有容器键时也认（`{"chunk_id": ..., "excerpt": ...}`）
    if any(key in payload for key in _TEXT_KEYS):
        return [payload]
    return []


# WeKnora 的 MCP 工具返回的是**XML 文本**（不是 JSON），形如：
#
#   <search_results count="2" mode="hybrid"><query>库存增量怎么算</query>
#   <chunk rank="1" chunk_id="..." chunk_index="2" knowledge_id="..." knowledge_base_id="..."
#          knowledge_title="产销存月报口径说明.md" score="0.016">
#   <match_snippet>…</match_snippet><content>…</content></chunk>…</search_results>
#
# 所以这里除了 JSON 还要认这种标签文本。**不用 xml.etree 硬解**：文档正文里带 `>` 和 `&`
# 是常事（`&gt;` 转义过，但没人保证永远转义），硬解会因一个字符整段失败；
# 正则宽容些，且我们只要属性 + 正文两样东西。
_PAIRED_TAG_RE = re.compile(r"<(chunk|document|knowledge)\b([^>]*)>(.*?)</\1>", re.S | re.I)
_SELFCLOSE_TAG_RE = re.compile(r"<(chunk|document|knowledge)\b([^>]*)/>", re.S | re.I)
_ATTR_RE = re.compile(r'([A-Za-z_][\w.-]*)\s*=\s*"([^"]*)"')
_INNER_RE = re.compile(r"<(content|match_snippet|excerpt)>(.*?)</\1>", re.S | re.I)


def _xml_passages(payload: str) -> list[dict[str, Any]]:
    passages: list[dict[str, Any]] = []
    if "<" not in payload:
        return passages
    for _tag, attrs, body in _PAIRED_TAG_RE.findall(payload):
        item: dict[str, Any] = {name: html.unescape(value) for name, value in _ATTR_RE.findall(attrs)}
        inner = {name.lower(): html.unescape(value) for name, value in _INNER_RE.findall(body)}
        # 正文优先用 content（完整切片），退而用 match_snippet（命中片段）
        for key in ("content", "match_snippet", "excerpt"):
            if inner.get(key):
                item["excerpt"] = inner[key]
                break
        passages.append(item)
    for _tag, attrs in _SELFCLOSE_TAG_RE.findall(payload):
        passages.append({name: html.unescape(value) for name, value in _ATTR_RE.findall(attrs)})
    return passages


def passages_of(payload: Any) -> list[dict[str, Any]]:
    """从 MCP 工具返回体里取出条目列表。

    认三种形态：`{"results": [...]}`（JSON）、`[...]`（列表）、XML 标签文本（WeKnora 的现行形态）。
    """
    if isinstance(payload, str):
        return _xml_passages(payload)
    return _as_list(payload)


def passage_to_hit(passage: dict[str, Any], titles: dict[str, str] | None = None) -> DocHit:
    """把一条检索结果转成 `DocHit`。

    **文档名找不到就留空**（不拿 id 冒充名字、更不编一个）—— 空名字会在
    `dip_contracts.doc_channel.background_of()` 那里被判"缺来源"并拒收。
    """
    document_id = _first_text(passage, _DOC_ID_KEYS)
    knowledge_id = document_id
    title = _first_text(passage, _TITLE_KEYS)
    if not title and titles and knowledge_id:
        title = titles.get(knowledge_id, "")
    citation = DocCitation(
        document_name=title,
        chunk_id=_first_text(passage, _CHUNK_KEYS),
        knowledge_id=knowledge_id,
        knowledge_base_id=_first_text(passage, _KB_ID_KEYS),
        url=_first_text(passage, _URL_KEYS) or None,
        position=_first_text(passage, _POSITION_KEYS) or None,
    )
    return DocHit(text=_first_text(passage, _TEXT_KEYS), citation=citation,
                  score=_first_float(passage, _SCORE_KEYS))


def parse_search_payload(payload: Any, titles: dict[str, str] | None = None) -> list[DocHit]:
    """`search_knowledge` 的返回体 → `DocHit` 列表。"""
    return [passage_to_hit(item, titles) for item in passages_of(payload)]


def parse_documents_payload(payload: Any) -> dict[str, str]:
    """`list_documents` 的返回体 → `{文档 id: 标题}`。

    检索结果里常常只有 id 没有标题，用它补齐 —— 补不齐就留空（宁缺勿编）。
    """
    mapping: dict[str, str] = {}
    for item in passages_of(payload):
        document_id = _first_text(item, ("knowledge_id", "document_id", "id"))
        title = _first_text(item, _TITLE_KEYS)
        if document_id and title:
            mapping[document_id] = title
    return mapping


def _json_from_text(text: str) -> Any:
    """从 MCP 文本块里抠出 JSON（有的实现会带 ```json 围栏）。抠不出就原样当纯文本。"""
    body = text.strip()
    if not body:
        return None
    if body.startswith("```"):
        body = body.strip("`")
        if body.lower().startswith("json"):
            body = body[4:]
        body = body.strip()
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return body


def payload_of_tool_result(result: Any) -> Any:
    """把 MCP `CallToolResult` 变成 Python 对象：优先结构化内容，其次文本里的 JSON。"""
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            parsed = _json_from_text(text)
            if parsed is not None:
                return parsed
    return None


# ---------------------------------------------------------------- 通道实现


class WeknoraMcpChannel:
    """按需连接 WeKnora 的 MCP endpoint 做检索。

    每次 `search()` 起一个会话（Streamable HTTP 是无状态的短连接语义），调用完就退：
    我们的用法是"用户问一句 → 查一次"，不需要长连接，也不想因为长连接挂了就整条通道不可用。
    """

    def __init__(
        self,
        url: str,
        token: str,
        *,
        kb_ids: Iterable[str] = (),
        limit: int = 5,
        timeout: float = 30.0,
        search_tool: str = SEARCH_TOOL,
        documents_tool: str = LIST_DOCUMENTS_TOOL,
    ) -> None:
        if not url or not token:
            raise DocChannelNotConfigured("文档通道需要 WEKNORA_MCP_URL 与 WEKNORA_MCP_TOKEN 两样都配齐")
        self.url = url
        self.token = token
        self.kb_ids = tuple(kb_ids)
        self.limit = limit
        self.timeout = timeout
        self.search_tool = search_tool
        self.documents_tool = documents_tool

    # --- 供日志与证据用（**不含令牌**）---
    def describe(self) -> dict[str, Any]:
        return {
            "channel": "documents",
            "transport": "mcp/streamable-http",
            "url": self.url,
            "tool_group": MCP_TOOL_GROUP_RETRIEVE,
            "search_tool": self.search_tool,
            "kb_ids": list(self.kb_ids),
            "limit": self.limit,
        }

    async def _call(self, tool: str, arguments: dict[str, Any]) -> Any:
        # 延迟 import：只有真的要用文档通道时才需要 mcp SDK（也让离线测试不必拉起它）
        #
        # 注意 SDK 版本差异（踩过）：mcp 2.x 里叫 `streamable_http_client`（老版本是 `streamablehttp_client`），
        # 且它 yield 的是**二元组** `(read, write)`（老版本是三元组带 session id）。
        # 头/超时用 SDK 自带的 `create_mcp_http_client`，别自己 new 一个 httpx 客户端
        # ——它带的是 MCP 传输需要的默认超时（read 300s，服务端可能压着流不放）。
        from mcp import ClientSession
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        async with create_mcp_http_client(
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=self.timeout,
        ) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool, arguments)
                    return payload_of_tool_result(result)

    async def search(self, query: str, *, limit: int | None = None) -> list[DocHit]:
        arguments: dict[str, Any] = {"query": query, "limit": limit or self.limit, "mode": "hybrid"}
        if self.kb_ids:
            arguments["knowledge_base_ids"] = list(self.kb_ids)
        payload = await self._call(self.search_tool, arguments)
        hits = parse_search_payload(payload)
        if hits and any(not hit.citation.document_name for hit in hits):
            # 只有 id 没有标题时，补一次文档清单（补不齐就留空，由契约层拒收）
            titles = await self.documents()
            if titles:
                hits = parse_search_payload(payload, titles)
        return hits

    async def documents(self, knowledge_base_id: str | None = None) -> dict[str, str]:
        """列出范围内的文档：`{id: 标题}`，用于给检索结果补文档名。

        WeKnora 的 `list_documents` **要求** `knowledge_base_id`：配置里给了 kb_ids 就用第一个
        （我们这个端点本来就只开了那一个库）；没给就空参调用 —— 服务端会回一句
        `knowledge_base_id is required` 的说明文本，解析成空清单。
        那是"补不到名字"，不是"搜到一批没名字的文档"，调用方按空处理即可，不该当错误炸掉。
        """
        kb = knowledge_base_id or (self.kb_ids[0] if self.kb_ids else "")
        arguments: dict[str, Any] = {"knowledge_base_id": kb} if kb else {}
        payload = await self._call(self.documents_tool, arguments)
        return parse_documents_payload(payload)


def channel_from_settings(settings: Any) -> WeknoraMcpChannel | None:
    """按配置造通道。没开或没配齐就返回 `None`（调用方据此明确回"未启用"）。"""
    if not getattr(settings, "docs_enabled", False):
        return None
    url = getattr(settings, "docs_mcp_url", None)
    token = getattr(settings, "docs_mcp_token", None)
    if not url or not token:
        return None
    return WeknoraMcpChannel(
        url,
        token,
        kb_ids=getattr(settings, "docs_kb_ids", ()) or (),
        limit=getattr(settings, "docs_top_k", 5),
        timeout=getattr(settings, "docs_timeout", 30.0),
    )

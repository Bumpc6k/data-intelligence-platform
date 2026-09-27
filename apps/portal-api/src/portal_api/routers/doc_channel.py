"""文档通道接口（工作项 M3-04 / Issue #15）：只读地查 WeKnora，结果**只能当背景**。

两个端点：

| 方法 | 路径 | 干什么 |
| --- | --- | --- |
| POST | `/api/knowledge/doc-search` | 只查文档通道，回一批**带来源**的背景材料 |
| GET | `/api/knowledge/doc-channel` | 通道状态：是否启用、走哪个 MCP endpoint、只开了哪组工具（**不含令牌**） |

三条硬规矩（都有测试钉住）：

1. **响应里 `usable_for_conclusion` 恒为 `false`** —— 调用方一眼就知道这段材料不能当结论；
   字段来自契约层的 `Background`，不是这里现写的一个字面量，改不掉也藏不住。
2. **缺来源直接失败**：WeKnora 返回的材料如果说不清文档名/切片，`background_of()` 会抛
   `CitationMissing`，这里翻成 502 并把问题逐条列出来 —— **不许"过滤掉没来源的、只留合规的"**，
   那会让人以为通道整体是干净的。
3. **通道没启用就明确报**：`DOCS_ENABLED=false` 或没配地址/令牌时返回 503 并说明缺什么，
   不静默返回空列表（"什么都没搜到"和"通道根本没开"是两件事）。

依赖注入点 `get_doc_channel()` 与 `store()` 同款：测试里 override 成假通道，不连 WeKnora。
"""

from __future__ import annotations

from typing import Annotated, Any

from dip_contracts.doc_channel import CitationMissing, background_of, render_background
from dip_docs import DocChannel, DocChannelNotConfigured, channel_from_settings
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..settings import settings

router = APIRouter(tags=["knowledge"])


def get_doc_channel() -> DocChannel | None:
    """文档通道（默认关闭）。测试里用 `app.dependency_overrides[get_doc_channel]` 换假实现。"""
    return channel_from_settings(settings)


class DocSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500, description="要查的话（用户原话）")
    limit: int | None = Field(default=None, ge=1, le=30, description="最多几条，默认用配置里的 top_k")


@router.post("/knowledge/doc-search")
async def doc_search(
    req: DocSearchRequest,
    channel: Annotated[DocChannel | None, Depends(get_doc_channel)] = None,
) -> dict[str, Any]:
    if channel is None:
        raise HTTPException(
            status_code=503,
            detail="文档通道未启用：需要 DOCS_ENABLED=true 且配好 WEKNORA_MCP_URL / WEKNORA_MCP_TOKEN",
        )

    try:
        hits = await channel.search(req.query, limit=req.limit)
    except DocChannelNotConfigured as exc:  # 配置在运行期被改坏
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001  网络/协议层失败：如实报，不装成"没搜到"
        raise HTTPException(status_code=502, detail=f"文档通道调用失败：{type(exc).__name__}: {exc}") from exc

    try:
        background = background_of(hits)
    except CitationMissing as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "文档通道返回的材料缺来源，按「引用必须标注来源」拒收（M3-04）",
                "problems": [p.model_dump(mode="json") for p in exc.problems],
            },
        ) from exc

    return {
        "query": req.query,
        "channel": background.channel,
        "usable_for_conclusion": background.usable_for_conclusion,
        "count": len(background.hits),
        "citations": background.citation_labels,
        "hits": [hit.model_dump(mode="json") for hit in background.hits],
        "rendered": render_background(background),
    }


@router.get("/knowledge/doc-channel")
def doc_channel_status(
    channel: Annotated[DocChannel | None, Depends(get_doc_channel)] = None,
) -> dict[str, Any]:
    """通道状态。**只报"开了哪组工具"，不报令牌**（令牌不进日志、不进响应）。"""
    if channel is None:
        return {
            "enabled": False,
            "channel": "documents",
            "usable_for_conclusion": False,
            "hint": "需要 DOCS_ENABLED=true 且配好 WEKNORA_MCP_URL / WEKNORA_MCP_TOKEN",
        }
    describe = getattr(channel, "describe", None)
    detail = describe() if callable(describe) else {"channel": "documents"}
    return {
        "enabled": True,
        "usable_for_conclusion": False,
        "tool_group": "retrieve",  # 只开只读那一组；ingest/chat/wiki 不授权
        **detail,
    }

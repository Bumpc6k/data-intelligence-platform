"""把文档通道的材料挂到答案上（编排层，M3-04 / Issue #15）。

规矩只有一条：**文档通道的状态只影响 `background` 和 `tool_calls`，永远不影响结论**。

- 通道没开、网络挂了、材料缺来源 → 结论照旧（结构化通道给的），只是在 `tool_calls` 里
  留一条"文档通道失败：原因"，让人在界面上看得见，而不是静默少一块。
- 只有真把材料挂上去时才调 `attach_background(strict=True)`；它一旦发现结论被动过或
  结论文本里混进了文档的数字，会直接抛 `ConclusionContaminated`（那是 bug，不该被吞掉）。
"""

from __future__ import annotations

import time
from typing import Any

from dip_contracts.doc_channel import (
    CHANNEL_DOCUMENTS,
    CitationMissing,
    ConclusionContaminated,
    attach_background,
)
from dip_contracts.models import Answer, ToolCall

DOC_SEARCH_TOOL = "search_knowledge"


async def with_document_background(answer: Answer, query: str, channel: Any | None) -> Answer:
    """查一次文档通道，把结果当背景挂到 `answer` 上（不动结论）。"""
    if channel is None:
        return _record_failure(answer, "文档通道未启用（缺 WEKNORA_MCP_URL / WEKNORA_MCP_TOKEN）")

    started = time.monotonic()
    try:
        hits = await channel.search(query)
        merged = attach_background(answer, hits, strict=True)
    except CitationMissing as exc:
        return _record_failure(answer, f"文档材料缺来源，按 M3-04 拒收：{[p.code for p in exc.problems]}")
    except ConclusionContaminated:
        raise  # 结论被污染 = 代码 bug，必须炸出来，不许装成"文档通道失败"
    except Exception as exc:  # noqa: BLE001  通道故障不拖累结构化通道的答案
        return _record_failure(answer, f"{type(exc).__name__}: {exc}")

    elapsed = int((time.monotonic() - started) * 1000)
    call = ToolCall(
        name=DOC_SEARCH_TOOL,
        args={"query": query, "channel": CHANNEL_DOCUMENTS},
        ms=elapsed,
        ok=True,
        endpoint=getattr(channel, "url", None),
    )
    return merged.model_copy(update={"tool_calls": [*merged.tool_calls, call]})


def _record_failure(answer: Answer, error: str) -> Answer:
    call = ToolCall(
        name=DOC_SEARCH_TOOL,
        args={"channel": CHANNEL_DOCUMENTS},
        ms=0,
        ok=False,
        error=error,
    )
    return answer.model_copy(update={"tool_calls": [*answer.tool_calls, call]})

"""问答入口（B2）：一句话 → 统一结果模型；随后**落库留痕**（W-117）。"""

from __future__ import annotations

from typing import Annotated, Any

from dip_agent import Agent
from dip_contracts import Answer
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import store as store_mod
from ..deps import get_agent
from ..doc_support import with_document_background
from ..view_support import with_platform_views
from .doc_channel import get_doc_channel

router = APIRouter(tags=["agent"])


def metrics_store() -> Any:
    """平台口径库的读入口（版本对照视图要用）。测试里 override 成假库。"""
    from dip_pg import knowledge

    return knowledge


def versions_store() -> Any:
    """口径版本表（M3-02 / #13）。测试里 override。"""
    from dip_pg import metric_versions

    return metric_versions


class AskRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000, description="用户原话")
    session_id: str = Field(default="default", max_length=64, description="会话标识（PG 持久化，W-112）")
    mode: str = Field(default="rule", pattern="^(rule|llm)$")
    with_docs: bool = Field(default=False, description="是否同时问文档通道（结果只作背景，M3-04 / #15）")


def store() -> Any:
    """依赖注入点：测试里用 dependency_overrides 覆盖，即可不连数据库。"""
    return store_mod


@router.post("/agent/ask", response_model=Answer)
async def ask(
    req: AskRequest,
    agent: Annotated[Agent, Depends(get_agent)],
    st: Annotated[Any, Depends(store)] = None,
    channel: Annotated[Any, Depends(get_doc_channel)] = None,
    metrics: Annotated[Any, Depends(metrics_store)] = None,
    versions: Annotated[Any, Depends(versions_store)] = None,
) -> Answer:
    # 刷新页面/重启服务后，用库里最后一次的表名回填上下文，追问才不会断
    if st.available() and not agent.context_tables(req.session_id):
        tables = st.last_tables(req.session_id)
        if tables:
            agent.seed_context(req.session_id, tables=tables)

    answer = agent.ask(req.text, session_id=req.session_id, mode=req.mode)

    # 平台侧视图（M4-01 / #17）：口径版本对照来自平台口径库，接在内核视图之后
    try:
        answer = with_platform_views(answer, metrics_store=metrics, versions_store=versions)
    except Exception:  # noqa: BLE001  视图是加分项，取不到不该把回答拖挂
        pass

    # 文档通道（M3-04）：只往 background 与 tool_calls 里加东西，**结论一个字不动**
    if req.with_docs:
        answer = await with_document_background(answer, req.text, channel)

    if st.available():
        try:
            audit_id = st.record_ask(req.session_id, req.text, answer.model_dump(mode="json"))
            answer = answer.model_copy(update={"audit_id": f"aud-{audit_id}"})
        except Exception:  # 落库失败不能影响回答本身，但要能被发现
            pass
    return answer

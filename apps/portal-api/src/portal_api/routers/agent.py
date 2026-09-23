"""问答入口（B2）：一句话 → 统一结果模型；随后**落库留痕**（W-117）。"""

from __future__ import annotations

from typing import Annotated, Any

from dip_agent import Agent
from dip_contracts import Answer
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import store as store_mod
from ..deps import get_agent

router = APIRouter(tags=["agent"])


class AskRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000, description="用户原话")
    session_id: str = Field(default="default", max_length=64, description="会话标识（PG 持久化，W-112）")
    mode: str = Field(default="rule", pattern="^(rule|llm)$")


def store() -> Any:
    """依赖注入点：测试里用 dependency_overrides 覆盖，即可不连数据库。"""
    return store_mod


@router.post("/agent/ask", response_model=Answer)
def ask(
    req: AskRequest,
    agent: Annotated[Agent, Depends(get_agent)],
    st: Annotated[Any, Depends(store)] = None,
) -> Answer:
    # 刷新页面/重启服务后，用库里最后一次的表名回填上下文，追问才不会断
    if st.available() and not agent.context_tables(req.session_id):
        tables = st.last_tables(req.session_id)
        if tables:
            agent.seed_context(req.session_id, tables=tables)

    answer = agent.ask(req.text, session_id=req.session_id, mode=req.mode)

    if st.available():
        try:
            audit_id = st.record_ask(req.session_id, req.text, answer.model_dump(mode="json"))
            answer = answer.model_copy(update={"audit_id": f"aud-{audit_id}"})
        except Exception:  # 落库失败不能影响回答本身，但要能被发现
            pass
    return answer

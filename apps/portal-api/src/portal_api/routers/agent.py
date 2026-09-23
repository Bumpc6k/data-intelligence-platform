"""问答入口（B2）：一句话 → 统一结果模型。

契约就是 `dip_contracts.models.Answer`（前端只认这个结构，见《设计说明书》§8）。
失败也返回 200 + `status=unresolved`：界面上要显示"证据不足"，而不是弹一个技术性 5xx。
"""

from __future__ import annotations

from typing import Annotated

from dip_agent import Agent
from dip_contracts import Answer
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..deps import get_agent

router = APIRouter(tags=["agent"])


class AskRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000, description="用户原话")
    session_id: str = Field(default="default", max_length=64, description="会话标识（P1 用内存上下文，W-112 落库）")
    mode: str = Field(default="rule", pattern="^(rule|llm)$")


@router.post("/agent/ask", response_model=Answer)
def ask(req: AskRequest, agent: Annotated[Agent, Depends(get_agent)]) -> Answer:
    return agent.ask(req.text, session_id=req.session_id, mode=req.mode)

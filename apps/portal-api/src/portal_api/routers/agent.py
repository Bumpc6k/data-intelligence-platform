"""问答入口（B2 落地：接 dip_agent 的编排）——当前故意返回 501，避免伪造结果。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["agent"])


class AskRequest(BaseModel):
    text: str
    session_id: str | None = None


@router.post("/agent/ask")
def ask(req: AskRequest) -> dict:
    raise HTTPException(
        status_code=501,
        detail="对话编排尚未实现（工作项 W-114/W-103，B2）。当前可用：GET /api/health、内核 /analyze 等只读端点。",
    )

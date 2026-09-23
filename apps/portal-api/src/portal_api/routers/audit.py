"""审计与会话接口（W-117 / W-112）：让"平台回答过什么"可查证。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import store as store_mod

router = APIRouter(tags=["audit"])


def store() -> Any:
    """依赖注入点：测试里用 dependency_overrides 覆盖，即可不连数据库。"""
    return store_mod


@router.get("/audit")
def list_audit(
    session_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    st: Annotated[Any, Depends(store)] = None,
) -> dict:
    if not st.available():
        return {"success": False, "error": "数据库不可用（未落库）", "items": []}
    return {"success": True, "items": st.list_audit(session_id, limit)}


@router.get("/sessions")
def list_sessions(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    st: Annotated[Any, Depends(store)] = None,
) -> dict:
    if not st.available():
        return {"success": False, "error": "数据库不可用", "items": []}
    return {"success": True, "items": st.list_sessions(limit)}


@router.get("/sessions/{session_id}")
def session_detail(session_id: str, st: Annotated[Any, Depends(store)] = None) -> dict:
    if not st.available():
        raise HTTPException(status_code=503, detail="数据库不可用")
    return {"success": True, "session_id": session_id,
            "messages": st.session_history(session_id),
            "audit": st.list_audit(session_id, 50)}

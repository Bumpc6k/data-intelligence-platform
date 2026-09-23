from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """进程存活探针（不带内核依赖，供容器/lb 使用）。"""
    return {"status": "ok", "service": "portal-api"}

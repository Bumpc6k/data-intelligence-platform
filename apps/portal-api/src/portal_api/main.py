"""平台后端骨架：目前只有健康检查与一个占位的问答入口。

B2 会在这里挂上真正的编排（dip_agent），B5 补身份/会话/审计/报告代理。
"""

from __future__ import annotations

from pathlib import Path

from dip_core import load_settings
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from lineage_client import LineageClient

from . import store
from .routers import agent, audit, health, lineage, reports

settings = load_settings()

app = FastAPI(title="数据智能平台 · portal-api", version="0.2.0")

# 启动时幂等建表（数据库不可用不影响平台可用，界面会提示"未落库"）
try:
    store.init_schema()
except Exception as exc:  # noqa: BLE001
    print(f"[portal-api] 数据库不可用，审计将不落库：{exc}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:5190"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(agent.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(lineage.router, prefix="/api")
app.include_router(audit.router, prefix="/api")


@app.get("/api/health", tags=["health"])
def api_health() -> dict:
    """平台自身 + 内核连通性（degraded 而不是 500：界面要能显示"内核不可达"）。"""
    with LineageClient(settings.kernel_base_url, retries=0) as client:
        r = client.health()
    return {
        "platform": "ok",
        "kernel": {"ok": r.ok, "base_url": settings.kernel_base_url, "ms": r.ms, "error": r.error},
        "mode": settings.mode,
        "auth_mode": settings.auth_mode,
    }


def _not_implemented(what: str, work_item: str) -> HTTPException:
    return HTTPException(status_code=501, detail=f"{what} 尚未实现（工作项 {work_item}，见 docs/ 设计与 ADR）")

# B4 静态迭代版前端（零构建）由后端直接托管：http://127.0.0.1:18100/app/
_WEB_DIR = Path(__file__).resolve().parents[3] / "portal-web"
if _WEB_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")

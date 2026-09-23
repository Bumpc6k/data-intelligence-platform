"""报告代理（W-118）：前端只看平台地址，不暴露内核内网地址，也不跨域。

- `GET /api/reports/`      → 报告列表（内核 /reports）
- `GET /api/reports/{id}`  → 直接把内核生成的自包含 HTML 透传回来（可在 iframe 里打开）
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from lineage_client import LineageClient

from ..deps import get_client

router = APIRouter(tags=["reports"], prefix="/reports")


@router.get("/")
def list_reports(client: Annotated[LineageClient, Depends(get_client)]) -> dict:
    r = client.list_reports()
    if not r.ok:
        return {"success": False, "error": r.error, "reports": []}
    return r.data


@router.get("/{report_id}", response_class=HTMLResponse)
def get_report(report_id: str, client: Annotated[LineageClient, Depends(get_client)]) -> HTMLResponse:
    if not report_id or len(report_id) > 128 or any(ch in report_id for ch in "/\\.."):
        raise HTTPException(status_code=400, detail="report_id 非法")
    url = client.report_url(report_id)
    resp = client.raw_get(url)
    if resp is None:
        raise HTTPException(status_code=404, detail="报告不存在或内核不可达")
    return HTMLResponse(content=resp)

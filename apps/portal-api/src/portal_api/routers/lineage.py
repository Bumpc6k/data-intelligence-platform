"""血缘图数据（供前端证据画布画真实节点/边）。

内核的 /upstream 返回 tables + paths；前端要的是 nodes/edges，这里做一次形状转换，
仍然遵循"平台不造假"：转换只做重组，不新增任何推断出来的表或边。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from lineage_client import LineageClient

from ..deps import get_client

router = APIRouter(tags=["lineage"], prefix="/lineage")

LAYER_ORDER = ["ods", "dwd", "dws", "ads", "dim"]


def layer_of(table: str) -> str:
    """真实数据里 schema 是 cdw/dim，层次藏在表名里：cdw.dws_产销存汇总 → dws。"""
    leaf = (table or "").split(".")[-1].lower()
    head = leaf.split("_")[0]
    if head in LAYER_ORDER:
        return head
    schema = (table or "").split(".")[0].lower()
    return schema if schema in LAYER_ORDER else "other"


@router.get("/upstream")
def upstream(
    table: Annotated[str, Query(min_length=1, max_length=200)],
    depth: Annotated[int, Query(ge=1, le=10)] = 5,
    client: Annotated[LineageClient, Depends(get_client)] = None,  # type: ignore[assignment]
) -> dict:
    r = client.upstream(table, depth=depth)
    if not r.ok:
        raise HTTPException(status_code=502, detail=r.error or "内核不可用")
    tables = r.data.get("tables") or []
    edges: list[list[str]] = []
    for path in r.data.get("paths") or []:
        for a, b in zip(path, path[1:], strict=False):  # 路径上相邻两跳就是一条边（按方向：上游 → 下游）
            if [a, b] not in edges:
                edges.append([a, b])
    return {
        "success": True,
        "start_table": r.data.get("start_table"),
        "nodes": [{"name": t, "layer": layer_of(t)} for t in [*tables, r.data.get("start_table")] if t],
        "edges": edges,
        "upstream_count": r.data.get("upstream_count"),
        "edge_count": r.data.get("edge_count"),
        "levels": r.data.get("levels") or [],
        "endpoint": r.endpoint,
    }

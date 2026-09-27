"""工具结果 → 视图块（工作项 M4-01 / Issue #17）。

这里只做一件事：**把内核返回的真实结构翻译成"该用哪个视图渲染"的声明**（`ViewBlock`）。
不做任何加工、不新增事实 —— 视图块里的 `data` 就是内核/平台返回的原始形状
（血缘是 tables/paths/levels，检索是 groups，口径是 expression_raw），
**具体怎么画是前端那个视图的事**（`apps/portal-web/views/*.js`）。

三条设计取舍：

1. **视图归属可由 skill 声明覆盖**：内核工具产出哪类证据（`KIND_BY_TOOL`）决定默认视图，
   而 skill 声明里的 `renderer`（M1-02 的第 8 类字段）可以按证据种类覆盖它 ——
   `renderer_by_kind={"lineage": "table"}` 之后，血缘结果就交给表格视图渲染。
   这条链子是"前端按 skill 声明的 renderer 分派"的落点，有测试钉住。
2. **同一种视图合并成一块**：连续两次检索不该给用户两张表；同一 renderer 的块按顺序合并。
3. **给不出视图就不给**：认不出的工具返回 `None`，不硬凑一个空视图（空图比没图更误导）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from dip_contracts import ViewBlock

__all__ = [
    "DEFAULT_RENDERER_BY_KIND",
    "KIND_BY_TOOL",
    "MAX_ROWS",
    "block_for",
    "blocks_for",
    "merge_views",
    "renderer_for",
    "view_map",
    "views_from_results",
]

#: 内核工具 → 它产出的证据种类（决定默认视图，也是 skill 声明能覆盖的挂点）
KIND_BY_TOOL: dict[str, str] = {
    "upstream": "lineage",
    "impact": "lineage",
    "analyze": "lineage",
    "search": "metric",
    "metric": "metric",
    "ask": "metric",
}

#: 没被 skill 声明覆盖时的默认归属。取舍理由：血缘天然是图；检索/口径是表格；
#: report / log 现在也走表格（等有专门的视图再说，别凭空发明第四种）。
DEFAULT_RENDERER_BY_KIND: dict[str, str] = {
    "lineage": "graph",
    "metric": "table",
    "dict": "table",
    "report": "table",
    "log": "table",
}

#: 一块表格最多放多少行（多了人就看不完，界面也会卡；超了会写明"已截断"）
MAX_ROWS = 50


def renderer_for(tool: str, *, renderer_by_kind: dict[str, str] | None = None) -> str | None:
    """这个工具的结果该用哪个视图？认不出的工具返回 `None`（**不硬猜**）。"""
    kind = KIND_BY_TOOL.get(tool)
    if kind is None:
        return None
    merged = {**DEFAULT_RENDERER_BY_KIND, **(renderer_by_kind or {})}
    return merged.get(kind)


def _rows_of(groups: dict[str, Any]) -> list[dict[str, Any]]:
    """把 `/kb/search` 的 `groups` 摊平成表格行（每种命中都是真数据，不做二次解释）。"""
    rows: list[dict[str, Any]] = []
    for kind, items in (groups or {}).items():
        for item in items or []:
            if not isinstance(item, dict):
                continue
            rows.append({
                "kind": kind,
                "name": item.get("metric_name") or item.get("column") or item.get("table") or item.get("term") or "",
                "chinese_name": item.get("chinese_name") or item.get("chinese") or "",
                "table": item.get("table_name") or item.get("table") or "",
                "layer": item.get("layer") or "",
                "formula": item.get("formula") or item.get("formula_full") or "",
                "expression": item.get("expression_raw") or "",
                "score": item.get("score"),
            })
    rows.sort(key=lambda r: (-(r["score"] or 0), r["kind"], r["name"]))
    return rows


def _sql_statements(groups: dict[str, Any]) -> list[dict[str, Any]]:
    """口径命中里的 SQL 表达式 + 来源脚本（有就是有，没有就不列 —— 不编 SQL）。"""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in (groups or {}).get("metrics") or []:
        expression = (item.get("expression_raw") or "").strip()
        if not expression or expression in seen:
            continue
        seen.add(expression)
        out.append({
            "sql": expression,
            "table": item.get("table_name") or "",
            "metric_name": item.get("metric_name") or "",
            "formula": item.get("formula") or "",
            "source_file": item.get("source_file") or "",
            "source_stmt": item.get("source_stmt"),
            "notes": item.get("notes") or "",
        })
    return out


def _primary_block(
    tool: str,
    payload: dict[str, Any],
    *,
    renderer: str,
    endpoint: str | None,
) -> ViewBlock | None:
    """一个工具结果的**主要**视图（由证据种类决定，可被 skill 声明覆盖）。"""
    if tool in ("upstream", "impact", "analyze"):
        if not (payload.get("tables") or payload.get("paths")):
            return None
        return ViewBlock(
            renderer=renderer,
            title=f"血缘：{payload.get('start_table') or ''}",
            source=endpoint or f"POST /{tool}",
            data={
                "start_table": payload.get("start_table"),
                "direction": payload.get("direction") or ("downstream" if tool == "impact" else "upstream"),
                "tables": payload.get("tables") or [],
                "paths": payload.get("paths") or [],
                "levels": payload.get("levels") or [],
                "upstream_count": payload.get("upstream_count"),
                "downstream_count": payload.get("downstream_count"),
                "edge_count": payload.get("edge_count"),
            },
        )

    groups = payload.get("groups") or {}
    if not groups:
        return None
    rows = _rows_of(groups)
    statements = _sql_statements(groups)

    if renderer == "graph":
        # 有人把检索声明成 graph 视图：没有节点可画，就**不给视图**（不画空图）
        return None
    if renderer == "sql":
        if not statements:
            return None
        return ViewBlock(
            renderer="sql",
            title=f"口径 SQL：{payload.get('query') or ''}",
            source=endpoint or f"POST /{tool}",
            data={"statements": statements[:MAX_ROWS]},
        )
    if not rows:
        return None
    truncated = len(rows) > MAX_ROWS
    return ViewBlock(
        renderer=renderer,   # 默认 table；声明改成别的名字时也照给（表格视图能摊平任何结构）
        title=f"检索命中：{payload.get('query') or ''}",
        source=endpoint or f"POST /{tool}",
        data={"query": payload.get("query"), "total": payload.get("total"),
              "rows": rows[:MAX_ROWS], "truncated": truncated},
        note=f"共 {len(rows)} 行，只显示前 {MAX_ROWS} 行（已截断）" if truncated else None,
    )


def block_for(
    tool: str,
    data: dict[str, Any] | None,
    *,
    endpoint: str | None = None,
    renderer_by_kind: dict[str, str] | None = None,
) -> ViewBlock | None:
    """单个工具结果的**主要**视图块（认不出就返回 `None`）。"""
    renderer = renderer_for(tool, renderer_by_kind=renderer_by_kind)
    if renderer is None:
        return None
    return _primary_block(tool, data if isinstance(data, dict) else {}, renderer=renderer, endpoint=endpoint)


def blocks_for(
    tool: str,
    data: dict[str, Any] | None,
    *,
    endpoint: str | None = None,
    renderer_by_kind: dict[str, str] | None = None,
) -> list[ViewBlock]:
    """一个工具结果能给的所有视图：**主要视图 + 附加视图**。

    附加视图的规则只有一条：检索结果里带 `expression_raw`（口径 SQL 原文）时，
    再给一块 `sql` —— 同一批真数据的另一种看法（表格看命中，SQL 看口径怎么算的）。
    声明把主要视图改成 `sql` 时不重复给附加块（合并时也会去重）。
    """
    primary = block_for(tool, data, endpoint=endpoint, renderer_by_kind=renderer_by_kind)
    blocks = [primary] if primary else []
    if tool not in ("upstream", "impact", "analyze") and (primary is None or primary.renderer != "sql"):
        statements = _sql_statements((data or {}).get("groups") or {})
        if statements:
            blocks.append(ViewBlock(
                renderer="sql",
                title=f"口径 SQL：{(data or {}).get('query') or ''}",
                source=endpoint or f"POST /{tool}",
                data={"statements": statements[:MAX_ROWS]},
            ))
    return blocks


def merge_views(views: Iterable[ViewBlock]) -> list[ViewBlock]:
    """同一种视图合并成一块（表格行接起来、SQL 语句接起来）。

    为什么合并：连续两次检索会各出一张表，界面上就是两块几乎一样的卡片；
    合并后按出现顺序给出**一块表**，行里带 kind/score，人更容易扫。
    """
    merged: list[ViewBlock] = []
    index: dict[str, int] = {}
    for view in views:
        if view.renderer not in index:
            index[view.renderer] = len(merged)
            merged.append(view)
            continue
        slot = merged[index[view.renderer]]
        data = dict(slot.data)
        if view.renderer == "table":
            data["rows"] = [*data.get("rows", []), *view.data.get("rows", [])][:MAX_ROWS]
            data["truncated"] = bool(data.get("truncated")) or bool(view.data.get("truncated"))
        elif view.renderer == "sql":
            seen = {s.get("sql") for s in data.get("statements", [])}
            data["statements"] = [
                *data.get("statements", []),
                *[s for s in view.data.get("statements", []) if s.get("sql") not in seen],
            ][:MAX_ROWS]
        else:
            # 图/差异视图是"一图一景"，各自成块更清楚 —— 不合并
            merged.append(view)
            continue
        merged[index[view.renderer]] = slot.model_copy(update={"data": data})
    return merged


def view_map(
    results: Iterable[tuple[Any, Any]],
    *,
    renderer_by_kind: dict[str, str] | None = None,
) -> tuple[list[ViewBlock], dict[int, str]]:
    """一次算两样：**合并后的视图块** 与 **每个结果对应的视图名**（按 `id(result)`）。

    编排层两处都要：视图给前端渲染用，视图名要标在步骤条那一条 `ToolCall` 上
    （"这一步的结果是用什么画的"是可核对信息，不该只在前端现猜）。
    """
    blocks: list[ViewBlock] = []
    by_result: dict[int, str] = {}
    for step, res in results:
        if not getattr(res, "ok", False):
            continue
        produced = blocks_for(
            getattr(step, "tool", ""),
            getattr(res, "data", None),
            endpoint=getattr(res, "endpoint", None),
            renderer_by_kind=renderer_by_kind,
        )
        if produced:
            blocks.extend(produced)
            # 步骤条上标**主要**视图（第一块）—— 附加视图是同一步的另一种看法，别把名字盖掉
            by_result[id(res)] = produced[0].renderer
    return merge_views(blocks), by_result


def views_from_results(
    results: Iterable[tuple[Any, Any]],
    *,
    renderer_by_kind: dict[str, str] | None = None,
) -> list[ViewBlock]:
    """把一轮问答里的工具结果收成视图块列表（顺序即出现顺序）。

    `results` 是 `(PlanStep, ToolResult)` 的序列（与编排层手里那份一致）；
    失败的调用不产出视图 —— 没有数据就没有图，不许画空图充数。
    """
    return view_map(results, renderer_by_kind=renderer_by_kind)[0]

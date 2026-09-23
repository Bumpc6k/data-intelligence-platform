"""答案成型（W-103 / W-115）：把 `Findings` 变成统一的 `Answer`。

规则模式（默认）：模板生成文本——**数据完全来自内核**，措辞固定，可复现。
LLM 模式（B2 后续）：只允许改写措辞与生成建议追问，**不得生成事实**（ADR-0004）；
两种模式的 `result` 字段必须逐字段一致，由 `tests/test_agent_rule_mode.py` 强制。
"""

from __future__ import annotations

import uuid

from dip_contracts import Answer, Result, ResultValue, Source, Status, ToolCall

from .assemble import Findings


def _text(f: Findings, intents: list[str]) -> str:
    parts: list[str] = []
    if f.formula:
        parts.append(f.formula + "。")
        if f.formula_full and f.formula_full != f.formula:
            parts.append(f"忠实表达式：{f.formula_full}。")
    if f.notes:
        parts.append("；".join(f.notes) + "。")
    if not parts and f.lineage:
        data = f.lineage
        if data.get("upstream_count") is not None:
            parts.append(f"{data.get('start_table')} 的上游共 {data['upstream_count']} 张表。")
        elif data.get("downstream_count") is not None:
            parts.append(f"{data.get('start_table')} 的下游共 {data['downstream_count']} 张表。")
    if not parts:
        return "我只查到有限的线索，暂时无法给出可追溯的结论。"
    if Status(f.status).value != "verified":
        parts.append("这条结论的证据链尚未完全确认，建议人工核对后再使用。")
    return "".join(parts)


def _value(f: Findings) -> ResultValue | None:
    if f.formula:
        return ResultValue(type="formula", display=f.formula, expr=f.formula_full)
    if f.lineage and f.lineage.get("upstream_count") is not None:
        return ResultValue(
            type="table",
            display=f"{f.lineage.get('start_table')} 上游 {f.lineage.get('upstream_count')} 张表",
        )
    if f.lineage and f.lineage.get("downstream_count") is not None:
        return ResultValue(
            type="table",
            display=f"{f.lineage.get('start_table')} 下游 {f.lineage.get('downstream_count')} 张表",
        )
    return None


def _suggestions(f: Findings) -> list[str]:
    out: list[str] = []
    if f.table:
        col = (f.glossary or {}).get("column_name")
        out.append(f"改 {f.table}.{col} 会砸哪些下游？" if col else f"{f.table} 的下游有哪些表？")
    if f.formula_table and f.formula_table != f.table:
        out.append(f"{f.formula_table} 的这张口径表还有哪些字段？")
    if f.report:
        out.append("把这份报告导出给我")
    return out[:3]


def compose(
    f: Findings,
    *,
    intents: list[str],
    tool_calls: list[ToolCall],
    mode: str = "rule",
    version: str | None = None,
    answer_id: str | None = None,
) -> Answer:
    source: Source | None = None
    if f.glossary and (f.glossary.get("source_files") or []):
        source = Source(kind="kernel", file=f.glossary["source_files"][0])
    elif f.formula is not None:
        metric_ev = next((e for e in f.evidence if e.source and e.source.file), None)
        source = metric_ev.source if metric_ev else None

    return Answer(
        answer_id=answer_id or f"ans_{uuid.uuid4().hex[:12]}",
        text=_text(f, intents),
        result=Result(
            value=_value(f),
            confidence=f.confidence,
            status=f.status,
            evidence=f.evidence,
            source=source,
            version=version,
        ),
        suggestions=_suggestions(f),
        tool_calls=tool_calls,
        mode="rule" if mode == "rule" else "llm",
    )


def clarifier(text: str, *, reason: str, tool_calls: list[ToolCall] | None = None) -> Answer:
    """证据不足 / 实体缺失 → **反问**，不出结论（铁律 1 的另一面：不许猜）。"""
    return Answer(
        answer_id=f"ans_{uuid.uuid4().hex[:12]}",
        text=text,
        result=Result(value=None, confidence=0.0, status=Status.UNRESOLVED, evidence=[]),
        suggestions=["ads.ads_产销存月报 的产量怎么来的？", "ads.ads_产销存月报 的下游有哪些表？"],
        tool_calls=tool_calls or [],
    )

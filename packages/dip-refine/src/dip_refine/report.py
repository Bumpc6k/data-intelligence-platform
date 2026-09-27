"""报告渲染：Markdown（给人看）+ JSON（给下一步/留档）。

工作项 M3-05 / Issue #16。报告有两条硬要求：

1. **每条候选都要能指到出处**：来源脚本 + 语句序号 + 置信度 + 模型理由，缺一不可。
2. **不许把"没跑成"藏起来**：报告顶部第一条就是完整性声明 —— 有脚本或语句失败时明写
   **「本报告不完整，别据此入库」**，而不是只把能跑的那部分排版得很漂亮。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .pipeline import RefineReport, ScreenedCandidate

SAMPLE_TEMPLATE = "templates/人工抽检记录.md"


def _pct(value: float) -> str:
    return f"{value:.2f}"


def candidate_block(c: ScreenedCandidate) -> list[str]:
    """一条候选的完整交代（出处 + 公式 + 依赖 + 理由 + 校验）。"""
    lines = [
        f"#### `{c.subject}`",
        "",
        f"- 中文名：{c.draft.chinese_name or '（无）'}",
        f"- 公式：{c.draft.formula}",
        "- 依赖字段：" + ("、".join(f"`{d.table}.{d.column}`" for d in c.draft.depends_on) or "（无）"),
        f"- 来源：`{c.source_script}` 第 {c.statement_index} 条语句",
        f"- 置信度（模型自评）：**{_pct(c.confidence)}**",
        f"- 模型理由：{c.draft.note or '（模型未给理由）'}",
    ]
    check = c.check
    if check.ok:
        lines.append("- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）")
    else:
        lines.append("- 校验：**有问题（未通过）**")
        for problem in check.problem_list:
            lines.append(f"    - 契约：`{problem.code}` {problem.message}")
        for violation in check.violations:
            kind = getattr(violation.kind, "value", str(violation.kind))
            lines.append(f"    - 编造/越界：{kind} `{violation.token}`")
        for note in check.identifier_notes:
            lines.append(f"    - 白名单核对：{note}")
    return lines


def render_markdown(report: RefineReport) -> str:
    statements = sum(len(s.statements) for s in report.parsed_ok)
    lines: list[str] = [
        f"# 知识提炼报告 · {report.project}",
        "",
        "> **只出报告，未写入候选池**（Issue #16 的边界）。",
        "> 候选要进知识库，得先由**人**按 `docs/collaboration/notes/knowledge.md` 的流程抽检，",
        f"> 抽检合格后再用 `POST /api/knowledge/candidates` 提交（记录模板：`{SAMPLE_TEMPLATE}`）。",
        "",
    ]
    if report.incomplete:
        lines += [
            "> ⚠️ **本报告不完整**：有条目没跑成（见最后一节），**别据此入库**，修好之后重跑。",
            "",
        ]
    lines += [
        "## 0. 概况",
        "",
        f"- 生成时间：{report.generated_at}",
        f"- 模型（经平台模型网关）：{report.model}",
        f"- 脚本：{len(report.parsed_ok)}/{len(report.scripts)} 个解析成功，共 {statements} 条语句",
        f"- LLM 初筛调用：{report.llm_calls} 次",
        f"- 候选：{len(report.candidates)} 条（通过校验 {len(report.ok_candidates)} 条 / 有问题 {len(report.needs_human)} 条）",
        "",
        "## 1. 解析结果（内核 `/parse`）",
        "",
        "| 脚本 | 语句 | 表 | 字段 | 备注 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for script in report.scripts:
        note = "解析失败" if not script.ok else ""
        lines.append(
            f"| `{script.source_script}` | {len(script.statements)} | {len(script.tables)} | "
            f"{len(script.fields)} | {script.error or note} |"
        )
    lines += ["", f"## 2. 通过校验的候选（{len(report.ok_candidates)} 条）", ""]
    if report.ok_candidates:
        for c in report.ok_candidates:
            lines += candidate_block(c) + [""]
    else:
        lines.append("（无）")
        lines.append("")

    lines += [f"## 3. 需要人看的问题候选（{len(report.needs_human)} 条）", ""]
    if report.needs_human:
        for c in report.needs_human:
            lines += candidate_block(c) + [""]
    else:
        lines.append("（无）")
        lines.append("")

    lines += [f"## 4. 人工抽检建议（{len(report.sample)} 条）", "",
              "抽检规则：置信度**最低**的几条 + 最高的几条 + 随机几条（固定种子，报告可复现）。",
              f"抽检结论请记到 `{SAMPLE_TEMPLATE}`，**合格之前不要提交候选**。", "",
              "| 候选 | 置信度 | 来源 | 抽检结论（待人填） | 理由（待人填） |",
              "| --- | --- | --- | --- | --- |"]
    for c in report.sample:
        lines.append(f"| `{c.subject}` | {_pct(c.confidence)} | `{c.source_script}` #{c.statement_index} | | |")
    lines.append("")

    if report.errors:
        lines += ["## 5. 失败与错误", ""]
        lines += [f"- {e}" for e in report.errors]
        lines.append("")
    return "\n".join(lines)


def render_json(report: RefineReport) -> str:
    return json.dumps(to_dict(report), ensure_ascii=False, indent=2)


def to_dict(report: RefineReport) -> dict[str, Any]:
    return {
        "project": report.project,
        "model": report.model,
        "generated_at": report.generated_at,
        "wrote_to_candidate_pool": False,          # 报告自己声明边界（Issue #16）
        "incomplete": report.incomplete,
        "counts": {
            "scripts": len(report.scripts),
            "scripts_parsed_ok": len(report.parsed_ok),
            "statements": sum(len(s.statements) for s in report.parsed_ok),
            "llm_calls": report.llm_calls,
            "candidates": len(report.candidates),
            "candidates_ok": len(report.ok_candidates),
            "candidates_needing_human": len(report.needs_human),
        },
        "scripts": [
            {
                "source_script": s.source_script,
                "ok": s.ok,
                "error": s.error,
                "tables": list(s.tables),
                "fields": list(s.fields),
                "statements": [
                    {
                        "index": st.index,
                        "task_type": st.task_type,
                        "input_tables": list(st.input_tables),
                        "output_tables": list(st.output_tables),
                        "columns": [{"column": c, "expression": e, "source_table": t} for c, e, t in st.columns],
                    }
                    for st in s.statements
                ],
            }
            for s in report.scripts
        ],
        "candidates": [c.as_dict() for c in report.candidates],
        "sample": [c.as_dict() for c in report.sample],
        "errors": report.errors,
    }


def write_report(report: RefineReport, out_dir: Path, *, stem: str = "refine-report") -> tuple[Path, Path]:
    """写出 Markdown + JSON 两份（同内容不同形态），返回两个路径。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{stem}.md"
    json_path = out_dir / f"{stem}.json"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(render_json(report), encoding="utf-8")
    return md_path, json_path

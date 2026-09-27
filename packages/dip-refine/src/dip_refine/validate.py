"""规则校验：LLM 的产出必须过这一关才可能被提成候选。

工作项 M3-05 / Issue #16。两类检查，各管一段：

1. **契约校验**（复用 `dip_contracts.knowledge.validate_draft`）—— "这份口径写清楚了没有"：
   来源脚本、公式、依赖字段是否齐全合法。与 #12 的提交接口**同一套规则**，
   避免"流水线自己定一套格式"（那正是 #12 想消灭的东西）。
2. **事实校验**（复用 `dip_contracts.guards.check_answer`）—— "它有没有编"：
   表名、字段名、数字是否都在解析结果的白名单里。`subject` 与 `depends_on` 另外做精确核对
   （它们是要入库的字段，不靠正则猜）；`note`（模型的理由）整段过 guard，编了就在报告里点名。

结论只有三种，报告里分开列：`ok`（可提交）/ `needs_human`（有问题，要人看）/ 解析层面的错误在流水线那层。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dip_contracts.guards import Whitelist, check_answer
from dip_contracts.knowledge import CandidateDraft, Problem, validate_draft


@dataclass(frozen=True)
class CandidateCheck:
    """一条候选的校验结果。"""

    problem_list: tuple[Problem, ...] = ()          # 契约层的问题（缺来源 / 格式非法…）
    violations: tuple[Any, ...] = ()                # 出口事实校验的越界项（编造的表/字段/数字）
    identifier_notes: tuple[str, ...] = ()          # subject / depends_on 的白名单核对说明

    @property
    def ok(self) -> bool:
        return not self.problem_list and not self.violations and not self.identifier_notes

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "problems": [p.model_dump() for p in self.problem_list],
            "violations": [
                {"kind": getattr(v.kind, "value", str(v.kind)), "token": v.token, "normalized": v.normalized}
                for v in self.violations
            ],
            "identifier_notes": list(self.identifier_notes),
        }


def _table_of(subject: str) -> str:
    """`库.表.字段` → `库.表`；`库.表` → `库.表`；认不出就原样返回。"""
    parts = subject.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else subject


def check_identifiers(draft: CandidateDraft, whitelist: dict[str, set[str]]) -> list[str]:
    """`subject` 与 `depends_on` 逐一核对白名单 —— 这两样是要入库的，必须精确。"""
    notes: list[str] = []
    tables, fields = whitelist["tables"], whitelist["fields"]

    table = _table_of(draft.subject)
    if tables and table not in tables:
        notes.append(f"subject 的表 `{table}` 不在解析结果里（本项目真实存在的表：{len(tables)} 张）")
    tail = draft.subject.split(".")[-1]
    if fields and tail not in fields and table in tables:
        notes.append(f"subject 的字段 `{tail}` 不在解析结果里")

    for i, dep in enumerate(draft.depends_on):
        if dep.table and tables and dep.table not in tables:
            notes.append(f"第 {i + 1} 条依赖的表 `{dep.table}` 不在解析结果里")
        if dep.column and fields and dep.column not in fields:
            notes.append(f"第 {i + 1} 条依赖的字段 `{dep.column}` 不在解析结果里")
    return notes


def check_candidate(draft: CandidateDraft, whitelist: dict[str, set[str]]) -> CandidateCheck:
    """契约 + 事实两类校验，一次给全。

    数字白名单来自解析结果（脚本里的字面量与表达式里的数字）——
    SQL 里本来就有的 `NULLIF(x, 0)` 的 `0` 不算编造，凭空冒出来的数字才算。
    **自由文本里的标识符用 `quote_ok`**（真实表名 ∪ 脚本里出现过的 `别名.字段`）：
    模型复述 SQL 原文写的 `s.sale_qty` 不是编造，凭空造出来的才算。
    """
    problems = tuple(validate_draft(draft))
    violations: tuple[Any, ...] = ()
    note = (draft.note or "").strip()
    if note:
        verdict = check_answer(note, Whitelist(
            tables=frozenset(whitelist.get("quote_ok") or whitelist["tables"]),
            fields=frozenset(whitelist["fields"]),
            numbers=frozenset(whitelist.get("numbers") or ()),
        ))
        violations = tuple(verdict.violations)
    return CandidateCheck(
        problem_list=problems,
        violations=violations,
        identifier_notes=tuple(check_identifiers(draft, whitelist)),
    )

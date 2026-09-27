"""知识提炼流水线：解析 → LLM 初筛 → 规则校验 → **报告**（工作项 M3-05 / Issue #16）。

Issue 的边界写得明确：**先 1 个项目、只出报告不落库**。所以这条流水线**不做**任何写操作：

- 不调 `POST /api/knowledge/candidates`（候选池由**人**在抽检合格后提交，见 `docs/` 里的抽检记录模板）
- 不写知识库、不改口径

它只用两类只读能力：内核 `/parse`（解析脚本结构）与平台模型网关 `/v1/chat/completions`（LLM 初筛）。
报告是把这两步的结果摊开给人看：每个候选的来源、公式、依赖、置信度、模型理由、以及**有没有过校验**。

失败策略（都写进报告，不掩盖）：

| 情况 | 处理 |
| --- | --- |
| 某个脚本读不到 / 内核解析失败 | 记 `errors`，继续跑别的脚本（一个坏文件不该拖停全流程） |
| 某条语句模型返回的不是 JSON | 记 `errors`，报告标 `不完整`，**其余候选照常列**（但报告里写明"别据此入库"） |
| 模型整体不可用（一条候选都没筛出来） | **抛异常**，不产出报告 —— 宁可没有报告，也不给一份看起来完整的东西 |
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dip_contracts.knowledge import CandidateDraft

from .parse import ParsedScript, parse_project, whitelist_of
from .screen import Screener, screen_statement
from .validate import CandidateCheck, check_candidate


@dataclass(frozen=True)
class ScreenedCandidate:
    """一条被初筛出来的候选 + 它的置信度 + 校验结论 + 出处。"""

    draft: CandidateDraft
    confidence: float
    check: CandidateCheck
    source_script: str
    statement_index: int
    llm_raw: str = ""

    @property
    def subject(self) -> str:
        return self.draft.subject

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.draft.subject,
            "chinese_name": self.draft.chinese_name,
            "formula": self.draft.formula,
            "depends_on": [d.model_dump() for d in self.draft.depends_on],
            "source_script": self.source_script,
            "source_line": self.statement_index,
            "confidence": self.confidence,
            "reason": self.draft.note,
            "check": self.check.as_dict(),
        }


@dataclass
class RefineReport:
    """一次提炼的全部结果（报告是唯一产物）。"""

    project: str
    model: str
    scripts: list[ParsedScript] = field(default_factory=list)
    candidates: list[ScreenedCandidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    llm_calls: int = 0
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    sample: list[ScreenedCandidate] = field(default_factory=list)

    @property
    def ok_candidates(self) -> list[ScreenedCandidate]:
        return [c for c in self.candidates if c.check.ok]

    @property
    def needs_human(self) -> list[ScreenedCandidate]:
        return [c for c in self.candidates if not c.check.ok]

    @property
    def incomplete(self) -> bool:
        """报告是否不完整（有脚本 / 语句没跑成）。报告里必须显式写出来。"""
        return bool(self.errors)

    @property
    def parsed_ok(self) -> list[ParsedScript]:
        return [s for s in self.scripts if s.ok]


def run(
    project: Path,
    *,
    parser: Any,
    screener: Screener,
    project_root: Path | None = None,
    dialect: str = "hive",
    limit: int | None = None,
    sample_size: int = 10,
    seed: int = 20260927,
) -> RefineReport:
    """跑一遍流水线，返回报告对象（**不落库、不写知识库**）。"""
    root = project_root or project
    scripts = parse_project(project, parser=parser, project_root=root, dialect=dialect, limit=limit)
    whitelist = whitelist_of(scripts)
    report = RefineReport(project=str(project), model=getattr(screener, "model", "（未知）"), scripts=scripts)

    for script in scripts:
        if not script.ok:
            report.errors.append(f"{script.source_script}：{script.error}")
            continue
        for statement in script.statements:
            try:
                drafts, confidences, raw = screen_statement(script, statement, screener=screener, whitelist=whitelist)
            except Exception as exc:  # noqa: BLE001 - 单条语句失败记下来，别拖停整条流水线
                report.errors.append(f"{script.source_script} 第 {statement.index} 条语句：初筛失败（{exc}）")
                continue
            report.llm_calls += 1
            for draft, confidence in zip(drafts, confidences, strict=False):
                report.candidates.append(ScreenedCandidate(
                    draft=draft,
                    confidence=confidence,
                    check=check_candidate(draft, whitelist),
                    source_script=script.source_script,
                    statement_index=statement.index,
                    llm_raw=raw[:4000],
                ))

    if report.candidates == [] and report.errors:
        # 一条都没筛出来 + 有错 → 多半是模型/内核不可用：不产出报告（宁可没有，也不给假的）
        raise RuntimeError("一条候选都没筛出来，且存在错误，判定为初筛环节不可用：\n  " + "\n  ".join(report.errors[:5]))

    report.sample = pick_sample(report, size=sample_size, seed=seed)
    return report


def pick_sample(report: RefineReport, *, size: int = 10, seed: int = 20260927) -> list[ScreenedCandidate]:
    """给人工抽检挑样本：**最低置信度 + 最高置信度 + 随机**三类都放进去。

    只看高置信度等于自我安慰；只看低的又容易把把握大的漏掉。
    """
    if not report.candidates:
        return []
    pool = sorted(report.candidates, key=lambda c: c.confidence)
    head = pool[: max(1, size // 3)]
    tail = pool[-max(1, size // 3):]
    rest = [c for c in pool if c not in head and c not in tail]
    rng = random.Random(seed)  # 固定种子：同样的报告，抽检样本也一样（可复现）
    middle = rng.sample(rest, k=min(len(rest), max(0, size - len(head) - len(tail))))
    seen: list[ScreenedCandidate] = []
    for candidate in [*head, *middle, *tail]:
        if candidate not in seen:
            seen.append(candidate)
    return seen

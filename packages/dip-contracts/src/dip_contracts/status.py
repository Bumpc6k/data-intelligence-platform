"""`status` / `confidence` / `version` 的判定与推导（ADR-0002：平台说了算）。

内核**没有** status / version（只有 confidence 与 chinese_source），实测证据见
`tests/fixtures/kernel-probe-2026-09-23.json`。所以由平台按下面的表推导——这是 B2/B3 的核心逻辑，
写在契约层以便前端与后端共用同一套判定，避免"前后端各判一遍、结果不一致"。
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import Status

# 内核 `chinese_source` → 是否"人工维护"
CURATED_SOURCES = frozenset({"exact_glossary", "glossary"})
PENDING_SOURCES = frozenset({"pending"})
DERIVED_SOURCES = frozenset({"builtin", "rule"})

VERIFIED_CONFIDENCE_FLOOR = 0.9
LOW_CONFIDENCE = 0.7


def derive_confidence(confidences: Iterable[float | None], *, fallback: float = 0.0) -> float:
    """多证据时取**最小值**（而不是平均）：一条弱证据足以让整体结论不值得深信。"""
    vals = [float(c) for c in confidences if c is not None]
    return round(min(vals), 4) if vals else fallback


def derive_status(
    *,
    kernel_ok: bool = True,
    resolved: bool = True,
    kb_version_matches: bool = True,
    chinese_source: str | None = None,
    confidence: float | None = None,
    has_formula: bool = False,
    has_source_script: bool = False,
    has_lineage: bool = False,
    fuzzy_match: bool = False,
) -> Status:
    """按《B2 接口设计与评审》§4.2 的判定表推导 status（顺序即优先级）。

    >>> derive_status(chinese_source="exact_glossary", confidence=1.0)
    <Status.VERIFIED: 'verified'>
    >>> derive_status(has_lineage=True)                     # 链路成立但口径未定
    <Status.INFERRED: 'inferred'>
    >>> derive_status(kernel_ok=False)
    <Status.UNRESOLVED: 'unresolved'>
    """
    # 1) 硬失败：内核调用失败 / 字段血缘没解析出来
    if not kernel_ok or not resolved:
        return Status.UNRESOLVED
    # 2) 快照过期：内核知识库与平台记录对不上，先别下结论
    if not kb_version_matches:
        return Status.STALE
    # 3) 待审核术语
    if chinese_source in PENDING_SOURCES:
        return Status.CANDIDATE
    # 4) 模糊命中（表名不一致、score 低）
    if fuzzy_match:
        return Status.CANDIDATE
    # 5) 人工维护的词表且高置信
    if chinese_source in CURATED_SOURCES and (confidence is None or confidence >= 1.0):
        return Status.VERIFIED
    # 6) 脚本推导出的口径，来源明确
    if has_formula and has_source_script and (confidence or 0) >= VERIFIED_CONFIDENCE_FLOOR:
        return Status.VERIFIED
    # 7) 有链路没口径
    if has_lineage:
        return Status.INFERRED
    # 8) 兜底：证据不足
    return Status.UNRESOLVED


def needs_human_check(status: Status, confidence: float) -> bool:
    """界面提示"建议人工确认"的条件（《设计说明书》§3.3）。"""
    return status in {Status.CANDIDATE, Status.UNRESOLVED, Status.STALE} or confidence < LOW_CONFIDENCE


def derive_version(kb_schema_version: str | None, kb_built_at: str | None) -> str | None:
    """P1 的版本号 = 内核知识库快照（ADR-0002）。

    >>> derive_version("1.0.0", "2026-09-20 20:17:25+0800")
    'kb:1.0.0@2026-09-20'
    """
    if not kb_schema_version:
        return None
    day = (kb_built_at or "").split(" ")[0]
    return f"kb:{kb_schema_version}@{day}" if day else f"kb:{kb_schema_version}"

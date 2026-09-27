"""契约层：跨端共享的结果模型与 status 判定（工作项 W-103）。

模块分工：
- `models.py` —— 统一结果模型（答案/结论/证据/文档背景的数据形状）
- `status.py` —— status 与 confidence 的推导
- `guards.py` —— 出口事实校验（模型不许编表名/字段名/数字）
- `knowledge.py` —— 口径候选的契约与校验（M3-01/02/03）
- `doc_channel.py` —— 文档通道的「仅背景」约束（M3-04：文档只能当背景，不许进结论）
"""

from .doc_channel import (
    CHANNEL_DOCUMENTS,
    CHANNEL_STRUCTURED,
    CitationMissing,
    ConclusionContaminated,
    Problem,
    attach_background,
    background_of,
    check_citations,
    conclusion_contamination,
    empty_background,
    render_background,
    structured_numbers,
)
from .kernel import KernelToolkit, ToolResult
from .models import (
    Answer,
    Background,
    DocCitation,
    DocHit,
    Evidence,
    EvidenceType,
    Result,
    ResultValue,
    Source,
    Status,
    ToolCall,
)
from .status import derive_confidence, derive_status, derive_version, needs_human_check

__all__ = [
    "Answer",
    "Background",
    "CHANNEL_DOCUMENTS",
    "CHANNEL_STRUCTURED",
    "CitationMissing",
    "ConclusionContaminated",
    "DocCitation",
    "DocHit",
    "Evidence",
    "EvidenceType",
    "KernelToolkit",
    "Problem",
    "Result",
    "ResultValue",
    "Source",
    "Status",
    "ToolCall",
    "ToolResult",
    "attach_background",
    "background_of",
    "check_citations",
    "conclusion_contamination",
    "derive_confidence",
    "derive_status",
    "derive_version",
    "empty_background",
    "needs_human_check",
    "render_background",
    "structured_numbers",
]

"""契约层：跨端共享的结果模型与 status 判定（工作项 W-103）。"""

from .kernel import KernelToolkit, ToolResult
from .models import Answer, Evidence, EvidenceType, Result, ResultValue, Source, Status, ToolCall
from .status import derive_confidence, derive_status, derive_version, needs_human_check

__all__ = [
    "Answer",
    "Evidence",
    "EvidenceType",
    "KernelToolkit",
    "Result",
    "ResultValue",
    "Source",
    "Status",
    "ToolCall",
    "ToolResult",
    "derive_confidence",
    "derive_status",
    "derive_version",
    "needs_human_check",
]

"""血缘分析 skill：把内核的只读血缘能力包成一个 MCP skill（工作项 M1-03）。"""

from .analyze import (
    SKILL_ID,
    SKILL_NAME,
    SKILL_VERSION,
    LineageOutcome,
    LineageSkill,
    extract_table,
    looks_like_sql,
    summarize_sql,
)
from .declaration import SKILL_YAML, load_declaration
from .mcp_server import SERVER_NAME, TOOL_NAME, build_server

__all__ = [
    "SERVER_NAME",
    "SKILL_ID",
    "SKILL_NAME",
    "SKILL_VERSION",
    "SKILL_YAML",
    "TOOL_NAME",
    "LineageOutcome",
    "LineageSkill",
    "build_server",
    "extract_table",
    "load_declaration",
    "looks_like_sql",
    "summarize_sql",
]

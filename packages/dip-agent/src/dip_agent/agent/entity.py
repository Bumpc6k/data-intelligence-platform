"""实体识别（B2 实现，工作项 W-114）。

为什么必须自己做：实测内核 `/kb/ask` 的 `entity` 会把整句当实体（"ads.ads_产销存月报 产量怎么来"），
`evidence.metrics` 为 0（见《B2 接口设计与评审》§1.2-3）。

B2 要做的四步（顺序即优先级）：
  1. 精确匹配 `schema.table` 与 `table.column`
  2. 中文名反查（走内核 `/kb/search` 的 fields/terms 组）
  3. 会话上下文回填（"它/这个表" → 上一轮的表/字段）
  4. 兜底：整句当关键词检索（保留内核的稳退行为，但标记 confidence 低）
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Entities:
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)  # "schema.table.column"
    words: list[str] = field(default_factory=list)  # 中文名/术语
    ambiguous: bool = False  # 命中多个同分实体 → 需要澄清


def extract(text: str, *, context: dict | None = None) -> Entities:
    raise NotImplementedError("B2（W-114）：实体识别实现见模块 docstring 的四步")

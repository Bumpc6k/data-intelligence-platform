"""证据装配 + status 判定（B2/B3 核心，工作项 W-103 / W-114）。

三步（《B2 接口设计与评审》§4.3）：
  1. 打分挑选：表完全匹配 +100 / 字段名 +60 / 中文名 +40；层次加权 ads>dws>dwd>ods；
     `exact_glossary` +30；有 formula +25；最多取 3 条，血统链每层最多 1 条
  2. 跨层拼装：ads 字段(中文名) → 上游链路 → dwd 公式（"产量"的真实场景，别只取本表命中）
  3. 冲突处理：同字段两条口径公式不同 → 都列出、status=candidate、标差异点
"""

from __future__ import annotations

from dip_contracts import Evidence


def pick_evidence(tool_results: list, entities, *, limit: int = 3) -> list[Evidence]:
    raise NotImplementedError("B2（W-103）：证据挑选实现见模块 docstring")


def build_chain(evidence: list[Evidence]) -> list[str]:
    raise NotImplementedError("B2（W-103）：跨层链路拼装")

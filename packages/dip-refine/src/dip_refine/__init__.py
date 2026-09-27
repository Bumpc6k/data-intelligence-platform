"""知识提炼流水线（M3-05 / Issue #16）。

```
python -m dip_refine --project /root/projects/sql-lineage-mvp/examples/warehouse \
                     --out docs/evidence/refine
```

跑完只得到一份报告（Markdown + JSON）——**不写候选池、不写知识库**。
候选要进知识库，得先由人按 `docs/collaboration/notes/knowledge.md` 的流程抽检。
"""

from .parse import ParsedScript, ParsedStatement, SqlParser, parse_project, parse_script, whitelist_of
from .pipeline import RefineReport, ScreenedCandidate, run
from .report import render_json, render_markdown, to_dict, write_report
from .screen import GatewayLlm, Screener, build_prompt, parse_llm_json, screen_statement
from .validate import CandidateCheck, check_candidate, check_identifiers

__all__ = [
    "CandidateCheck",
    "GatewayLlm",
    "ParsedScript",
    "ParsedStatement",
    "RefineReport",
    "ScreenedCandidate",
    "Screener",
    "SqlParser",
    "build_prompt",
    "check_candidate",
    "check_identifiers",
    "parse_llm_json",
    "parse_project",
    "parse_script",
    "render_json",
    "render_markdown",
    "run",
    "screen_statement",
    "to_dict",
    "whitelist_of",
    "write_report",
]

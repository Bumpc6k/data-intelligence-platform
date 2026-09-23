"""编排子模块：entity → intent → planner → assemble → answer（顺序即数据流）。

对外只需要 `orchestrator.Agent`（或 `make_agent()`）。
"""

from .assemble import Findings, assemble, combine_status, score_field, score_metric
from .entity import Entities, extract
from .intent import Intent, IntentResult, classify
from .orchestrator import Agent, Context, make_agent
from .planner import PlanStep, plan

__all__ = [
    "Agent",
    "Context",
    "Entities",
    "Findings",
    "Intent",
    "IntentResult",
    "PlanStep",
    "assemble",
    "classify",
    "combine_status",
    "extract",
    "make_agent",
    "plan",
    "score_field",
    "score_metric",
]

"""对话编排（W-114/W-115/W-103）——规则优先、LLM 兜底（ADR-0004）。

    from dip_agent import make_agent
    agent = make_agent("http://127.0.0.1:18080")
    answer = agent.ask("ads.ads_产销存月报 的产量怎么来的？")
    print(answer.text, answer.result.status, len(answer.result.evidence))
"""

from .agent import Agent, Context, Entities, Findings, Intent, IntentResult, PlanStep, make_agent

__all__ = [
    "Agent",
    "Context",
    "Entities",
    "Findings",
    "Intent",
    "IntentResult",
    "PlanStep",
    "make_agent",
]

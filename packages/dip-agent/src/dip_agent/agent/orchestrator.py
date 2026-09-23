"""编排入口：一句话 → 带凭证的答案（B2 的"心脏"）。

流水线（《B2 接口设计与评审》§2）：

    提问 → 实体识别 → 意图判定 → 工具计划 → 执行 → 证据装配 → 结果模型 → 答案

约束：事实全部来自内核；无 LLM key 时走规则模式，结论与证据不变（ADR-0004）。
会话上下文目前放在进程内存（P1 的持久化是 W-112）；这样"它/这个字段"能接着上一轮问。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dip_contracts import Answer, KernelToolkit, Status, ToolCall

from . import answer as answering
from .assemble import assemble
from .entity import Entities, extract, pick_topic
from .intent import classify
from .planner import PlanStep, plan


@dataclass
class Context:
    """一轮之内的会话上下文（用于"它/这个字段"类追问）。"""

    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    topic: str | None = None


class Agent:
    def __init__(self, client: KernelToolkit, *, depth: int = 3) -> None:
        self.client = client
        self.depth = depth
        self._contexts: dict[str, Context] = {}
        self._version: str | None = None

    # ---------------- 内部 ----------------
    def _kb_version(self) -> str | None:
        if self._version is None:
            s = self.client.kb_summary()
            if s.ok:
                self._version = f"kb:{s.data.get('schema_version')}@{(s.data.get('built_at') or '').split(' ')[0]}"
        return self._version

    @staticmethod
    def _tool_calls(results: list[tuple[PlanStep, object]]) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for step, res in results:
            calls.append(
                ToolCall(
                    name=step.tool,
                    args=step.args,
                    ms=getattr(res, "ms", 0),
                    ok=bool(getattr(res, "ok", False)),
                    endpoint=getattr(res, "endpoint", None),
                    error=getattr(res, "error", None),
                )
            )
        return calls

    def _execute(self, steps: list[PlanStep]) -> list[tuple[PlanStep, object]]:
        results: list[tuple[PlanStep, object]] = []
        for step in steps:
            method = getattr(self.client, step.tool, None)
            if method is None:  # 计划里出现了客户端没有的方法 → 记录而不是崩
                results.append((step, _MissingTool(step.tool)))
                continue
            results.append((step, method(**step.args)))
        return results

    # ---------------- 对外 ----------------
    def ask(self, text: str, *, session_id: str = "default", mode: str = "rule") -> Answer:
        ctx = self._contexts.setdefault(session_id, Context())
        entities = extract(text, context={"tables": ctx.tables, "columns": ctx.columns})

        # 闸门一：连表/字段/SQL 都没有 → 直接反问，不调工具（P1 不做全库模糊搜索）
        if not (entities.tables or entities.columns or entities.sql):
            return answering.clarifier(
                "还差一个定位：请给「表名」或「表.字段」，例如：ads.ads_产销存月报 的产量怎么来的？",
                reason="no_entity",
            )

        # 意图判定：关键词规则为主；只有当规则给不出信号时才去问内核（省一次调用）
        intent_res = classify(text, entities)
        if not intent_res.intents:
            kernel = self.client.ask(text)
            intent_res = classify(text, entities, kernel_intent=(kernel.data.get("intent") if kernel.ok else None))

        if entities.ambiguity:
            return answering.clarifier(
                "；".join(entities.ambiguity) + "。请指明一张表，我再查。",
                reason="ambiguous_entity",
            )

        steps = plan(intent_res, entities, depth=self.depth)
        results = self._execute(steps)
        calls = self._tool_calls(results)

        if not any(getattr(r, "ok", False) for _, r in results):
            errs = "；".join(f"{c.name}: {c.error}" for c in calls if c.error) or "没有可用的工具结果"
            return answering.clarifier(
                f"内核调用没有成功（{errs}）。我不编结论——请确认内核服务可用后重试。",
                reason="kernel_failed",
                tool_calls=calls,
            )

        topic = pick_topic(entities) or (ctx.topic if entities.from_context else None)
        findings = assemble(results, entities, intents=[i.value for i in intent_res.intents], topic=topic)
        if not findings.evidence:
            return answering.clarifier(
                f"没查到与「{topic or text[:12]}」对得上的口径或血缘（内核返回的检索命中与提问实体不匹配）。"
                "换一种说法，或先确认表名 / 字段名。",
                reason="no_matching_evidence",
                tool_calls=calls,
            )
        ans = answering.compose(
            findings,
            intents=[i.value for i in intent_res.intents],
            tool_calls=calls,
            mode=mode,
            version=self._kb_version(),
        )

        # 更新上下文（供下一轮追问）
        if entities.tables:
            ctx.tables = entities.tables
        if entities.columns:
            ctx.columns = entities.columns
        if findings.topic:
            ctx.topic = findings.topic
        return ans


class _MissingTool:
    """计划里出现客户端没有的方法——当作一次失败调用处理，便于审计发现。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.ok = False
        self.ms = 0
        self.endpoint = None
        self.error = f"客户端未实现工具 {name}"
        self.data: dict = {}


def make_agent(client: KernelToolkit, *, depth: int = 3) -> Agent:
    """工厂只做装配：客户端由调用方（portal-api 或脚本）注入，本包不依赖具体适配器。"""
    return Agent(client, depth=depth)


__all__ = ["Agent", "Context", "Entities", "Status", "make_agent", "pick_topic"]

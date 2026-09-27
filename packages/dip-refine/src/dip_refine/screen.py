"""LLM 初筛：把解析结果喂给模型，要它给出「这个脚本里值得留下的口径」候选草稿。

工作项 M3-05 / Issue #16。三条硬规矩：

1. **只走平台的模型网关**（`apps/model-gateway`），不直接连模型厂商 —— 换模型只改配置，
   调用记账也在网关那边（M1-04 定的薄层）。
2. **模型不许编**：提示词里给出白名单（真实存在的表 / 字段），并要求**只输出 JSON**；
   产出之后还要过 `validate.py` 那一道（`dip_contracts.guards` 出口事实校验）。
   这里不做"看起来像就行"的容忍 —— 编造的表名字段名会在校验环节逐条列出来。
3. **拿不到模型就不出报告**：这一步失败就让整条流水线失败（异常往上抛），
   **不许**用规则凑一份"看起来完整"的报告 —— 那是伪造结论（AGENTS 铁律 1）。

模型返回的解析**只认 JSON**：先剥掉可能的 ```json 围栏，再 `json.loads`；
解析不了就是失败，不做"正则抠字段"的猜测。
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

import httpx
from dip_contracts.knowledge import CandidateDraft, FieldRef

from .parse import ParsedScript, ParsedStatement

#: 未入库的标记：抽检合格后由**人**提交候选，届时 submitted_by 换成真人
PIPELINE_SUBMITTER = "提炼流水线（未入库，待人工抽检）"

SYSTEM_PROMPT = """你是数据平台的知识提炼助手。任务：从给定的 SQL 脚本片段里，找出**值得写成正式口径**的派生指标。

硬性要求（违反即作废）：
1. 只能使用我给出的表名与字段名清单 —— **不许出现清单之外的任何表名、字段名、数字**。
2. 只输出 JSON，不要解释、不要 Markdown 代码围栏之外的文字。
3. 判断不了就少给候选，不要凑数。宁缺勿错。

输出格式：
{"candidates": [
  {"subject": "库.表.字段",
   "chinese_name": "中文名或 null",
   "formula": "一句话公式，例如 产量 = 打码量 + 跳码量 - 重码量",
   "depends_on": [{"table": "库.表", "column": "字段"}],
   "confidence": 0.0-1.0,
   "reason": "为什么值得留下（只能引用上面给的事实，不要编）"}
]}

什么值得写成口径：对指标做了计算/聚合（SUM/CASE/ROUND/NULLIF…）、有业务含义的中文名、
能被下游复用的派生字段。单纯的字段搬运（a.col AS col）不算。
"""


@runtime_checkable
class Screener(Protocol):
    """只需要一个方法：把提示词交给模型，拿回文本。"""

    def complete(self, *, system: str, user: str) -> str: ...


class GatewayLlm:
    """经模型网关（OpenAI 兼容）调用模型的实现。

    `base_url` 指向网关（默认 :18200），**不指向模型厂商** —— 这是 M1-04 的分工。
    """

    def __init__(self, base_url: str, *, model: str, api_key: str | None = None,
                 timeout: float = 60.0, max_attempts: int = 2) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)

    def complete(self, *, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
        }
        last_error: Exception | None = None
        for _ in range(self.max_attempts):
            try:
                # trust_env=False：本机调用别被系统代理带崩（AGENTS §8 的坑）
                with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                    resp = client.post(f"{self.base_url}/v1/chat/completions", json=payload, headers=headers)
                if resp.status_code >= 400:
                    last_error = RuntimeError(f"模型网关返回 HTTP {resp.status_code}：{resp.text[:300]}")
                    continue
                body = resp.json()
                return str(body["choices"][0]["message"]["content"])
            except Exception as exc:  # noqa: BLE001 - 重试后再决定是否抛出
                last_error = exc
        raise RuntimeError(f"模型调用失败（{self.max_attempts} 次）：{last_error}")


def build_prompt(script: ParsedScript, statement: ParsedStatement, whitelist: dict[str, set[str]]) -> str:
    """给一条语句构造提示词：事实（白名单 + 列血缘）在前，要求在后。"""
    lines = [
        f"脚本：{script.source_script}",
        f"语句序号：{statement.index}（task_type={statement.task_type or '未知'}）",
        f"输入表：{', '.join(statement.input_tables) or '（无）'}",
        f"输出表：{', '.join(statement.output_tables) or '（无）'}",
        "",
        "这条语句的字段计算（目标字段 ← 表达式 ← 来源表）：",
    ]
    if statement.columns:
        lines += [f"  - {col}  ←  {expr}   （来源 {table}）"
                  for col, expr, table in statement.columns]
    else:
        lines.append("  （内核未给出列血缘）")
    lines += [
        "",
        f"可用表名（只能用这些）：{', '.join(sorted(whitelist['tables']))}",
        f"可用字段名（只能用这些）：{', '.join(sorted(whitelist['fields']))}",
        "",
        "SQL 原文：",
        "```sql",
        statement.sql.strip()[:4000],
        "```",
        "",
        "请按约定的 JSON 输出候选口径。",
    ]
    return "\n".join(lines)


def parse_llm_json(text: str) -> list[dict[str, Any]]:
    """把模型返回的文本解析成候选字典列表。**解析不了就抛**，不做正则猜测。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1] if cleaned.count("```") >= 2 else cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"模型没有给出 JSON：{text[:200]!r}")
    payload = json.loads(cleaned[start:end + 1])
    items = payload.get("candidates")
    if not isinstance(items, list):
        raise ValueError("JSON 里没有 candidates 数组")
    return [item for item in items if isinstance(item, dict)]


def draft_of(item: dict[str, Any], script: ParsedScript, statement: ParsedStatement) -> CandidateDraft:
    """把模型给的一条候选变成契约层的草稿（来源与语句序号由我们填，**不听模型的**）。"""
    deps = []
    for dep in item.get("depends_on") or []:
        if isinstance(dep, dict):
            deps.append(FieldRef(table=str(dep.get("table") or ""), column=str(dep.get("column") or "")))
    return CandidateDraft(
        kind="metric",
        subject=str(item.get("subject") or ""),
        chinese_name=(str(item["chinese_name"]) if item.get("chinese_name") else None),
        formula=str(item.get("formula") or ""),
        depends_on=deps,
        source_script=script.source_script,
        source_line=statement.index,
        note=str(item.get("reason") or ""),
        submitted_by=PIPELINE_SUBMITTER,
    )


def screen_statement(
    script: ParsedScript,
    statement: ParsedStatement,
    *,
    screener: Screener,
    whitelist: dict[str, set[str]],
) -> tuple[list[CandidateDraft], list[float], str]:
    """初筛一条语句，返回 (候选草稿, 各自置信度, 模型原始文本)。

    置信度单独返回：它不在契约里（契约只管"这份口径写清楚了没有"），是初筛环节的自评，
    报告里要显示、抽检时要看 —— 但**不进候选**。
    """
    raw = screener.complete(system=SYSTEM_PROMPT, user=build_prompt(script, statement, whitelist))
    items = parse_llm_json(raw)
    drafts: list[CandidateDraft] = []
    confidences: list[float] = []
    for item in items:
        drafts.append(draft_of(item, script, statement))
        try:
            confidences.append(float(item.get("confidence", 0.0)))
        except (TypeError, ValueError):
            confidences.append(0.0)
    return drafts, confidences, raw

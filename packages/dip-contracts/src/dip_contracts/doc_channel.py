"""文档通道的契约与「仅背景」约束（工作项 M3-04 / Issue #15）。

一句话规矩：**结构化通道是唯一能产出结论的通道；文档通道（WeKnora）只能当背景说明**。
本模块把这句口号变成四条可测的规则（靠代码，不靠提示词、也不靠自觉）：

1. **引用必须指得到出处**：每条文档材料都要有文档名 + 切片 id，缺了就是不合格（验收②）。
   注意是"不合格"而**不是"过滤掉"** —— 静默丢弃会让"文档引用标注了来源"变成
   "看起来标注了来源"，两者在界面上一模一样，但一个能追、一个不能（AGENTS §3 铁律 1）。
2. **结论只取结构化通道**：把文档材料挂到答案上时，`result`（含 `evidence`、含 `value`）
   必须**一字未变**，文档材料只能进 `background` 字段。
   **尤其不许进 `Result.evidence`** —— 铁律 1 是"无凭证不发布结论"，凭据链里一旦混得进文档，
   文档就能给结论背书，整条约束当场作废。
3. **结论文本里的数字必须来自结构化通道**：挂背景之后，用 #5 的出口校验器
   （`guards.check_answer`）拿结构化通道说过的数字当白名单再扫一遍结论文本，越界即报。
   文档里写"库存增量 = 产量 − 销量，2025 年占 37%"不代表平台可以拿 37% 当结论。
4. **背景材料的渲染固定**：`render_background()` 是唯一渲染入口，标签里**必然**带来源，
   不给人"漏掉引用"的写法留位置（验收②在界面侧也成立）。

三个取舍：

- **违反规则时抛异常，不返回警告**：这是结论链路的闸门，静默降级等于没有闸门
  （AGENTS §3 铁律 1）。需要温和版时有 `check_citations()` / `conclusion_contamination()`
  两个纯函数拿问题清单。
- **不新增通道枚举**：通道只有两个字符串常量；将来换掉 WeKnora 也只改 client（`dip_docs`）。
- **Unicode 感知**：中文文档名、中文切片标题一样要能当引用（沿用 #5 的教训）。
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from .guards import NUMBER_RE, ViolationKind, Whitelist, check_answer, normalize_number
from .models import Answer, Background, DocHit, Result

__all__ = [
    "CHANNEL_DOCUMENTS",
    "CHANNEL_STRUCTURED",
    "CitationMissing",
    "ConclusionContaminated",
    "Problem",
    "attach_background",
    "background_of",
    "check_citations",
    "conclusion_contamination",
    "empty_background",
    "render_background",
    "structured_numbers",
]

CHANNEL_STRUCTURED = "structured"
CHANNEL_DOCUMENTS = "documents"

#: 单条摘录的上限（字符）。文档原文可能很长，报告/界面只放得下摘要；
#: 截断要**显式留痕**（写 `…（已截断）`），不能悄悄砍掉后半句 —— 那会改变语义。
MAX_EXCERPT = 800


class Problem(BaseModel):
    """一条不合格项。形状与 `knowledge.py` 里的 `Problem` 一致：机器看 `code`，人看 `message`。"""

    model_config = ConfigDict(frozen=True)

    code: str
    field: str = ""
    message: str


class CitationMissing(ValueError):
    """文档材料缺来源（验收②）。**不静默丢弃**，直接把问题抛出来。"""

    def __init__(self, problems: Iterable[Problem]) -> None:
        self.problems = tuple(problems)
        detail = "；".join(f"[{p.code}] {p.message}" for p in self.problems) or "引用缺失"
        super().__init__(f"文档材料缺来源，拒绝作为背景使用：{detail}")


class ConclusionContaminated(ValueError):
    """文档通道越界影响了结论（「仅背景」铁律）。"""

    def __init__(self, problems: Iterable[Problem]) -> None:
        self.problems = tuple(problems)
        detail = "；".join(f"[{p.code}] {p.message}" for p in self.problems) or "结论被污染"
        super().__init__(f"文档通道不得影响结论：{detail}")


# ---------------------------------------------------------------- 规则 1：引用


def check_citations(hits: Iterable[DocHit]) -> tuple[Problem, ...]:
    """验收②：每条文档材料都要能指到出处（文档名 + 切片 id）。

    返回空元组表示合格。`url` / `position` 是加分项，不作为硬要求 ——
    不同解析器给得到的东西不一样，硬要链接会把能用的材料逼成不合格。
    """
    problems: list[Problem] = []
    for index, hit in enumerate(hits):
        at = f"hits[{index}]"
        if not hit.citation.document_name:
            problems.append(Problem(
                code="citation_missing_document",
                field=f"{at}.citation.document_name",
                message=f"第 {index + 1} 条文档材料没有文档名，无法追到原文",
            ))
        if not hit.citation.chunk_id:
            problems.append(Problem(
                code="citation_missing_chunk",
                field=f"{at}.citation.chunk_id",
                message=f"第 {index + 1} 条文档材料没有切片 id，无法定位到原文那一段",
            ))
        if not hit.text.strip() and hit.citation.document_name:
            problems.append(Problem(
                code="citation_empty_excerpt",
                field=f"{at}.text",
                message=f"第 {index + 1} 条文档材料只有出处没有正文（空摘录）",
            ))
    return tuple(problems)


def background_of(hits: Iterable[DocHit], *, require_citations: bool = True) -> Background:
    """把检索结果收成「背景材料」。

    `require_citations=True`（默认）时，只要有一条材料说不清出处就抛 `CitationMissing` ——
    这正是验收②想要的行为：不是"尽量标注"，而是"标不出来就别用"。
    """
    material = list(hits)
    if require_citations:
        problems = check_citations(material)
        if problems:
            raise CitationMissing(problems)
    return Background(hits=material)


def empty_background() -> Background:
    """查不到文档时也要有明确形状（空背景），别用 `None` 让调用方各自解释。"""
    return Background(hits=[])


# ---------------------------------------------------------------- 规则 2 & 3：结论


def structured_numbers(answer: Answer) -> set[str]:
    """结构化通道**说过**的数字：它给出的结论文本 + 结论值 + 证据链 + 来源（含脚本行号）。

    拿来当"结论文本里允许出现的数字"白名单。**注意白名单不是从结果里反推的**：
    结构化通道自己说过的话（包括它标出的来源行号 `第 12 行`）都算数；
    只有**新冒出来**的数字才可能是文档带进来的 —— 这正是要抓的东西。

    归一化用 `guards.normalize_number`，与出口校验器同一套口径（`1,234` 与 `1234` 视为同一个数）。
    """
    parts: list[str] = [answer.text or ""]
    value = answer.result.value
    if value is not None:
        parts.extend([value.display or "", value.expr or ""])
    for item in answer.result.evidence:
        parts.extend([item.ref or "", item.summary or ""])
        if item.source is not None:
            parts.extend([item.source.file or "", str(item.source.line or "")])
    source = answer.result.source
    if source is not None:
        parts.extend([source.file or "", str(source.line or "")])
    numbers: set[str] = set()
    for text in parts:
        numbers.update(normalize_number(token) for token in NUMBER_RE.findall(text))
    return numbers


def _evidence_signature(result: Result) -> tuple[tuple[str, str, str], ...]:
    """证据链指纹（类型 + 引用 + 摘要）。用它判断"结论有没有被悄悄动过"。"""
    return tuple((item.type.value, item.ref or "", item.summary or "") for item in result.evidence)


def conclusion_contamination(before: Answer, after: Answer) -> tuple[Problem, ...]:
    """「仅背景」的硬校验：挂了文档材料之后，结论必须一个字都没变，且数字都来自结构化通道。

    两类问题都会报出来：

    | code | 含义 |
    | --- | --- |
    | `result_changed` | `result` 被动过（值/状态/证据链里任何一个）—— 文档材料不许碰结论 |
    | `conclusion_number_not_structured` | 结论文本里出现了结构化通道没说过的数字 |
    """
    problems: list[Problem] = []

    if after.result != before.result:
        changed: list[str] = []
        if after.result.value != before.result.value:
            changed.append("结论值")
        if after.result.status != before.result.status:
            changed.append("status")
        if after.result.confidence != before.result.confidence:
            changed.append("confidence")
        if _evidence_signature(after.result) != _evidence_signature(before.result):
            changed.append("证据链")
        problems.append(Problem(
            code="result_changed",
            field="result",
            message=f"挂文档背景时结论被改动了：{'、'.join(changed) or '未知字段'}；"
                    "文档通道只能进 background",
        ))

    whitelist = Whitelist(numbers=frozenset(structured_numbers(before)))
    verdict = check_answer(after.text or "", whitelist)
    for violation in verdict.violations:
        if violation.kind is not ViolationKind.NUMBER:
            continue  # 表名/字段名的越界由 #5 的出口校验管，这里只管"数字不许从文档来"
        problems.append(Problem(
            code="conclusion_number_not_structured",
            field="text",
            message=f"结论文本里的数字 {violation.token} 不是结构化通道给的"
                    "（文档里的数字不许当结论，见 M3-04「仅背景」）",
        ))
    return tuple(problems)


def attach_background(
    answer: Answer,
    hits: Iterable[DocHit] | Background | None,
    *,
    compose: str | None = None,
    strict: bool = True,
) -> Answer:
    """把文档通道的材料挂到答案上。**只加 `background`，`result` 一字不动**。

    `compose`：如果要**另写一段最终结论文字**（比如让模型看过文档后重写一遍），就传它 ——
    那段文字会被逐条校验"里面的数字是不是都来自结构化通道"。不传则沿用结构化通道的原话。

    `strict=True`（默认）时，一旦发现结论被动过、或有文档数字混进了结论文本，直接抛
    `ConclusionContaminated` —— 宁可这次不给答案，也不给一个"看起来有据"的错答案。
    想拿问题清单而不是异常时：`strict=False`，再 `conclusion_contamination(原答案, 结果)`。
    """
    if hits is None:
        material = empty_background()
    elif isinstance(hits, Background):
        material = hits
    else:
        material = background_of(hits)

    update: dict[str, object] = {"background": material}
    if compose is not None:
        update["text"] = compose
    merged = answer.model_copy(update=update)

    if strict:
        problems = conclusion_contamination(answer, merged)
        if problems:
            raise ConclusionContaminated(problems)
    return merged


# ---------------------------------------------------------------- 规则 4：渲染用文字


def _excerpt(text: str, limit: int = MAX_EXCERPT) -> str:
    body = " ".join((text or "").split())
    if len(body) <= limit:
        return body
    return body[:limit] + "…（已截断）"


def render_background(background: Background | None) -> str:
    """把背景材料渲染成给人看的文字。**每条都带来源标签**（验收②在界面这条路上也成立）。

    返回空字符串表示"没有文档材料" —— 调用方据此不加这一段，而不是加一段"（无）"。
    """
    if background is None or not background.hits:
        return ""
    lines = ["背景（来自文档通道，仅作参考，不作为结论）："]
    for index, hit in enumerate(background.hits, start=1):
        score = f"，相关度 {hit.score:.2f}" if hit.score is not None else ""
        lines.append(f"{index}. [{hit.citation.label}{score}] {_excerpt(hit.text)}")
    return "\n".join(lines)

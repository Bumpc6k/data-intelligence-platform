"""知识候选的契约与校验（工作项 M3-01 / Issue #12）。

口径回写不许直接改知识库，只能走「候选 → 责任人审核 → 入库」，所以候选**必须带齐**三件东西
（《双人分工与 Windows 数据开发约定》§3.3）：**来源脚本路径 + 公式 + 依赖字段**。
本模块就是那三件的契约与校验，纯函数、纯 pydantic，不碰数据库、不碰 HTTP ——
将来 M3-05 的提炼流水线要产出候选，也走这里，避免"流水线自己定一套格式"。

**为什么校验放在契约层而不是接口层**：候选有两个产生入口（人提交、流水线产出），
还有第三个更危险的入口（绕过接口直接写库）。前两个靠这里把话说清楚（逐条 `Problem` + 错误码），
第三个靠 `dip_pg` 里的 `CHECK` 约束兜住 —— 两层，缺一层都不行。

**为什么捕获不了问题也不抛异常**：验收要求"候选被拒时说明原因（缺来源/格式非法）"。
抛异常只能给一个字符串，拿不到"哪几条不对"；这里返回 `list[Problem]`，每条带
`code`（给机器）/`field`（给定位）/`message`（给人看）。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- 常量

#: 本轮只落地「口径」这一类。term / rule 的通道等各自的 Issue —— 但**显式拒绝**，
#: 而不是静默当成 metric 处理（认不出的类型必须报错，AGENTS §3 铁律 1）。
SUPPORTED_KINDS: tuple[str, ...] = ("metric",)
KNOWN_KINDS: tuple[str, ...] = ("metric", "term", "rule")
#: 提交意图（M3-03 / #14）：`new` = 新口径；`replace` = 替换现有生效口径（冲突清单留痕）
KNOWN_INTENTS: tuple[str, ...] = ("new", "replace")

#: 口径等级（ADR-0006 / Issue #38）：**人的判断**，不是系统推断。
#: `p0` 生产报表/对外口径（最严）；`p1` 默认；`p2` 只影响临时分析、无下游引用（免仲裁）。
KNOWN_TIERS: tuple[str, ...] = ("p0", "p1", "p2")
#: **默认从严**：想免仲裁必须有人明确标 `p2` 并写明依据。
DEFAULT_TIER: str = "p1"
#: 免仲裁的那一档（只放宽"冲突拦截"，不放松任何质量门禁）
FREE_ARBITRATION_TIER: str = "p2"

#: 来源脚本必须是仓库里的脚本文件（内核的 `source_file` 就是这种形状：
#: `examples/warehouse/ads/ads_产销存月报.sql`）。挡掉"来源：我脑子里"这类填法。
SOURCE_EXTENSIONS: tuple[str, ...] = (".sql", ".py", ".yaml", ".yml")

#: Unicode 感知的标识符：本仓库的表名/字段名含中文（`ads.ads_产销存月报.output_qty`）。
#: 用 ASCII 正则会**静默**把中文标识符判成非法（AGENTS §8 的坑表第 1 条），所以这里显式带上 CJK 区段。
IDENT_RE = re.compile(r"^[\w.\u4e00-\u9fff]+$", re.UNICODE)

#: 公式归一化用（见 `normalize_formula`）
_WS_RE = re.compile(r"\s+")

MAX_SUBJECT = 200
MAX_FORMULA = 1000
MAX_SCRIPT = 400
MAX_NAME = 200

# ---------------------------------------------------------------- 数据形状


class FieldRef(BaseModel):
    """一个依赖字段。形状对齐内核 `/kb/metric` 的 `depends_on` 元素（table + column）。"""

    table: str = ""
    column: str = ""


class CandidateDraft(BaseModel):
    """一份待审的候选（口径）。

    **刻意不做"模型级校验"**：字段全部宽松（`str | None`、空列表），问题由
    `validate_draft()` 逐条收集。这样一次提交能拿到**所有**毛病，而不是改一条报一条。
    """

    kind: str = "metric"
    #: 口径主体：`表.字段/指标`，例如 `ads.ads_产销存月报.output_qty`
    subject: str = ""
    chinese_name: str | None = None
    #: 公式，例如 `产量 = 打码量 + 跳码量 - 重码量`
    formula: str | None = None
    depends_on: list[FieldRef] = Field(default_factory=list)
    #: 来源脚本路径（入库的硬门槛）
    source_script: str | None = None
    #: 脚本内的语句序号 / 行号（内核叫 `source_stmt`，可缺）
    source_line: int | None = None
    #: 提交意图（M3-03 / #14）：`new` = 新口径（与现有口径冲突就要先仲裁）；
    #: `replace` = 人明说"这是替换现有生效口径"（冲突清单仍会留痕，但要有人认账）
    intent: str = "new"
    #: 口径等级（ADR-0006 / #38）：`p0 | p1 | p2`，默认 `p1`。
    #: `p2` = 免仲裁档，必须同时给 `tier_reason` 与 `tier_set_by`（谁说的、凭什么）。
    tier: str = DEFAULT_TIER
    #: 标 `p2` 的依据（人写的，不是系统算的）
    tier_reason: str = ""
    #: 谁定的等级（审计要的是人）
    tier_set_by: str = ""
    note: str = ""
    submitted_by: str = ""


class Problem(BaseModel):
    """一条被拒的原因。`code` 给机器（测试与前端判分支），`message` 给人看。"""

    code: str
    field: str
    message: str


# ---------------------------------------------------------------- 校验


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _check_ident(value: str, field: str, *, label: str, max_len: int) -> list[Problem]:
    if _blank(value):
        return [Problem(code=f"missing_{field}", field=field, message=f"缺{label}")]
    if len(value) > max_len:
        return [
            Problem(
                code=f"invalid_{field}",
                field=field,
                message=f"{label}过长（{len(value)} > {max_len}）",
            )
        ]
    if not IDENT_RE.match(value):
        return [
            Problem(
                code=f"invalid_{field}",
                field=field,
                message=f"{label}格式非法（只允许字母/数字/下划线/点/中文）：{value!r}",
            )
        ]
    return []


def validate_draft(draft: CandidateDraft) -> list[Problem]:
    """逐条给出候选的问题；空列表 = 可以受理。

    规则（每条都可测，不搞模糊判断）：

    | 项 | 规则 | 为什么 |
    | --- | --- | --- |
    | `kind` | 必须是 `metric` | term/rule 通道还没有，认不出就必须报错 |
    | `subject` | 必填、Unicode 标识符、≤200 | 口径要指到「哪个表的哪个字段/指标」 |
    | `formula` | 必填、≤1000、无换行、无分号 | 一条候选只表达一条公式，分号说明塞了多条 SQL |
    | `depends_on` | 至少 1 条，每条 table/column 合法 | 口径的价值在于能追到它的输入 |
    | `source_script` | 必填、无空白、扩展名在白名单内 | **质量门禁：没有来源脚本的口径不许入库** |
    | `source_line` | 可缺；给了就必须 ≥1 | 行号是从 1 数起的 |
    | `submitted_by` | 必填 | 候选要有人负责 |
    """
    problems: list[Problem] = []

    # 等级（ADR-0006 / #38）：认不出的等级必须报错；标 p2（免仲裁）**必须**有依据和定级人
    if draft.tier not in KNOWN_TIERS:
        problems.append(
            Problem(
                code="invalid_tier",
                field="tier",
                message=f"认不出的口径等级：{draft.tier!r}（可选 {'/'.join(KNOWN_TIERS)}）",
            )
        )
    elif draft.tier == FREE_ARBITRATION_TIER:
        if _blank(draft.tier_reason):
            problems.append(
                Problem(
                    code="missing_tier_reason",
                    field="tier_reason",
                    message=f"标 {FREE_ARBITRATION_TIER}（免仲裁）必须写明依据：凭什么说它低风险",
                )
            )
        if _blank(draft.tier_set_by):
            problems.append(
                Problem(
                    code="missing_tier_setter",
                    field="tier_set_by",
                    message=f"标 {FREE_ARBITRATION_TIER}（免仲裁）必须写明谁定的等级（审计要的是人）",
                )
            )

    if draft.intent not in KNOWN_INTENTS:
        problems.append(
            Problem(
                code="invalid_intent",
                field="intent",
                message=f"认不出的提交意图：{draft.intent!r}（可选 {'/'.join(KNOWN_INTENTS)}）",
            )
        )

    if draft.kind not in KNOWN_KINDS:
        problems.append(
            Problem(code="unknown_kind", field="kind", message=f"认不出的类型：{draft.kind!r}")
        )
    elif draft.kind not in SUPPORTED_KINDS:
        problems.append(
            Problem(
                code="unsupported_kind",
                field="kind",
                message=f"{draft.kind!r} 通道本轮未实现（已支持：{'/'.join(SUPPORTED_KINDS)}）",
            )
        )

    problems += _check_ident(draft.subject, "subject", label="口径主体", max_len=MAX_SUBJECT)

    if _blank(draft.formula):
        problems.append(Problem(code="missing_formula", field="formula", message="缺公式"))
    else:
        formula = draft.formula or ""
        if len(formula) > MAX_FORMULA:
            problems.append(
                Problem(code="invalid_formula", field="formula", message=f"公式过长（> {MAX_FORMULA}）")
            )
        elif "\n" in formula or "\r" in formula:
            problems.append(
                Problem(code="invalid_formula", field="formula", message="公式里不许有换行（一条候选一条公式）")
            )
        elif ";" in formula:
            problems.append(
                Problem(
                    code="invalid_formula",
                    field="formula",
                    message="公式里出现分号：一条候选只表达一条公式，多条请拆开提",
                )
            )

    real_deps = [d for d in draft.depends_on if not (_blank(d.table) and _blank(d.column))]
    if not real_deps:
        problems.append(
            Problem(code="missing_depends_on", field="depends_on", message="缺依赖字段（至少 1 条）")
        )
    else:
        for i, dep in enumerate(real_deps):
            for field, label in (("table", "依赖表"), ("column", "依赖字段")):
                value = getattr(dep, field)
                if _blank(value):
                    problems.append(
                        Problem(
                            code="invalid_depends_on",
                            field=f"depends_on[{i}].{field}",
                            message=f"第 {i + 1} 条依赖缺{label}",
                        )
                    )
                elif not IDENT_RE.match(value):
                    problems.append(
                        Problem(
                            code="invalid_depends_on",
                            field=f"depends_on[{i}].{field}",
                            message=f"第 {i + 1} 条依赖的{label}格式非法：{value!r}",
                        )
                    )

    script = draft.source_script
    if _blank(script):
        problems.append(
            Problem(
                code="missing_source_script",
                field="source_script",
                message="缺来源脚本：没有来源脚本的口径不许入库（质量门禁）",
            )
        )
    else:
        script = (script or "").strip()
        if len(script) > MAX_SCRIPT:
            problems.append(
                Problem(code="invalid_source_script", field="source_script", message="来源脚本路径过长")
            )
        elif any(ch.isspace() for ch in script):
            problems.append(
                Problem(
                    code="invalid_source_script",
                    field="source_script",
                    message="来源脚本路径里有空白字符（不能是路径以外的说明文字）",
                )
            )
        elif not script.lower().endswith(SOURCE_EXTENSIONS):
            problems.append(
                Problem(
                    code="invalid_source_script",
                    field="source_script",
                    message=f"来源脚本必须是脚本文件（{'/'.join(SOURCE_EXTENSIONS)}）：{script!r}",
                )
            )

    if draft.source_line is not None and draft.source_line < 1:
        problems.append(
            Problem(
                code="invalid_source_line",
                field="source_line",
                message=f"来源行号必须 ≥ 1，实际 {draft.source_line}",
            )
        )

    if _blank(draft.submitted_by):
        problems.append(Problem(code="missing_submitter", field="submitted_by", message="缺提交人"))

    return problems


def draft_from_row(row: dict[str, Any]) -> CandidateDraft:
    """把库里的候选行还原成草稿 —— **入库前再校验一次**用的。

    为什么入库要再验一遍：候选从提交到入库中间隔着一次人工审核，中间可能有人直接改过库
    （本项目明确要防的就是"绕过接口直接写库"）。哪怕库层 `CHECK` 已经拦了一道，
    这里再给一次"逐条原因"，用户看到的是"为什么进不去"，而不是一句数据库报错。
    """
    deps = row.get("depends_on") or []
    return CandidateDraft(
        kind=row.get("kind") or "metric",
        subject=row.get("subject") or "",
        chinese_name=row.get("chinese_name"),
        formula=row.get("formula"),
        depends_on=[FieldRef(table=d.get("table", ""), column=d.get("column", "")) for d in deps],
        source_script=row.get("source_script"),
        source_line=row.get("source_line"),
        intent=row.get("intent") or "new",
        tier=row.get("tier") or DEFAULT_TIER,
        tier_reason=row.get("tier_reason") or "",
        tier_set_by=row.get("tier_set_by") or "",
        note=row.get("note") or "",
        submitted_by=row.get("submitted_by") or "",
    )


def problems_as_dicts(problems: list[Problem]) -> list[dict[str, str]]:
    return [p.model_dump() for p in problems]


# ---------------------------------------------------------------- 来源精度（ADR-0003）


class SourceRef(BaseModel):
    """一条口径的来源。

    ADR-0003 定的规矩：设计要求"精确到文件 + 行号"，但内核只给 `source_script`（没有行号）。
    所以平台侧**有行号就说行号，没有行号就明说"文件级"** —— 不许把"文件级"含糊成"有来源"。
    """

    script: str | None = None
    line: int | None = None
    precision: str = "none"      # line / file / none
    label: str = ""


def source_ref(source_script: str | None, source_line: int | None) -> SourceRef:
    """按来源脚本与行号给出**可展示**的来源，并标清精度。"""
    script = (source_script or "").strip() or None
    if script is None:
        return SourceRef(precision="none", label="无来源（不许入库）")
    if source_line is not None and source_line >= 1:
        return SourceRef(script=script, line=source_line, precision="line",
                         label=f"{script} 第 {source_line} 条语句")
    return SourceRef(script=script, precision="file", label=f"{script}（文件级：行号待补）")


# ---------------------------------------------------------------- 公式冲突（M3-03 / #14）

#: 冲突错误码
CONFLICT_WITH_ACTIVE = "conflict_with_active_metric"
CONFLICT_WITH_PENDING = "conflict_with_pending_candidate"


def normalize_formula(formula: str | None) -> str:
    """公式归一化：去掉所有空白再比。

    `产量 = A + B` 与 `产量=A+B` 是同一条公式，不该被判成"冲突"——
    否则同一份口径换个空格写法就要走一遍仲裁，纯属折腾人。
    **不做**更多等价判断（不解析表达式、不判 `A+B` 与 `B+A` 是否相同）：
    那属于"自动仲裁"，Issue #14 明文不做。
    """
    return _WS_RE.sub("", (formula or "").strip())


def _as_text(value: Any) -> Any:
    """把 psycopg 返回的 `datetime` 等类型转成 JSON 可写的形式。

    冲突项会被**存进候选行的 `conflicts` jsonb 列**（留痕），所以这里必须保证可直接 `json.dumps`。
    """
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else value


def conflict_entry(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    """把库里的一行整理成"冲突项"（展示与留痕都用这个形状）。"""
    entry: dict[str, Any] = {
        "kind": kind,                       # metric（生效口径）| candidate（未决候选）
        "id": row.get("id"),
        "subject": row.get("subject"),
        "formula": row.get("formula"),
        "source_script": row.get("source_script"),
        "source_line": row.get("source_line"),
        "status": row.get("status"),
    }
    if kind == "metric":
        entry["version"] = row.get("version")
        entry["who"] = row.get("approved_by")
        entry["at"] = _as_text(row.get("created_at"))
    else:
        entry["who"] = row.get("submitted_by")
        entry["at"] = _as_text(row.get("submitted_at"))
        entry["intent"] = row.get("intent")
    return entry


def detect_conflicts(
    formula: str | None,
    *,
    active_rows: list[dict[str, Any]],
    pending_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """列出与这条公式冲突的现存记录：`{"active": [...], "pending": [...]}`。

    只有**公式归一化后不同**才算冲突 —— 同一条公式重复提交不是冲突（去重留给审核人判断价值）。
    """
    mine = normalize_formula(formula)
    if not mine:
        return {"active": [], "pending": []}
    active = [conflict_entry("metric", r) for r in active_rows if normalize_formula(r.get("formula")) != mine]
    pending = [conflict_entry("candidate", r) for r in pending_rows if normalize_formula(r.get("formula")) != mine]
    return {"active": active, "pending": pending}


def blocking_conflicts(
    intent: str | None,
    conflicts: dict[str, list[dict[str, Any]]],
    tier: str | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    """按候选声明的 `intent` 与**等级**判断"该不该拦"，返回 (错误码, 冲突项)。

    - 与**未决候选**冲突：一律拦 —— 两条竞争候选同时待审，就是"没择一"。
    - 与**生效口径**冲突：只有 `intent='replace'`（人明说"这是替换"）才放行。
    - **`tier='p2'`（免仲裁档，ADR-0006 / #38）**：上面两条都不再拦 ——
      但仍由调用方把冲突清单记在候选行上（不拦 ≠ 不记）。低等级口径的风险由定级人背书。
    """
    if (tier or DEFAULT_TIER) == FREE_ARBITRATION_TIER:
        return None, []
    if conflicts["pending"]:
        return CONFLICT_WITH_PENDING, conflicts["pending"]
    if conflicts["active"] and (intent or "new") != "replace":
        return CONFLICT_WITH_ACTIVE, conflicts["active"]
    return None, []


def has_conflict(*rows: dict[str, Any]) -> bool:
    """一组记录里是否存在"归一化后不止一种公式"（给冲突全景用）。"""
    formulas = {normalize_formula(r.get("formula")) for r in rows if normalize_formula(r.get("formula"))}
    return len(formulas) > 1

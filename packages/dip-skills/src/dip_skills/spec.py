"""skill 契约声明与校验器（工作项 M1-02 / Issue #1）。

依据《架构讨论：Harness + 插件化》§3「Skill 契约（最小字段集）」的 **9 类字段**：

    name+version / when / input_schema+output_schema / side_effect / auth_scope
    / idempotent+timeout+retry / evidence_kind / renderer / budget

本模块**只做两件事**：把 YAML 声明解析成强类型对象、把不合法的声明拒掉。它**不做**
skill 运行时、不做 MCP 服务（那是 M1-03），也不引入任何重量级依赖。

范围说明（这三条是刻意的取舍，不是遗漏）：

1. `input_schema` / `output_schema` 只校验"非空映射"。§3 给的例子（`{sql, dialect, depth}`）
   本身并不是一份完整 JSON Schema，所以这里在声明期做结构校验会误伤合法声明；真正的
   入参校验发生在调用期（M1-03）。
2. §3 只对 `side_effect` / `evidence_kind` / `renderer` 给了明确的取值集合，其余字段只说
   了语义。凡 §3 未规定的**数值边界**（长度、取值范围、超时上下限），本实现自定并集中
   在下方的常量区，便于评审时逐条确认。
3. 版本兼容按"声明版本不得高于本实现支持的上限"判定（见 `MAX_SUPPORTED_VERSION`）。
"""

from __future__ import annotations

import enum
import pathlib
from typing import Any

import pydantic
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "BUDGET_MAX_CALLS",
    "CONTRACT_FIELDS",
    "CONTRACT_VERSION",
    "FIELD_GROUPS",
    "MAX_SUPPORTED_VERSION",
    "SkillSpec",
    "SkillSpecError",
    "load_skill_spec",
    "validate_skill_spec",
]

# ── 契约版本与兼容性 ────────────────────────────────────────────────────────
CONTRACT_VERSION = 1
MAX_SUPPORTED_VERSION = 1  # 声明版本高于此值 = 声明了本实现还不认识的契约 → 拒绝

# ── 字段取值集合（§3 明确给出的枚举）────────────────────────────────────────


class SideEffect(str, enum.Enum):
    """副作用的种类 —— 它决定防火墙要求的最低档位（§3）。"""

    NONE = "none"
    READ = "read"
    WRITE = "write"
    DDL = "ddl"
    DML = "dml"


class EvidenceKind(str, enum.Enum):
    """返回值能作为哪一类证据（出口事实校验要按它查回执白名单）。"""

    LINEAGE = "lineage"
    METRIC = "metric"
    REPORT = "report"
    PLAN = "plan"


class Renderer(str, enum.Enum):
    """结果用哪个视图渲染（前端按名注册组件，M4-01）。"""

    TABLE = "table"
    GRAPH = "graph"
    SQL = "sql"
    DIFF = "diff"


class Retry(str, enum.Enum):
    """重试语义。§3 的例子是"仅连接类错误重试"，故默认值取 CONNECT_ONLY。

    注意：§3 没有给 `retry` 明确取值集合，这里这两个取值是本实现定的，待评审确认。
    """

    NONE = "none"
    CONNECT_ONLY = "connect_only"


# ── §3 未规定的数值边界（本实现自定，集中在此便于评审）──────────────────────
NAME_PATTERN = r"^[a-z][a-z0-9_.]*$"  # §3 例子：lineage.analyze
NAME_MAX_LENGTH = 64
WHEN_MAX_LENGTH = 500  # "when" 是给决策模型看的描述，过长会挤占上下文
TIMEOUT_MIN_SECONDS = 1
TIMEOUT_MAX_SECONDS = 600
BUDGET_MIN_CALLS = 1
BUDGET_MAX_CALLS = 64  # 防循环的硬上限；§3 的例子是 6

# 9 类字段的分组（照抄 §3 表格的行结构）。测试会拿它和模型字段做双向比对，
# 任何一侧漏字段或凭空多字段都会红 —— 这就是验收第 ③ 条的机器化版本。
FIELD_GROUPS: tuple[tuple[str, ...], ...] = (
    ("name", "version"),  # 1 唯一名 + 版本
    ("when",),  # 2 何时用
    ("input_schema", "output_schema"),  # 3 入参出参 JSON Schema
    ("side_effect",),  # 4 副作用种类
    ("auth_scope",),  # 5 需要的权限点
    ("idempotent", "timeout", "retry"),  # 6 幂等 / 超时 / 重试语义
    ("evidence_kind",),  # 7 证据类别
    ("renderer",),  # 8 渲染视图
    ("budget",),  # 9 单次会话调用上限
)
CONTRACT_FIELDS: tuple[str, ...] = tuple(field for group in FIELD_GROUPS for field in group)


class SkillSpecError(ValueError):
    """声明不合法。消息里逐条列出原因，便于人和 AI 直接照改。"""


class SkillSpec(BaseModel):
    """一个 skill 被 harness 调用前必须声明的全部内容（9 类字段，缺一不可）。"""

    # extra="forbid"：契约之外的字段一律拒绝，防止"声明里写了但没人读"的字段悄悄长出来。
    # frozen=True：契约是只读真相，解析出来之后不该被就地改写。
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH, pattern=NAME_PATTERN)
    version: int = Field(ge=1, le=MAX_SUPPORTED_VERSION)
    when: str = Field(min_length=1, max_length=WHEN_MAX_LENGTH)
    input_schema: dict[str, Any] = Field(min_length=1)
    output_schema: dict[str, Any] = Field(min_length=1)
    side_effect: SideEffect
    auth_scope: list[str] = Field(min_length=1)
    idempotent: bool
    timeout: int = Field(ge=TIMEOUT_MIN_SECONDS, le=TIMEOUT_MAX_SECONDS)
    retry: Retry
    evidence_kind: EvidenceKind
    renderer: Renderer
    budget: int = Field(ge=BUDGET_MIN_CALLS, le=BUDGET_MAX_CALLS)

    @field_validator("auth_scope")
    @classmethod
    def _auth_scope_items_must_be_non_empty(cls, value: list[str]) -> list[str]:
        # 空白的权限点等于没写权限，属于"看起来声明了"的假象，必须拒。
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("每个权限点都必须是非空字符串（如 lineage:read）")
        return value

    @property
    def qualified_name(self) -> str:
        """`name@version` 形式（§3 的例子：`lineage.analyze@1`）。"""
        return f"{self.name}@{self.version}"


# pydantic 错误类型 → 中文原因。没列到的类型退回 pydantic 原文，不吞信息。
_REASON = {
    "missing": "缺少必填字段",
    "extra_forbidden": "契约之外的字段",
    "enum": "取值不在允许集合内",
    "literal_error": "取值不在允许集合内",
    "string_too_long": "文本过长",
    "string_too_short": "文本过短",
    "string_pattern_mismatch": "格式不合法",
    "greater_than_equal": "取值过小",
    "less_than_equal": "取值过大",
    "too_short": "不能为空",
    "too_long": "元素过多",
    "int_type": "应为整数",
    "int_parsing": "应为整数",
    "bool_type": "应为布尔值",
    "bool_parsing": "应为布尔值",
    "dict_type": "应为映射（key: value）",
    "str_type": "应为文本",
    "string_type": "应为文本",
    "list_type": "应为列表",
}


def _short(value: str, limit: int = 40) -> str:
    """错误消息里只展示前 40 字：超长声明的原文不该把消息刷爆。"""
    return value if len(value) <= limit else f"{value[:limit]}…（共 {len(value)} 字）"


def _format_errors(exc: pydantic.ValidationError) -> str:
    lines: list[str] = []
    for err in exc.errors():
        field = ".".join(str(part) for part in err["loc"]) or "(根)"
        reason = _REASON.get(err["type"], err["msg"])
        if err["type"] == "missing":
            lines.append(f"- 缺少必填字段：{field}")
            continue
        if err["type"] == "extra_forbidden":
            lines.append(f"- {field}：契约之外的字段（不在 §3 的 9 类字段内）")
            continue
        got = err.get("input")
        if isinstance(got, str):
            shown = f"，当前 {_short(got)!r}"
        elif isinstance(got, (int, float, bool)):
            shown = f"，当前 {got!r}"
        else:
            shown = ""
        lines.append(f"- {field}：{reason}{shown}（{err['msg']}）")
    return "skill 契约声明不合法：\n" + "\n".join(lines)


def validate_skill_spec(data: Any) -> SkillSpec:
    """把已解析的 YAML 结构（通常是 dict）校验成 `SkillSpec`。

    不合法时抛 `SkillSpecError`，消息里逐条列出字段与原因。
    """
    if not isinstance(data, dict):
        raise SkillSpecError(f"skill 声明必须是映射（key: value），当前是 {type(data).__name__}")
    try:
        return SkillSpec.model_validate(data)
    except pydantic.ValidationError as exc:
        raise SkillSpecError(_format_errors(exc)) from exc


def load_skill_spec(path: str | pathlib.Path) -> SkillSpec:
    """从 YAML 文件读入并校验一个 skill 声明。

    固定用 UTF-8 读（中文 Windows 下默认编码会把中文标识符读成乱码，见 AGENTS.md §8），
    固定用 `yaml.safe_load`（不解析任意 Python 对象）。
    """
    text = pathlib.Path(path).read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SkillSpecError(f"YAML 解析失败：{exc}") from exc
    return validate_skill_spec(data)

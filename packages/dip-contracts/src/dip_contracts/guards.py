"""出口事实校验：只允许用回执里出现过的表名 / 字段名 / 数字（工作项 M1-05 / Issue #5）。

把"模型不许编"从**提示词**变成**机制**：模型输出先过这里，越界即拦并逐条标记。

四条边界（都写进代码与测试，不靠自觉）：

1. **纯函数**：入参只有「模型文本 + 白名单」，不依赖任何模型实现 —— 因此好测，也不需要起服务。
2. **不自建白名单**：白名单是调用方的入参（Issue 的接口就是这么定义的）。本模块只提供
   `normalize_number` 给调用方复用，保证建表与校验两侧口径一致。
3. **不重复实现 status 推导**：本模块**不产出** `Status`。`verified/inferred/candidate` 那套判定
   在 `dip_contracts/status.py`；这里只回答一个问题 —— "这段文本里的表名/字段名/数字，是不是
   都来自回执"。
4. **Unicode 感知**：本仓库的表名/字段名含中文（如 `ads.ads_产销存月报`）。用 ASCII 正则会
   **静默截断**（`ads.ads_产销存月报` → `ads.ads_`），漏检且不报错，所以这里一律 Unicode 感知。

三类越界的判定口径（都是可测的规则，不搞模糊）：

| 类型 | 判定 | 为什么这么定 |
| --- | --- | --- |
| 表名 | 文本里任何 `库.表` token 必须在白名单里 | 点号形态是表名的高置信信号 |
| 字段名 | 文本里任何 `snake_case` ASCII 标识符必须在白名单里 | 本仓库字段名形如 `output_qty`；普通英文散文不会带下划线 |
| 数字 | 文本里任何**独立**数字字面量，归一化后必须在白名单里 | 数字是幻觉最高发的地方 |

三个刻意的"不误伤"设计（都有测试钉住）：
- `v1.2` 这类版本号**不算表名** —— 表名要求点号后至少有一个非数字字符；
- 标识符**内部**的数字不算数字断言 —— 扫数字前先把 ASCII 标识符串整段遮掉，
  否则 `t_2026_01` 会被误报成"编造数字 2026"；
- **表名的一部分不算字段** —— 扫字段前先遮掉已识别的表名，否则合法答案里的
  `dim.dim_brand` 会被再报一次"编造字段 dim_brand"（假红，且这个表名来自真实内核）。
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field

__all__ = [
    "GuardVerdict",
    "Violation",
    "ViolationKind",
    "Whitelist",
    "check_answer",
    "normalize_number",
]

# `库.表`：Unicode 感知；点号后必须含至少一个非数字字符（排除 v1.2 这类版本号）
TABLE_RE = re.compile(r"[A-Za-z_][\w\u4e00-\u9fff]*\.(?=[\w\u4e00-\u9fff]*[A-Za-z_\u4e00-\u9fff])[\w\u4e00-\u9fff]+")

# `snake_case` ASCII 标识符：至少一个下划线（本仓库字段名形态）
FIELD_RE = re.compile(r"\b[a-z][a-z0-9]*_[a-z0-9_]+\b")

# 数字字面量（含千分位与小数）
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# 扫描数字前要遮掉的"标识符串"：ASCII 字母/下划线开头的连续串（含点与数字）
# —— 表名、字段名、版本号、`t_2026_01` 这类都在里面，它们内部的数字不是数字断言
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


class ViolationKind(str, Enum):
    """越界的类型。"""

    TABLE = "table"
    FIELD = "field"
    NUMBER = "number"


class Violation(BaseModel):
    """一条越界：文本里出现了、但回执白名单里没有的东西。"""

    model_config = ConfigDict(frozen=True)

    kind: ViolationKind
    token: str = Field(description="文本里出现的原文 token（照抄，便于人一眼定位）")
    normalized: str | None = Field(default=None, description="数字类越界的归一化形式，便于与白名单对照")


class Whitelist(BaseModel):
    """回执白名单 —— 由调用方从**技能结果与回执**里收集（本模块不负责生成）。"""

    model_config = ConfigDict(frozen=True)

    tables: frozenset[str] = Field(default_factory=frozenset, description="允许出现的表名（库.表）")
    fields: frozenset[str] = Field(default_factory=frozenset, description="允许出现的字段名")
    numbers: frozenset[str] = Field(
        default_factory=frozenset,
        description="允许出现的数字，**必须是 normalize_number 归一化后的形式**",
    )


class GuardVerdict(BaseModel):
    """校验结论。"""

    model_config = ConfigDict(frozen=True)

    violations: tuple[Violation, ...] = Field(default=(), description="越界项，空表示全部来自回执")
    checked: int = Field(default=0, ge=0, description="本次扫描到的待验 token 数；0 表示文本里没有可验证的事实")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ok(self) -> bool:
        """越界即拦：没有任何违规才算通过。

        用 `computed_field` 而不是普通 property —— 这个判断必须出现在序列化结果里，
        外壳/审计要直接读它，而不是自己按空数组猜规则（M1-03 踩过这个坑）。
        """
        return not self.violations

    @property
    def reason(self) -> str:
        """给人看的一句话结论；有越界时逐条列出。"""
        if self.ok:
            return f"通过：{self.checked} 项事实均来自回执"
        parts = []
        for item in self.violations:
            if item.kind is ViolationKind.NUMBER:
                parts.append(f"编造数字 {item.token}（归一化 {item.normalized}）")
            elif item.kind is ViolationKind.TABLE:
                parts.append(f"编造表名 {item.token}")
            else:
                parts.append(f"编造字段 {item.token}")
        return "拦截：" + "；".join(parts)


def normalize_number(token: str) -> str:
    """把数字字面量归一成可比较的形式：去千分位、去无意义的小数尾零。

        '1,234' -> '1234'
        '30.0'  -> '30'
        '0.500' -> '0.5'
        '30.'   -> '30'

    调用方**建白名单时**必须用同一个函数处理回执里的数字，两边口径才一致。
    （百分数 ⇄ 小数这类**业务口径**换算不在本函数的职责里 —— 那取决于具体指标怎么定义，
    不属于"事实是否来自回执"这件事，故不做隐式转换。）
    """
    cleaned = token.replace(",", "").strip()
    if "." not in cleaned:
        return cleaned
    head, _, tail = cleaned.partition(".")
    tail = tail.rstrip("0")
    return f"{head}.{tail}" if tail else head


def _mask_identifiers(text: str) -> str:
    """把 ASCII 标识符串整段替换成等长空格，只留中文与纯数字。

    这样 `t_2026_01`、`v1.2`、`output_qty` 内部的数字都不会被当成数字断言。
    """
    return IDENTIFIER_RE.sub(lambda match: " " * len(match.group(0)), text)


def _mask_spans(text: str, matches: list[re.Match[str]]) -> str:
    """把给定的若干 span 替换成等长空格（长度不变，便于回查原文位置）。"""
    if not matches:
        return text
    chars = list(text)
    for match in matches:
        for index in range(match.start(), match.end()):
            chars[index] = " "
    return "".join(chars)


def check_answer(model_text: str, whitelist: Whitelist) -> GuardVerdict:
    """校验一段模型输出：表名 / 字段名 / 数字是否都来自回执白名单。越界即拦。

    纯函数：同样的入参永远得到同样的结论，不发请求、不读文件、不看时间。

    扫描分三段，后一段在前一段"遮罩"后的文本上进行 —— 这是为了避免把同一样东西
    换一种形态重复判一次：
    1. 表名（`库.表`）；
    2. **遮掉表名后**再找字段 —— 否则 `dim.dim_brand` 的 `dim_brand` 会被再报一次"编造字段"；
    3. 再遮掉字段名与其它 ASCII 标识符串，剩下的才算**独立的数字断言**。
    """
    violations: list[Violation] = []
    seen: set[tuple[str, str]] = set()
    checked = 0

    def record(kind: ViolationKind, token: str, normalized: str | None = None) -> None:
        key = (kind.value, token)
        if key in seen:
            return  # 同一个 token 出现多次只报一次，避免噪音淹没真正的越界
        seen.add(key)
        violations.append(Violation(kind=kind, token=token, normalized=normalized))

    table_matches = list(TABLE_RE.finditer(model_text))
    for match in table_matches:
        checked += 1
        token = match.group(0)
        if token not in whitelist.tables:
            record(ViolationKind.TABLE, token)

    without_tables = _mask_spans(model_text, table_matches)

    field_matches = list(FIELD_RE.finditer(without_tables))
    for match in field_matches:
        checked += 1
        token = match.group(0)
        if token not in whitelist.fields:
            record(ViolationKind.FIELD, token)

    number_scan_text = _mask_identifiers(_mask_spans(without_tables, field_matches))
    for match in NUMBER_RE.finditer(number_scan_text):
        checked += 1
        token = match.group(0)
        normalized = normalize_number(token)
        if normalized not in whitelist.numbers:
            record(ViolationKind.NUMBER, token, normalized)

    return GuardVerdict(violations=tuple(violations), checked=checked)

"""出口事实校验器（M1-05 / Issue #5）的测试。

Issue 要求：**3 正例 + 3 反例**（编造表名 / 编造字段 / 编造数字）、有中文标识符用例、
反例全部被拦。这里按这个结构组织，并额外把两个"刻意不误伤"的设计和几条边界钉住。

中文用例不是锦上添花：本仓库表名含中文（`ads.ads_产销存月报`），用 ASCII 正则会把合法表名
**截断成 `ads.ads_`**，导致合法答案被误拦（假红）；人为了让测试过就会去放宽规则，最后连编造
也放行。最后一组用例把这件事实测出来。
"""

from __future__ import annotations

import json
import re

import pydantic
import pytest
from dip_contracts.guards import (
    TABLE_RE,
    GuardVerdict,
    ViolationKind,
    Whitelist,
    check_answer,
    normalize_number,
)

CHINESE_TABLE = "ads.ads_产销存月报"

WHITELIST = Whitelist(
    tables=frozenset({CHINESE_TABLE, "cdw.dws_产销存汇总", "dim.dim_brand"}),
    fields=frozenset({"output_qty", "dama_qty", "tiaoma_qty"}),
    numbers=frozenset({normalize_number(token) for token in ("9", "13", "1,234")}),
)

EMPTY = Whitelist()


# ============================================================ 正例 1～3：应当通过
def test_positive_1_everything_traces_to_the_receipt():
    """正例 1：表名 / 字段名 / 数字**全部**来自回执 → 通过。"""
    text = f"{CHINESE_TABLE} 的 output_qty 由 dama_qty 与 tiaoma_qty 汇总而来，上游共 9 张表。"

    verdict = check_answer(text, WHITELIST)

    assert verdict.ok is True, verdict.reason
    assert verdict.violations == ()
    assert verdict.checked == 5, "3 个字段 + 1 个表名 + 1 个数字"


def test_positive_2_no_verifiable_fact_at_all():
    """正例 2：文本里没有任何可验证事实 → 通过（checked=0）。

    注意：这里**不**顺手去管"没有证据就不许给结论"——那是铁律 2，由 `Result` 模型负责，
    本模块按边界不重复实现（否则同一条规矩有两个实现，迟早不一致）。
    """
    verdict = check_answer("产量口径暂缺，暂时无法回答。", EMPTY)

    assert verdict.ok is True
    assert verdict.checked == 0


def test_positive_3_thousands_separator_and_percent_free_text():
    """正例 3：千分位写法与白名单口径一致（白名单用 normalize_number 建）→ 通过。"""
    verdict = check_answer(f"{CHINESE_TABLE} 上游共 1,234 条边、9 张表。", WHITELIST)

    assert verdict.ok is True, verdict.reason


# ============================================================ 反例 1～3：必须被拦
def test_negative_1_fabricated_table_is_blocked():
    """反例 1：编造表名 —— 点号结构正确但白名单里没有 → 拦。"""
    verdict = check_answer(f"{CHINESE_TABLE} 与 ads.ads_产量月报 的口径一致。", WHITELIST)

    assert verdict.ok is False
    assert [v.kind for v in verdict.violations] == [ViolationKind.TABLE]
    assert verdict.violations[0].token == "ads.ads_产量月报"
    assert "编造表名 ads.ads_产量月报" in verdict.reason


def test_negative_2_fabricated_field_is_blocked():
    """反例 2：编造字段 —— snake_case 形态但白名单里没有 → 拦。"""
    verdict = check_answer(f"{CHINESE_TABLE} 的产量 = SUM(sales_amount)。", WHITELIST)

    assert verdict.ok is False
    assert [v.kind for v in verdict.violations] == [ViolationKind.FIELD]
    assert verdict.violations[0].token == "sales_amount"
    assert "编造字段 sales_amount" in verdict.reason


def test_negative_3_fabricated_number_is_blocked():
    """反例 3：编造数字 —— 白名单里没有 → 拦，并带上归一化形式便于对照。"""
    verdict = check_answer(f"{CHINESE_TABLE} 的 output_qty 是 9527。", WHITELIST)

    assert verdict.ok is False
    assert [v.kind for v in verdict.violations] == [ViolationKind.NUMBER]
    assert verdict.violations[0].token == "9527"
    assert verdict.violations[0].normalized == "9527"
    assert "编造数字 9527" in verdict.reason


# ==================================================================== 中文标识符
def test_chinese_table_name_is_not_misread():
    """中文表名必须被完整读出（对照 ASCII 正则的截断，见下一个用例）。"""
    assert TABLE_RE.search(f"{CHINESE_TABLE} 的血缘").group(0) == CHINESE_TABLE
    assert check_answer(f"看下 {CHINESE_TABLE} 的上游", WHITELIST).ok is True


def test_fabricated_chinese_table_is_blocked():
    """编造中文表名也必须拦得住 —— 这是 Issue 点名的那条。"""
    verdict = check_answer("看下 ads.ads_编造月报 的上游", WHITELIST)

    assert verdict.ok is False
    assert verdict.violations[0].token == "ads.ads_编造月报"


def test_ascii_only_table_regex_breaks_on_chinese_identifiers():
    """实测 ASCII 正则的后果：**合法中文表名被截断**，从而被误判成"编造"（假红）。

    假红的危害不比漏检小：为了让它变绿，人会去放宽规则，最后连编造也放行。
    我们用的是 Unicode 感知的表名规则，所以不会走到那一步。
    """
    ascii_only = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z0-9_]*")
    text = f"{CHINESE_TABLE} 上游共 9 张表"

    truncated = ascii_only.search(text)
    assert truncated is not None
    assert truncated.group(0) == "ads.ads_", "ASCII 正则会静默截断"
    assert truncated.group(0) not in WHITELIST.tables, "截断后的假表名不在白名单 → 假红"

    assert TABLE_RE.search(text).group(0) == CHINESE_TABLE, "我们的规则给出完整表名"


# ====================================================== 三个"刻意不误伤"的设计
def test_version_like_token_is_not_a_table():
    """`v1.2` 不是表名：表名要求点号后至少有一个非数字字符。"""
    verdict = check_answer("这个方案 v1.2 已经定稿，上游 9 张表。", WHITELIST)

    assert verdict.ok is True, verdict.reason
    assert TABLE_RE.search("v1.2") is None


def test_real_ascii_table_does_not_trigger_field_violation():
    """真实内核里的表名 `dim.dim_brand` 会撞上字段规则 —— 遮罩表名后不再误报。

    不修的话：合法答案里出现 `dim.dim_brand` 就会被判成"编造字段 dim_brand"（假红），
    而它是白名单里正儿八经的表。
    """
    whitelist = Whitelist(
        tables=frozenset({"dim.dim_brand"}),
        fields=frozenset({"brand_name"}),
        numbers=frozenset(),
    )

    verdict = check_answer("dim.dim_brand 的 brand_name 来自哪里", whitelist)

    assert verdict.ok is True, verdict.reason
    assert [v.kind for v in verdict.violations] == []


def test_digits_inside_identifiers_are_not_number_claims():
    """标识符内部的数字不算数字断言 —— 否则 `t_2026_01` 会被误报成"编造数字 2026"。"""
    verdict = check_answer("看下 ods.t_2026_01 的上游，共 9 张表。", WHITELIST)

    assert all(v.kind is not ViolationKind.NUMBER for v in verdict.violations), "标识符内部数字不算数字断言"
    # 该表名确实不在白名单里 → 只应报"编造表名"，不该把表名的一部分再报成"编造字段"
    assert [v.kind for v in verdict.violations] == [ViolationKind.TABLE]
    assert verdict.violations[0].token == "ods.t_2026_01"


# ==================================================================== 其它边界
def test_same_token_reported_once():
    """同一越界重复出现只报一次，避免噪音淹没真正的越界。"""
    verdict = check_answer("ads.ads_编造月报 和 ads.ads_编造月报 都对不上。", WHITELIST)

    assert len(verdict.violations) == 1


def test_multiple_kinds_are_all_reported():
    verdict = check_answer("ads.ads_编造月报 的 sales_amount 是 9527。", WHITELIST)

    assert {v.kind for v in verdict.violations} == {ViolationKind.TABLE, ViolationKind.FIELD, ViolationKind.NUMBER}
    assert verdict.checked == 3


def test_ok_is_present_in_serialized_json():
    """回归：`ok` 必须出现在序列化结果里，外壳不该自己按空数组猜规则（M1-03 踩过）。"""
    payload = json.loads(check_answer(f"{CHINESE_TABLE} 上游 9 张表。", WHITELIST).model_dump_json())
    assert payload["ok"] is True

    payload = json.loads(check_answer("ads.ads_编造月报", WHITELIST).model_dump_json())
    assert payload["ok"] is False
    assert payload["violations"][0]["kind"] == "table"


def test_is_a_pure_function():
    """同样入参永远同样结论（不发请求、不读文件、不看时间）。"""
    text = "ads.ads_编造月报 的 sales_amount 是 9527。"
    first = check_answer(text, WHITELIST)
    second = check_answer(text, WHITELIST)

    assert first == second
    assert [v.token for v in first.violations] == ["ads.ads_编造月报", "sales_amount", "9527"], "token 照抄原文"


def test_verdict_is_read_only():
    verdict: GuardVerdict = check_answer("", EMPTY)
    with pytest.raises(pydantic.ValidationError):
        verdict.checked = 5  # type: ignore[misc]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,234", "1234"),
        ("30.0", "30"),
        ("0.500", "0.5"),
        ("30.", "30"),
        ("9", "9"),
        ("1,000,000", "1000000"),
    ],
)
def test_normalize_number_rules(raw, expected):
    """归一化规则写死在这里：调用方建白名单时必须用同一个函数，两边口径才一致。"""
    assert normalize_number(raw) == expected

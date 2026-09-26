"""skill 契约声明与校验器的测试（工作项 M1-02 / Issue #1）。

对应验收标准：
- 缺字段的声明被拒（有测试） → `test_every_contract_field_is_required` 等一组反例
- 模板复制即用，`gate` 绿 → `test_shipped_template_and_example_are_valid`
- 契约字段与《架构讨论》§3 的 9 类完全一致 → `test_contract_fields_match_the_nine_groups`

反例比正例值钱，所以每个约束都配了"该被拒"的用例；数值边界另配"边界值本身合法"的用例，
免得把边界写成"只有中间值能过"。
"""

from __future__ import annotations

import pathlib

import pydantic
import pytest
import yaml
from dip_skills import SkillSpec, SkillSpecError, load_skill_spec, validate_skill_spec
from dip_skills.spec import (
    BUDGET_MAX_CALLS,
    CONTRACT_FIELDS,
    FIELD_GROUPS,
    MAX_SUPPORTED_VERSION,
    TIMEOUT_MAX_SECONDS,
    WHEN_MAX_LENGTH,
    EvidenceKind,
    Renderer,
    Retry,
    SideEffect,
)

ROOT = pathlib.Path(__file__).parents[1]
TEMPLATE = ROOT / "templates/skill.yaml"
EXAMPLE = ROOT / "templates/skill.example.yaml"


def valid_declaration() -> dict:
    """一份合法的声明基线；各反例只改其中一处。"""
    return {
        "name": "lineage.analyze",
        "version": 1,
        "when": "用户给了 SQL 或问某脚本的血缘",
        "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}}},
        "output_schema": {"type": "object", "properties": {"graph": {"type": "object"}}},
        "side_effect": "read",
        "auth_scope": ["lineage:read"],
        "idempotent": True,
        "timeout": 30,
        "retry": "connect_only",
        "evidence_kind": "lineage",
        "renderer": "graph",
        "budget": 6,
    }


# --------------------------------------------------------------- 验收 ③：9 类字段
def test_contract_fields_match_the_nine_groups():
    """契约字段必须与《架构讨论》§3 的 9 类**完全一致**（双向比对，多一个少一个都红）。"""
    assert len(FIELD_GROUPS) == 9, "《架构讨论》§3 给的是 9 类字段"
    assert set(SkillSpec.model_fields) == set(CONTRACT_FIELDS), "模型字段与 9 类字段分组必须完全一致"
    assert CONTRACT_FIELDS == (
        "name",
        "version",
        "when",
        "input_schema",
        "output_schema",
        "side_effect",
        "auth_scope",
        "idempotent",
        "timeout",
        "retry",
        "evidence_kind",
        "renderer",
        "budget",
    )


# --------------------------------------------------------- 验收 ①：缺字段必须被拒
@pytest.mark.parametrize("field", CONTRACT_FIELDS)
def test_every_contract_field_is_required(field):
    """9 类字段逐个删掉都必须被拒 —— 不是抽查一个就算。"""
    decl = valid_declaration()
    decl.pop(field)
    with pytest.raises(SkillSpecError, match=f"缺少必填字段：{field}"):
        validate_skill_spec(decl)


@pytest.mark.parametrize(
    "field,bad_value,allowed_tokens",
    [
        ("side_effect", "readonly", ["'none'", "'read'", "'write'", "'ddl'", "'dml'"]),
        ("evidence_kind", "chart", ["'lineage'", "'metric'", "'report'", "'plan'"]),
        ("renderer", "pie", ["'table'", "'graph'", "'sql'", "'diff'"]),
        ("retry", "always", ["'none'", "'connect_only'"]),
    ],
)
def test_out_of_enum_value_is_rejected(field, bad_value, allowed_tokens):
    """枚举取值越界必须被拒，且消息里要说清"当前是什么、允许什么"。"""
    decl = valid_declaration()
    decl[field] = bad_value
    with pytest.raises(SkillSpecError) as excinfo:
        validate_skill_spec(decl)
    message = str(excinfo.value)
    assert f"{field}：取值不在允许集合内，当前 '{bad_value}'" in message
    missing = [token for token in allowed_tokens if token not in message]
    assert not missing, f"错误消息应列出全部允许取值，缺了 {missing}"


def test_unknown_field_is_rejected():
    """契约之外的字段一律拒绝：避免"声明里写了但没人读"的字段悄悄长出来。"""
    decl = valid_declaration()
    decl["description"] = "契约外的字段"
    with pytest.raises(SkillSpecError, match="description：契约之外的字段"):
        validate_skill_spec(decl)


# ------------------------------------------------------------------ 超长描述（反例）
def test_overlong_when_is_rejected():
    decl = valid_declaration()
    decl["when"] = "长" * (WHEN_MAX_LENGTH + 1)
    with pytest.raises(SkillSpecError, match="when：文本过长"):
        validate_skill_spec(decl)


def test_when_at_the_limit_is_accepted():
    """边界值本身必须合法（中文按字符数算，不按字节）。"""
    decl = valid_declaration()
    decl["when"] = "长" * WHEN_MAX_LENGTH
    assert validate_skill_spec(decl).when == "长" * WHEN_MAX_LENGTH


def test_overlong_value_is_truncated_in_the_message():
    """超长声明的原文不该把错误消息刷爆：只展示前 40 字 + 总字数。"""
    decl = valid_declaration()
    decl["when"] = "长" * (WHEN_MAX_LENGTH + 1)
    with pytest.raises(SkillSpecError) as excinfo:
        validate_skill_spec(decl)
    message = str(excinfo.value)
    assert "共 501 字" in message
    assert message.count("长") <= 41, "错误消息里不该出现整段超长原文"


def test_blank_when_is_rejected():
    decl = valid_declaration()
    decl["when"] = "   "
    assert validate_skill_spec(decl).when == "   "  # 空白只要能读到就不算缺字段……
    with pytest.raises(SkillSpecError, match="when：文本过短"):
        validate_skill_spec({**decl, "when": ""})  # ……但空字符串必须拒


# ------------------------------------------------------------------ 版本兼容（反例）
def test_version_above_supported_is_rejected():
    decl = valid_declaration()
    decl["version"] = MAX_SUPPORTED_VERSION + 1
    with pytest.raises(SkillSpecError, match="version：取值过大"):
        validate_skill_spec(decl)


def test_version_zero_is_rejected():
    decl = valid_declaration()
    decl["version"] = 0
    with pytest.raises(SkillSpecError, match="version：取值过小"):
        validate_skill_spec(decl)


# -------------------------------------------------------------------- auth_scope
def test_empty_auth_scope_is_rejected():
    decl = valid_declaration()
    decl["auth_scope"] = []
    with pytest.raises(SkillSpecError, match="auth_scope：不能为空"):
        validate_skill_spec(decl)


def test_blank_auth_scope_item_is_rejected():
    """空白权限点 = 看起来声明了权限的假象，必须拒。"""
    decl = valid_declaration()
    decl["auth_scope"] = ["lineage:read", "   "]
    with pytest.raises(SkillSpecError, match="每个权限点都必须是非空字符串"):
        validate_skill_spec(decl)


# --------------------------------------------------------------------- schema
@pytest.mark.parametrize("field", ["input_schema", "output_schema"])
def test_empty_schema_is_rejected(field):
    decl = valid_declaration()
    decl[field] = {}
    with pytest.raises(SkillSpecError, match=f"{field}：不能为空"):
        validate_skill_spec(decl)


@pytest.mark.parametrize("field", ["input_schema", "output_schema"])
def test_non_mapping_schema_is_rejected(field):
    decl = valid_declaration()
    decl[field] = "sql"
    with pytest.raises(SkillSpecError, match=f"{field}：应为映射"):
        validate_skill_spec(decl)


# ------------------------------------------------------------- timeout / budget
@pytest.mark.parametrize(
    "field,bad_value,reason",
    [
        ("timeout", 0, "取值过小"),
        ("timeout", TIMEOUT_MAX_SECONDS + 1, "取值过大"),
        ("budget", 0, "取值过小"),
        ("budget", BUDGET_MAX_CALLS + 1, "取值过大"),
    ],
)
def test_out_of_range_numbers_are_rejected(field, bad_value, reason):
    decl = valid_declaration()
    decl[field] = bad_value
    with pytest.raises(SkillSpecError, match=f"{field}：{reason}"):
        validate_skill_spec(decl)


def test_non_bool_idempotent_is_rejected():
    decl = valid_declaration()
    decl["idempotent"] = "maybe"
    with pytest.raises(SkillSpecError, match="idempotent：应为布尔值"):
        validate_skill_spec(decl)


# ------------------------------------------------------------- 整份声明就不是映射
@pytest.mark.parametrize("bad", [["name"], "name: x", None, 42])
def test_non_mapping_declaration_is_rejected(bad):
    with pytest.raises(SkillSpecError, match="必须是映射"):
        validate_skill_spec(bad)


def test_empty_file_is_rejected():
    """空 YAML 文件解析出来是 None —— 要给出可读的拒绝理由，而不是崩在 pydantic 里。"""
    with pytest.raises(SkillSpecError, match="必须是映射"):
        validate_skill_spec(yaml.safe_load(""))


def test_broken_yaml_is_rejected(tmp_path):
    broken = tmp_path / "broken.yaml"
    broken.write_text("name: [没闭合\n", encoding="utf-8")
    with pytest.raises(SkillSpecError, match="YAML 解析失败"):
        load_skill_spec(broken)


# ------------------------------------------------------------------- 中文与编码
def test_chinese_content_survives_the_yaml_roundtrip(tmp_path):
    """中文标识符/描述必须原样保真（中文 Windows 下默认编码会读成乱码，见 AGENTS.md §8）。"""
    decl = valid_declaration()
    decl["when"] = "用户问「ads.ads_产销存月报 的产量怎么来的？」时使用"
    decl["auth_scope"] = ["lineage:read"]
    path = tmp_path / "skill.yaml"
    path.write_text(yaml.safe_dump(decl, allow_unicode=True), encoding="utf-8")

    spec = load_skill_spec(path)
    assert spec.when == "用户问「ads.ads_产销存月报 的产量怎么来的？」时使用"


def test_chinese_name_is_rejected_with_readable_message():
    """name 限定 ASCII（§3 例子是 lineage.analyze）；中文名要给能看懂的原因。"""
    decl = valid_declaration()
    decl["name"] = "血缘.分析"
    with pytest.raises(SkillSpecError, match="name：格式不合法"):
        validate_skill_spec(decl)


def test_error_message_lists_multiple_problems_at_once():
    """一次把所有问题列全，别让人（和 AI）改一个跑一次。"""
    decl = valid_declaration()
    del decl["side_effect"]
    decl["renderer"] = "pie"
    with pytest.raises(SkillSpecError) as excinfo:
        validate_skill_spec(decl)
    message = str(excinfo.value)
    assert "缺少必填字段：side_effect" in message
    assert "renderer" in message


# ----------------------------------------------- 验收 ②：模板/样例复制即用
def test_shipped_template_and_example_are_valid():
    """仓库里发布的模板与样例必须自身合法 —— 否则"复制即用"就是空话。"""
    for path in (TEMPLATE, EXAMPLE):
        spec = load_skill_spec(path)
        assert isinstance(spec, SkillSpec)
        assert spec.qualified_name == f"{spec.name}@{spec.version}"


def test_example_is_the_architecture_doc_example():
    """样例要与 §3 表格里的例子对齐，别自己另发明一个。"""
    spec = load_skill_spec(EXAMPLE)
    assert spec.qualified_name == "lineage.analyze@1"
    assert spec.side_effect is SideEffect.READ
    assert spec.auth_scope == ["lineage:read"]
    assert spec.evidence_kind is EvidenceKind.LINEAGE
    assert spec.renderer is Renderer.GRAPH
    assert spec.retry is Retry.CONNECT_ONLY
    assert spec.timeout == 30
    assert spec.budget == 6


def test_template_declares_all_enum_values_as_comments():
    """模板要能当说明书用：9 类字段的取值集合都得在模板里写明。"""
    text = TEMPLATE.read_text(encoding="utf-8")
    for token in ("none / read / write / ddl / dml", "lineage / metric / report / plan", "table / graph / sql / diff"):
        assert token in text


# --------------------------------------------------------------------- 只读契约
def test_spec_is_read_only_after_parsing():
    spec = validate_skill_spec(valid_declaration())
    with pytest.raises(pydantic.ValidationError):
        spec.name = "别的名字"

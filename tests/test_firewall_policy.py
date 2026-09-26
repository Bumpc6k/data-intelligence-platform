"""防火墙策略判定（M2-01）。

验收要求「三档各 1 例」+「拿不准按上一档处理（fail-closed）」，
所以这里既有正例，也有**反例**（策略表写错、动作同时命中宽严两条规则）。
"""

from __future__ import annotations

import logging
import pathlib
import re

import pytest
from firewall.policy import (
    DISPOSITION,
    Policy,
    PolicyError,
    Tier,
    action_fingerprint,
    stricter,
)

ROOT = pathlib.Path(__file__).parents[1]
SHIPPED = ROOT / "apps" / "firewall" / "policies" / "default.yaml"


@pytest.fixture(scope="module")
def policy() -> Policy:
    return Policy.load(SHIPPED)


def write_policy(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / "policy.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------- 三档各 1 例（验收）


def test_read_only_action_is_auto_pass(policy: Policy):
    """验收①：只读自动通过。"""
    verdict = policy.judge(actor="analyst", action="select", target="ads.ads_产销存月报")
    assert verdict.tier is Tier.AUTO
    assert verdict.disposition == "auto_pass"
    assert verdict.matched is True
    assert verdict.matched_rules == ("read-only",)
    assert verdict.requires_token is False


def test_production_write_requires_approval(policy: Policy):
    """验收②：生产写需审批。"""
    verdict = policy.judge(actor="engineer", action="insert", target="ads.ads_产销存月报")
    assert verdict.tier is Tier.APPROVAL
    assert verdict.disposition == "require_approval"
    assert verdict.matched is True
    assert verdict.requires_token is True


def test_truncate_is_rejected(policy: Policy):
    """验收③：truncate / drop 拒绝。"""
    for action in ("truncate", "TRUNCATE", "truncate table", "drop", "DROP TABLE"):
        verdict = policy.judge(actor="engineer", action=action, target="ads.ads_产销存月报")
        assert verdict.tier is Tier.DENY, f"{action} 应当被拒"
        assert verdict.disposition == "reject"
        assert verdict.requires_token is False


def test_destructive_is_rejected_even_on_a_lookalike_target(policy: Policy):
    """破坏性操作不分目标：换一张"看起来无关"的表也不能放过去。"""
    verdict = policy.judge(actor="engineer", action="truncate", target="tmp.scratch")
    assert verdict.tier is Tier.DENY


# ---------------------------------------------------------------- fail-closed（拿不准按上一档）


def test_unknown_action_falls_back_to_declared_default_tier(policy: Policy):
    """未命中任何规则 → 按**显式声明的** default_tier，且 matched=False。

    这两件事必须能分开：档位碰巧一样，但"命中了策略"和"没人管、走的默认档"含义不同。
    没写进策略表的动作默认当"需要人确认一次"，而不是"没人管所以放过去"。
    """
    verdict = policy.judge(actor="engineer", action="something-nobody-defined")
    assert verdict.matched is False
    assert verdict.matched_rules == ()
    assert verdict.tier is policy.default_tier is Tier.APPROVAL
    assert verdict.requires_token is True
    assert "未命中" in verdict.reason


def test_miss_is_logged_explicitly(policy: Policy, caplog: pytest.LogCaptureFixture):
    """M2-05：未命中必须在**日志**里写明。

    响应里的 `matched=false` 随请求一起消失了，日志是唯一的长期记录。否则"没人管、走默认档"
    与"命中了策略"在事后完全分不出来 —— 而这正是静默失效的样子：看起来档位判对了，
    其实是策略表漏了它。
    """
    with caplog.at_level(logging.WARNING, logger="firewall.policy"):
        verdict = policy.judge(actor="么慌", action="没人定义过这个动作", target="ads.t")

    assert verdict.matched is False
    logged = caplog.text
    assert "未命中" in logged
    assert "default_tier=approval" in logged
    assert "么慌" in logged, "日志要能定位到是谁做的"
    assert "没人定义过这个动作" in logged
    assert "ads.t" in logged
    assert policy.digest in logged, "日志要带策略指纹：事后能回答当时用的是哪一版策略"


def test_hit_does_not_warn_about_a_miss(policy: Policy, caplog: pytest.LogCaptureFixture):
    """命中了就别刷"未命中"警告 —— 日志里全是噪音等于没有日志。"""
    with caplog.at_level(logging.WARNING, logger="firewall.policy"):
        policy.judge(actor="x", action="select", target="ads.t")
    assert "未命中" not in caplog.text


def test_policy_carries_its_own_digest(policy: Policy):
    """指纹由 Policy 自己算（单一真相）：日志与接口用的是同一个值。"""
    assert len(policy.digest) == 12
    assert all(char in "0123456789abcdef" for char in policy.digest)
    assert Policy.load(SHIPPED).digest == policy.digest


def test_taking_the_stricter_tier_when_two_rules_match(tmp_path: pathlib.Path):
    """fail-closed 的核心：同时命中宽规则与严规则时，**取更严的那一档**。

    `delete` 命中 production-write（approval），真实场景里也可能有人给它再加一条
    auto 规则 —— 只要还有一条判严的规则命中，结果就必须是严的。
    这里直接用两份规则构造"宽的在后"的情形，确认顺序不影响结果。
    """
    path = write_policy(
        tmp_path=tmp_path,
        body="""
version: 1
default_tier: auto
rules:
  - name: narrow-strict
    tier: deny
    actions: ["nuke*"]
  - name: sloppy-wide
    tier: auto
    actions: ["nuke*"]
""",
    )
    loaded = Policy.load(path)
    verdict = loaded.judge(actor="x", action="nuke-everything")
    assert verdict.matched is True
    assert set(verdict.matched_rules) == {"narrow-strict", "sloppy-wide"}
    assert verdict.tier is Tier.DENY


def test_severity_order_is_declared_not_assumed():
    """档位顺序是显式声明的，别让人靠猜。"""
    assert stricter(Tier.AUTO, Tier.DENY) is Tier.DENY
    assert stricter(Tier.APPROVAL, Tier.AUTO) is Tier.APPROVAL
    assert stricter(Tier.DENY, Tier.APPROVAL) is Tier.DENY
    assert set(DISPOSITION) == {Tier.AUTO, Tier.APPROVAL, Tier.DENY}


def test_role_constrained_rule_only_applies_to_that_role(tmp_path: pathlib.Path):
    path = write_policy(
        tmp_path=tmp_path,
        body="""
version: 1
default_tier: approval
rules:
  - name: dba-can-read
    tier: auto
    actions: ["select"]
    require_any_role: ["dba"]
""",
    )
    loaded = Policy.load(path)
    as_dba = loaded.judge(actor="a", action="select", roles=("dba",))
    as_other = loaded.judge(actor="a", action="select", roles=("guest",))
    assert as_dba.tier is Tier.AUTO and as_dba.matched is True
    assert as_other.tier is Tier.APPROVAL and as_other.matched is False


# ---------------------------------------------------------------- 策略表写错就别起来


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("version: 1\nrules:\n  - {name: a, tier: auto, actions: ['x']}\n", "default_tier"),
        ("version: 1\ndefault_tier: maybe\nrules:\n  - {name: a, tier: auto, actions: ['x']}\n", "不认识的档位"),
        ("version: 1\ndefault_tier: auto\n", "至少要有一条"),
        ("version: 1\ndefault_tier: auto\nrules: []\n", "至少要有一条"),
        (
            "version: 1\ndefault_tier: auto\nrules:\n  - {name: a, tier: auto}\n",
            "actions",
        ),
        (
            "version: 1\ndefault_tier: auto\nrules:\n"
            "  - {name: a, tier: auto, actions: ['x']}\n"
            "  - {name: a, tier: deny, actions: ['y']}\n",
            "规则名重复",
        ),
        ("- 这不是映射\n", "根节点必须是映射"),
    ],
)
def test_broken_policy_is_rejected_loudly(tmp_path: pathlib.Path, body: str, expected: str):
    path = write_policy(tmp_path, body)
    with pytest.raises(PolicyError) as excinfo:
        Policy.load(path)
    assert expected in str(excinfo.value)


def test_missing_policy_file_is_rejected_loudly(tmp_path: pathlib.Path):
    with pytest.raises(PolicyError) as excinfo:
        Policy.load(tmp_path / "nope.yaml")
    assert "策略表不存在" in str(excinfo.value)


def test_shipped_policy_declares_its_default_tier_explicitly(policy: Policy):
    """Issue 明确要求 default_tier 写在策略表里（不是代码里的常量）。"""
    text = SHIPPED.read_text(encoding="utf-8")
    assert "default_tier:" in text
    assert policy.default_tier is Tier.APPROVAL
    assert {rule.name for rule in policy.rules} == {
        "destructive-ddl",
        "read-only",
        "production-write",
        "production-release-cn",
    }


def test_shipped_policy_contains_at_least_one_chinese_action(policy: Policy):
    """M2-04 的交付物：策略表至少要有一条**中文动作**规则。"""
    chinese = [
        rule.name for rule in policy.rules if any(re.search(r"[\u4e00-\u9fff]", action) for action in rule.actions)
    ]
    assert chinese, "策略表里没有一条中文动作规则"


def test_chinese_action_must_hit_the_policy_not_the_default_tier(policy: Policy):
    """M2-04 验收：中文动作**必须命中策略**，不得落到默认档。

    危险之处在于"落到默认档"**看起来是对的**：default_tier 也是 approval，档位一模一样，
    调用方只看 tier 根本发现不了。所以这里断言的不是档位，而是 `matched` 与命中的规则名 ——
    档位相同，含义相反。
    """
    verdict = policy.judge(actor="么慌", action="上线工作流到生产")
    assert verdict.tier is Tier.APPROVAL
    assert verdict.matched is True, "中文动作掉进默认档了：档位看着对，但那是静默失效"
    assert verdict.matched_rules == ("production-release-cn",)
    assert "未命中" not in verdict.reason


@pytest.mark.parametrize("action", ["上线工作流到预发", "上线工作流到测试环境", "上线工作流到备份库"])
def test_chinese_action_variants_are_covered_by_the_glob(policy: Policy, action: str):
    """用通配而不是精确写死：动作名会带宾语，只写 '上线工作流到生产' 会漏掉变体。"""
    verdict = policy.judge(actor="么慌", action=action)
    assert verdict.matched is True, f"{action} 应当被通配规则覆盖"
    assert verdict.matched_rules == ("production-release-cn",)


def test_unrelated_chinese_action_honestly_falls_to_the_default_tier(policy: Policy):
    """反例：不是"只要是中文就命中"。

    没写进策略的中文动作仍然算未命中（会进日志、`matched=false`）——
    不能让"中文能匹配"变成"中文一律放行/一律命中"的错觉。
    """
    verdict = policy.judge(actor="么慌", action="修改调度依赖")
    assert verdict.matched is False
    assert verdict.matched_rules == ()
    assert "未命中" in verdict.reason


# ---------------------------------------------------------------- 动作指纹


def test_fingerprint_changes_when_anything_changes():
    """绑指纹的意义：改一个字符就失效，授权不能挪用。"""
    base = action_fingerprint(action="insert", target="ads.ads_产销存月报", sql="insert into t values (1)")
    assert base == action_fingerprint(action="insert", target="ads.ads_产销存月报",
                                      sql="insert into t values (1)")
    assert base != action_fingerprint(action="insert", target="ads.ads_产销存月报",
                                      sql="insert into t values (2)")
    assert base != action_fingerprint(action="insert", target="ads.ads_产销存月报x",
                                      sql="insert into t values (1)")
    assert base != action_fingerprint(action="update", target="ads.ads_产销存月报",
                                      sql="insert into t values (1)")


def test_fingerprint_is_stable_across_whitespace_only_differences():
    """周围空白不算改动（否则审批人复制粘贴时多一个空格就白批了）。"""
    assert action_fingerprint(action=" insert ", target=" ads.t ") == action_fingerprint(
        action="insert", target="ads.t"
    )


def test_verdict_fingerprint_covers_the_sql(policy: Policy):
    """回归：指纹必须看见 SQL。

    第一版 `judge()` 忘了把 sql 传进指纹，于是"审批了这一句 SQL"实际绑定的只是
    "insert 这个动作"——换掉 SQL 照样能用。被
    `test_token_does_not_transfer_to_another_action` 抓到，这里钉死。
    """
    first = policy.judge(actor="a", action="insert", target="ads.t", sql="insert into ads.t select 1")
    second = policy.judge(actor="a", action="insert", target="ads.t", sql="insert into ads.t select 2")
    assert first.fingerprint != second.fingerprint

    # 档位判定不受 sql 影响（档位由动作与目标决定），受影响的只有指纹。
    assert first.tier is second.tier is Tier.APPROVAL


def test_fingerprint_ignores_formatting_differences():
    """M2-02：人复制粘贴带来的差异（换行、缩进、结尾分号）不算改动。

    否则审批人批完、执行方从日志里复制出来多一个换行，就会被判"指纹不一致"而白批。
    """
    canonical = action_fingerprint(
        action="insert", target="ads.t", sql="insert into ads.t select 1"
    )
    assert canonical == action_fingerprint(
        action="insert", target="ads.t", sql="  insert into ads.t select 1  ;  "
    )
    assert canonical == action_fingerprint(
        action="insert",
        target="ads.t",
        sql="insert into ads.t\n    select 1",
    )
    assert canonical == action_fingerprint(
        action="  INSERT  ", target="ads.t", sql="insert into ads.t select 1"
    ), "动作是词汇，大小写不该算改动"


def test_fingerprint_preserves_whitespace_inside_string_literals():
    """反例：引号**里面**的空白是有语义的，折了就等于改了授权内容。

    这条是"规范化不能过头"的守卫 —— 无脑 `re.sub(r"\\s+", " ", sql)` 会在这里红。
    """
    one_space = action_fingerprint(action="insert", target="ads.t", sql="insert into ads.t values ('a b')")
    two_spaces = action_fingerprint(action="insert", target="ads.t", sql="insert into ads.t values ('a  b')")
    assert one_space != two_spaces


def test_fingerprint_keeps_target_case_sensitive():
    """表名可能大小写敏感：target 只去首尾空白，不折叠大小写。"""
    lower = action_fingerprint(action="insert", target="ads.t")
    upper = action_fingerprint(action="insert", target="ADS.T")
    assert lower != upper


def test_fingerprint_is_short_and_hex():
    value = action_fingerprint(action="select", target="ads.t")
    assert len(value) == 16
    assert all(char in "0123456789abcdef" for char in value)

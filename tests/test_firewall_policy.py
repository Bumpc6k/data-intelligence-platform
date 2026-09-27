"""防火墙策略判定（M2-01）。

验收要求「三档各 1 例」+「拿不准按上一档处理（fail-closed）」，
所以这里既有正例，也有**反例**（策略表写错、动作同时命中宽严两条规则）。
"""

from __future__ import annotations

import pathlib

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
    assert {rule.name for rule in policy.rules} == {"destructive-ddl", "read-only", "production-write"}


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


def test_fingerprint_is_short_and_hex():
    value = action_fingerprint(action="select", target="ads.t")
    assert len(value) == 16
    assert all(char in "0123456789abcdef" for char in value)

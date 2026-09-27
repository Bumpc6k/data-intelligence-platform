"""审批令牌的语义（M2-02）：一次性 / 绑指纹 / 限时。

时钟是注入的 —— "5 分钟过期"如果用 `sleep(300)` 来测，这种测试没人会跑第二遍。
"""

from __future__ import annotations

import pytest
from firewall.tokens import DEFAULT_TTL_SECONDS, TokenStore, ttl_seconds


class FakeClock:
    """假时钟：测试里手动推进，不真的等。"""

    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def store(clock: FakeClock) -> TokenStore:
    return TokenStore(clock=clock)


def test_default_ttl_is_five_minutes():
    """Issue 明确要求 5 分钟时效，别让它悄悄变。"""
    assert DEFAULT_TTL_SECONDS == 300
    assert ttl_seconds() == 300


def test_token_is_single_use(store: TokenStore):
    """验收①：同一令牌第二次使用被拒。"""
    token = store.issue(actor="engineer", fingerprint="abc")

    first_ok, first_reason = store.verify(value=token.value, fingerprint="abc")
    assert first_ok is True and first_reason == "ok"

    second_ok, second_reason = store.verify(value=token.value, fingerprint="abc")
    assert second_ok is False
    assert "已使用过" in second_reason


def test_token_binds_to_the_action(store: TokenStore):
    """验收②：令牌跨动作不可用（改一个字符即失效）。"""
    token = store.issue(actor="engineer", fingerprint="abc")
    ok, reason = store.verify(value=token.value, fingerprint="abC")  # 只改一个字符
    assert ok is False
    assert "指纹不一致" in reason


def test_mismatch_does_not_consume_the_token(store: TokenStore):
    """挪用的尝试不该把审批人对**原动作**的授权弄没。"""
    token = store.issue(actor="engineer", fingerprint="abc")
    assert store.verify(value=token.value, fingerprint="stolen")[0] is False

    ok, _ = store.verify(value=token.value, fingerprint="abc")
    assert ok is True, "指纹不符的拒绝不应消费令牌"


def test_token_expires_after_the_ttl(store: TokenStore, clock: FakeClock):
    """时效：刚好在时效内可用，过了就作废。"""
    token = store.issue(actor="engineer", fingerprint="abc")

    clock.advance(DEFAULT_TTL_SECONDS - 1)
    assert store.verify(value=token.value, fingerprint="abc")[0] is True


def test_expired_token_is_rejected_and_reports_expiry(store: TokenStore, clock: FakeClock):
    token = store.issue(actor="engineer", fingerprint="abc")
    clock.advance(DEFAULT_TTL_SECONDS + 1)

    ok, reason = store.verify(value=token.value, fingerprint="abc")
    assert ok is False
    assert "已过期" in reason
    assert "301 秒前" in reason, "拒绝原因要说清过了多久，便于审计"


def test_expiry_boundary_is_inclusive(store: TokenStore, clock: FakeClock):
    """正好到时效那一刻算过期（>= 而不是 >）。边界上宁严勿松。"""
    token = store.issue(actor="engineer", fingerprint="abc")
    clock.advance(DEFAULT_TTL_SECONDS)
    assert store.verify(value=token.value, fingerprint="abc")[0] is False


def test_used_wins_over_expired_in_the_message(store: TokenStore, clock: FakeClock):
    """一张既用过又过期的令牌，拒它的真正原因是"用过"（检查顺序是有意的）。"""
    token = store.issue(actor="engineer", fingerprint="abc")
    assert store.verify(value=token.value, fingerprint="abc")[0] is True

    clock.advance(DEFAULT_TTL_SECONDS * 10)
    ok, reason = store.verify(value=token.value, fingerprint="abc")
    assert ok is False
    assert "已使用过" in reason


def test_expiry_does_not_need_a_real_wait(store: TokenStore, clock: FakeClock):
    """反例式断言：不需要真的等 —— 时钟推进即可（这条是给未来的自己看的）。"""
    token = store.issue(actor="engineer", fingerprint="abc")
    clock.advance(3600)
    assert store.verify(value=token.value, fingerprint="abc")[0] is False


def test_unknown_token_is_rejected(store: TokenStore):
    ok, reason = store.verify(value="not-a-real-token", fingerprint="abc")
    assert ok is False
    assert "不存在" in reason


def test_ttl_can_be_overridden_by_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FIREWALL_TOKEN_TTL_SECONDS", "60")
    assert ttl_seconds() == 60
    assert TokenStore(ttl_seconds=ttl_seconds()).ttl_seconds == 60


@pytest.mark.parametrize("bad", ["soon", "0", "-5", ""])
def test_bad_ttl_env_is_rejected_loudly(monkeypatch: pytest.MonkeyPatch, bad: str):
    """改错环境变量就别起来 —— 静默按默认值跑等于把时效悄悄换掉。"""
    monkeypatch.setenv("FIREWALL_TOKEN_TTL_SECONDS", bad)
    with pytest.raises(ValueError):
        ttl_seconds()


def test_tokens_are_unique(store: TokenStore):
    values = {store.issue(actor="a", fingerprint="f").value for _ in range(50)}
    assert len(values) == 50
    assert all(len(value) >= 40 for value in values)


def test_info_does_not_consume(store: TokenStore):
    token = store.issue(actor="a", fingerprint="f")
    assert store.info(value=token.value) is not None
    assert store.verify(value=token.value, fingerprint="f")[0] is True, "info 不该把令牌用掉"

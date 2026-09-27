"""防火墙 HTTP 接口（M2-01）。

策略判定的逻辑在 `test_firewall_policy.py` 里逐条覆盖；这里只测**接口层**的三件事：
三档能通过 HTTP 走通、判定结果带得回"用的是哪一版策略"、令牌只能一次性用。
"""

from __future__ import annotations

import os
import pathlib

import pytest
from fastapi.testclient import TestClient
from firewall.main import app

ROOT = pathlib.Path(__file__).parents[1]


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_health_reports_the_effective_policy(client: TestClient):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["default_tier"] == "approval"
    assert len(body["policy_digest"]) == 12
    assert {rule["name"] for rule in body["rules"]} == {"destructive-ddl", "read-only", "production-write"}


@pytest.mark.parametrize(
    ("action", "target", "tier", "disposition"),
    [
        ("select", "ads.ads_产销存月报", "auto", "auto_pass"),
        ("insert", "ads.ads_产销存月报", "approval", "require_approval"),
        ("truncate", "ads.ads_产销存月报", "deny", "reject"),
    ],
)
def test_three_tiers_over_http(client: TestClient, action: str, target: str, tier: str, disposition: str):
    """验收「三档各 1 例」走接口再验一遍 —— 判定不能只在单元测试里成立。"""
    response = client.post("/judge", json={"actor": "么慌", "action": action, "target": target})
    assert response.status_code == 200, "拒绝是判定结论，不是接口错误，不该返回 4xx"
    body = response.json()
    assert body["tier"] == tier
    assert body["disposition"] == disposition
    assert body["matched"] is True
    assert body["fingerprint"]


def test_judge_carries_the_policy_revision(client: TestClient):
    """「策略表可改且改动有记录」：判定结果里带着生效策略的版本与指纹。"""
    body = client.post("/judge", json={"actor": "x", "action": "select"}).json()
    assert body["policy_version"] == 1
    assert len(body["policy_digest"]) == 12
    assert body["policy_path"].endswith("default.yaml")


def test_token_is_single_use(client: TestClient):
    """验收「同一令牌第二次使用被拒」。"""
    issued = client.post(
        "/tokens/issue",
        json={
            "approver": "leader",
            "actor": "engineer",
            "action": "insert",
            "target": "ads.ads_产销存月报",
            "sql": "insert into ads.ads_产销存月报 select 1",
        },
    ).json()
    payload = {
        "token": issued["token"],
        "action": "insert",
        "target": "ads.ads_产销存月报",
        "sql": "insert into ads.ads_产销存月报 select 1",
    }

    first = client.post("/verify", json=payload)
    assert first.status_code == 200 and first.json()["ok"] is True

    second = client.post("/verify", json=payload)
    assert second.status_code == 403, "第二次必须被拒"
    assert "已使用过" in second.json()["detail"]


def test_token_does_not_transfer_to_another_action(client: TestClient):
    """绑指纹：同一张令牌换一个动作（哪怕只改一个字符）就不能用。"""
    issued = client.post(
        "/tokens/issue",
        json={"approver": "leader", "actor": "engineer", "action": "insert", "target": "ads.t", "sql": "x"},
    ).json()

    stolen = client.post(
        "/verify",
        json={"token": issued["token"], "action": "insert", "target": "ads.t", "sql": "y"},
    )
    assert stolen.status_code == 403
    assert "指纹不一致" in stolen.json()["detail"]

    # 而且这一次不匹配**不该**把令牌消费掉：审批人对原动作的授权仍然有效。
    legit = client.post(
        "/verify",
        json={"token": issued["token"], "action": "insert", "target": "ads.t", "sql": "x"},
    )
    assert legit.status_code == 200


def test_unknown_token_is_rejected(client: TestClient):
    response = client.post("/verify", json={"token": "not-a-real-token", "action": "insert", "target": "ads.t"})
    assert response.status_code == 403
    assert "不存在" in response.json()["detail"]


def test_verify_requires_a_token(client: TestClient):
    assert client.post("/verify", json={"action": "insert"}).status_code == 422
    assert client.post("/judge", json={"actor": "x"}).status_code == 422


class _FakeClock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_expired_token_is_rejected_over_http(monkeypatch: pytest.MonkeyPatch):
    """时效也走接口验一遍：过期后同一张令牌被拒（用假时钟，不真的等 5 分钟）。"""
    from firewall import main as firewall_main
    from firewall.tokens import DEFAULT_TTL_SECONDS, TokenStore

    clock = _FakeClock()
    monkeypatch.setattr(firewall_main, "tokens", TokenStore(clock=clock))
    client = TestClient(app)

    def issue_and_payload():
        issued = client.post(
            "/tokens/issue",
            json={"approver": "leader", "actor": "engineer", "action": "insert", "target": "ads.t"},
        ).json()
        return {"token": issued["token"], "action": "insert", "target": "ads.t"}

    fresh = issue_and_payload()
    assert client.post("/verify", json=fresh).status_code == 200

    stale = issue_and_payload()
    clock.advance(DEFAULT_TTL_SECONDS + 1)
    response = client.post("/verify", json=stale)
    assert response.status_code == 403
    assert "已过期" in response.json()["detail"]

    # 时效可以配：环境变量一改，健康检查里就能看到
    monkeypatch.setenv("FIREWALL_TOKEN_TTL_SECONDS", "60")
    assert client.get("/health").json()["token_ttl_seconds"] == DEFAULT_TTL_SECONDS, (
        "已构造好的 store 不该被环境变量影响（它是启动时读的）"
    )


def test_policy_change_takes_effect_and_shows_up_in_the_digest(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """改策略表文件 → 判定跟着变，且指纹变了（"改动有记录"靠它）。

    同时证明：**不用重启进程**，改文件即生效（mtime 变了就重载）。
    """
    path = tmp_path / "policy.yaml"
    path.write_text(
        "version: 1\ndefault_tier: auto\nrules:\n  - {name: read-only, tier: auto, actions: ['select']}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FIREWALL_POLICY", str(path))
    client = TestClient(app)

    before = client.post("/judge", json={"actor": "x", "action": "nobody-defined-this"}).json()
    assert before["tier"] == "auto" and before["matched"] is False

    path.write_text(
        "version: 2\ndefault_tier: deny\nrules:\n  - {name: read-only, tier: auto, actions: ['select']}\n",
        encoding="utf-8",
    )
    os.utime(path, (1_700_000_000, 1_700_000_001))  # 显式改 mtime，免得测试跑太快缓存没失效

    after = client.post("/judge", json={"actor": "x", "action": "nobody-defined-this"}).json()
    assert after["tier"] == "deny" and after["matched"] is False
    assert after["policy_version"] == 2
    assert after["policy_digest"] != before["policy_digest"]

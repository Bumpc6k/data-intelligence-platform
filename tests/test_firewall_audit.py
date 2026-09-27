"""判定留痕（M2-03 / Issue #9）：落库成败不能影响判定。

本文件**不需要真数据库** —— 把 `dip_pg` 的记录函数换掉即可，验证的是"接口层怎么对待落库结果"。
真库的往返在 `test_pg_judgments.py`（标 `pg`，没有数据库就跳过）。
"""

from __future__ import annotations

import dip_pg
import pytest
from fastapi.testclient import TestClient
from firewall.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_db_probe(monkeypatch: pytest.MonkeyPatch):
    """健康检查会探数据库（连不上要等 connect_timeout）。测试里换掉，别让探测拖慢/打挂测试。"""
    monkeypatch.setattr("dip_pg.available", lambda: True)


def test_judgment_is_persisted_with_the_full_picture(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """留痕要记全：谁、什么动作、哪一档、依据、指纹、当时的策略版本。"""
    calls: list[dict] = []

    def fake_record(**kwargs):
        calls.append(kwargs)
        return dip_pg.Persisted(ok=True, id=1)

    monkeypatch.setattr("dip_pg.record_judgment", fake_record)
    body = client.post(
        "/judge",
        json={"actor": "么慌", "action": "insert", "target": "ads.ads_产销存月报", "sql": "insert into ads.t select 1"},
    ).json()

    assert body["persisted"] is True
    assert body["persist_error"] is None
    (recorded,) = calls
    assert recorded["actor"] == "么慌"
    assert recorded["action"] == "insert"
    assert recorded["target"] == "ads.ads_产销存月报"
    assert recorded["tier"] == "approval"
    assert recorded["disposition"] == "require_approval"
    assert recorded["matched"] is True
    assert recorded["matched_rules"] == ("production-write",)
    assert recorded["reason"], "依据不能是空的（审计要能回答「凭什么判成这样」）"
    assert recorded["fingerprint"] == body["fingerprint"]
    assert recorded["policy_version"] == body["policy_version"]
    assert recorded["policy_digest"] == body["policy_digest"]


def test_db_outage_does_not_affect_the_judgment(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """验收：**DB 不可用时判定不受影响，但要显式提示"未落库"**。

    这条是整件工作最要紧的行为：一个数据库抖动不该把安全判定带崩，
    但也绝不能悄悄不留痕 —— 调用方必须能从响应里看出"这次没记上"。
    """
    monkeypatch.setattr(
        "dip_pg.record_judgment",
        lambda **_: dip_pg.Persisted(ok=False, error="OperationalError: 数据库连接被拒"),
    )
    response = client.post("/judge", json={"actor": "么慌", "action": "truncate", "target": "ads.t"})

    assert response.status_code == 200, "判定照做，不该因为数据库挂了就报错"
    body = response.json()
    assert body["tier"] == "deny" and body["disposition"] == "reject", "判定结果必须照常给出"
    assert body["matched"] is True
    assert body["persisted"] is False, "必须显式提示未落库"
    assert "数据库连接被拒" in body["persist_error"]


def test_token_issue_and_verify_are_recorded(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """验收里的"令牌"那一档：签发与校验都要留痕，**被拒的那次也要**。"""
    events: list[dict] = []
    monkeypatch.setattr(
        "dip_pg.record_token_event",
        lambda **kwargs: (events.append(kwargs), dip_pg.Persisted(ok=True, id=len(events)))[1],
    )

    issued = client.post(
        "/tokens/issue",
        json={"approver": "组长", "actor": "么慌", "action": "insert", "target": "ads.t", "sql": "insert into ads.t select 1"},
    ).json()
    assert issued["persisted"] is True

    payload = {"token": issued["token"], "action": "insert", "target": "ads.t", "sql": "insert into ads.t select 1"}
    assert client.post("/verify", json=payload).status_code == 200
    assert client.post("/verify", json=payload).status_code == 403  # 二次使用被拒

    assert [(e["event"], e["ok"]) for e in events] == [
        ("issued", True),
        ("verified", True),
        ("verified", False),
    ], "被拒的那次必须也留痕，否则『谁试图用一张不合法的令牌』查不到"
    assert events[0]["approver"] == "组长", "审批人要记下来（「上一级确认一次」的那个人）"
    assert "已使用过" in events[2]["reason"]


def test_rejected_verify_is_stored_as_rejected(monkeypatch: pytest.MonkeyPatch):
    """落库层把 `verified + ok=False` 改写成 `rejected` —— 单测改写逻辑本身。

    不然的话，"校验失败"在库里全叫 verified、只看 token_event 分不出成败，只能靠 ok 反推。
    """
    captured: dict = {}

    def fake_insert(sql, params):
        captured["params"] = params
        return dip_pg.Persisted(ok=True, id=9)

    monkeypatch.setattr("dip_pg.store._insert", fake_insert)
    dip_pg.store.record_token_event(
        event="verified", actor="么慌", action="insert", ok=False, reason="令牌已过期"
    )
    assert "rejected" in captured["params"], "失败的校验必须落成 rejected"
    assert "令牌已过期" in captured["params"]


def test_verify_403_says_so_when_it_could_not_be_recorded(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """403 的响应体只有 detail，所以"未落库"要缀在原因里 —— 不能让调用方以为留痕了。"""
    monkeypatch.setattr(
        "dip_pg.record_token_event",
        lambda **_: dip_pg.Persisted(ok=False, error="OperationalError: 连不上"),
    )
    response = client.post("/verify", json={"token": "not-a-real-token", "action": "insert", "target": "ads.t"})
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert "不存在" in detail
    assert "未落库" in detail and "连不上" in detail


def test_health_reports_database_availability(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("dip_pg.available", lambda: False)
    assert client.get("/health").json()["db_available"] is False
    monkeypatch.setattr("dip_pg.available", lambda: True)
    assert client.get("/health").json()["db_available"] is True

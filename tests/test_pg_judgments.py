"""判定留痕的真库往返与审计接口（M2-03 / Issue #9）。

标了 `pg` 的用例需要真 PostgreSQL（`bash ops/start-pg.sh`），没有就跳过 ——
和 `smoke` 用例对待内核的方式一致：**不在就跳过，而不是假装通过**。

不标 `pg` 的那条用假 store 覆盖，验的是"数据库不可用时接口怎么说话"。
"""

from __future__ import annotations

import uuid
from typing import Any

import dip_pg
import pytest
from fastapi.testclient import TestClient
from portal_api.routers import audit as audit_router


def _pg_up() -> bool:
    try:
        return dip_pg.available()
    except Exception:  # noqa: BLE001
        return False


requires_pg = pytest.mark.skipif(not _pg_up(), reason="PostgreSQL 不在（127.0.0.1:15432），跳过")


# ---------------------------------------------------------------- 不需要真库：接口怎么说话


class _FakeJudgments:
    def __init__(self, *, up: bool) -> None:
        self._up = up

    def available(self) -> bool:
        return self._up

    def list_judgments(self, **_: Any) -> list[dict]:
        return [{"id": 1, "kind": "judge", "action": "select", "tier": "auto"}]


@pytest.fixture()
def client_with_fake():
    """造一个"数据库在/不在"的假 store 客户端。

    **必须清理 `dependency_overrides`**：不清理会泄漏到后面的真库用例上
    （第一版就踩了：真库用例拿到假 store 的行，`KeyError: 'actor'`）。
    """
    from portal_api.main import app

    def _make(*, up: bool) -> TestClient:
        app.dependency_overrides[audit_router.judgments_store] = lambda: _FakeJudgments(up=up)
        return TestClient(app)

    yield _make
    app.dependency_overrides.pop(audit_router.judgments_store, None)


def test_judgments_endpoint_says_so_when_the_database_is_down(client_with_fake):
    """数据库不可用时**明说未落库**，不返回一个看起来正常的空列表。

    "空列表"和"查不到因为没落库"是两件事：前者会让人以为"什么都没发生过"，
    后者才让人去查为什么没落库。
    """
    body = client_with_fake(up=False).get("/api/audit/judgments").json()
    assert body["success"] is False
    assert "未落库" in body["error"]
    assert body["items"] == []


def test_judgments_endpoint_returns_items_when_up(client_with_fake):
    body = client_with_fake(up=True).get("/api/audit/judgments").json()
    assert body["success"] is True
    assert body["items"][0]["action"] == "select"


# ---------------------------------------------------------------- 需要真库


@requires_pg
def test_four_judgments_are_queryable():
    """验收：**四次判定（自动 / 审批 / 拒绝 / 令牌）都能查到**。

    这条直接调 store，验的是落库与查询本身；下面再用 portal-api 的接口验一遍
    —— 验收说的是"能在接口里查到"，所以两条都要有。
    """
    dip_pg.init_schema()
    actor = f"验收-{uuid.uuid4().hex[:8]}"  # 每次跑用新标记，避免被历史数据干扰

    dip_pg.record_judgment(
        actor=actor, action="select", target="ads.ads_产销存月报",
        tier="auto", disposition="auto_pass", matched=True,
        matched_rules=("read-only",), reason="只读查询自动通过", fingerprint="fp-auto",
        policy_version=1, policy_digest="digest",
    )
    dip_pg.record_judgment(
        actor=actor, action="insert", target="ads.ads_产销存月报",
        tier="approval", disposition="require_approval", matched=True,
        matched_rules=("production-write",), reason="生产库写操作需要审批", fingerprint="fp-appr",
        policy_version=1, policy_digest="digest",
    )
    dip_pg.record_judgment(
        actor=actor, action="truncate", target="ads.ads_产销存月报",
        tier="deny", disposition="reject", matched=True,
        matched_rules=("destructive-ddl",), reason="破坏性操作不可审批", fingerprint="fp-deny",
        policy_version=1, policy_digest="digest",
    )
    dip_pg.record_token_event(
        event="issued", actor=actor, action="insert", ok=True, approver="组长",
        reason="审批通过，签发令牌", fingerprint="fp-appr",
    )

    mine = [row for row in dip_pg.list_judgments(limit=500) if row["actor"] == actor]
    assert len(mine) == 4, f"应当查到 4 条，实际 {len(mine)}"
    assert {row["tier"] for row in mine if row["kind"] == "judge"} == {"auto", "approval", "deny"}
    token_rows = [row for row in mine if row["kind"] == "token"]
    assert len(token_rows) == 1
    assert token_rows[0]["token_event"] == "issued"
    assert token_rows[0]["approver"] == "组长"
    assert token_rows[0]["tier"] is None, "令牌行不该有档位（tier 只表示判定结果）"
    # 依据与指纹都要落下来：审计的价值就在这两列
    assert all(row["reason"] for row in mine)
    assert all(row["fingerprint"] for row in mine)


@requires_pg
def test_judgments_are_visible_through_the_audit_endpoint():
    """验收：四次判定**在接口里**查得到（走 portal-api 的 `/api/audit/judgments`）。"""
    from portal_api.main import app

    dip_pg.init_schema()
    actor = f"接口验收-{uuid.uuid4().hex[:8]}"
    dip_pg.record_judgment(
        actor=actor, action="truncate", target="ads.t", tier="deny", disposition="reject",
        matched=True, matched_rules=("destructive-ddl",), reason="破坏性操作不可审批",
        fingerprint="fp", policy_version=1, policy_digest="digest",
    )

    body = TestClient(app).get("/api/audit/judgments", params={"limit": 200}).json()
    assert body["success"] is True
    mine = [row for row in body["items"] if row["actor"] == actor]
    assert len(mine) == 1
    row = mine[0]
    assert row["kind"] == "judge"
    assert row["action"] == "truncate"
    assert row["tier"] == "deny"
    assert row["disposition"] == "reject"
    assert row["matched"] is True
    assert row["reason"] == "破坏性操作不可审批"
    assert row["created_at"], "时间必须落下来（审计三要素：谁、什么时候、做了什么）"


@requires_pg
def test_pg_filtering_works():
    """按 kind / action 过滤可用（判定视图要能按动作筛）。"""
    dip_pg.init_schema()
    actor = f"筛选验收-{uuid.uuid4().hex[:8]}"
    action = f"action-{uuid.uuid4().hex[:6]}"
    dip_pg.record_judgment(
        actor=actor, action=action, tier="auto", disposition="auto_pass", matched=True,
        reason="只读", fingerprint="fp",
    )
    dip_pg.record_token_event(event="issued", actor=actor, action=action, ok=True, reason="签发")

    judge_rows = dip_pg.list_judgments(limit=50, kind="judge", action=action)
    token_rows = dip_pg.list_judgments(limit=50, kind="token", action=action)
    assert len(judge_rows) == 1
    assert len(token_rows) == 1
    assert judge_rows[0]["kind"] == "judge" and token_rows[0]["kind"] == "token"
    # 审计视图要用的列一个都不能少
    assert {"kind", "actor", "action", "target", "tier", "disposition", "matched", "reason",
            "fingerprint", "created_at"} <= set(judge_rows[0])


@requires_pg
def test_persist_failure_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch):
    """落库真的失败时（这里用坏 DSN 模拟），返回失败结果而**不抛异常**。"""
    monkeypatch.setenv("DIP_PG_DSN", "postgresql://dip:dip@127.0.0.1:1/dip")
    result = dip_pg.record_judgment(
        actor="x", action="select", tier="auto", disposition="auto_pass", matched=True
    )
    assert result.ok is False
    assert result.id is None
    assert result.error, "失败要带上原因，否则『未落库』就成了没有信息的一句话"

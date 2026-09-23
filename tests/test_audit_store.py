"""审计与会话存储测试（W-117 / W-112）。

两层：
  ① `test_audit_api_*`：用**假存储**覆盖依赖，不连数据库（可在任何环境跑）
  ② `test_store_*`：连真实 PostgreSQL，仅在库可用时执行（否则跳过，不阻塞）
"""

from __future__ import annotations

import pytest
from dip_agent import Agent
from fakes import FakeKernel
from fastapi.testclient import TestClient
from portal_api import store as store_mod
from portal_api.deps import get_agent
from portal_api.main import app
from portal_api.routers import agent as agent_router
from portal_api.routers import audit as audit_router

API_BASE = {"text": "ads.ads_产销存月报 的产量怎么来的？", "session_id": "s-test"}


class FakeStore:
    """假存储：记录调用，返回固定行，用来验证"接口与落库时机"而不碰 PG。"""

    def __init__(self, *, available: bool = True, tables: list[str] | None = None) -> None:
        self._available = available
        self.recorded: list[tuple[str, str, dict]] = []
        self.seeded: list[tuple[str, list[str]]] = []
        self.tables = tables or []

    def available(self) -> bool:
        return self._available

    def record_ask(self, session_id: str, question: str, answer: dict) -> int:
        self.recorded.append((session_id, question, answer))
        return 42

    def last_tables(self, session_id: str) -> list[str]:
        return self.tables

    def list_audit(self, session_id: str | None = None, limit: int = 50) -> list[dict]:
        return [{"id": 1, "session_id": session_id or "s-test", "question": API_BASE["text"],
                 "status": "verified", "evidence_count": 3, "kernel_calls": 3, "total_ms": 21}]

    def list_sessions(self, limit: int = 20) -> list[dict]:
        return [{"id": "s-test", "title": "产量怎么来的", "turns": 2}]

    def session_history(self, session_id: str, limit: int = 50) -> list[dict]:
        return [{"role": "user", "text": API_BASE["text"]}, {"role": "assistant", "text": "产量 = …"}]


@pytest.fixture()
def api_client():
    fake_kernel = FakeKernel()
    fake_store = FakeStore()
    app.dependency_overrides[get_agent] = lambda: Agent(fake_kernel)
    app.dependency_overrides[agent_router.store] = lambda: fake_store
    app.dependency_overrides[audit_router.store] = lambda: fake_store
    yield TestClient(app), fake_store
    app.dependency_overrides.clear()


# ---------------------------------------------------------------- ① 接口层（不连库）


def test_ask_writes_audit_and_returns_audit_id(api_client):
    client, fake_store = api_client
    r = client.post("/api/agent/ask", json=API_BASE)
    assert r.status_code == 200
    body = r.json()
    assert body["audit_id"] == "aud-42", "回答里要带审计号，前端才能链到留痕"
    assert len(fake_store.recorded) == 1
    session_id, question, answer = fake_store.recorded[0]
    assert session_id == "s-test" and question == API_BASE["text"]
    assert answer["result"]["status"] == "verified" and len(answer["result"]["evidence"]) == 3


def test_ask_restores_context_from_db(api_client):
    """刷新页面/重启服务后追问不断：库里有上次的表名就先回填给编排层。"""
    client, fake_store = api_client
    fake_store.tables = ["ads.ads_产销存月报"]
    r = client.post("/api/agent/ask", json={"text": "那它的上游还有哪些表？", "session_id": "s-test"})
    assert r.status_code == 200
    assert r.json()["result"]["value"] is not None, "回填了表名就不该再反问要定位"


def test_ask_still_works_when_db_unavailable():
    """数据库挂了不能让问答跟着挂：回答照出，只是没有 audit_id。"""
    app.dependency_overrides[get_agent] = lambda: Agent(FakeKernel())
    app.dependency_overrides[agent_router.store] = lambda: FakeStore(available=False)
    try:
        r = TestClient(app).post("/api/agent/ask", json=API_BASE)
        assert r.status_code == 200
        assert r.json()["audit_id"] is None
        assert r.json()["result"]["status"] == "verified"
    finally:
        app.dependency_overrides.clear()


def test_audit_endpoints_surface_db_state(api_client):
    client, _ = api_client
    audit = client.get("/api/audit", params={"session_id": "s-test"}).json()
    assert audit["success"] and audit["items"][0]["kernel_calls"] == 3
    sessions = client.get("/api/sessions").json()
    assert sessions["success"] and sessions["items"][0]["turns"] == 2
    detail = client.get("/api/sessions/s-test").json()
    assert detail["success"] and len(detail["messages"]) == 2


def test_audit_reports_unavailable_db():
    app.dependency_overrides[audit_router.store] = lambda: FakeStore(available=False)
    try:
        body = TestClient(app).get("/api/audit").json()
        assert body["success"] is False and "不可用" in body["error"]
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------- ② 真实 PostgreSQL


requires_pg = pytest.mark.skipif(not store_mod.available(), reason="PostgreSQL 未运行（bash ops/start-pg.sh）")


@requires_pg
def test_store_roundtrip_against_real_postgres():
    store_mod.init_schema()
    session_id = "pytest-store-roundtrip"
    answer = {
        "text": "产量 = 打码量 + 跳码量 - 重码量",
        "mode": "rule",
        "result": {
            "status": "verified",
            "confidence": 0.9,
            "version": "kb:1.0.0@2026-09-20",
            "value": {"type": "formula", "display": "产量 = 打码量 + 跳码量 - 重码量"},
            "evidence": [
                {"type": "lineage", "ref": "graph:ads.ads_产销存月报", "summary": "上游 9 张 / 13 条边"},
                {"type": "metric", "ref": "metric:产量@chanliang_qty", "summary": "口径在 dwd 层"},
            ],
        },
        "tool_calls": [
            {"name": "search", "ms": 13, "ok": True, "endpoint": "POST /kb/search"},
            {"name": "upstream", "ms": 1, "ok": True, "endpoint": "POST /upstream"},
        ],
    }
    audit_id = store_mod.record_ask(session_id, "ads.ads_产销存月报 的产量怎么来的？", answer)
    assert audit_id > 0

    rows = store_mod.list_audit(session_id)
    assert rows and rows[0]["status"] == "verified"
    assert rows[0]["evidence_count"] == 2 and rows[0]["kernel_calls"] == 2
    assert rows[0]["total_ms"] == 14, "总耗时 = 各工具耗时之和"
    assert rows[0]["value_display"] == "产量 = 打码量 + 跳码量 - 重码量"

    history = store_mod.session_history(session_id)
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert store_mod.last_tables(session_id)[0] == "ads.ads_产销存月报", "能从证据里取回表名供追问回填"
    assert any(s["id"] == session_id for s in store_mod.list_sessions())

"""HTTP 层契约测试（W-101/W-114/W-118）：问答接口 + 报告代理。

用依赖注入把假内核塞进去，所以不依赖内核服务；真实内核的端到端在 tests/test_api_live.py。
"""

from __future__ import annotations

from dip_agent import Agent
from fakes import FakeKernel
from fastapi.testclient import TestClient
from portal_api.deps import get_agent, get_client
from portal_api.main import app

client = TestClient(app)
fake = FakeKernel()


def setup_module(module):  # noqa: ANN001
    app.dependency_overrides[get_agent] = lambda: Agent(fake)
    app.dependency_overrides[get_client] = lambda: fake


def teardown_module(module):  # noqa: ANN001
    app.dependency_overrides.clear()


def test_health_endpoint():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["service"] == "portal-api"


def test_ask_returns_unified_result_model():
    r = client.post("/api/agent/ask", json={"text": "ads.ads_产销存月报 的产量怎么来的？"})
    assert r.status_code == 200, r.text
    body = r.json()
    # 前端只认这个结构（《设计说明书》§8）
    assert set(body) >= {"answer_id", "text", "result", "suggestions", "tool_calls", "mode"}
    assert set(body["result"]) >= {"value", "confidence", "status", "evidence", "source", "version"}
    assert body["result"]["status"] == "verified"
    assert body["result"]["value"]["type"] == "formula"
    assert len(body["result"]["evidence"]) == 3
    assert [c["name"] for c in body["tool_calls"]] == ["search", "search", "upstream"]
    assert body["mode"] == "rule"


def test_ask_unknown_entity_returns_unresolved_not_5xx():
    r = client.post("/api/agent/ask", json={"text": "你好呀"})
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["status"] == "unresolved"
    assert body["result"]["evidence"] == [] and body["result"]["value"] is None


def test_ask_validates_input():
    assert client.post("/api/agent/ask", json={"text": ""}).status_code == 422
    assert client.post("/api/agent/ask", json={"text": "x", "mode": "magic"}).status_code == 422


def test_reports_list_and_proxy():
    r = client.get("/api/reports/")
    assert r.status_code == 200 and r.json()["total"] == 1
    html = client.get("/api/reports/rpt_demo")
    assert html.status_code == 200 and "血缘报告" in html.text
    assert html.headers["content-type"].startswith("text/html")


def test_report_proxy_rejects_path_traversal():
    for bad in ["..%2Fetc%2Fpasswd", "a/../b"]:
        assert client.get(f"/api/reports/{bad}").status_code in (400, 404)


def test_evidence_empty_never_carries_value():
    """铁律 1 在 HTTP 层也要成立：任何返回里 evidence 为空 ⇒ 不许有结论值。"""
    r = client.post("/api/agent/ask", json={"text": "ads.ads_产销存月报 的产量怎么来的？"})
    body = r.json()
    res = body["result"]
    if not res["evidence"]:
        assert res["value"] is None
    assert res["status"] in {"verified", "inferred", "candidate", "unresolved", "stale"}

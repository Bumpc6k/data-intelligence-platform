"""文档通道的接口层测试（M3-04 / Issue #15）—— 全离线，用假通道。

钉四件事：

1. `/api/knowledge/doc-search` 的 `usable_for_conclusion` 恒为 `false`，且每条材料都带来源（验收②）；
2. 材料缺来源时**整个请求失败并列问题**，不是"过滤掉没来源的、只回合规的"；
3. 通道没启用时明确 503（"没搜到" ≠ "通道没开"）；
4. **`/api/agent/ask` 带 `with_docs` 时，结论与不带时逐字节相同**（验收①），
   文档材料只出现在 `background`；通道失败只多一条 `tool_calls`，不影响结论。
"""

from __future__ import annotations

from dip_agent import Agent
from dip_contracts.models import DocCitation, DocHit
from fakes import FakeKernel
from fastapi.testclient import TestClient
from portal_api.deps import get_agent, get_client
from portal_api.main import app
from portal_api.routers.doc_channel import get_doc_channel

client = TestClient(app)

ASK = {"text": "ads.ads_产销存月报 的产量怎么来的？", "session_id": "doc-test"}


class FakeDocChannel:
    """假文档通道：只实现 `search`，其余（网络、MCP 协议）都不碰。"""

    url = "http://127.0.0.1:18300/mcp/fake-endpoint"

    def __init__(self, hits=None, error: Exception | None = None) -> None:
        self.hits = hits if hits is not None else [_hit()]
        self.error = error

    async def search(self, query: str, *, limit: int | None = None):  # noqa: ANN201
        if self.error is not None:
            raise self.error
        return self.hits

    def describe(self) -> dict:
        return {"channel": "documents", "url": self.url, "tool_group": "retrieve"}


def _hit(*, document: str = "产销存月报口径说明.md", chunk: str = "chunk-7",
         text: str = "库存增量 = 产量 − 销量；月报里按厂区汇总。") -> DocHit:
    return DocHit(
        text=text,
        citation=DocCitation(document_name=document, chunk_id=chunk, knowledge_id="doc-1",
                             knowledge_base_id="kb-1", url="http://127.0.0.1:18380/doc/doc-1"),
        score=0.81,
    )


def setup_function(_func):  # noqa: ANN001
    app.dependency_overrides[get_agent] = lambda: Agent(FakeKernel())
    app.dependency_overrides[get_client] = lambda: FakeKernel()


def teardown_function(_func):  # noqa: ANN001
    app.dependency_overrides.clear()


def _use(channel):  # noqa: ANN001, ANN202
    app.dependency_overrides[get_doc_channel] = lambda: channel


# ---------------------------------------------------------------- doc-search


def test_文档检索只回背景且每条带来源():
    _use(FakeDocChannel([_hit(), _hit(document="产量口径.md", chunk="chunk-9", text="产量含打码、跳码、重码")]))
    r = client.post("/api/knowledge/doc-search", json={"query": "库存增量怎么算"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channel"] == "documents"
    assert body["usable_for_conclusion"] is False           # 验收①：不能当结论
    assert body["count"] == 2
    assert body["citations"] == ["产销存月报口径说明.md#chunk-7", "产量口径.md#chunk-9"]
    for hit in body["hits"]:                                 # 验收②：每条都能指到出处
        assert hit["citation"]["document_name"] and hit["citation"]["chunk_id"]
    assert "仅作参考，不作为结论" in body["rendered"]


def test_缺来源的材料整体拒收而不是被过滤():
    _use(FakeDocChannel([_hit(), _hit(document="", chunk="chunk-1")]))
    r = client.post("/api/knowledge/doc-search", json={"query": "库存增量怎么算"})

    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["problems"][0]["code"] == "citation_missing_document"
    assert "拒收" in detail["message"]


def test_通道没启用时明确报未启用():
    _use(None)
    r = client.post("/api/knowledge/doc-search", json={"query": "库存增量怎么算"})
    assert r.status_code == 503
    assert "未启用" in r.json()["detail"]


def test_通道故障如实报错而不是空结果():
    _use(FakeDocChannel(error=RuntimeError("connect timeout")))
    r = client.post("/api/knowledge/doc-search", json={"query": "库存增量怎么算"})
    assert r.status_code == 502
    assert "文档通道调用失败" in r.json()["detail"]


def test_通道状态不含令牌且只开只读组():
    _use(FakeDocChannel())
    r = client.get("/api/knowledge/doc-channel")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["tool_group"] == "retrieve"
    assert body["usable_for_conclusion"] is False
    assert "token" not in r.text.lower()


# ---------------------------------------------------------------- /agent/ask 双通道


def test_带文档的问答结论与不带时完全一致():
    app.dependency_overrides[get_doc_channel] = lambda: None
    baseline = client.post("/api/agent/ask", json=ASK).json()

    _use(FakeDocChannel())
    with_docs = client.post("/api/agent/ask", json={**ASK, "with_docs": True}).json()

    # 验收①：结论（含证据链）逐字段相同 —— 文档通道不许动结论
    assert with_docs["result"] == baseline["result"]
    assert with_docs["text"] == baseline["text"]

    # 文档材料只在 background，且每条带来源（验收②）
    assert with_docs["background"]["usable_for_conclusion"] is False
    assert with_docs["background"]["hits"][0]["citation"]["chunk_id"] == "chunk-7"

    # 工具步骤条上能看见"问过文档通道"
    call = with_docs["tool_calls"][-1]
    assert call["name"] == "search_knowledge" and call["ok"] is True
    assert baseline.get("background") is None


def test_文档通道失败只留一条失败记录不影响结论():
    app.dependency_overrides[get_doc_channel] = lambda: None
    baseline = client.post("/api/agent/ask", json=ASK).json()

    _use(FakeDocChannel(error=RuntimeError("mcp unreachable")))
    r = client.post("/api/agent/ask", json={**ASK, "with_docs": True})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == baseline["result"]        # 结论照旧
    assert body["background"] is None                  # 没材料就不装
    call = body["tool_calls"][-1]
    assert call["name"] == "search_knowledge" and call["ok"] is False
    assert "mcp unreachable" in call["error"]


def test_缺来源时也不影响结论且记录下来():
    _use(FakeDocChannel([_hit(chunk="")]))
    r = client.post("/api/agent/ask", json={**ASK, "with_docs": True})

    assert r.status_code == 200
    body = r.json()
    assert body["background"] is None
    call = body["tool_calls"][-1]
    assert call["ok"] is False and "缺来源" in call["error"]


def test_不问文档时行为完全不变():
    """默认 `with_docs=False`：老调用方的请求体与响应体都不受影响。"""
    _use(FakeDocChannel())
    body = client.post("/api/agent/ask", json=ASK).json()
    assert body["background"] is None
    assert all(call["name"] != "search_knowledge" for call in body["tool_calls"])

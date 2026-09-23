"""内核客户端测试：单元行为 + 用**真实录制响应**做契约校验。

fixture 来源：`tests/fixtures/kernel-probe-2026-09-23.json`
（2026-09-23 对运行中的内核（:18080）发真实请求录下的原始响应，未经加工）。
这样即使内核服务不在，也能校验「我们对内核响应的假设」是否还成立——
一旦内核改了响应形状，这里会红，而不是等到联调才发现。
"""

from __future__ import annotations

import json
import pathlib

import httpx
import pytest
from lineage_client import LineageClient

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "kernel-probe-2026-09-23.json"
REAL = json.loads(FIXTURE.read_text(encoding="utf-8"))["endpoints"]


def make_client(handler, **kw) -> LineageClient:
    return LineageClient("http://kernel.test", transport=httpx.MockTransport(handler), **kw)


# ---------------------------------------------------------------- 单元行为


def test_success_maps_to_ok_result():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"success": True, "value": 42})

    with make_client(handler) as c:
        r = c.analyze("SELECT 1", dialect="hive", depth=2)
    assert r.ok is True
    assert r.data["value"] == 42
    assert r.endpoint == "POST /analyze"
    assert r.http_status == 200 and r.attempts == 1
    assert isinstance(r.ms, int) and r.ms >= 0
    assert seen["body"] == {"mode": "sql", "sql": "SELECT 1", "dialect": "hive", "depth": 2}


def test_http_200_with_success_false_is_failure():
    """内核最常见的坑：HTTP 200 + success=false（实测 /kb/search 传错参数就是这样）。"""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "error": "query 不能为空"})

    with make_client(handler) as c:
        r = c.search("产量")
    assert r.ok is False
    assert r.error is not None and "query 不能为空" in r.error
    assert r.http_status == 200
    # 关键：失败不抛异常，编排层要把它记进审计与工具步骤条
    assert r.data["success"] is False


def test_http_error_is_reported_without_retry():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    with make_client(handler, retries=2) as c:
        r = c.impact("cdw.dws_产销存汇总")
    assert r.ok is False and r.http_status == 500
    assert calls["n"] == 1, "HTTP 5xx 不应重试（重试只针对连接类错误）"
    assert "HTTP 500" in (r.error or "")


def test_transport_error_retries_then_reports():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("connection refused", request=req)

    with make_client(handler, retries=2) as c:
        r = c.health()
    assert r.ok is False
    assert calls["n"] == 3, "1 次 + 2 次重试"
    assert r.attempts == 3
    assert "重试" in (r.error or "") or "连接失败" in (r.error or "")


def test_param_normalization_matches_kernel_reality():
    """业务层写 search/metric，客户端负责换成内核的真实参数名。"""
    bodies = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append((req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"success": True})

    with make_client(handler) as c:
        c.search("产量")
        c.metric("产量")
        c.upstream("ads.ads_产销存月报", depth=5)
    assert bodies[0] == ("/kb/search", {"query": "产量", "limit": 20})
    assert bodies[1] == ("/kb/metric", {"name": "产量"})
    assert bodies[2] == ("/upstream", {"table": "ads.ads_产销存月报", "depth": 5})


def test_report_url_and_base_url_normalization():
    with make_client(lambda req: httpx.Response(200, json={"success": True})) as c:
        assert c.report_url("rpt_1") == "http://kernel.test/report/rpt_1"
    c2 = LineageClient("http://127.0.0.1:18080/")
    assert c2.base_url == "http://127.0.0.1:18080"
    c2.close()
    with pytest.raises(ValueError):
        LineageClient("")


# ------------------------------------------------- 用真实录制响应做契约校验


def _replay(name: str):
    """把录制的真实响应喂给客户端，走一遍真实解析路径。"""
    recorded = REAL[name]["response"]

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == REAL[name]["request"]["path"]
        return httpx.Response(200, json=recorded)

    return recorded, handler


@pytest.mark.parametrize(
    "name,method,args",
    [
        ("analyze", "analyze", ("INSERT INTO t SELECT 1",)),
        ("upstream", "upstream", ("ads.ads_产销存月报",)),
        ("impact", "impact", ("cdw.dws_产销存汇总",)),
        ("kb_search", "search", ("产量",)),
        ("kb_summary", "kb_summary", ()),
        ("kb_ask", "ask", ("产量怎么算",)),
    ],
)
def test_recorded_real_response_roundtrip(name, method, args):
    recorded, handler = _replay(name)
    with make_client(handler) as c:
        r = getattr(c, method)(*args)
    assert r.ok is True, r.error
    assert r.data == recorded, "客户端不得改写内核返回的原始 data"


def test_recorded_analyze_has_fields_platform_depends_on():
    """平台对 /analyze 的依赖点必须是真实存在的（缺了就是 P0 缺陷，不是'yet'）。"""
    recorded = REAL["analyze"]["response"]
    assert recorded["column_lineage"], "字段血缘必须有"
    cl = recorded["column_lineage"][0]
    for key in ("target_table", "target_column", "source_table", "source_column", "expression", "resolved"):
        assert key in cl, f"字段血缘缺 {key}"
    out_qty = [e for e in recorded["column_lineage"] if e["target_column"] == "output_qty"]
    assert out_qty, "ads.ads_产销存月报.output_qty 的血缘条目必须存在"
    assert out_qty[0]["source_table"] == "cdw.dws_产销存汇总"
    # 口径命中：公式 + 置信度 + 来源脚本（**没有 status/version/行号** —— 见 ADR-0002/0003）
    metrics = recorded["knowledge"]["metrics"]
    assert metrics and all("formula" in m and "confidence" in m for m in metrics)
    assert any("source_script" in m for m in metrics)
    for absent in ("status", "version"):
        assert absent not in recorded["knowledge"]["metrics"][0], f"内核一旦提供 {absent} 就该改 ADR-0002"
    assert "report_url" in recorded and recorded["report_url"].startswith("http")


def test_recorded_upstream_shape():
    recorded = REAL["upstream"]["response"]
    assert recorded["direction"] == "upstream"
    assert recorded["upstream_count"] > 0
    assert isinstance(recorded["levels"], list) and recorded["levels"][0]["level"] == 1
    assert recorded["tables"], "上游表清单必须非空"
    assert recorded["paths"], "路径列表必须非空"


def test_recorded_kb_search_groups_are_the_evidence_pool():
    """证据挑选（§4.3）依赖这 5 组命中 + score；少了哪组要显式知道。"""
    recorded = REAL["kb_search"]["response"]
    groups = recorded["groups"]
    for kind in ("metrics", "fields", "tables", "terms", "rules"):
        assert kind in groups, f"检索结果缺 {kind} 组"
    assert recorded["counts"]["metrics"] > 0
    field_hit = [f for f in groups["fields"] if f["table_name"] == "ads.ads_产销存月报"]
    assert field_hit, "该表字段命中必须存在（用于把中文名'产量'挂到 output_qty 上）"
    assert field_hit[0]["chinese_name"] == "产量"
    assert field_hit[0]["chinese_source"] == "exact_glossary"  # → status 判定为 verified（§4.2）

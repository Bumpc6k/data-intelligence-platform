"""内核冒烟测试：**只有内核服务在跑时才执行**（不在则自动跳过，不阻塞 CI）。

跑法：
    bash /usr/local/bin/start-lineage-api.sh     # 内核仓库里执行，起 :18080
    make smoke
"""

from __future__ import annotations

import json
import os
import pathlib
import urllib.request

import pytest
from lineage_client import LineageClient

BASE = os.environ.get("LINEAGE_BASE", "http://127.0.0.1:18080")
DEMO_SQL = pathlib.Path(
    os.environ.get(
        "LINEAGE_DEMO_SQL",
        os.path.expanduser("~/projects/sql-lineage-mvp/docs/ds_demo_workflows/sql/wf_ads_报表/t_ads_产销存月报.sql"),
    )
)

pytestmark = pytest.mark.smoke


def kernel_alive() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=3) as r:  # noqa: S310
            return r.status == 200
    except Exception:
        return False


requires_kernel = pytest.mark.skipif(not kernel_alive(), reason=f"内核服务未运行（{BASE}）")


@requires_kernel
def test_health_and_kb_summary():
    with LineageClient(BASE) as c:
        h = c.health()
        s = c.kb_summary()
    assert h.ok, h.error
    assert s.ok, s.error
    assert s.data["counts"]["kb_metrics"] > 0, "口径库不应为空（空库说明内核没准备好演示数据）"


@requires_kernel
def test_analyze_and_upstream_end_to_end():
    sql = DEMO_SQL.read_text(encoding="utf-8")
    with LineageClient(BASE) as c:
        a = c.analyze(sql, dialect="hive")
        assert a.ok, a.error
        assert a.data["column_lineage_count"] > 0
        out_qty = [e for e in a.data["column_lineage"] if e["target_column"] == "output_qty"]
        assert out_qty and out_qty[0]["source_table"] == "cdw.dws_产销存汇总"

        up = c.upstream("ads.ads_产销存月报", depth=5)
        assert up.ok and up.data["upstream_count"] > 0
        imp = c.impact("cdw.dws_产销存汇总")
        assert imp.ok and imp.data["downstream_count"] > 0

    # 落盘的报告 URL 必须真的能打开（内核行为：/analyze 会生成 HTML 报告）
    report_url = a.data["report_url"].replace("localhost", "127.0.0.1")
    with urllib.request.urlopen(report_url, timeout=10) as r:  # noqa: S310
        html = r.read().decode("utf-8", "replace")
    assert r.status == 200 and "血缘" in html


@requires_kernel
def test_unknown_parameter_surfaces_kernel_error_text():
    """故意用错参数名，验证「HTTP 200 + success=false」被正确转成失败结果。"""
    with LineageClient(BASE) as c:
        r = c._call("POST", "/kb/search", {"keyword": "产量"})  # 故意的：内核要 query
    assert r.ok is False
    assert r.http_status == 200
    assert "query" in (r.error or ""), "必须保留内核的原始 error 文本，便于区分钟平台/内核问题"
    assert json.loads(json.dumps(r.data))["success"] is False

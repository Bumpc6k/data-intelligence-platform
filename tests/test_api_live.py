"""真内核的 HTTP 端到端（冒烟）：把 uvicorn 起过的同样请求走一遍。"""

from __future__ import annotations

import os
import urllib.request

import pytest

BASE = os.environ.get("PORTAL_API_BASE", "http://127.0.0.1:18100")


def _alive() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=2) as r:  # noqa: S310
            return r.status == 200
    except Exception:
        return False


@pytest.mark.smoke
@pytest.mark.skipif(not _alive(), reason=f"portal-api 未运行（{BASE}）")
def test_ask_over_http_against_real_kernel():
    import json

    req = urllib.request.Request(
        BASE + "/api/agent/ask",
        data=json.dumps({"text": "ads.ads_产销存月报 的产量怎么来的？"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
        body = json.loads(r.read().decode("utf-8"))
    assert body["result"]["status"] == "verified"
    assert body["result"]["value"]["display"] == "产量 = 打码量 + 跳码量 - 重码量"
    assert any("dwd" in (e["summary"] or "") for e in body["result"]["evidence"])

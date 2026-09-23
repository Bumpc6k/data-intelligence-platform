"""测试用假内核：按方法名回放**录制的真实响应**（不需要起内核服务）。"""

from __future__ import annotations

import json
import pathlib

from lineage_client import ToolResult

FIXTURE = pathlib.Path(__file__).parents[1] / "integrations/lineage-client/tests/fixtures/kernel-probe-2026-09-23.json"
REAL = json.loads(FIXTURE.read_text(encoding="utf-8"))["endpoints"]


class FakeKernel:
    def __init__(self, *, fail_all: bool = False) -> None:
        self.calls: list[tuple[str, object]] = []
        self.fail_all = fail_all

    def _res(self, endpoint: str, key: str) -> ToolResult:
        if self.fail_all:
            return ToolResult(False, endpoint, 3, {}, f"连接失败：内核不可达（{endpoint}）")
        return ToolResult(True, endpoint, 7, REAL[key]["response"])

    def search(self, keyword: str, limit: int = 20) -> ToolResult:
        self.calls.append(("search", keyword))
        return self._res("POST /kb/search", "kb_search")

    def upstream(self, table: str, depth: int = 5, graph: str | None = None) -> ToolResult:
        self.calls.append(("upstream", table))
        return self._res("POST /upstream", "upstream")

    def impact(self, table: str, direction: str = "downstream") -> ToolResult:
        self.calls.append(("impact", table))
        return self._res("POST /impact", "impact")

    def analyze(self, sql: str, dialect: str = "hive", depth: int = 3) -> ToolResult:
        self.calls.append(("analyze", len(sql)))
        return self._res("POST /analyze", "analyze")

    def kb_summary(self) -> ToolResult:
        self.calls.append(("kb_summary", None))
        return self._res("POST /kb/summary", "kb_summary")

    def ask(self, question: str) -> ToolResult:
        self.calls.append(("ask", question))
        return self._res("POST /kb/ask", "kb_ask")

    # 报告代理用
    def list_reports(self) -> ToolResult:
        self.calls.append(("list_reports", None))
        return ToolResult(True, "GET /reports", 3, {"success": True, "reports": [{"report_id": "rpt_demo"}], "total": 1})

    def report_url(self, report_id: str) -> str:
        return f"http://kernel.test/report/{report_id}"

    def raw_get(self, url: str) -> str | None:
        return f"<html><body>血缘报告 {url}</body></html>"

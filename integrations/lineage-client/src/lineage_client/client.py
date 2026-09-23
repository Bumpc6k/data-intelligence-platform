"""内核客户端（工作项 W-113）——平台与数据智能内核之间的**唯一耦合面**。

设计要点（来自对内核真实响应的探针结论，见《B2 接口设计与评审》§1.2/§3）：

1. **`success` 判定优先于状态码**：内核失败时同样返回 HTTP 200，body 里 `success=false` + `error`。
2. **参数差异关在这里**：`/kb/search` 要 `query`、`/kb/metric` 要 `name`、`/upstream` 要 `table`+`depth`；
   业务层只写 `search("产量")`，不关心内核的参数名。
3. **一律返回 `ToolResult` 而不是抛异常**：编排层需要把失败也记进审计与「工具步骤条」，
   异常只留给程序员错误（如 base_url 未配）。
4. **只读优先**：本客户端当前只封装只读端点；写类端点（`/generate/*`）在 P3 按「草稿→审核→执行」单独设计。

用法：

    from lineage_client import LineageClient
    c = LineageClient("http://127.0.0.1:18080")
    r = c.analyze(sql, dialect="hive")
    if r.ok:
        print(r.data["column_lineage_count"], r.data["report_url"])
    else:
        print("失败：", r.error)   # 内核原文，便于定位是平台还是内核的问题
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from dip_contracts import ToolResult

from .errors import KernelAPIError, KernelHTTPError

DEFAULT_BASE_URL = "http://127.0.0.1:18080"

# 各端点的默认超时（秒）：分析类会解析 SQL + 落盘报告，给长一些
TIMEOUTS: dict[str, float] = {
    "/analyze": 30.0,
    "/analyze-workflow": 60.0,
    "/parse": 20.0,
    "/report": 30.0,
    "/upstream": 10.0,
    "/impact": 10.0,
    "/kb/search": 10.0,
    "/kb/metric": 10.0,
    "/kb/ask": 15.0,
    "/kb/summary": 10.0,
    "/health": 5.0,
    "/reports": 10.0,
}


class LineageClient:
    """数据智能内核的 HTTP 客户端（只读）。"""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        retries: int = 1,
        timeout: float | None = None,
        transport: httpx.BaseTransport | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url 不能为空（例如 http://127.0.0.1:18080）")
        self.base_url = base_url.rstrip("/")
        self.retries = max(0, retries)
        self.default_timeout = timeout or 15.0
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Content-Type": "application/json", **(headers or {})},
            transport=transport,
            timeout=self.default_timeout,
            # 内核是本机/内网服务：不要被 http_proxy / ALL_PROXY 影响
            # （系统里的 SOCKS 代理会让 httpx 抛 socksio 缺失，而不是干净地连不上）
            trust_env=False,
        )

    # ---------- 底层 ----------
    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> ToolResult:
        endpoint = f"{method} {path}"
        timeout = TIMEOUTS.get(path, self.default_timeout)
        last_err: str | None = None
        started = time.perf_counter()

        for attempt in range(1, self.retries + 2):  # 1 次 + retries 次重试
            try:
                resp = self._client.request(method, path, json=payload, timeout=timeout)
            except httpx.TimeoutException as exc:  # 可重试
                last_err = f"超时（{timeout}s）：{exc!s}"
                continue
            except httpx.TransportError as exc:  # 连接被拒/重置，可重试
                last_err = f"连接失败：{exc!s}"
                continue

            ms = int((time.perf_counter() - started) * 1000)
            if resp.status_code != 200:
                err = KernelHTTPError(resp.status_code, path, resp.text)
                return ToolResult(False, endpoint, ms, {}, str(err), resp.status_code, attempt)

            try:
                body = resp.json()
            except ValueError:
                return ToolResult(False, endpoint, ms, {}, "响应不是合法 JSON", resp.status_code, attempt)

            if not isinstance(body, dict):
                return ToolResult(False, endpoint, ms, {}, "响应顶层不是对象", resp.status_code, attempt)

            if body.get("success") is not True:
                # 关键：HTTP 200 + success=false 也是失败，保留内核 error 原文
                msg = str(body.get("error") or body.get("msg") or "内核返回 success=false 但未给 error")
                api_err = KernelAPIError(path, msg)
                return ToolResult(False, endpoint, ms, body, str(api_err), resp.status_code, attempt)

            return ToolResult(True, endpoint, ms, body, None, resp.status_code, attempt)

        ms = int((time.perf_counter() - started) * 1000)
        return ToolResult(False, endpoint, ms, {}, f"重试 {self.retries} 次后仍失败：{last_err}", None, self.retries + 1)

    # ---------- 血缘 ----------
    def analyze(self, sql: str, dialect: str = "hive", depth: int = 3, mode: str = "sql") -> ToolResult:
        """单脚本血缘 + 口径命中（内核主端点，**会顺带落盘一份 HTML 报告**）。"""
        return self._call("POST", "/analyze", {"mode": mode, "sql": sql, "dialect": dialect, "depth": depth})

    def analyze_workflow(self, tasks: list[dict[str, Any]], **kw: Any) -> ToolResult:
        """工作流级血缘（历史已有任务流）。"""
        return self._call("POST", "/analyze-workflow", {"tasks": tasks, **kw})

    def parse(self, sql: str, dialect: str = "hive") -> ToolResult:
        """纯解析（不落报告、不查口径）。"""
        return self._call("POST", "/parse", {"sql": sql, "dialect": dialect})

    def upstream(self, table: str, depth: int = 5, graph: str | None = None) -> ToolResult:
        """上游溯源（表级，读 warehouse_graph.json）。"""
        body: dict[str, Any] = {"table": table, "depth": depth}
        if graph:
            body["graph"] = graph
        return self._call("POST", "/upstream", body)

    def impact(self, table: str, direction: str = "downstream") -> ToolResult:
        """下游影响面。"""
        return self._call("POST", "/impact", {"table": table, "direction": direction})

    # ---------- 口径 / 知识库 ----------
    def search(self, keyword: str, limit: int = 20) -> ToolResult:
        """口径/字段/表/术语/规则 检索（内核参数名是 `query`）。"""
        return self._call("POST", "/kb/search", {"query": keyword, "limit": limit})

    def metric(self, name: str) -> ToolResult:
        """单个口径详情（内核参数名是 `name`）。"""
        return self._call("POST", "/kb/metric", {"name": name})

    def ask(self, question: str) -> ToolResult:
        """内核自带的规则式问数。**只取 intent/intent_label 做信号**——
        实测它的 `entity` 会把整句当实体、`evidence.metrics` 常为空，不能当问答引擎。"""
        return self._call("POST", "/kb/ask", {"question": question})

    def kb_summary(self) -> ToolResult:
        """知识库概况（口径/字段/术语条数、构建时间）。"""
        return self._call("POST", "/kb/summary", {})

    # ---------- 报告 ----------
    def make_report(self, payload: dict[str, Any]) -> ToolResult:
        """用分析结果生成单文件 HTML 报告。"""
        return self._call("POST", "/report", payload)

    def list_reports(self) -> ToolResult:
        """报告列表。"""
        return self._call("GET", "/reports")

    def report_url(self, report_id: str) -> str:
        """报告的可点击地址（注意：容器场景下内核会给出 internal_url）。"""
        return f"{self.base_url}/report/{report_id}"

    # ---------- 健康 ----------
    def health(self) -> ToolResult:
        return self._call("GET", "/health")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LineageClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

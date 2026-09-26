"""模型网关的 HTTP 壳（工作项 M1-04）。

    PYTHONPATH=apps/model-gateway/src:packages/dip-core/src \
      .venv/bin/uvicorn model_gateway.main:app --host 127.0.0.1 --port 18200

路由：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/v1/chat/completions` | OpenAI 兼容入口，薄转发 |
| GET | `/health` | 配置摘要（**不含 key**，只说配没配） |
| GET | `/usage` | 调用记账：次数、失败数、token 数、最近若干条 |

**错误一律用明确的 code 回**（见 `GatewayError`），不静默降级 —— 这是 Issue #4 的验收第 ② 条。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from dip_core import load_settings
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .gateway import GatewayConfig, GatewayError, ModelGateway

SERVICE_NAME = "model-gateway"
SERVICE_VERSION = "0.1.0"


def build_app(*, gateway: ModelGateway | None = None) -> FastAPI:
    """构建应用。`gateway` 参数用于测试注入（例如塞一个指向假上游的 transport）。"""
    active = gateway if gateway is not None else ModelGateway(GatewayConfig.from_settings(load_settings()))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await active.aclose()

    app = FastAPI(title="DIP 模型网关（薄层）", version=SERVICE_VERSION, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "service": SERVICE_NAME, "version": SERVICE_VERSION, **active.config.describe()}

    @app.get("/usage")
    async def usage() -> dict[str, Any]:
        return active.usage().model_dump(mode="json")

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse(
                {"error": {"type": "gateway_error", "code": "bad_json", "message": "请求体不是合法 JSON"}},
                status_code=400,
            )
        if not isinstance(payload, dict):
            return JSONResponse(
                {"error": {"type": "gateway_error", "code": "bad_payload", "message": "请求体必须是 JSON 对象"}},
                status_code=400,
            )
        try:
            data = await active.chat_completion(payload)
        except GatewayError as exc:
            return JSONResponse(exc.as_payload(), status_code=exc.status)
        return JSONResponse(data, status_code=200)

    return app


app = build_app()

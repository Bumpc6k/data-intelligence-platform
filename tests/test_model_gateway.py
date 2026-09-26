"""模型网关（M1-04 / Issue #4）的测试。

对应验收标准：
- 换模型只需改配置 → `test_switching_model_is_config_only`（配置 diff 见 PR 描述）
- 网关不可达时**明确报错**，不静默失败 → `test_unreachable_*` 一组

全部用假上游（`httpx.MockTransport`）：不碰真实网络、不需要真 key、在 CI 上也能跑。
HTTP 层用 `httpx.ASGITransport` 直挂 ASGI app（不用 starlette 的 TestClient）——
这样整条链路都在同一个事件循环里，不会碰到"client 建在别的 loop 上"这类偶发问题。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from dip_core import load_settings
from model_gateway import GatewayConfig, GatewayError, ModelGateway, build_app

FAKE_KEY = "sk-fake-for-tests-not-a-real-key"
UPSTREAM_BODY: dict[str, Any] = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "产量来自 dwd 层"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
}


class FakeUpstream:
    """假上游：记录收到的请求，可按需返回错误或抛异常。"""

    def __init__(
        self,
        *,
        status: int = 200,
        body: dict[str, Any] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.status = status
        self.body = UPSTREAM_BODY if body is None else body
        self.raise_exc = raise_exc
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_exc is not None:
            raise self.raise_exc
        return httpx.Response(self.status, json=self.body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def sent_bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


def make_config(**overrides: Any) -> GatewayConfig:
    base: dict[str, Any] = {
        "base_url": "https://api.example.com",
        "model": "deepseek-chat",
        "api_key": FAKE_KEY,
        "timeout": 5.0,
        "max_attempts": 2,
        "rate_limit_per_min": 0,
    }
    base.update(overrides)
    return GatewayConfig(**base)


def make_gateway(upstream: FakeUpstream, **overrides: Any) -> ModelGateway:
    return ModelGateway(make_config(**overrides), transport=upstream.transport)


# ================================================== 验收 ①：换模型只改配置
async def test_switching_model_is_config_only():
    """同一个网关代码，只有配置里的 model 不同 → 发给上游的 model 跟着变。"""
    upstream = FakeUpstream()

    first = make_gateway(upstream, model="deepseek-chat")
    await first.chat_completion({"messages": [{"role": "user", "content": "hi"}]})
    await first.aclose()

    second = make_gateway(upstream, model="deepseek-reasoner")
    await second.chat_completion({"messages": [{"role": "user", "content": "hi"}]})
    await second.aclose()

    assert [body["model"] for body in upstream.sent_bodies()] == ["deepseek-chat", "deepseek-reasoner"]


async def test_caller_supplied_model_is_overridden_by_config():
    """调用方各写一套模型是不允许的：模型以配置为准。"""
    upstream = FakeUpstream()
    gateway = make_gateway(upstream, model="deepseek-chat")

    await gateway.chat_completion({"model": "whatever-caller-wants", "messages": []})
    await gateway.aclose()

    assert upstream.sent_bodies()[0]["model"] == "deepseek-chat"


async def test_request_body_is_forwarded_untouched_except_model():
    """薄层：除了 model，其余字段原样转发（不做提示词加工）。"""
    upstream = FakeUpstream()
    gateway = make_gateway(upstream)
    payload = {
        "messages": [{"role": "user", "content": "ads.ads_产销存月报 的产量怎么来的？"}],
        "temperature": 0.2,
        "top_p": 0.9,
    }

    await gateway.chat_completion(payload)
    await gateway.aclose()

    sent = upstream.sent_bodies()[0]
    assert sent["messages"] == payload["messages"]
    assert (sent["temperature"], sent["top_p"]) == (0.2, 0.9)


# ================================================== 验收 ②：不可达必明确报错
async def test_unreachable_errors_loudly_without_fallback():
    """网关不可达 → 明确报错并给出原因，**不存在**规则模式之类的静默降级。"""
    upstream = FakeUpstream(raise_exc=httpx.ConnectError("connection refused"))
    gateway = make_gateway(upstream)

    with pytest.raises(GatewayError) as excinfo:
        await gateway.chat_completion({"messages": []})
    await gateway.aclose()

    assert excinfo.value.code == "upstream_error"
    assert excinfo.value.status == 502
    assert "网关不可达" in excinfo.value.message
    assert gateway.usage().failures == 1
    assert gateway.usage().calls == 1


async def test_connect_error_is_retried_up_to_max_attempts():
    """连接类错误重试是安全的（请求根本没送出去）。"""
    upstream = FakeUpstream(raise_exc=httpx.ConnectError("nope"))
    gateway = make_gateway(upstream, max_attempts=3)

    with pytest.raises(GatewayError):
        await gateway.chat_completion({"messages": []})
    await gateway.aclose()

    assert len(upstream.requests) == 3, "应当重试到上限"
    assert gateway.usage().recent[0].attempts == 3


async def test_read_timeout_is_not_retried():
    """读超时**不**重试：请求可能已被上游处理，重试会重复计费。"""
    upstream = FakeUpstream(raise_exc=httpx.ReadTimeout("too slow"))
    gateway = make_gateway(upstream, max_attempts=5)

    with pytest.raises(GatewayError) as excinfo:
        await gateway.chat_completion({"messages": []})
    await gateway.aclose()

    assert len(upstream.requests) == 1, "读超时不该重试"
    assert "不重试" in excinfo.value.message


async def test_missing_key_fails_loudly_and_never_calls_upstream():
    upstream = FakeUpstream()
    gateway = make_gateway(upstream, api_key=None)

    with pytest.raises(GatewayError) as excinfo:
        await gateway.chat_completion({"messages": []})
    await gateway.aclose()

    assert excinfo.value.code == "no_api_key"
    assert excinfo.value.status == 503
    assert upstream.requests == [], "没配 key 就不该打上游"


async def test_missing_model_fails_loudly():
    upstream = FakeUpstream()
    gateway = make_gateway(upstream, model=None)

    with pytest.raises(GatewayError) as excinfo:
        await gateway.chat_completion({"messages": []})
    await gateway.aclose()

    assert excinfo.value.code == "no_model"
    assert upstream.requests == []


async def test_stream_is_rejected_not_silently_downgraded():
    upstream = FakeUpstream()
    gateway = make_gateway(upstream)

    with pytest.raises(GatewayError) as excinfo:
        await gateway.chat_completion({"messages": [], "stream": True})
    await gateway.aclose()

    assert excinfo.value.code == "stream_unsupported"
    assert upstream.requests == [], "拒绝掉的请求不该真的发出去"


async def test_upstream_error_is_reported_verbatim_and_not_retried():
    """上游明确回错（4xx/5xx）：照实回报原文，不重试、不替换成自己的话。"""
    upstream = FakeUpstream(status=400, body={"error": {"message": "Model Not Exist"}})
    gateway = make_gateway(upstream)

    with pytest.raises(GatewayError) as excinfo:
        await gateway.chat_completion({"messages": []})
    await gateway.aclose()

    assert excinfo.value.status == 400
    assert "Model Not Exist" in excinfo.value.message
    assert len(upstream.requests) == 1, "上游已经答了，不该重试"


# ================================================== 记账 / 限流 / 不泄密
async def test_accounting_takes_tokens_from_upstream():
    upstream = FakeUpstream()
    gateway = make_gateway(upstream)

    data = await gateway.chat_completion({"messages": []})
    await gateway.aclose()

    assert data["usage"]["total_tokens"] == 18, "响应体原样返回"
    snapshot = gateway.usage()
    assert (snapshot.calls, snapshot.failures) == (1, 0)
    assert (snapshot.prompt_tokens, snapshot.completion_tokens, snapshot.total_tokens) == (11, 7, 18)
    assert snapshot.recent[0].model == "deepseek-chat"
    assert "产量来自 dwd 层" not in snapshot.model_dump_json(), "记账不该把回答内容也装进去"


async def test_missing_usage_in_upstream_response_is_recorded_as_zero():
    """上游没给 usage 就记 0 —— 宁可少记，也不编一个数。"""
    upstream = FakeUpstream(body={"choices": []})
    gateway = make_gateway(upstream)

    await gateway.chat_completion({"messages": []})
    await gateway.aclose()

    assert gateway.usage().total_tokens == 0


async def test_rate_limit_placeholder_blocks_then_recovers():
    now = [1000.0]
    upstream = FakeUpstream()
    gateway = ModelGateway(make_config(rate_limit_per_min=1), transport=upstream.transport, clock=lambda: now[0])

    await gateway.chat_completion({"messages": []})
    with pytest.raises(GatewayError) as excinfo:
        await gateway.chat_completion({"messages": []})

    assert excinfo.value.code == "rate_limited"
    assert excinfo.value.status == 429
    assert len(upstream.requests) == 1

    now[0] += 61  # 滑过窗口
    await gateway.chat_completion({"messages": []})
    await gateway.aclose()
    assert len(upstream.requests) == 2


async def test_key_never_leaks():
    upstream = FakeUpstream(raise_exc=httpx.ConnectError("boom"))
    gateway = make_gateway(upstream)

    with pytest.raises(GatewayError) as excinfo:
        await gateway.chat_completion({"messages": []})
    await gateway.aclose()

    assert FAKE_KEY not in json.dumps(gateway.config.describe())
    assert FAKE_KEY not in excinfo.value.message
    assert gateway.config.key_configured is True, "只说配没配，不说是什么"


async def test_key_is_sent_as_bearer_to_upstream():
    """对照：key 确实用上了（否则"不泄密"就变成"没用上"了）。"""
    upstream = FakeUpstream()
    gateway = make_gateway(upstream)

    await gateway.chat_completion({"messages": []})
    await gateway.aclose()

    assert upstream.requests[0].headers["authorization"] == f"Bearer {FAKE_KEY}"


# ================================================== HTTP 层
async def test_http_chat_completions_usage_and_health():
    upstream = FakeUpstream()
    gateway = make_gateway(upstream)
    app = build_app(gateway=gateway)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as client:
        response = await client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
        assert response.status_code == 200
        assert response.json()["usage"]["total_tokens"] == 18

        usage = (await client.get("/usage")).json()
        assert usage["total_tokens"] == 18
        assert usage["calls"] == 1

        health = (await client.get("/health")).json()
        assert health["ok"] is True
        assert health["model"] == "deepseek-chat"
        assert health["key_configured"] is True
        assert FAKE_KEY not in json.dumps(health)

    await gateway.aclose()


async def test_http_unreachable_is_502_with_machine_readable_code():
    upstream = FakeUpstream(raise_exc=httpx.ConnectError("down"))
    gateway = make_gateway(upstream)
    app = build_app(gateway=gateway)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as client:
        response = await client.post("/v1/chat/completions", json={"messages": []})

    await gateway.aclose()

    assert response.status_code == 502
    payload = response.json()
    assert payload["error"]["code"] == "upstream_error"
    assert "网关不可达" in payload["error"]["message"]


async def test_http_bad_payloads_are_400():
    upstream = FakeUpstream()
    gateway = make_gateway(upstream)
    app = build_app(gateway=gateway)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as client:
        not_json = await client.post(
            "/v1/chat/completions", content=b"not json", headers={"Content-Type": "application/json"}
        )
        not_object = await client.post("/v1/chat/completions", json=["a", "list"])

    await gateway.aclose()

    assert not_json.status_code == 400
    assert not_json.json()["error"]["code"] == "bad_json"
    assert not_object.status_code == 400
    assert not_object.json()["error"]["code"] == "bad_payload"
    assert upstream.requests == []


# ================================================== 配置（环境变量 → 网关）
def test_settings_read_both_documented_env_names():
    """`.env.example` 建议的 `DEEPSEEK_*` 与本模块既有的 `LLM_*` 都要认（两处文档不一致）。"""
    settings = load_settings(
        {
            "DEEPSEEK_API_KEY": "sk-x",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
            "LLM_MODEL": "deepseek-chat",
            "LLM_TIMEOUT": "12",
            "LLM_MAX_ATTEMPTS": "4",
            "LLM_RATE_LIMIT_PER_MIN": "9",
        }
    )
    config = GatewayConfig.from_settings(settings)

    assert config.base_url == "https://api.deepseek.com"
    assert config.model == "deepseek-chat"
    assert config.key_configured is True
    assert (config.timeout, config.max_attempts, config.rate_limit_per_min) == (12.0, 4, 9)


def test_llm_base_url_wins_over_deepseek_base_url():
    settings = load_settings({"LLM_BASE_URL": "https://a.example.com", "DEEPSEEK_BASE_URL": "https://b.example.com"})
    assert GatewayConfig.from_settings(settings).base_url == "https://a.example.com"


def test_base_url_defaults_to_deepseek_and_strips_trailing_slash():
    assert GatewayConfig.from_settings(load_settings({})).base_url == "https://api.deepseek.com"
    assert (
        GatewayConfig.from_settings(load_settings({"LLM_BASE_URL": "https://api.deepseek.com/"})).base_url
        == "https://api.deepseek.com"
    )


def test_max_attempts_is_at_least_one():
    assert GatewayConfig.from_settings(load_settings({"LLM_MAX_ATTEMPTS": "0"})).max_attempts == 1


def test_chat_url_is_openai_compatible_path():
    config = GatewayConfig.from_settings(load_settings({"LLM_BASE_URL": "https://api.deepseek.com"}))
    assert config.chat_url == "https://api.deepseek.com/chat/completions"

"""模型网关（工作项 M1-04）—— 平台唯一的模型出口。

**它做什么**

- 对外暴露一个 **OpenAI 兼容**的 `POST /v1/chat/completions`：薄层，请求体与响应体都**原样**转发/返回，
  不加工内容；
- 换模型 / 换供应商 = **只改配置**（`LLM_MODEL` / `LLM_BASE_URL` / `DEEPSEEK_API_KEY`），不改代码；
- 超时、**仅连接类错误**重试、限流占位、每次调用记账（token 数）。

**三条刻意的设计**（都来自 Issue #4 的边界与验收）

1. **不保留无模型降级**：《v2 项目规划 v1》§6 已定"不保留无模型降级"。上游不可达、未配 key、
   未配模型 —— 一律**明确报错**，绝不静默返回一个"看起来像答案"的东西。
   （注意这与 v1 的 ADR-0004「未配 key 进规则模式」相反，v2 是有意推翻它的。）
2. **只做薄层**：不做提示词工程、不做多轮对话编排 —— 那是 skill 与 harness 的事。
3. **key 永不外泄**：不进日志、不进错误消息、不进响应体；`describe()` 只回报"配没配"。

**重试口径**：只对**连接阶段**的错误重试（`ConnectError` / `ConnectTimeout`）。
读超时不重试 —— 请求可能已经被上游处理了，重试会造成重复计费；这一条有测试钉住。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from dip_core import Settings
from dip_core.config import DEFAULT_LLM_BASE
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CallRecord",
    "GatewayConfig",
    "GatewayError",
    "ModelGateway",
    "RateLimiter",
    "UsageSnapshot",
]

CHAT_PATH = "/chat/completions"
_USAGE_HISTORY = 50


@dataclass(frozen=True)
class GatewayConfig:
    """网关配置 —— 全部来自 `dip_core.load_settings()`（配置只有一处真相）。"""

    base_url: str
    model: str | None
    api_key: str | None
    timeout: float
    max_attempts: int
    rate_limit_per_min: int

    @classmethod
    def from_settings(cls, settings: Settings) -> GatewayConfig:
        return cls(
            base_url=(settings.llm_base_url or DEFAULT_LLM_BASE).rstrip("/"),
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout,
            max_attempts=max(1, settings.llm_max_attempts),
            rate_limit_per_min=max(0, settings.llm_rate_limit_per_min),
        )

    @property
    def key_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}{CHAT_PATH}"

    def describe(self) -> dict[str, Any]:
        """给 `/health` 看的摘要 —— **不含 key 本身**，只说配没配。"""
        return {
            "base_url": self.base_url,
            "model": self.model,
            "key_configured": self.key_configured,
            "timeout": self.timeout,
            "max_attempts": self.max_attempts,
            "rate_limit_per_min": self.rate_limit_per_min,
        }


class GatewayError(Exception):
    """网关侧的错误（区别于上游返回的错误）。`code` 机器可读，`status` 是要回给调用方的 HTTP 码。"""

    def __init__(self, code: str, message: str, *, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def as_payload(self) -> dict[str, Any]:
        return {"error": {"type": "gateway_error", "code": self.code, "message": self.message}}


class CallRecord(BaseModel):
    """一次调用的记账（token 数取自上游响应的 `usage`，不自算）。"""

    model_config = ConfigDict(frozen=True)

    model: str
    ms: int = Field(ge=0)
    attempts: int = Field(ge=1)
    ok: bool
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    http_status: int | None = None
    error: str | None = None


class UsageSnapshot(BaseModel):
    """`GET /usage` 的返回。"""

    model_config = ConfigDict(frozen=True)

    calls: int
    failures: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    recent: tuple[CallRecord, ...] = ()


class RateLimiter:
    """限流占位：滑动窗口内超过上限就拒；`per_minute <= 0` 表示关闭。

    刻意做得这么小 —— Issue 要的是"限流占位"，不是一套完整的配额系统。
    `clock` 可注入，测试才能不靠 sleep。
    """

    WINDOW_SECONDS = 60.0

    def __init__(self, per_minute: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self._clock = clock
        self._hits: deque[float] = deque()

    def allow(self) -> bool:
        if self.per_minute <= 0:
            return True
        now = self._clock()
        while self._hits and now - self._hits[0] >= self.WINDOW_SECONDS:
            self._hits.popleft()
        if len(self._hits) >= self.per_minute:
            return False
        self._hits.append(now)
        return True


class ModelGateway:
    """薄的模型网关客户端。请求体照转、响应体照返，只加超时/重试/记账/限流。"""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._clock = clock
        self._limiter = RateLimiter(config.rate_limit_per_min, clock=clock)
        self._calls = 0
        self._failures = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._recent: deque[CallRecord] = deque(maxlen=_USAGE_HISTORY)
        # trust_env=False：不让系统代理悄悄介入平台内部调用（本仓库踩过的坑）
        self._client = httpx.AsyncClient(timeout=config.timeout, transport=transport, trust_env=False)

    # ------------------------------------------------------------------ 生命周期
    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ 主入口
    async def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """转发一次 chat 请求。返回上游的**原始** JSON；失败则抛 `GatewayError`。"""
        if not self._limiter.allow():
            raise GatewayError(
                "rate_limited",
                f"触发限流：每分钟最多 {self.config.rate_limit_per_min} 次（LLM_RATE_LIMIT_PER_MIN 可调）",
                status=429,
            )
        if not self.config.key_configured:
            raise GatewayError(
                "no_api_key",
                "未配置模型凭据：请设置 DEEPSEEK_API_KEY（本地放 .env，仓库只留 .env.example）。"
                "本网关**不提供**无模型降级。",
                status=503,
            )
        if not self.config.model:
            raise GatewayError("no_model", "未配置模型：请设置 LLM_MODEL。本网关不猜默认模型。", status=503)
        if payload.get("stream"):
            raise GatewayError("stream_unsupported", "本薄层暂不支持 stream=true（不做静默降级）。", status=400)

        body = dict(payload)
        body["model"] = self.config.model  # 模型以配置为准，杜绝调用方各写一套
        body.pop("stream", None)

        attempt = await self._post_with_retry(body)
        record = attempt.record
        self._recent.append(record)
        self._calls += 1
        if not record.ok:
            self._failures += 1
        self._prompt_tokens += record.prompt_tokens
        self._completion_tokens += record.completion_tokens
        if not record.ok:
            raise GatewayError("upstream_error", record.error or "上游调用失败", status=record.http_status or 502)
        return attempt.response

    # ------------------------------------------------------------------ 内部
    async def _post_with_retry(self, body: dict[str, Any]) -> _Attempt:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json; charset=utf-8",  # 中文 Windows 下显式 UTF-8（本仓库的坑）
        }
        attempts = 0
        last_error: str | None = None
        started = self._clock()
        while attempts < self.config.max_attempts:
            attempts += 1
            try:
                response = await self._client.post(self.config.chat_url, json=body, headers=headers)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # 只对连接阶段重试：请求根本没送出去，重试是安全的
                last_error = f"网关不可达：{self.config.base_url}（{exc.__class__.__name__}）"
                continue
            except httpx.TimeoutException as exc:
                # 读/写超时不重试：上游可能已经处理了，重试会重复计费
                return self._failed(
                    body, attempts, started, f"上游超时（不重试，避免重复计费）：{exc.__class__.__name__}", None
                )
            except httpx.HTTPError as exc:
                return self._failed(body, attempts, started, f"上游传输错误：{exc.__class__.__name__}", None)

            usage = _usage_of(response)
            if response.status_code >= 400:
                # 上游明确回错（4xx/5xx）：照实回报，不重试、不掩盖
                return self._failed(body, attempts, started, _upstream_message(response), response.status_code, usage)
            try:
                data = response.json()
            except ValueError:
                return self._failed(body, attempts, started, "上游返回的不是 JSON", response.status_code, usage)
            return _Attempt(
                record=CallRecord(
                    model=str(body["model"]),
                    ms=int((self._clock() - started) * 1000),
                    attempts=attempts,
                    ok=True,
                    prompt_tokens=usage[0],
                    completion_tokens=usage[1],
                    total_tokens=usage[2],
                    http_status=response.status_code,
                    error=None,
                ),
                response=data,
            )

        return self._failed(body, attempts, started, last_error or "网关不可达", 502)

    def _failed(
        self,
        body: dict[str, Any],
        attempts: int,
        started: float,
        error: str,
        http_status: int | None,
        usage: tuple[int, int, int] | None = None,
    ) -> _Attempt:
        prompt, completion, total = usage or (0, 0, 0)
        return _Attempt(
            record=CallRecord(
                model=str(body.get("model") or ""),
                ms=int((self._clock() - started) * 1000),
                attempts=attempts,
                ok=False,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
                http_status=http_status,
                error=error,
            ),
            response={},
        )

    # ------------------------------------------------------------------ 记账
    def usage(self) -> UsageSnapshot:
        return UsageSnapshot(
            calls=self._calls,
            failures=self._failures,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            total_tokens=self._prompt_tokens + self._completion_tokens,
            recent=tuple(self._recent),
        )


class _Attempt(BaseModel):
    """内部用：记账 + 上游原始响应（响应体**不进**记账，避免把内容打进 /usage）。"""

    record: CallRecord
    response: dict[str, Any] = Field(default_factory=dict)


def _usage_of(response: httpx.Response) -> tuple[int, int, int]:
    """从上游响应的 `usage` 里取 token 数；拿不到就记 0（宁可少记，也不编）。"""
    try:
        usage = response.json().get("usage") or {}
    except ValueError:
        return (0, 0, 0)

    def as_int(key: str) -> int:
        try:
            value = int(usage.get(key) or 0)
        except (TypeError, ValueError):
            return 0
        return value if value > 0 else 0

    prompt, completion = as_int("prompt_tokens"), as_int("completion_tokens")
    total = as_int("total_tokens") or (prompt + completion)
    return (prompt, completion, total)


def _upstream_message(response: httpx.Response) -> str:
    """把上游的错误原文取出来（不加工、不替换成自己的话）。"""
    try:
        data = response.json()
    except ValueError:
        return f"上游返回 HTTP {response.status_code}：{response.text[:200]}"
    error = data.get("error")
    if isinstance(error, dict) and error.get("message"):
        return f"上游返回 HTTP {response.status_code}：{error['message']}"
    return f"上游返回 HTTP {response.status_code}：{str(data)[:200]}"

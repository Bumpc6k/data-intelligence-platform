"""平台配置（环境变量优先，P1 只有必要项）。"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_KERNEL_BASE = "http://127.0.0.1:18080"
DEFAULT_LLM_BASE = "https://api.deepseek.com"


def first_of(env: dict[str, str], *names: str) -> str | None:
    """按顺序取第一个非空环境变量。

    存在的理由：《协作者验证包》里建议的 `.env` 名字是 `DEEPSEEK_BASE_URL`/`DEEPSEEK_API_KEY`，
    而本模块既有约定是 `LLM_BASE_URL`。两处文档不一致，与其猜哪边权威，不如都认。
    """
    for name in names:
        value = env.get(name)
        if value:
            return value
    return None


@dataclass(frozen=True)
class Settings:
    kernel_base_url: str = DEFAULT_KERNEL_BASE
    kernel_timeout: float = 15.0
    kernel_retries: int = 1
    # 认证：P1 生产用 Casdoor OIDC，开发用固定身份（ADR 见规划 §3.4）；此处只保留模式位
    auth_mode: str = "dev"
    # 模型：v1 的 dip-agent 在未配 key 时进入"规则模式"（ADR-0004）。
    # **M1-04 的模型网关不沿用这个降级**：《v2 项目规划 v1》§6 已决定"不保留无模型降级"，
    # 网关侧必须明确报错而不是静默返回规则答案。
    llm_enabled: bool = False
    llm_model: str | None = None
    llm_base_url: str | None = None
    # 模型网关（M1-04）：换模型 / 换供应商只改这几项，不用改代码
    llm_api_key: str | None = None
    llm_timeout: float = 30.0
    llm_max_attempts: int = 2
    llm_rate_limit_per_min: int = 0  # 0 = 关闭（限流占位）
    # 报告代理：前端只看平台地址，不暴露内核内网地址
    report_proxy_enabled: bool = True

    @property
    def mode(self) -> str:
        return "llm" if self.llm_enabled else "rule"


def load_settings(env: dict[str, str] | None = None) -> Settings:
    e = os.environ if env is None else env
    return Settings(
        kernel_base_url=e.get("KERNEL_BASE_URL", DEFAULT_KERNEL_BASE),
        kernel_timeout=float(e.get("KERNEL_TIMEOUT", "15")),
        kernel_retries=int(e.get("KERNEL_RETRIES", "1")),
        auth_mode=e.get("AUTH_MODE", "dev"),
        llm_enabled=e.get("LLM_ENABLED", "false").lower() == "true",
        llm_model=e.get("LLM_MODEL"),
        llm_base_url=first_of(e, "LLM_BASE_URL", "DEEPSEEK_BASE_URL"),
        llm_api_key=first_of(e, "DEEPSEEK_API_KEY", "LLM_API_KEY"),
        llm_timeout=float(e.get("LLM_TIMEOUT", "30")),
        llm_max_attempts=int(e.get("LLM_MAX_ATTEMPTS", "2")),
        llm_rate_limit_per_min=int(e.get("LLM_RATE_LIMIT_PER_MIN", "0")),
        report_proxy_enabled=e.get("REPORT_PROXY_ENABLED", "true").lower() == "true",
    )

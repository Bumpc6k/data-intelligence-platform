"""平台配置（环境变量优先，P1 只有必要项）。"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_KERNEL_BASE = "http://127.0.0.1:18080"


@dataclass(frozen=True)
class Settings:
    kernel_base_url: str = DEFAULT_KERNEL_BASE
    kernel_timeout: float = 15.0
    kernel_retries: int = 1
    # 认证：P1 生产用 Casdoor OIDC，开发用固定身份（ADR 见规划 §3.4）；此处只保留模式位
    auth_mode: str = "dev"
    # 模型：未配 key 时进入"规则模式"（ADR-0004）
    llm_enabled: bool = False
    llm_model: str | None = None
    llm_base_url: str | None = None
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
        llm_base_url=e.get("LLM_BASE_URL"),
        report_proxy_enabled=e.get("REPORT_PROXY_ENABLED", "true").lower() == "true",
    )

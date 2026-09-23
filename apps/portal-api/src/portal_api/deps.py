"""依赖装配：把内核客户端与编排 Agent 交给 FastAPI 管理（测试可覆盖）。

生产形态是"一个进程一个内核客户端"，所以这里用 lru_cache 复用连接池；
测试里用 `app.dependency_overrides[get_agent]` 换成假内核，不需要起内核。
"""

from __future__ import annotations

from functools import lru_cache

from dip_agent import Agent
from lineage_client import LineageClient

from .settings import settings


@lru_cache(maxsize=1)
def get_client() -> LineageClient:
    return LineageClient(settings.kernel_base_url, retries=settings.kernel_retries, timeout=settings.kernel_timeout)


def get_agent() -> Agent:
    return Agent(get_client())

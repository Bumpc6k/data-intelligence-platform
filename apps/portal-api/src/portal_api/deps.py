"""依赖装配：把内核客户端与编排 Agent 交给 FastAPI 管理（测试可覆盖）。

生产形态是"一个进程一个内核客户端"，所以这里用 lru_cache 复用连接池；
测试里用 `app.dependency_overrides[get_agent]` 换成假内核，不需要起内核。
"""

from __future__ import annotations

import pathlib
from functools import lru_cache

from dip_agent import Agent
from lineage_client import LineageClient

from .settings import settings

#: skill 声明文件的位置：`packages/dip-skills` 是规范，`packages/*/skill.yaml` 是各家声明
SKILL_GLOB = "packages/*/skill.yaml"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]


@lru_cache(maxsize=1)
def skill_renderer_by_kind() -> dict[str, str]:
    """从 skill 声明里读 `{证据种类: 视图名}`（M4-01 / #17）。

    为什么要读声明而不是在代码里写死：`renderer` 是 M1-02 契约的第 8 类字段，
    **改声明就该改渲染**（把血缘声明的 `graph` 改成 `table`，前端就得改用表格渲染血缘结果）。
    声明坏掉的按"没有这条声明"处理并打一行日志 —— 一个坏 YAML 不该让整个问答服务起不来。
    """
    from dip_skills import load_skill_spec

    renderers: dict[str, str] = {}
    for path in sorted(REPO_ROOT.glob(SKILL_GLOB)):
        try:
            spec = load_skill_spec(path)
        except Exception as exc:  # noqa: BLE001
            print(f"[portal-api] skill 声明读不了，按没有处理：{path}（{exc}）")
            continue
        renderers[spec.evidence_kind.value] = spec.renderer.value
    return renderers


@lru_cache(maxsize=1)
def get_client() -> LineageClient:
    return LineageClient(settings.kernel_base_url, retries=settings.kernel_retries, timeout=settings.kernel_timeout)


def get_agent() -> Agent:
    return Agent(get_client(), renderer_by_kind=skill_renderer_by_kind())

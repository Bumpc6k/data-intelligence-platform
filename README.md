# 数据智能平台（Data Intelligence Platform / DIP）

> 对话即入口，**回答必带凭证**。平台纳管数据调度与开发资产（DolphinScheduler / DataWorks / Airflow），
> 把「血缘 · 业务口径 · 报告」串成可追溯的答案；生成能力一律走「草稿 → 审核 → 执行」，只读优先。

本仓库是**新仓库（门户 + 平台层）**；数据智能内核（SQL 血缘解析、业务口径知识库、HTML 报告、海豚插件）
在另一个仓库，二者**只通过 HTTP 契约通信，互不 import 内部模块**。

```
┌─ portal-web（对话工作台）────────────┐
│                                       │  新仓库 = 本仓库
│  portal-api ── dip-agent ── dip-contracts
│       │            │
│       └──────── integrations/lineage-client        （唯一耦合面：HTTP 契约）
└───────────────────────┬───────────────┘
                        ↓
        sql-lineage-mvp（Data Intelligence Kernel，已冻结）
        /analyze /upstream /impact /kb/* /report …（17 个端点）
```

## 🚩 先读这个（AI 助手 / 协作者）

| 你是谁 | 从哪里开始 |
| --- | --- |
| **AI 助手** | [`AGENTS.md`](AGENTS.md)（权威须知：铁律、边界、环境、交付流程、已知的坑） |
| **人类协作者** | [`CONTRIBUTING.md`](CONTRIBUTING.md)（工作流 + 三条硬规矩 + 一条命令起环境） |
| **要认领任务** | [GitHub Issues](https://github.com/Bumpc6k/data-intelligence-platform/issues)（按 `M1-垂直切片` 等标签筛；关键路径是 #1 与 #3） |
| **想了解全局** | [`docs/planning/2026-09-26-v2-项目规划-v1.md`](docs/planning/2026-09-26-v2-项目规划-v1.md)（里程碑 + 工作项边界与验收） |
| **具体怎么上手** | [`docs/collaboration/2026-09-25-协作者验证包.md`](docs/collaboration/2026-09-25-协作者验证包.md) |
| **规则细节** | [`docs/collaboration/2026-09-26-开发规范与AI协作.md`](docs/collaboration/2026-09-26-开发规范与AI协作.md) |

> 给 AI 的一句话：**挑一个 Issue → 读它的边界与验收 → 小步改、带测试（含反例）→ 门禁绿 → 提 PR 并写清"怎么验证"**。拿不准就停下来问，不要猜、不要编、不要顺手改。

## 目录结构（P1：3 个部署单元 + 1 个库）

| 路径 | 说明 | 对应工作项 |
| --- | --- | --- |
| `apps/portal-api/` | 平台后端（FastAPI）：身份、会话、Agent 编排入口、审计、报告代理 | W-101 / W-111 / W-112 / W-117 / W-118 |
| `apps/portal-web/` | 对话工作台前端（Vue3 + Vite + TS）——**B4 才落码**，当前放设计说明与原型链接 | W-121~W-130 |
| `packages/dip-contracts/` | 契约层：统一结果模型 + `status` 判定规则（跨端共享） | **W-103** |
| `packages/dip-core/` | 领域模型与配置（P1 保持极薄） | W-101 |
| `packages/dip-agent/` | 对话编排：实体识别 / 意图判定 / 工具计划 / 证据装配 / 答案成型 | W-114 / W-115 |
| `integrations/lineage-client/` | **内核客户端**：6+ 方法、`success` 判定、参数归一化、超时重试 | **W-113** |
| `docs/adr/` | 架构决策记录（为什么这么定） | — |
| `ops/` | 本地环境与一键验收 | W-105 / W-141 |

## 快速开始

```bash
make venv          # 建虚拟环境并装依赖
make test          # 单元 + 契约测试（不需要内核）
make smoke         # 内核冒烟（需要内核服务在 127.0.0.1:18080；不在则自动跳过）
make gate-fast     # lint + test，一条命令
```

依赖内核服务时先在内核仓库里执行：`bash /usr/local/bin/start-lineage-api.sh`。

## 三条硬规矩（违反即缺陷）

1. **无凭证不发布**：结论必须带可点开的证据；`evidence` 为空时不出结论数字。
2. **不确定必须显式说**：`status ∈ verified / inferred / candidate / unresolved / stale`，界面上可见。
3. **只读优先**：P1 不落地任何写操作；生成能力必须走「草稿 → 人工审核 → 执行」。

## 关键决策（详见 `docs/adr/`）

| ADR | 决策 |
| --- | --- |
| [0001](docs/adr/0001-kernel-coupling.md) | 与内核只走 HTTP 契约，跨仓库不 import |
| [0002](docs/adr/0002-status-ownership.md) | `status` / `version` 由**平台推导**（内核零改动） |
| [0003](docs/adr/0003-source-line.md) | 来源行号由内核**新增只读端点**提供；在此之前降级为文件级 |
| [0004](docs/adr/0004-rule-first-agent.md) | **规则优先、LLM 兜底**：无 key 也能跑通验收路径 |

## 相关文档（在设计阶段产出，位于内核仓库）

- 项目规划与功能设计 v2、设计说明书 v1.0（11 页 + 令牌 + 组件清单）
- 功能点清单与工作分解（27 个工作项）、协作与分工机制
- B2 接口设计与评审（含内核真实响应快照与探针脚本）

## 许可证

Apache License 2.0（见 `LICENSE`）。

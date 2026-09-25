# 协作说明（先读这三份）

| 文档 | 作用 |
| --- | --- |
| `docs/planning/2026-09-26-v2-项目规划-v1.md` | **做什么、分几个里程碑、每个工作项的边界与验收** |
| `docs/collaboration/2026-09-25-协作者验证包.md` | **怎么上手**：环境、现有服务、共同规则、回报方式 |
| `docs/adr/` | 已定的架构决策（含"为什么这么定"） |

## 工作流

1. 在 Issues 里认领任务（用 `M1-垂直切片` 等里程碑标签筛，用 `角色-*` 标签找人）
2. 分支 `feat/m?-??-简述`，**一个任务一个 PR**
3. 提 PR 前：`bash ops/gate.sh` **必须 ALL_PASS**（ruff + pytest + 分层守卫）
4. PR 描述里写清"怎么验证"（命令 + 原始输出 + **反例**）
5. 验收人按 Issue 的验收标准**逐条跑**，全通过才合并；不通过会写明原因

## 三条硬规矩

1. **事实不许编**：结论必须能指到来源（文件/端点/行号）；拿不到就说"没查到"。
2. **冻结资产只调用不修改**：数据智能内核、`integrations/lineage-client`、`packages/dip-contracts`、`dip-pg`。
3. **不许提交密钥**：本地用 `.env`（已 gitignore），仓库只留 `.env.example`。

## 环境（一条命令）

```bash
bash ops/start-pg.sh                      # 审计库 PostgreSQL :15432
bash /usr/local/bin/start-lineage-api.sh  # 数据智能内核 :18080（首次需先构建演示数据，见验证包）
bash ops/start-portal.sh                  # 平台后端 + 前端 :18100
bash ops/gate.sh                          # 提交前必跑
```

> Windows 用户：脚本同时支持 `.venv/bin` 与 `.venv/Scripts`；**中文 Windows 下做 HTTP 验证必须显式 UTF-8**（否则编码问题会被误判成产品缺陷）。

## 建 Issue

维护者执行一次（需要 `gh auth login` 或 `GITHUB_TOKEN`）：

```bash
bash ops/create-issues.sh --dry-run   # 先预览
bash ops/create-issues.sh             # 建 14 个标签 + 19 个 Issue
```

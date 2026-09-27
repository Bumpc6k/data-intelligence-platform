# 演示与验收路径（M4-03 / M4-04）

> 对应《v2 项目规划 v1》§5 的**一条验收路径**（7 步），以及`docs/evidence/`的存证约定。

## 0. 一条命令起全栈 + 跑完 7 步

```bash
bash ops/demo.sh                 # 起全栈 → 逐条跑 7 步 → 证据落 docs/evidence/demo-<时间戳>.txt
bash ops/demo.sh --no-dsh        # 不起 dsh 壳（没装 Node/pnpm 的机器）
bash ops/demo.sh --check         # 只体检服务状态（不起服务、不调模型）
bash ops/gate.sh --demo          # 门禁里带上演示路径（会真调模型，所以只在显式要求时跑）
```

全栈 = **PG(15432) + 内核(18080) + 模型网关(18200) + 防火墙(18210) + 平台(18100) + 血缘 skill(18360) + dsh 壳(3080)**，
另可选 **WeKnora(18300/18380)** 提供文档通道。脚本幂等：已在跑的服务不重起。

## 1. 七步怎么跑、留证在哪

| # | 规划 §5 的要求 | 脚本怎么跑 | 证据里能看到什么 |
| --- | --- | --- | --- |
| 1 | 一条命令起全栈 | 第 1 步逐个起/探活，打印每个服务的地址与状态 | 各服务实际状态；dsh 壳还会打印**带 token 的 URL** |
| 2 | 对话框问旗舰问题 → 答案 + 3 条凭证 + status/confidence/version | 第 2 步 `POST /api/agent/ask`（跟界面同一条链路） | `status=verified`、`confidence=0.9`、3 条凭证（血缘/词表/口径）、结论值、4 个视图块 |
| 3 | 点开凭证：血缘图可交互、口径卡有公式与来源（行号缺失标"文件级"） | 第 3 步 `GET /api/lineage/upstream`（前端画图用的同一 payload）+ 内核 `/kb/metric` 原文 | 节点/边数、前几个节点及层次；口径卡的公式/来源脚本/**行号缺失时明确写"文件级（行号待补）"** |
| 4 | 危险动作（truncate）→ 被拒 + 审计可查 | 第 4 步 `POST /judge` → 再查 `GET /api/audit/judgments` | `tier=deny` / `disposition=reject` / 命中 `destructive-ddl` / 已落库；审计里查得到这条判定 |
| 5 | 生产写 → 要求审批 → 审批后放行（令牌不可复用） | 第 5 步 `/judge` → `/tokens/issue` → `/verify`（放行）→ 再 `/verify`（复用） | `require_approval`、令牌长度、第一次 `ok=true`、**第二次 `HTTP 403 令牌已使用过（一次性）`** |
| 6 | 构造"模型编造表名" → 被出口校验拦下 | 第 6 步直接调 `dip_contracts.guards.check_answer`（白名单只放真表） | `verdict.ok=False`，违规逐条列出（编造表名 + 编造数字） |
| 7 | 知识回写：无来源脚本的候选 → 入不了库 | 第 7 步两道：① 接口 `POST /api/knowledge/candidates`（缺 `source_script`）② 绕过接口直接写库把 `pending` 改成 `ingested` | ① HTTP 400 + `missing_source_script`（还有 `missing_depends_on`/`missing_submitter`）② 库层 CHECK `knowledge_candidates_ingest_requires_source_chk` 拦下（演示数据随即清理） |

## 2. 界面里怎么点（脚本代不了的部分）

**平台前端** `http://127.0.0.1:18100/app/`（进页面自动问一次旗舰问题）：

1. 左侧是会话与工具步骤条；中间是答案 + 凭证卡；右边是「视图」区。
2. 凭证卡点开：血缘图（M4-01 的 `graph` 视图，可滚动/缩放）、口径卡（公式 + 来源脚本；行号缺失会写"文件级"）。
3. 视图区一次能看到四块：`table`（检索命中）/ `sql`（口径表达式 + 来源）/ `graph`（血缘）/ `diff`（口径版本对照）。
   截图见 `docs/evidence/issue-17/`。
4. 审计与危险动作：平台里走防火墙判定的入口在演示脚本里是 `POST /judge`；界面上对应的"审计流水"卡会列出判定结果。

**dsh 壳**（第 1 步打印的带 token 的 URL，形如 `http://127.0.0.1:3080/?token=…`）：

1. 复制**完整** URL（含 token）到浏览器；选工作区（不选之前输入框是禁用的）。
2. 问同一句旗舰问题，并点名工具：`用 mcp__lineage__lineage_analyze 查血缘`。
3. 答案里会出现工具名、回执（`evidence_count`/`ok`）与上游表数。截图见 `docs/evidence/issue-18/`。

## 3. `docs/evidence/` 的约定

- **命名**：`issue-<编号>-<短名>.txt`（任务证据，一个 issue 一份）+ `demo-<时间戳>.txt`（整条演示路径，脚本每次重新生成）。
- **子目录**：界面截图放 `docs/evidence/issue-<编号>/`（如 `issue-17/view-graph.png`、`issue-18/dsh-web-answer.png`）。
- **内容要求**（规划 §5 的存证要求）：命令 + **原始输出（关键行不截断）** + 截图；**反例必须留**。
  演示脚本的每一节都打印"实际返回"，失败时直接指出卡在第几步、返回了什么。
- **不落密钥**：口令/令牌一律不写进证据（示例：MCP 端点只打印 URL 与 kb id，令牌长度可以写、值不写）。

## 4. 停掉现场

```bash
bash ops/start-firewall.sh stop          # 防火墙（tmux 会话）
bash ops/start-portal.sh stop 2>/dev/null || tmux kill-session -t portal-api
pkill -f "dip_lineage_skill --transport http"   # 血缘 skill（WSL）
# dsh 壳（Windows）：
powershell -NoProfile -Command "Stop-Process -Id (Get-NetTCPConnection -LocalPort 3080 -State Listen).OwningProcess -Force"
# WeKnora（WSL docker）：
cd /root/projects/_weknora-src/WeKnora-main && docker compose down
```

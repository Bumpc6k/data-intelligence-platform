# AGENTS.md — 给 AI 助手的仓库须知

> **如果你是 AI 助手（Claude Code / Codex / Cursor / Copilot / 其他），请先读这份文件，再动手。**
> 人类协作者请读 [`CONTRIBUTING.md`](CONTRIBUTING.md)；完整的协作规范见 [`docs/collaboration/2026-09-26-开发规范与AI协作.md`](docs/collaboration/2026-09-26-开发规范与AI协作.md)。

---

## 1. 这个项目是什么

**数据智能平台（DIP）**：对话即入口，**回答必带凭证**。平台纳管数据调度与开发资产（DolphinScheduler / DataWorks / Airflow），
把「血缘 · 业务口径 · 报告」串成**可追溯**的答案；能力以 **Skill** 插件形式增长，核心技术底座是 harness + MCP。

**架构已定（不要再重新设计）**：DeepSeek API（薄网关）＋ **DeepSeek Harness（dsh）** 作插件运行时与 Web 壳 ＋ **MCP** 作 skill 通道；
**智能防火墙独立部署**（三档：自动通过 / 需上级审批 / 禁止）；**出口事实校验**（回执白名单）；**知识库双通道**（结构化通道唯一可产出结论 + WeKnora 文档通道）。
决策依据见 `docs/adr/` 与 `docs/planning/2026-09-26-v2-项目规划-v1.md`。

**已有资产（已跑通，别重写）**：内核契约客户端（17 端点）、统一结果模型与 `status` 判定、审计与会话落库（PostgreSQL）、对话前端（静态迭代版，含证据面板）。

---

## 2. 你要做的事从哪来

**任务在 GitHub Issues**，不在对话里。开工前先看：

```bash
gh issue list --state open --limit 30                    # 全部任务
gh issue list --label M1-垂直切片                        # 按里程碑
gh issue list --label 关键路径                            # 卡住后面所有任务的
```

**第一批优先（关键路径）**：#1 `M1-02 skill 契约声明与校验器` → #3 `M1-03 血缘 skill（MCP 服务）`；可并行：#2 `M1-01 dsh 起壳`、#5 `M1-05 出口事实校验器`。

每个 Issue 里有三样东西，**必须逐条对待**：**交付物**（做到什么）、**边界（不要做什么）**、**验收（可执行）**。

---

## 3. 五条铁律（违反即打回）

| # | 铁律 | 具体含义 |
| --- | --- | --- |
| 1 | **事实不许编** | 结论必须能指到来源（文件/端点/行号）；拿不到就说"没查到"，**不许编一个像样的答案** |
| 2 | **无凭证不出结论** | `evidence` 为空时不许给数字/公式（有测试断言） |
| 3 | **没有证据 = 没完成** | 交付必须带：命令 + 原始输出（不截断）+ 测试（**含反例**）+ 边界声明 |
| 4 | **冻结资产只调用不修改** | 数据智能内核（`sql-lineage-mvp`）、`integrations/lineage-client`、`packages/dip-contracts`、`dip-pg` |
| 5 | **`main` 只进 PR** | 不许直接推 main；不许 `force push`；不许改 CI / `ops/gate.sh` / 分层守卫来绕过红灯 |

---

## 4. 环境（三条命令）

```bash
bash ops/start-pg.sh                      # 审计库 PostgreSQL :15432（会话/审计）
bash /usr/local/bin/start-lineage-api.sh  # 数据智能内核 :18080（血缘/口径/报告；首次需先构建演示数据，见验证包）
bash ops/start-portal.sh                  # 平台后端 + 前端 :18100 → http://127.0.0.1:18100/app/
```

自检（**提交前必跑**）：

```bash
bash ops/gate.sh            # lint + 测试，必须看到 GATE: ALL_PASS
bash ops/gate.sh --with-smoke   # 带上真内核的冒烟
```

**门禁红了不许提 PR**（这条踩过两次：红灯推上去，验收白跑）。

---

## 5. 交付流程

```bash
git checkout -b feat/m1-03-lineage-skill            # 一个 Issue 一个分支
# …改代码、写测试（含反例）…
bash ops/gate.sh                                    # 必须 ALL_PASS
git commit -m "feat(m1-03): 血缘 skill 包成 MCP 服务\n\n- …\n\nCloses #3"
git push -u origin feat/m1-03-lineage-skill
gh pr create --fill                                 # PR 描述必须写"怎么验证"
```

PR 会被**逐条跑验收标准**；不通过会按格式打回（哪一条没过、实际输出、期望值、怎么改）。

---

## 6. 你（AI）的权限边界

**可以做**：读改本仓库代码、写测试、跑命令、创建草稿 PR、查 Issue 与 ADR。

**必须由人来做**（你要停下来请人操作）：合并 PR、修改 CI / 门禁 / 分层守卫、改冻结资产、处理任何凭证、`force push`、推翻既有架构决策（ADR）。

**绝对禁止**：
- 把**没跑通**的验证说成"已验证"；伪造命令输出或截图
- 为了让门禁变绿而**修改测试断言或门禁规则**
- 在仓库里写密钥/token（本地用 `.env`，仓库只留 `.env.example`）
- 自行决定架构（发现设计问题 → 开 Issue 讨论，不要边写边改架构）
- 顺手修改 Issue 范围之外的模块（发现别的坑 → 开 Issue 或 PR 里留言）

---

## 7. 开工前的自我检查（务必先做）

1. 读：`CONTRIBUTING.md` → 目标 Issue → `docs/collaboration/2026-09-26-开发规范与AI协作.md` → 相关 `docs/adr/`
2. **用三句话复述**：要做什么 / 边界在哪 / 验收是什么 —— 复述错了别动手
3. 列出你打算新建或修改的文件清单，并说明**为什么不动其他文件**

---

## 8. 已知的坑（都踩过，别再踩）

| 坑 | 后果 | 正确做法 |
| --- | --- | --- |
| 用 ASCII 正则校验含中文的标识符 | **漏检全部中文标识符，测试还假绿** | Unicode 感知（`[\w\u4e00-\u9fff]`），并且**中文用例必写** |
| 测试用固定 session id | 同库重复跑累积数据 → **假红**，白排查 | 用唯一 id（`uuid4`） |
| 中文 Windows 下不指定 UTF-8 发 HTTP 请求 | 中文变乱码，**被误判成产品缺陷** | `Content-Type: application/json; charset=utf-8` + UTF-8 字节体 |
| 假设内核"只读" | 内核有 `/generate/*` 且 `/analyze` 会落盘报告 | 客户端保持只读；写类调用必须过防火墙 |
| 在 import 期连数据库 | 数据库不可达时**整个测试收集挂死** | 放到 FastAPI lifespan；DSN 带 `connect_timeout` |
| 默认依赖系统代理发本地请求 | 本地调用被 SOCKS 代理带崩 | `httpx.Client(trust_env=False)`（已是默认） |

---

## 9. 代码结构速览

```
apps/portal-api/       平台后端（FastAPI）：身份/会话/编排入口/审计/报告代理/血缘图接口
apps/portal-web/       对话前端（零构建静态迭代版，含证据面板；B4 将升级为 Vite+TS）
packages/dip-contracts/契约层：统一结果模型 + status 判定（**只许 pydantic + 标准库**）
packages/dip-agent/    对话编排：实体识别/意图/计划/证据装配/答案成型（v2 迁到 skill + harness）
integrations/lineage-client/  内核客户端：参数归一化、success 判定、超时重试（**唯一耦合面**）
docs/adr/              架构决策（含"为什么"与"被否掉的备选"）
docs/collaboration/    协作规范、验证包、AI 提示词卡片
docs/planning/         v2 项目规划（里程碑 + 工作项边界与验收）
ops/                   启服务与门禁脚本
```

**分层铁律（有测试强制）**：依赖只能自上而下；`dip-contracts` / `dip-core` / `lineage-client` 不得依赖上层；只有 API 层知道数据库。

---

## 10. 必读清单（按顺序）

| # | 文档 | 你从中得到 |
| --- | --- | --- |
| 1 | 本文件 | 规矩与边界 |
| 2 | 目标 **Issue** | 具体要做什么、验收是什么 |
| 3 | [`docs/collaboration/2026-09-26-开发规范与AI协作.md`](docs/collaboration/2026-09-26-开发规范与AI协作.md) | 代码/测试/提交规范 + AI 边界与证据要求 |
| 4 | [`docs/collaboration/_AI_PROMPT_CARDS.md`](docs/collaboration/_AI_PROMPT_CARDS.md) | 可直接复制的提示词卡片（开工前/实现中/交活前/卡点） |
| 5 | [`docs/planning/2026-09-26-v2-项目规划-v1.md`](docs/planning/2026-09-26-v2-项目规划-v1.md) | 全局视野：里程碑、工作项、风险 |
| 6 | `docs/adr/` | 已定决策，**不要重新发明** |
| 7 | [`docs/collaboration/2026-09-25-协作者验证包.md`](docs/collaboration/2026-09-25-协作者验证包.md) | 环境细节与既有资产的可复用清单 |

---

## 11. 一句话总结

> **挑一个 Issue → 读边界与验收 → 小步改、带测试（含反例）→ 门禁绿 → 提 PR 并写清"怎么验证" → 等逐条验收。**
> 拿不准就停下来问，**不要猜、不要编、不要顺手改**。

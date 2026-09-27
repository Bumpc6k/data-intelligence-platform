# dsh 壳接我们的 skill（M4-02 / Issue #18）

一句话：**dsh 的 Web 壳里问业务问题，工具调用落到我们的血缘 skill，答案带回执校验。**

```
┌─ Windows ─────────────────────────┐        ┌─ WSL ──────────────────────────┐
│ dsh web (:3080, Node 24)          │        │ python -m dip_lineage_skill    │
│   └─ @deepseek-ai/dsh-mcp-client  │  HTTP  │      --transport http          │
│        serverName: lineage ───────┼───────►│      :18360/mcp（MCP）         │
│        tool: mcp__lineage__       │        │        │                       │
│              lineage_analyze      │        │        ▼ LineageClient         │
└───────────────────────────────────┘        │   内核 :18080（/upstream）      │
                                             └────────────────────────────────┘
```

## 1. 怎么接（一条命令 + 两步人工）

```bash
# WSL 里（skill 用仓库的 Linux venv）：
bash ops/dsh-connect.sh
#   [1/3] 起 skill 的 HTTP 传输（:18360/mcp，setsid 脱离会话）
#   [2/3] 把 ops/dsh/cordis.patch.mcp.yml 并进每个 profile 的 cordis.patch.yml
#   [3/3] 用真 MCP 客户端握手 + tools/list + 一次真调用（不靠"端口通了"就说成功）

# Git Bash 里（壳在 Windows 侧跑）：
export PATH="/c/Program Files/nodejs:/c/Users/Administrator/AppData/Roaming/npm:$PATH"
export DSH_HOME=D:/Projects/_dsh-probe/dsh-home     # ← 原生路径，别写 /d/...
set -a; . /d/Projects/data-intelligence-platform/.env; set +a
unset HTTPS_PROXY HTTP_PROXY
cd /d/Projects/_dsh-probe/dsh && pnpm dsh web --no-open
# 复制打印出来的 http://127.0.0.1:3080/?token=... 到浏览器，选工作区，然后问：
#   ads.ads_产销存月报 的产量怎么来的？用 mcp__lineage__lineage_analyze 查血缘
```

`--check` 只自检（不起服务、不改配置）。

## 2. 为什么走 HTTP 而不是 stdio

M1 的时候外壳是"内嵌驱动 + stdio 子进程"（`ops/demo-m1.sh`），因为壳和 skill 都在 WSL 里。
M4 的壳是 **Windows 上的 dsh**、skill 是 **WSL 里的 Linux venv**，让 Windows 去 WSL 起 stdio 子进程
要套一层 `wsl.exe`，链路长、错难查。改成 skill 监听一个口：
WSL 与 Windows 在 mirrored 网络模式下**共享 loopback**，两边用 `127.0.0.1:18360` 就能通
（Windows 侧实测裸 `GET /mcp` 返回 400 —— 那是 MCP 对裸 GET 的正常回应，说明通）。

stdio 仍然保留（默认行为没变）：`python -m dip_lineage_skill` 还是 stdio，`--transport http` 才是 HTTP。

## 3. 配置写在哪

dsh 的 profile 在 `$DSH_HOME/profiles/<name>/`，**每个入口一个 profile，配置不共享**：
`web`、`headless` 各一份 `cordis.patch.yml`。所以 `ops/dsh-connect.sh` 对**每个** profile 都写一遍。

写的是 `cordis.patch.yml`（用户 patch 层）—— dsh 的注释明说"编辑 patch，不要改 `cordis.yml`"；
我们不改 dsh 任何源码。

## 4. 失败路径（三种，都实测过）

| 情形 | 外壳里看到什么 | 说明 |
| --- | --- | --- |
| **模型不可用**（密钥无效） | `处理失败 / 本轮运行失败 API 密钥无效 / AUTH` | 外壳自己的错误，跟我们的链路无关 |
| **skill 也挂了**（内核挂 + skill 进程没了） | 模型如实说"两次调用只返回 `Error: fetch failed`，没拿到回执…具体原因从这条报错看不出来，我不编造" | 传输层失败，没有回执可读 —— 模型没有编造原因（这条也是我们要的行为） |
| **内核挂了但 skill 在** ✅ | 模型把**回执**逐字段贴出来：`ok/usable=false`、`error=重试 1 次后仍失败：连接失败：[Errno 111] Connection refused`、`evidence_count=0`、`http_status=null`、`attempts=2`，并说明"按无证据不出结论的规则，本轮不能给出上游表数量" | **这是我们想要的失败路径**：精确原因来自回执，不是猜的 |

三种情形的原始文字与截图都在 `docs/evidence/issue-18-dsh-shell.txt` 与 `docs/evidence/issue-18/`。

## 5. 真跑踩到的坑（都别再踩）

| 坑 | 现象 | 正解 |
| --- | --- | --- |
| 新增插件必须裹在 **`insert:`** 里 | 顶格写 `- id: …` 是"按 id 覆盖已有行"，目标不存在时 dsh 只打一句 warning **然后什么都不做**（静默无效，最难查） | 照 `packages/bundle/base/cordis.patch.yml` 的写法：`- insert:` 下面再列行 |
| profile 按入口分 | 只写了 `web`，`dsh headless` 里工具照样不存在 | `web` / `headless` 各写一遍（脚本已经循环 write） |
| 生成的 patch 占位是 `[]` | 直接往后追加列表项 → 两个 YAML 文档 → 启动报 `document separator is expected` | 合并前先把 `[]` 那行去掉 |
| **DSH_HOME 必须写原生路径** | 写 `/d/Projects/...` → node 当成"当前盘下的相对路径"，造出 `D:\d\Projects\...` 一套**镜像目录**（里面还有 pnpm store），读不到真配置 | 用 `D:/Projects/_dsh-probe/dsh-home` |
| `nohup` 不够，要 `setsid` | 用 `wsl.exe … -- bash 脚本` 起的后台服务，命令一返回进程就被收走，几分钟后无声消失（表现是外壳报 `fetch failed`） | `setsid nohup … &` + `disown`（`ops/dsh-connect.sh` 里已这么写） |
| 端口被旧实例占着 | `dsh web` 报 `EADDRINUSE 127.0.0.1:3080`，而旧实例**没有新配置**，让人误以为"配置没生效" | 先 `netstat -ano | grep :3080` 找到 PID 杀掉再起 |
| 工具名加了前缀 | 契约名 `lineage.analyze`，外壳里叫 `mcp__lineage__lineage_analyze` | MCP 客户端统一加 `mcp__<serverName>__` 前缀（避免多服务撞名），契约名不变，靠回执 `skill` 字段对上 |
| 工具超时 vs 重试 | 外壳工具超时 60s，skill 对内核的失败重试可能更久 | 内核不可达时是"连接被拒"立刻返回（毫秒级），所以不会撞超时；真出现慢失败就把 `toolCallTimeoutMs` 或 skill 的 retries 调小 |

## 6. 边界（Issue 明确不做）

- **不做多用户与权限**：本地单用户演示口子，`failOnStartupError: false` 让"skill 没起"不拖垮外壳。
- **不改 dsh 源码**：只写 `$DSH_HOME` 下的 profile patch 层。
- **不动 M1 的 stdio 路径**：`ops/demo-m1.sh` 那条链一个字没改。
- **不在壳里渲染图表**：M4-01 的视图机制在 portal-web；这里 dsh 用自己的界面渲染工具结果与回答。

## 7. 停掉现场

```bash
# 停壳（Windows）
powershell -NoProfile -Command "Stop-Process -Id (Get-NetTCPConnection -LocalPort 3080 -State Listen).OwningProcess -Force"
# 停 skill（WSL）；18360 那一路
pkill -f "dip_lineage_skill --transport http"
```

# dsh 起壳与版本锁定（M1-01）

> 对应 Issue #2 ｜ 落点：`docs/collaboration/notes/`
> 环境：Windows 11 + **WSL2 Ubuntu 24.04**（本仓 `ops/*.sh` 与 `bash ops/gate.sh` 都跑在 WSL 里，所以 dsh 也放这儿）
> 本文命令与输出**均为本机实跑所得**，原始记录见 `docs/evidence/m1-01/`

## 0. 一句话

装一个 Node、把 npm 指向国内镜像、**按精确版本**装 `@deepseek-ai/dsh`，然后用启动时打印的 **带 token 的 URL** 打开 Web 壳就能对话。
本文同时给出**版本锁定写法**（`ops/dsh/`）、**4 类已知坑**与**验证命令**。

## 1. 版本与环境（本次实测）

| 项 | 值 | 说明 |
| --- | --- | --- |
| OS | WSL2 Ubuntu 24.04（Windows 11 宿主） | 与 `ops/*.sh`、门禁同一环境 |
| Node | **v24.21.0**（linux-x64） | dsh 的 `engines` 要求 `^22.19.0 \|\| >=24.0.0` |
| npm | 11.19.0 | 随 Node 一同安装 |
| dsh | **0.1.5-rc.3**（本次锁定） | 镜像 `latest` 解析到的就是它；`next` 是 0.1.7-rc.2 |
| 包源 | https://registry.npmmirror.com | **必须**，见坑 1 |
| Web 端口 | **3080**（默认） | `dsh web --no-open --port 3080 --host 127.0.0.1` |
| `DSH_HOME` | `~/.dsh`（首次启动自动创建） | 内含 `profiles/`、`storages/`、`.credentials.yaml` |

> Node 采用"解压即用"的官方 tar.xz，放在 `/opt/dsh-toolchain/node-v24.21.0-linux-x64`：不碰系统包管理器，也不依赖 nvm。

## 2. 从零到能对话（5 步，可直接复制）

```bash
# ---- 0) 前置：镜像源（本机 nodejs.org / registry.npmjs.org 不可达或极慢）----
export NPM_MIRROR=https://registry.npmmirror.com

# ---- 1) 装 Node（tar.xz，解压即用）----
sudo mkdir -p /opt/dsh-toolchain && cd /opt/dsh-toolchain
curl -sL -o node-v24.21.0-linux-x64.tar.xz \
  https://registry.npmmirror.com/-/binary/node/v24.21.0/node-v24.21.0-linux-x64.tar.xz
tar -xJf node-v24.21.0-linux-x64.tar.xz
export PATH=/opt/dsh-toolchain/node-v24.21.0-linux-x64/bin:$PATH
node -v          # v24.21.0
npm -v           # 11.19.0

# ---- 2) npm 指向镜像 ----
npm config set registry $NPM_MIRROR
npm config get registry          # https://registry.npmmirror.com/

# ---- 3) 装 dsh：精确版本，不写 latest、不写 ^ ----
npm install -g @deepseek-ai/dsh@0.1.5-rc.3
dsh --version                    # 0.1.5-rc.3

# ---- 4) 起 Web 壳（日志会打印带 token 的 URL）----
export DEEPSEEK_API_KEY=sk-xxx                       # 可放在本仓 .env（注意坑 4）
export DEEPSEEK_BASE_URL=https://api.deepseek.com    # 必须 export，不能进 .env
dsh web --no-open --port 3080 --host 127.0.0.1
# stdout 第一行即要用的地址：
#   dsh web: http://127.0.0.1:3080/?token=<每次启动随机>

# ---- 5) 不用浏览器也能验证"真能对话"（最快冒烟）----
dsh --profile headless "reply with the single word PONG"   # -> PONG
```

**打开 Web 壳的姿势（重要）**：复制第 4 步打印的**整条带 `?token=` 的 URL**。
直接访问 `http://127.0.0.1:3080/` 会得到 **HTTP 401** —— 这是 dsh 的"浏览器信任栅栏"，**不是启动失败**（坑 2）。带 token 首次访问会返回 **303 并下发信任 cookie**（浏览器自动跟随），之后访问 `/` 才是 200（实测 27724 字节、`<title>DeepSeek Harness</title>`）。

**首屏还要两步才能对话**：先点内测声明里的「继续」，再点「选择工作区」挑一个目录（本次用 `/root/dsh-workspace`）。
工作区选定后提示语从"选择一个工作区开始"变为"描述你想要构建的内容…"，输入框才可用。

**能对话的存证**：
- `docs/evidence/m1-01/dsh-web-chat.png`：Web 壳里问「只回答数字：1 加 1 等于几？」→ 回答 `2`，显示 `用量 8.2K tok`、`154 tok/s`
- `docs/evidence/m1-01/03-headless-chat.txt`：headless 一问一答原始输出（英文 PONG + 中文提问）
## 3. 版本锁定写法（不追 latest）

**为什么不能用 `npm install -g @deepseek-ai/dsh`**：不带版本号 = 取 `latest`（今天解析到 0.1.5-rc.3，明天可能是别的），而且全局安装没有 lockfile，**整棵依赖树不可复现**。dsh 是 v0.1 预览版、会有破坏性变更（《v2 项目规划 v1》§6 已列为风险），所以必须锁。

**本仓的锁法**：`ops/dsh/` —— 精确版本 + 锁文件 + 自带镜像源：

```
ops/dsh/
├── package.json        # "@deepseek-ai/dsh": "0.1.5-rc.3"   ← 精确版本，不带 ^ / ~
├── package-lock.json   # 整棵依赖树的锁定（这才是真正的"锁"）
├── .npmrc              # registry=https://registry.npmmirror.com（随仓库走，不依赖个人配置）
└── .gitignore          # node_modules/
```

**一个取舍要写明**：`package-lock.json` 里的 `resolved` 记的是**镜像地址**（`registry.npmmirror.com`）。本机（受限网络）必须这样；若同事那边直连 npm 官方源更顺，改 `.npmrc` 后重跑 `npm install --package-lock-only` 重新生成即可，**不要手改 lock 文件**。

用法（复现同一棵树，而不是"再解析一次"）：

```bash
cd ops/dsh
npm ci                                # 严格按 package-lock.json 装，对不上就报错
./node_modules/.bin/dsh --version     # 0.1.5-rc.3
npm ls @deepseek-ai/dsh               # 确认树里只有这一个版本
```

**升级流程**（有意为之，而不是被动漂移）：

```bash
cd ops/dsh
npm install @deepseek-ai/dsh@<新版本> --package-lock-only   # 只重算锁，不下载安装
npm ci && ./node_modules/.bin/dsh --version                # 复核版本
# 再重跑 §2 的第 5 步与 Web 壳冒烟，把新结果补进 docs/evidence/m1-01/
```

**版本漂移要记录在案**（2026-09-26 实测）：
镜像 `dist-tags` = `latest: 0.1.5-rc.3`、`next: 0.1.7-rc.2`、`alpha: 0.1.7-alpha.2`；上游仓库 master 声明 `0.1.7-rc.2`。
**我们的选择**：锁"装得上且跑通过"的 `0.1.5-rc.3`，并把两个号都记录在 §1；换版本时必须写清**以哪个源为准**，不要混着说。
## 4. 已知坑（都踩过；前 3 条是 Issue #2 指定的）

| # | 坑 | 现象 | 正确做法 |
| --- | --- | --- | --- |
| **1** | **必须走 `registry.npmmirror.com`** | 直连 `nodejs.org` / `registry.npmjs.org` 不通或极慢（实测 ~0.05MB/s） | `npm config set registry https://registry.npmmirror.com`；**Node 本体也从镜像取**（`.../-/binary/node/`，走 nodejs.org 会卡住） |
| **2** | **裸访问 `/` 返回 401** | 打开 `http://127.0.0.1:3080/` 得到 401，看着像"服务没起来" | 用启动行打印的**整条 `?token=...` URL**；无 token 的 `/` 返回 401 是**设计如此**（浏览器信任栅栏），`--trusted-host` 可加白名单 |
| **3** | **安装会跳过原生模块 install script** | npm 打印 `5 packages have install scripts not yet covered by allowScripts`：`@deepseek-ai/dsh-subprocess-local` / `koffi` / `node-pty` / `@google/genai` / `protobufjs` | 这些包自带 prebuild，**实测 headless 与 Web 壳都能跑**，不影响本阶段；要执行脚本用 `npm install -g --allow-scripts=<包名列表>`。**别把这条警告当失败** |
| **4** | **`.env` 里不能写 `DEEPSEEK_BASE_URL`**（本次新发现，坑里最阴的一条） | 在仓库根目录启动 dsh 会直接抛错退出：`dsh: <path>/.env sets "DEEPSEEK_BASE_URL", which only the launching environment may set ...; export DEEPSEEK_BASE_URL instead of putting it in a .env file` | dsh 定义了 **bootstrap-only 名单**：`DSH_*` / `XDG_*` / `LD_*` / `DYLD_*` / `BASH_FUNC_*` 前缀，以及 `PATH`、`NODE_OPTIONS`、`PYTHONPATH`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_SEARCH_BASE_URL`、`SSL_CERT_FILE`、各类代理等具体名。这些**只能由继承环境提供**；代理类额外允许写在 `$DSH_HOME/.env`（不随仓库走）。**做法：`export DEEPSEEK_BASE_URL=...`，仓库 `.env` 只保留 `DEEPSEEK_API_KEY` 这类可放行变量** |
| 5 | `dsh --dump-config` 必须带 `--profile` | 不带时把 usage 错误打到 stdout，看着像"配置只有一行" | `dsh --profile web --dump-config` |
| 6 | 首次启动会建很大的 profile 树 | `$DSH_HOME/profiles/node_modules` 体积大、耗时 | 预留磁盘与时间；不要在 CI 里反复冷启 |
| 7 | Web UI 首屏不能直接聊天 | 界面停在内测声明 / 工作区选择 | 先点「继续」→ 点「选择工作区」选目录，输入框才出现 |
| 8 | `@modelcontextprotocol/sdk` 用 CJS `require` 失败 | 报 `Cannot find module .../dist/cjs/index.js` | 它是 **ESM-only**，CJS 加载失败是**预期行为**，不是装坏了（M1-03 接 MCP 时会碰到） |
## 5. 起停与排障

```bash
# 端口是否在听
ss -ltnp | grep 3080

# 冒烟三连（不需要浏览器）
curl -s -o /dev/null -w "GET /                 -> %{http_code}\n" http://127.0.0.1:3080/           # 期望 401
curl -s -c /tmp/cj -o /dev/null -w "GET /?token=...     -> %{http_code}\n" \
  "http://127.0.0.1:3080/?token=<token>"                                                          # 期望 303（下发 cookie）
curl -s -b /tmp/cj -o /tmp/idx.html -w "GET / (带 cookie)   -> %{http_code}\n" http://127.0.0.1:3080/
grep -o "<title>.*</title>" /tmp/idx.html                                                         # DeepSeek Harness
# 浏览器里不用管这些：打开启动行打印的整条 URL，303 是自动跟随的。

# 停掉
pkill -f "dsh web"
```

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| `GET /` 401 | 没带 token | 用启动行里的完整 URL（坑 2） |
| 启动即抛 `sets "..." which only the launching environment may set` | `.env` 里写了 bootstrap-only 变量（如 `DEEPSEEK_BASE_URL`） | 改成 `export`（坑 4） |
| 端口被占 | 3080 已被占用 | `--port <其它>`；**换端口后 URL 里的端口也要改** |
| `npm install` 卡住 | 没走镜像 | `npm config get registry` 确认（坑 1） |
| 界面停在内测声明/工作区选择 | 首屏必须走两步 | 「继续」→「选择工作区」（坑 7） |

## 6. Windows 原生（备用路径，非推荐）

Windows 侧也验证过能跑（便携 Node 解压 + `npm install -g --prefix <目录>` + `dsh.cmd web`），**但本项目的正式环境是 WSL**：
《双人分工与 Windows 数据开发约定》§2 明确"**不要在 Windows 上跑平台服务**"（路径、venv 布局、编码都会带来假问题）。所以本文的正式路径是 WSL，Windows 只作为排障时的旁证。

## 7. 边界声明（按 Issue 要求，**没做**的事）

- **不接业务 skill**：没有注册任何 MCP skill、没有碰血缘服务（那是 M1-03）。
- **不做鉴权与多用户**：用的是 dsh 自带的浏览器信任栅栏，没有账号体系、没有权限模型。
- **不改 dsh 源码**：只做安装、配置、启动、文档；没有打补丁、没有 fork、没有改它内部的 `.env` 规则。
- 也没做：systemd / 常驻守护、多机部署、TLS、性能优化（均不在 M1-01 范围内）。

## 8. 证据索引（`docs/evidence/m1-01/`）

| 文件 | 内容 |
| --- | --- |
| `dsh-web-chat.png` | **能对话的截图**：Web 壳里问「只回答数字：1 加 1 等于几？」→ 回答 `2`，`用量 8.2K tok` |
| `dsh-web-boot.png` | Web 壳启动、工作区已选定的界面（含模型 `DeepSeek-V41-Flash`） |
| `01-node-and-dsh-versions.txt` | `node -v` / `npm -v` / `dsh --version` / 镜像 dist-tags 原始输出 |
| `02-dsh-web-boot.txt` | 启动行（token 已脱敏）+ `/` 401 与带 token 200 探测 + 端口监听 |
| `03-headless-chat.txt` | headless 一问一答原始输出（英文 PONG + 中文提问） |
| `04-pitfall-env-bootstrap.txt` | 坑 4 的可复现原始报错 |
| `05-version-lock.txt` | `ops/dsh` 锁文件内容摘要与实际解析出的版本 |

> 复跑方式：按 §2 走一遍 + `bash ops/gate.sh`（门禁）；下面的命令即可自查：
> `bash ops/gate.sh && curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3080/`
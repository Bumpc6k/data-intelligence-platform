# DeepSeek Harness (dsh) Windows 从零到能对话 —— 安装文档与踩坑记录

> 目标机器：Windows 11 + Git Bash（MSYS）。本文所有命令均在 **Git Bash** 中执行（不是 PowerShell、不是 cmd）。
> 实测锁定版本：**dsh `0.1.7-rc.2`**（Git tag `dsh-v0.1.7-rc.2`，commit `477b4f420553e8a52c2fbccc464d7561b239c443`）
> 实测日期：2026-09-26
> **结论：从零到能对话全链路已实测通过**（含真实模型往返，见 §5.1）。版本、401、tag/commit 三项关键结论另由第二位执行者独立复核，复核输出见 `docs/evidence/issue-2-dsh-setup.txt`。

---

## 0. 一句话结论

在 Windows 上用 **源码安装** 这条路是通的：`Node 24.19.0 + pnpm 11.7.0`，`pnpm install` 约 1 分 30 秒，`pnpm run build` 约 3 分钟，然后 `pnpm dsh web` 就能在 `http://127.0.0.1:3080` 起一个带 token 校验的 Web UI。
**最后一步“真的发消息给模型”需要你自己的 DeepSeek API key**，本文不包含任何密钥。

---

## 1. 环境前置

### 1.1 版本要求

| 组件 | 要求 | 本次实测 |
|---|---|---|
| Node.js | `^22.19.0 \|\| >=24.0.0`（仓库 `package.json` 的 `engines`） | **v24.19.0** |
| pnpm | 仓库 `packageManager` 字段锁死 `pnpm@11.7.0` | **11.7.0** |
| npm | 随 Node 附带 | 11.17.0 |
| git | ≥ 2.26（lefthook hooks 需要 worktree-specific config） | 2.55 |

### 1.2 安装 Node（winget）

```bash
winget install --id OpenJS.NodeJS.LTS -e --silent --accept-package-agreements --accept-source-agreements
```

实测输出结尾：

```
Found Node.js (LTS) [OpenJS.NodeJS.LTS] Version 24.19.0
Downloading https://nodejs.org/dist/v24.19.0/node-v24.19.0-x64.msi
Successfully verified installer hash
Starting package install...
Successfully installed
```

> **坑（Git Bash 特有）**：winget 装完的 exe **不在当前 bash 会话的 PATH 里**。Node 的 MSI 会装到 `C:\Program Files\nodejs`，**不是** WinGet Links 目录。每次开新 shell 要么重开终端，要么手工加：
>
> ```bash
> export PATH="/c/Program Files/nodejs:$PATH"
> ```

### 1.3 安装 pnpm（不要用 corepack）

```bash
export PATH="/c/Program Files/nodejs:$PATH"
npm config set registry https://registry.npmmirror.com
npm install -g pnpm@11.7.0
```

`npm prefix -g` 实测是 `C:\Users\Administrator\AppData\Roaming\npm`，pnpm 的 shim 装在这里，所以完整 PATH 是：

```bash
export PATH="/c/Program Files/nodejs:/c/Users/Administrator/AppData/Roaming/npm:$PATH"
node --version    # v24.19.0
npm  --version    # 11.17.0
pnpm --version    # 11.7.0
```

> **坑**：官方文档建议 `corepack enable`，但在 **Git Bash 下 corepack 是坏的**——MSYS 路径转换会把 `C:\Program Files\nodejs` 变成 `D:\c\Program Files\nodejs`，直接报：
>
> ```
> Error: Cannot find module 'D:\c\Program Files\nodejs\node_modules\corepack\dist\corepack.js'
> ```
>
> 用 `npm install -g pnpm@11.7.0` 绕开，版本号与仓库 `packageManager` 字段一致，效果相同。

### 1.4 网络

| 目标 | 直连结果（实测，不带代理） |
|---|---|
| `github.com` | ❌ 超时（`curl` code=000，20s 超时） |
| `registry.npmjs.org` | ✅ 通，但慢（见 D1） |
| `registry.npmmirror.com` | ✅ 通，快 |

- 拉 GitHub 仓库：走代理 `export HTTPS_PROXY=http://127.0.0.1:10808 HTTP_PROXY=http://127.0.0.1:10808`
- 装 npm 依赖：走镜像 `registry.npmmirror.com`（**不需要代理**）

---

## 2. 版本锁定写法（关键）

**绝对不要用 `latest`。** 实测 `@deepseek-ai/dsh` 在 npm 上的 dist-tags 是：

```
dist-tags: {"alpha":"0.1.7-alpha.2","latest":"0.1.5-rc.3","next":"0.1.7-rc.2"}
```

也就是说 **`latest` 指向 `0.1.5-rc.3`，比仓库 HEAD（`0.1.7-rc.2`）老了两个小版本**。`npx @deepseek-ai/dsh web` 拿到的是旧版。

### 2.1 源码安装（本文推荐路径）——按 tag 克隆

```bash
export HTTPS_PROXY=http://127.0.0.1:10808 HTTP_PROXY=http://127.0.0.1:10808
git clone --branch dsh-v0.1.7-rc.2 --depth 50 \
  https://github.com/deepseek-ai/deepseek-harness.git dsh
cd dsh
```

实测：

```
Note: switching to '477b4f420553e8a52c2fbccc464d7561b239c443'.
```

校验锁定的 commit：

```bash
git rev-parse HEAD
# 477b4f420553e8a52c2fbccc464d7561b239c443
git tag --points-at HEAD
# dsh-v0.1.7-rc.2
```

### 2.2 npm 安装（备选）——必须带精确版本号

```bash
npx @deepseek-ai/dsh@0.1.7-rc.2 web      # ✅ 正确
npx @deepseek-ai/dsh web                 # ❌ 会装到 0.1.5-rc.3
```

### 2.3 可直接复制的锁定片段

仓库根 `package.json`（**上游原文，不要改**，这就是版本锁的来源）：

```jsonc
{
  "name": "@deepseek-ai/dsh-root",
  "version": "0.1.7-rc.2",
  "private": true,
  "type": "module",
  "packageManager": "pnpm@11.7.0",          // ← pnpm 版本锁
  "engines": {
    "node": "^22.19.0 || >=24.0.0"          // ← Node 版本范围
  }
}
```

`pnpm-lock.yaml`（由 `--frozen-lockfile` 保证不漂移）：

```yaml
lockfileVersion: '9.0'

settings:
  autoInstallPeers: true
  excludeLinksFromLockfile: false
```

要绝对复现，装依赖时加 `--frozen-lockfile`：

```bash
pnpm install --frozen-lockfile --registry=https://registry.npmmirror.com
```

CI / 生产建议直接用上述命令；本地首次调试可以省掉 `--frozen-lockfile`。

---

## 3. 从零到能对话（逐条命令）

**全部在 Git Bash 里执行。**

### 步骤 1：准备 PATH 与代理

```bash
# Node + pnpm 的 shim 目录
export PATH="/c/Program Files/nodejs:/c/Users/Administrator/AppData/Roaming/npm:$PATH"

# 只有拉 GitHub 才需要代理；装依赖走镜像不需要
export HTTPS_PROXY=http://127.0.0.1:10808 HTTP_PROXY=http://127.0.0.1:10808
```

验证：`node --version && pnpm --version` → `v24.19.0` / `11.7.0`

### 步骤 2：克隆并锁定版本

```bash
mkdir -p /d/Projects/_dsh-probe && cd /d/Projects/_dsh-probe
git clone --branch dsh-v0.1.7-rc.2 --depth 50 \
  https://github.com/deepseek-ai/deepseek-harness.git dsh
cd dsh
```

### 步骤 3：装依赖（走国内镜像）

```bash
pnpm install --registry=https://registry.npmmirror.com
```

实测：`Done in 1m 30.2s using pnpm v11.7.0`（1387 个包）。
期间会跑 `postinstall` 装 lefthook git hooks，属正常。

> 想更严格复现 `pnpm install --frozen-lockfile --registry=https://registry.npmmirror.com`

### 步骤 4：构建

```bash
pnpm run build
```

实测结尾：

```
build: recorded 343 client artifact(s) with 2 public value(s)
```

内部依次跑 `build:native-system` → `build:lib`（tsc + tsdown）→ `build:web`（vite）。

### 步骤 5：启动 Web UI

```bash
pnpm dsh web --no-open
```

实测输出：

```
$ node --import tsx/esm apps/cli/src/bin.ts "web" "--no-open"
dsh web: http://127.0.0.1:3080/?token=<一串 per-process 随机 token>
```

- 默认端口 **3080**（可用 `--port 8080` 改）
- `--no-open` 表示不自动开浏览器
- **这个带 `?token=` 的 URL 必须完整复制**，去掉 token 会 401（见 D2）
- 首次启动会在 `$DSH_HOME`（默认 `~/.dsh`）下自动生成 `profiles/web/`，无需手工创建

### 步骤 6：打开 UI 并配置模型（这一步需要你自己的 key）

1. 浏览器打开上面打印的**完整** URL（含 `?token=`）。
2. 首次访问会被 303 重定向到不带 token 的干净地址，并写入一个 30 天有效的 HttpOnly cookie —— 之后就正常了。
3. 进入 **Settings → Models**，填入你的 DeepSeek API key，保存。
   - 环境变量名：**`DEEPSEEK_API_KEY`**
   - UI 里是 `credentialOnboarding` 引导填，也可以启动时用环境变量注入：`DEEPSEEK_API_KEY=<你的key> pnpm dsh web`
   - 注意：**启动时从环境变量注入的 key 优先级最高且不可在 UI 里覆盖**（会被标记为只读）。
4. 点击 **Choose workspace**，选择你启动 `dsh` 的目录。
   - ⚠️ 新装的 Web UI **默认没有选中任何 workspace，没选 workspace 之前输入框是禁用的**——这是最容易卡住人的一步。
5. 新建会话，发送 `Summarize this repository and identify its main packages.`

---

## 4. 已知坑与解（D1 / D2 / D3）

### D1 —— 装依赖必须走 `registry.npmmirror.com`

**结论：方向正确，但“否则装不动”这句是夸张的。**

实测同一棵依赖树（15 个包，冷 store，不走代理）对比：

| registry | 结果 | 耗时 |
|---|---|---|
| `registry.npmmirror.com` | ✅ 全部装好 | **15.9s** |
| `registry.npmjs.org` | ✅ 也能装好 | ~70s |

npmjs 侧还刷了一堆告警：

```
[WARN] Request took 14066ms: https://registry.npmjs.org/@types%2Fnode
[WARN] Tarball download average speed 43 KiB/s (size 171 KiB) is below 50 KiB/s:
       https://registry.npmjs.org/layout-base/-/layout-base-2.0.1.tgz (GET)
```

元数据接口裸测（各 5 次，不带代理）：npmjs 0.80 ~ 1.71s，npmmirror 0.06 ~ 0.16s，**npmmirror 稳定快约 10 倍**。

**准确的结论**：在这台机器上 npmjs **是通的**，不会被墙死；但它慢 ~4.4 倍且频繁触发 <50KiB/s 告警。1387 个包的 dsh 主仓库如果走 npmjs，耗时会从 1m30s 膨胀到十几分钟甚至因为单请求超时而失败，**所以在国内用 npmmirror 是强烈建议，理由是速度与稳定性，而不是“完全连不上”**。

另外，dsh 自己就把 npmmirror 写成了默认兜底源 —— `packages/boot/plugin-manager/README.md`：

| 字段 | 默认值 | 说明 |
|---|---|---|
| `fallbackRegistries` | `['https://registry.npmmirror.com/']` | 前一个注册表不可达或没有该包副本时依次询问的注册表 |

**解法**：

```bash
npm config set registry https://registry.npmmirror.com   # 写入 ~/.npmrc，一次即可
# 或每次显式传：
pnpm install --registry=https://registry.npmmirror.com
```

---

### D2 —— 裸访问会 401，必须带 token

**结论：完全证实。** 这是 dsh 的浏览器会话认证（`packages/client/connection/src/browser-auth.ts`）。

**裸访问根路径**（实测）：

```bash
curl -s -i http://127.0.0.1:3080/
```

```
HTTP/1.1 401 Unauthorized
Cache-Control: no-store
Content-Type: text/plain; charset=utf-8

dsh web authentication required; reopen the URL printed by dsh web.
```

API 也一样：

```bash
curl -s -i -X POST http://127.0.0.1:3080/api
# HTTP/1.1 401  body: unauthorized
```

**带 token 访问**（实测）：

```bash
curl -s -i "http://127.0.0.1:3080/?token=<启动时打印的 token>"
```

```
HTTP/1.1 303 See Other
Location: ./
Set-Cookie: dsh-auth-VPhEEcLKeqRDBoBalzN2Nm7CnfxKhLE00pKIDWxt1sw=v1.eyJ2ZX...UB-4;
            Max-Age=2592000; Path=/; Expires=Mon, 26 Oct 2026 13:39:48 GMT;
            HttpOnly; SameSite=Strict
```

用 cookie jar 走完整链路，最后拿到真页面：

```bash
curl -s -L -c cj.txt -b cj.txt -o body.html -w "%{http_code}\n" \
  "http://127.0.0.1:3080/?token=<token>"
# 200   text/html; charset=utf-8   34239 bytes
```

页面里有 `<title>DSH Local Build</title>`、`<div id="root">`、以及 66 条客户端插件清单。

**关键细节**：

- token 是 **每次进程启动重新随机生成的**（`randomBytes(32)` → base64url），重启 `dsh web` 就换一个，**必须重新复制新的 URL**。
- 只有 **index 页面**（`/` 或配置的 index 路径）需要认证。**普通静态资源是公开的**，实测：
  - `/assets/index-Q6zc2uHV.js` → `200`（无需认证）
  - `/manifest.webmanifest` → `200`
  - `/plugins/??@deepseek-ai/dsh-client-modules/client.js&rev=...` → `200`，40330 bytes（模块联邦引导包，前端靠它启动）
- 带 token 的 URL 只能用于 `GET /` 且 `token` 参数恰好出现一次；其它情况一律 401。

**解法**：别手敲 URL，直接把终端打印的那一整行 `dsh web: http://...` 复制到浏览器。cookie 有效期 30 天（`Max-Age=2592000`），期间刷新页面不用再带 token。

---

### D3 —— 安装会跳过原生模块的 install script，需要补

**结论：证实，而且项目源码里写明了机制和补救方式。**

**机制**：pnpm 10+ 起，任何带 install/build script 的依赖默认**不予执行**，除非被显式允许。在 dsh 仓库里用的是 `pnpm-workspace.yaml` 的 **`allowBuilds`** 键。

**最小复现**（我单独建的探针工程，14 个包，其中 esbuild 带 postinstall）：

不写 `allowBuilds` 时：

```
[ERR_PNPM_IGNORED_BUILDS] Ignored build scripts: esbuild@0.28.1

Run "pnpm approve-builds" to pick which dependencies should be allowed to run scripts.
# 退出码 1   ← esbuild 的 install.js 根本没跑
```

补上之后：

```yaml
# pnpm-workspace.yaml
allowBuilds:
  esbuild: true
```

```
.../esbuild@0.28.1/node_modules/esbuild postinstall: Done

Done in 15.9s using pnpm v11.7.0
# 退出码 0
```

**在 dsh 主仓库里，这个坑已经被官方预先填好了** —— `pnpm-workspace.yaml` 里有：

```yaml
allowBuilds:
  esbuild: true
  lefthook: true
  node-pty: true     # Windows ConPTY 持久 PTY 后端
  koffi: true        # Windows 上 JSONL 持久化要调 MoveFileExW
  '@deepseek-ai/dsh-subprocess-local@file:packages/subprocess/subprocess-local': true
  electron-winstaller: false
  msgpackr-extract: false
  '@google/genai': false
  protobufjs: false
  node-addon-require-builtin: false
```

实测这些脚本**确实执行了**（`pnpm install` 日志原文）：

```
.../esbuild@0.28.1/node_modules/esbuild postinstall$ node install.js
.../.pnpm/koffi@3.1.1/node_modules/koffi install$ node ./cnoke.cjs -P . -D src/koffi --prebuild --release
.../lefthook@2.1.9/node_modules/lefthook postinstall$ node postinstall.js
.../node_modules/node-pty install$ node scripts/prebuild.js || node-gyp rebuild
.../node_modules/node-pty postinstall: > Moving conpty.dll...
.../node_modules/node-pty postinstall:   Copying ...\third_party\conpty\1.25.260303002\win10-x64\conpty.dll -> ...\build\Release\conpty\conpty.dll
```

**那这个坑在哪里会真的咬人？两条路：**

1. **npm / npx 安装路径**（没有仓库自带的 `pnpm-workspace.yaml`）。
2. **往 profile 里装插件**。实测 dsh 首次启动自动生成的 `$DSH_HOME/profiles/web/pnpm-workspace.yaml` 只有：

   ```yaml
   packages:
     - .
   nodeLinker: hoisted
   autoInstallPeers: false
   ```

   **没有 `allowBuilds`**。此时装带原生模块的插件会直接撞上 `ERR_PNPM_IGNORED_BUILDS`。

   项目自己的 plugin-manager 就是为这件事写的（`packages/boot/plugin-manager/README.md:58`，原文摘录）：

   > When **pnpm 11 blocks dependency scripts**, the failed installation reports every pending package name in the profile under `pendingBuilds` ... The Web plugin page offers **Allow these scripts and retry** ... Approval **persists by package name in this profile**.

   对应的源码：`src/install-failure.ts` 把 `ERR_PNPM_IGNORED_BUILDS|Ignored build scripts` 归类为 `build-blocked`；`src/build-approval.ts` 读写 `pnpm-workspace.yaml` 里的 `allowBuilds`。

**解法（三选一）**：

```bash
# A. Web UI 里点 —— 插件安装失败时页面会出现 "Allow these scripts and retry"
# B. 手工写进对应 profile 的 pnpm-workspace.yaml
#    $DSH_HOME/profiles/web/pnpm-workspace.yaml
#    allowBuilds:
#      <报错的包名>: true
# C. 通用 pnpm 命令（交互式）
pnpm approve-builds
```

---

## 5. 怎么验证“跑通了”

**一条可复现命令**（服务已在跑）：

```bash
# 1) 裸访问应当是 401
curl -s -o /dev/null -w "bare=%{http_code}\n" http://127.0.0.1:3080/

# 2) 用启动时打印的 token 换 cookie 并跟随重定向，应当拿到 200 + HTML
curl -s -L -c cj.txt -b cj.txt -o body.html \
     -w "authed=%{http_code} type=%{content_type} bytes=%{size_download}\n" \
     "http://127.0.0.1:3080/?token=<粘贴启动日志里的 token>"

grep -c 'id="root"' body.html     # 期望 >= 1
```

**期望输出**：

```
bare=401
authed=200 type=text/html; charset=utf-8 bytes=34239
1
```

**实测记录**：

```
=== 1) BARE GET / (no token, no cookie) ===
HTTP/1.1 401 Unauthorized
dsh web authentication required; reopen the URL printed by dsh web.

=== 4) token exchange -> cookie -> follow to index ===
final_code=200 type=text/html; charset=utf-8 bytes=34239 url=http://127.0.0.1:3080/

=== bootstrap bundle ===
code=200 type=text/javascript; charset=utf-8 bytes=40330
```

浏览器里打开那个 URL 能看到聊天界面（侧边栏 / 会话列表 / 输入框），`<title>` 为 `DSH Local Build`。

### 5.1 终端侧对话（`dsh headless`）—— 验"能对话"最快的一条路

Web UI 要浏览器 + 先配好模型才能说话；**终端里一句 `dsh headless` 就能把最后一公里验完**：

```bash
export DSH_HOME=/d/Projects/_dsh-probe/dsh-home
set -a; . /d/Projects/data-intelligence-platform/.env; set +a   # 提供 DEEPSEEK_API_KEY
unset HTTPS_PROXY HTTP_PROXY                                    # DeepSeek 国内直连，别走代理
cd /d/Projects/_dsh-probe/dsh
pnpm dsh headless "用一句话说明：ads.ads_产销存月报 的产量口径为什么必须带来源脚本？"
```

**实测（2026-09-26，退出码 0，约 25 秒）**：模型没有空口作答 —— dsh 先给它工作区检索与读文件的能力，它自己找到了兄弟项目里的文件，然后回答：

> 因为「产量」在 `ads.ads_产销存月报` 里只是一个由来源脚本决定的派生结果——取数范围、过滤条件（`WHERE s.dt = ...`）和计算逻辑共同定义了它，不带上来源脚本（`examples/warehouse/ads/ads_产销存月报.sql`）就无法唯一说明、也无法追溯这个口径。

⚠️ **顺带实测到的两件事（用之前要知道）**：

1. `dsh headless` 能读工作区**之外**的目录（本次它读到了同盘的兄弟仓库）。它是「能干活的 agent」，不是「只回答问题的问答框」—— 别把它指向不该看的目录。
2. 它会**真的调用模型（计费）**。调试时挑最便宜的模型，别拿它当 `echo` 用。

---

## 6. 无法跑通的部分（如实记录）

| 项 | 状态 | 说明 |
|---|---|---|
| 服务启动、Web UI 加载、认证链路 | ✅ 已跑通 | 见第 5 节原始输出 |
| **真正发一条消息给模型并拿到回复** | ✅ **已验证（2026-09-26 复核追加）** | 见 §5.1：用 `dsh headless` 实测通过，退出码 0。模型不是空口作答——它自己搜工作区、读文件，给了一句**带出处**的中文回答。需要你自己的 DeepSeek API key（本仓库不含任何 key）。 |
| `npx @deepseek-ai/dsh@0.1.7-rc.2 web` 这条 npm 路径 | ⚠️ 未实测 | 只核实了 registry 上的 dist-tags 与版本号。选择源码路径是因为它能用 commit 精确锁定。 |
| 插件安装流程（`dsh plugin --profile web add ...`） | ⚠️ 未实测 | 只从源码/文档确认了 `ERR_PNPM_IGNORED_BUILDS` 的处理逻辑，没有真的装一个带原生模块的插件。 |

**过程中遇到的其它现象（非阻塞）**：

1. `pnpm install` 刷 `[WARN] Failed to create bin ... lib/bin.js.EXE`（3 条）—— 因为此时还没 `pnpm run build`，`lib/bin.js` 尚不存在。构建后消失，不影响使用。
2. `[WARN] Unsupported platform` × 4 —— `native/system/packages/{darwin,linux}-*` 在 Windows 上被跳过，符合预期。
3. `[WARN] There are cyclic workspace dependencies` —— 上游 monorepo 自身就有循环依赖，官方接受，不影响构建。
4. `build:native-system` 在 Windows 上是 **no-op**：`native/system/scripts/build.ts` 开头就判断 `process.platform !== 'linux' && !== 'darwin'` 时带 `--host-addon-only` 直接 `process.exit(0)`。所以 Windows 不需要 C 编译器。
5. 仓库自带的 lefthook `postinstall` 会写 **worktree-local 的 git hooks 配置**（改的是 `.git` 配置，不是源码）。本次安装后 `git status --porcelain` 为空，工作区干净，未产生任何提交。

---

## 附录 A：完整命令清单（可直接整段粘贴）

```bash
# ---------- 0. 环境 ----------
export PATH="/c/Program Files/nodejs:/c/Users/Administrator/AppData/Roaming/npm:$PATH"

# ---------- 1. 装 Node（若未装）----------
winget install --id OpenJS.NodeJS.LTS -e --silent \
  --accept-package-agreements --accept-source-agreements

# ---------- 2. 装 pnpm + 配镜像 ----------
npm config set registry https://registry.npmmirror.com
npm install -g pnpm@11.7.0

# ---------- 3. 拉代码（走代理，锁定 tag）----------
export HTTPS_PROXY=http://127.0.0.1:10808 HTTP_PROXY=http://127.0.0.1:10808
mkdir -p /d/Projects/_dsh-probe && cd /d/Projects/_dsh-probe
git clone --branch dsh-v0.1.7-rc.2 --depth 50 \
  https://github.com/deepseek-ai/deepseek-harness.git dsh
cd dsh
git rev-parse HEAD     # 应为 477b4f420553e8a52c2fbccc464d7561b239c443

# ---------- 4. 装依赖（走镜像，不需要代理）----------
unset HTTPS_PROXY HTTP_PROXY
pnpm install --registry=https://registry.npmmirror.com

# ---------- 5. 构建 ----------
pnpm run build

# ---------- 6. 启动 ----------
pnpm dsh web --no-open
# 复制打印出来的 http://127.0.0.1:3080/?token=... 到浏览器
```

## 附录 B：实测环境快照

```
OS            : Windows 11 (Git Bash / MSYS)
Node.js       : v24.19.0        (winget OpenJS.NodeJS.LTS)
npm           : 11.17.0
pnpm          : 11.7.0          (npm install -g pnpm@11.7.0)
git           : 2.55
dsh           : 0.1.7-rc.2      (tag dsh-v0.1.7-rc.2 / commit 477b4f420553e8a52c2fbccc464d7561b239c443)
默认端口      : 3080
pnpm install  : Done in 1m 30.2s (1387 packages)
pnpm run build: build: recorded 343 client artifact(s)
DSH_HOME      : D:/Projects/_dsh-probe/dsh-home  (首次启动自动生成 profiles/web/)
模型往返      : `dsh headless` 退出码 0，约 25s（真调 DeepSeek：自己检索工作区、读文件后带出处作答）
```

# 文档通道（M3-04 / Issue #15）

一句话：**WeKnora 是只读的文档来源，它的产出永远只能当背景说明，进不了结论。**

```
用户问一句话
   ├── 结构化通道（内核血缘 / 口径库）  ──►  结论（text + result + evidence + source）  ← 唯一能产结论的
   └── 文档通道（WeKnora，MCP 只读）    ──►  background（每条材料都带文档名 + 切片 id）  ← 只能当背景
```

## 1. 规矩落在哪一层（别问"靠不靠自觉"，三条都是代码）

| 规则 | 落在哪 | 怎么强制 |
| --- | --- | --- |
| 引用必须标注来源 | `dip_contracts/doc_channel.py::check_citations` | 缺文档名/切片 id 的材料**直接拒收**（`CitationMissing`），不是过滤掉 |
| 结论只取结构化通道 | `conclusion_contamination` + `attach_background(strict=True)` | 挂背景前后 `result` 必须逐字段相同，否则抛 `ConclusionContaminated` |
| 结论里的数字不许来自文档 | 同上（复用 #5 的 `guards.check_answer`） | 拿"结构化通道说过的数字"当白名单扫结论文本，新冒出来的数字即违规 |
| 背景渲染必带来源 | `render_background` | 唯一的渲染入口，每条都拼 `文档名#切片` 标签 |

**文档材料尤其不许进 `Result.evidence`**：铁律 1 是"无凭证不发布结论"，凭据链里一旦混得进文档，
文档就能给结论背书，整条约束当场作废 —— 所以测试里专门有一条反例盯着这件事。

## 2. 平台侧代码（三层，各管各的）

| 文件 | 层 | 管什么 |
| --- | --- | --- |
| `packages/dip-contracts/src/dip_contracts/doc_channel.py` | 契约 | 上面四条规则（纯函数，离线可测） |
| `packages/dip-contracts/src/dip_contracts/models.py` | 契约 | `DocCitation` / `DocHit` / `Background` 形状；`Answer.background` |
| `packages/dip-docs/src/dip_docs/weknora.py` | 取数 | 跟 WeKnora 的 MCP Server 说话，把返回收成 `DocHit`（解析是纯函数） |
| `apps/portal-api/src/portal_api/doc_support.py` | 编排 | 什么时候问文档通道、失败怎么记 |
| `apps/portal-api/src/portal_api/routers/doc_channel.py` | 接口 | `POST /api/knowledge/doc-search`、`GET /api/knowledge/doc-channel` |

接口：

- `POST /api/knowledge/doc-search {query, limit?}` → 背景材料 + `usable_for_conclusion: false`
  （每条带 `citation`，缺来源则 **502 并列问题**；通道没开则 **503**）
- `GET /api/knowledge/doc-channel` → 通道状态（启用与否、走哪个 MCP endpoint、只开了哪组工具；**不含令牌**）
- `POST /api/agent/ask {text, with_docs}` → `with_docs=true` 时同时问文档通道。
  **结论与不加时逐字节相同**，文档材料只进 `background`；通道失败只多一条 `tool_calls`（`ok=false` + 原因）。

## 3. WeKnora 部署（WSL，docker compose）

```bash
cd /root/projects/_weknora-src/WeKnora-main
docker compose pull          # 5 个镜像
docker compose up -d         # 起 6 个容器（含向量模型 ollama）
docker compose ps
```

一次性初始化（建号 → 建库 → 配模型 → 传文档 → 建只读 MCP 端点）用仓外脚本
`D:/Projects/_weknora-setup.sh`（幂等：端点存在就先删再建），它按 WeKnora 官方 quickstart 的
API 顺序调用；产出的入库信息（KB id / 端点 id / 令牌）写在 `D:/Projects/_weknora-conf/`，**不进仓**：
`admin-credentials.txt`（管理员口令）、`endpoint.json`（MCP 端点 URL + 令牌）。

| 服务 | 镜像 | 端口 | 说明 |
| --- | --- | --- | --- |
| `frontend` | `wechatopenai/weknora-ui:v0.8.2` | 18380 | Web UI |
| `app` | `wechatopenai/weknora-app:v0.8.2` | 18300 | API + **内置 MCP Server**（`/mcp/<endpoint_id>`） |
| `docreader` | `wechatopenai/weknora-docreader:v0.8.2` | — | 文档解析（gRPC） |
| `postgres` | `paradedb/paradedb:v0.22.6-pg17` | — | PG + 向量/全文检索 |
| `redis` | `redis:7.0-alpine` | — | 队列 |
| `ollama` | `ollama/ollama:latest` | — | 向量模型 `nomic-embed-text`（768 维） |

端口按既有约定错开：内核 18080 / portal-api 18100 / 模型网关 18200 → **WeKnora 用 183xx**。

### 3.1 三个必踩的坑

| 坑 | 现象 | 正解 |
| --- | --- | --- |
| 镜像 tag 带 `v` | `wechatopenai/weknora-app:0.8.2` → 镜像源返回 403（tag 不存在） | `WEKNORA_VERSION=v0.8.2`（Docker Hub 上就是带 v 的） |
| docker 镜像源 | daemon 里配的 ustc / 163 / 百度**域名根本不解析**，每次拉镜像白等 30 秒 | 只留 `https://docker.m.daocloud.io`；dockerd 走 Windows 侧 v2rayN 的 `127.0.0.1:10808`（WSL 是 mirrored 网络模式，与宿主共享 loopback） |
| ollama 安装脚本 | 官方脚本报 "requires zstd"（v0.34 只发 `.tar.zst`） | 先 `apt-get install zstd`，或直接下 `.tar.zst` 解压 |

### 3.2 模型怎么配（`config/builtin_models.yaml`，覆盖式挂载）

```yaml
builtin_models:
  - id: builtin-llm-deepseek        # 对话：**直连 DeepSeek**
    type: KnowledgeQA
    source: remote
    is_default: true
    name: ${LLM_MODEL_NAME}
    parameters: { base_url: ${LLM_BASE_URL}, api_key: ${LLM_API_KEY}, provider: ${LLM_PROVIDER} }
  - id: builtin-embedding-ollama    # 向量：compose 内的 ollama
    type: Embedding
    source: local
    is_default: true
    name: ${EMBEDDING_MODEL_NAME}
    parameters: { embedding_parameters: { dimension: 768, truncate_prompt_tokens: 0 } }
```

**为什么对话直连 DeepSeek 而不走平台模型网关**：平台网关**明确拒绝 `stream=true`**
（400 `stream_unsupported`，且刻意不做静默降级），而 WeKnora 的对话是流式的 —— 硬走网关只会拿到 400。
这是本项目唯一给外部服务开的口子，写在这里留痕。向量则**不出去**：`nomic-embed-text` 跑在本机 ollama。

### 3.3 MCP 端点（只读的边界在这）

WeKnora v0.8.2 内置 MCP Server（Python 版 `mcp-server/` 已弃用）：

- 管理接口：`/api/v1/mcp-endpoints`（建端点 / 轮换令牌 / 看工具目录）
- 服务地址：`http://127.0.0.1:18300/mcp/<endpoint_id>`（Streamable HTTP，自带 Bearer 令牌）
- **工具组只开 `retrieve`**：`search_knowledge`（我们用的）、`list_documents`（补文档名）。
  `ingest`（写文档）那一组**不授权** —— 不是"我们不去调"，是令牌根本没有那个能力。

平台侧配置（`.env`，默认关闭）：

```
DOCS_ENABLED=true
WEKNORA_MCP_URL=http://127.0.0.1:18300/mcp/<endpoint_id>
WEKNORA_MCP_TOKEN=<端点令牌>
WEKNORA_KB_IDS=<知识库 id，可留空=范围内全部>
WEKNORA_TOP_K=5
```

## 4. 怎么验证

```bash
# 离线（不需要 WeKnora）
.venv/bin/python -m pytest -q tests/test_doc_channel.py tests/test_doc_client.py tests/test_doc_router.py

# 通道状态（启用了才继续）
curl -s http://127.0.0.1:18100/api/knowledge/doc-channel | jq

# 只查文档通道：应当看到 usable_for_conclusion=false 且每条带 citation
curl -s -X POST http://127.0.0.1:18100/api/knowledge/doc-search \
  -H 'content-type: application/json' -d '{"query":"库存增量怎么算"}' | jq

# 双通道：结论只来自结构化通道，文档材料只在 background
curl -s -X POST http://127.0.0.1:18100/api/agent/ask \
  -H 'content-type: application/json' \
  -d '{"text":"ads.ads_产销存月报 的产量怎么来的？","with_docs":true}' | jq '{text, result, background}'
```

## 5. 真跑才暴露的四个坑（都写下来，别让下一个人再踩）

| 坑 | 现象 | 正解 |
| --- | --- | --- |
| MCP 返回的是 **XML 文本**，不是 JSON | 解析器按 JSON 解 → 0 条材料（还以为是"没搜到"） | 认 `<chunk chunk_id=… knowledge_title=…><content>…</content></chunk>` 这种标签文本；真跑抄回来的样例进了用例（`SEARCH_XML`） |
| `mcp` SDK **2.x 换了名字** | `from mcp.client.streamable_http import streamablehttp_client` → `ImportError` | 2.x 是 `streamable_http_client`（yield **二元组** `(read, write)`），头/超时用 `create_mcp_http_client` |
| MCP 端点创建用 **snake_case** | 写 `knowledgeBaseIds` 不报错，但**范围不生效** —— 端点变成"租户内全部知识库" | 字段名是 `knowledge_base_ids`；建完必须回读确认（证据里有对照） |
| `list_documents` 要求 `knowledge_base_id` | 空参调用回一句 `knowledge_base_id is required` | 配置里带 kb_ids 就传第一个；补不到名字就当"补不到"（空清单），别当错误炸掉 |

另外两条与部署环境有关（写在第 3.1 节）：镜像 tag 带 `v`、ollama 安装要 `zstd`。

**凭据怎么放**：WeKnora 的管理员口令与 MCP 端点令牌都在**仓外**（`D:/Projects/_weknora-conf/`），
不进仓、不进日志、不进接口响应（`GET /api/knowledge/doc-channel` 只报 endpoint 地址和工具组）。
平台侧从环境变量读（`WEKNORA_MCP_URL` / `WEKNORA_MCP_TOKEN`）。

## 6. 当前不做的事（别指望它）

- **不让它写**：没有 ingest 授权，也没有任何回写知识的代码路径。
- **不做"文档答业务口径"**：`ask` 工具我们不用（它会跑 WeKnora 自己的 agent、耗时可达分钟级，
  而且那是"文档答结论"这条我们明确禁止的路）。只用 `search_knowledge` 拿原文切片。
- **不做多知识库路由**：现在按配置的范围检索，没有按业务域分库。
- **不做缓存**：每次现查（先要可复现、可核对；性能后面再说）。

## 6. 谁负责

- 部署与令牌：AI 起草（Hermes Agent），提交人复核后合并；令牌不进仓、不进日志、不进响应。
- 「仅背景」的验收：由人确认"问业务口径时结构化通道优先、文档只补充说明"确实成立。

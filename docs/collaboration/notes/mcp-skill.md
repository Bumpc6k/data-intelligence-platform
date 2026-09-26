# mcp-skill：血缘 skill 怎么注册、怎么被调用

> 工作项 **M1-03**（Issue #3）｜ 实现：`packages/dip-lineage-skill/` ｜ 契约声明：`packages/dip-lineage-skill/skill.yaml`

## 一句话

把内核的只读血缘能力（`/upstream` 表级 + `/analyze` 字段级）包成一个 **MCP skill**，
外壳（dsh 等）按标准 MCP 协议起它、列工具、调工具，返回值**必带回执**。

## 1. 怎么起

```bash
# 在仓库根目录；用仓库自己的 venv
LINEAGE_BASE=http://127.0.0.1:18080 .venv/bin/python -m dip_lineage_skill
```

它按 **stdio** 传输提供服务（默认传输方式），stdout 是协议通道、日志走 stderr —— 直接手跑
会看到它"卡住不动"，那是正常的：它在等你按 MCP 协议喂 JSON-RPC。要看效果用第 5 节的自测。

### ⚠️ 必须带 `PYTHONPATH`（本仓库的既有特性，不是本 skill 的毛病）

`pyproject.toml` 里 `[tool.setuptools] packages = []` 关掉了自动发现（P0-1 的修复），
所以 `pip install -e .` **不会**把这些包装进 site-packages —— 它们靠 pytest 的
`pythonpath` 配置挂载。因此**任何非 pytest 的入口都要自己给 PYTHONPATH**：

```bash
PYTHONPATH=.\
:packages/dip-contracts/src\
:packages/dip-skills/src\
:packages/dip-lineage-skill/src\
:integrations/lineage-client/src\
LINEAGE_BASE=http://127.0.0.1:18080 .venv/bin/python -m dip_lineage_skill
```

（Windows 上用 `;` 分隔、venv 是 `.venv/Scripts/python.exe`。）

> 这条对 **M4-03（一键起全栈）** 是个必须先解决的摩擦点，已在此记录，避免到时候重新踩。

## 2. 怎么注册到外壳

MCP 客户端的配置是通用的 JSON 形态（不同外壳字段名可能略有差异，认的是 `command`/`args`/`env`）：

```json
{
  "mcpServers": {
    "dip-lineage": {
      "command": "/绝对路径/到/仓库/.venv/bin/python",
      "args": ["-m", "dip_lineage_skill"],
      "env": {
        "LINEAGE_BASE": "http://127.0.0.1:18080",
        "PYTHONPATH": "/绝对路径/到/仓库:.../packages/dip-contracts/src:.../packages/dip-skills/src:.../packages/dip-lineage-skill/src:.../integrations/lineage-client/src"
      }
    }
  }
}
```

注册成功后，外壳的 `tools/list` 里会出现 **`lineage_analyze`** 一个工具。

## 3. 工具签名

**工具名**：`lineage_analyze`（契约名是 `lineage.analyze`，见下面「坑 2」为什么不一样）

| 入参 | 类型 | 说明 |
| --- | --- | --- |
| `question` | string? | 自然语言问题，如 `ads.ads_产销存月报 的产量怎么来的？` |
| `table` | string? | 直接给表名（`库.表`），走 `/upstream` |
| `sql` | string? | 直接给 SQL，走 `/analyze` |
| `depth` | integer = 3 | 血缘向上追溯层数 |

`question` / `table` / `sql` **三选一**，优先级 `sql` > `table` > 从 `question` 里抽取。
三者都没给，或问句里既没有 `库.表` 也没有 SQL → **不调用内核**，直接返回说明。

**返回**：一个 JSON 字符串，形如

```json
{
  "receipt": {
    "skill": "lineage.analyze@1",
    "endpoint": "POST /upstream",
    "ms": 41,
    "evidence_count": 7,
    "ok": true,
    "attempts": 1,
    "http_status": 200,
    "error": null
  },
  "kind": "upstream",
  "subject": "ads.ads_产销存月报",
  "tables": ["ads.ads_产销存月报", "..."],
  "lineage": { "…内核原始响应，不改写…" },
  "report_url": null,
  "message": ""
}
```

## 4. 怎么读回执（别只看 `ok`）

| 字段 | 意义 |
| --- | --- |
| `endpoint` | 这次数据**来自哪个内核端点**（溯源用） |
| `ms` / `attempts` | 真实耗时与尝试次数（含重试），给「工具步骤条」用 |
| `evidence_count` | 本方能引用的**证据条数**：`/upstream` 取 `upstream_count`，`/analyze` 取 `column_lineage_count` |
| `ok` | **这次调用本身**成没成功（失败也是 HTTP 200，真相应由 `ok`/`error` 表达） |

**关键：`ok=true` 不等于能出结论。** 判定用 `receipt.usable`（= `ok` 且 `evidence_count > 0`）——
`evidence_count == 0` 时只能支撑"没查到"，不能支撑任何数字或公式（铁律 2：无凭证不出结论）。
失败时 `evidence_count` 一律记 0，不虚报。

## 5. 自测（不需要外壳，也不需要内核）

```bash
bash ops/gate.sh                     # 门禁：含 24 条本 skill 的测试（全部用 FakeKernel 假内核，无需起服务）
.venv/bin/python -m pytest tests/test_lineage_skill.py -q -rs
```

其中一条是**真起进程**的端到端：用真实 MCP 客户端走完 握手 → `tools/list` → `tools/call`，
把 `LINEAGE_BASE` 指向必然连不上的端口，验证**内核挂掉时工具不崩、如实返回失败回执**。

## 6. 四个实测坑（都别再踩）

1. **`mcp` SDK 2.x 里 `FastMCP` 已改名 `MCPServer`**
   （`from mcp.server.mcpserver import MCPServer`）。网上绝大多数示例还是 v1 的
   `from mcp.server.fastmcp import FastMCP`，照抄直接 ImportError。`pyproject.toml` 已锁 `mcp>=2.2,<3`。
2. **工具名不能用契约里的点号形态**。MCP 官方 SDK 按 SEP-986 允许点号，但外壳常把工具名
   转发成模型供应商的 function-calling 名字，而主流供应商只接受 `[a-zA-Z0-9_-]`（最紧公共子集
   `^[a-zA-Z][a-zA-Z0-9_-]{0,63}$`）。故注册用 `lineage_analyze`，**契约名 `lineage.analyze` 不变**，
   两者靠回执的 `skill` 字段对上。
3. **日志必须走 stderr**。stdout 是 MCP 的协议通道，往里多写一个字符就握手失败。
   入口里 `logging.basicConfig(stream=sys.stderr)`。
4. **中文标识符必须 Unicode 感知**。`ads.ads_产销存月报` 用 ASCII 正则会得到 `ads.ads_`
   这个**假表名**（悄悄截断，不报错）——比"取不到"更危险。实现里显式写了 `[\w\u4e00-\u9fff]`，
   并有对照测试把"ASCII 正则会截断"这件事钉住。

## 7. 已知局限（别当成 bug，是有边界的选择）

- **不做意图识别/编排**：只在问句里做最小实体抽取（取一个 `库.表`）。完整的实体识别、意图、计划
  属 `dip-agent`（v2 迁到 skill + harness），本项按 Issue 边界不重复实现。
- **版本号会被误判成表名**：`这个方案 v1.2 已经定稿了` 会抽出 `v1.2`。同上，消歧不在本项范围。
- **只读、单 skill**：不碰写操作类能力；不做鉴权与多用户（按 Issue 边界）。

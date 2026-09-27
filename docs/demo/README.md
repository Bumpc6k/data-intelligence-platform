# 演示资产（给人看的东西都在这）

> 工作项 **M4-04 / Issue #20**。这里只放"怎么演示、看哪里、资产在哪"；
> 工程侧的 7 步映射在 [`docs/collaboration/notes/demo-path.md`](../collaboration/notes/demo-path.md)，
> 存证约定在 [`docs/evidence/README.md`](../evidence/README.md)。

## 0. 一键把现场铺好

```bash
bash ops/demo.sh            # 起全栈（PG/内核/网关/防火墙/平台/血缘 skill/dsh 壳）+ 跑完 7 步验收
bash ops/demo.sh --check    # 只体检（不起服务、不调模型）
```

跑完会在屏幕上打印**两个入口**：平台的 `http://127.0.0.1:18100/app/` 和 dsh 壳带 token 的 URL。

## 1. 点击路径（平台前端）

入口：`http://127.0.0.1:18100/app/`（进页面会自动问一次旗舰问题，省得手敲）

| 步 | 点哪 / 看哪 | 应该看到什么 | 资产 |
| --- | --- | --- | --- |
| 1 | 页面加载完 | 顶部状态条：`规则模式` / `口径库 75` / `内核正常`；中间是问答区 | `platform-answer.png` |
| 2 | 中间对话区 | 答案文本 + 下面一排**凭证卡**：`[lineage]` 血缘、`[dict]` 词表、`[metric]` 口径；每张卡都写出来源端点 | `platform-answer.png` |
| 3 | 凭证卡里的血缘卡 | 血缘图（按层分列：ods → dwd → dws → ads + dim），节点可读、边连着上下游 | `docs/evidence/issue-17/view-graph.png` |
| 4 | 凭证卡里的口径卡 | 公式 + **来源脚本**；没有行号时明确写"文件级（行号待补）"，不假装有行号 | `docs/evidence/issue-17/view-sql.png` |
| 5 | 右侧「视图」区 | 四块并排：`table`（检索命中）/ `sql`（口径表达式）/ `graph`（血缘）/ `diff`（口径版本对照） | `platform-views.png`、`docs/evidence/issue-17/view-*.png` |
| 6 | 输入框随便问一句 | 工具步骤条会显示真实调用与耗时；答不出来时也会明说"没查到"，不会编 | — |
| 7 | 底部提示行 | `回答必须带可点开的凭证` —— 这是产品规矩，不是装饰 | `platform-answer.png` |

## 2. 点击路径（dsh 壳）

入口：`ops/demo.sh` 第 1 步打印的 `http://127.0.0.1:3080/?token=…`（**完整复制**，去掉 token 会 401；每次重启都会换）

| 步 | 点哪 / 看哪 | 应该看到什么 | 资产 |
| --- | --- | --- | --- |
| 1 | 第一次进页面 | 要先 **选工作区**（不选之前输入框是禁用的） | — |
| 2 | 输入框 | 问：`ads.ads_产销存月报 的产量怎么来的？用 mcp__lineage__lineage_analyze 查血缘` | — |
| 3 | 回答 | 工具名 `mcp__lineage__lineage_analyze`、回执 `evidence_count=9 ok=true`、上游 9 张表/13 条边 | `docs/evidence/issue-18/dsh-web-answer.png` |
| 4 | 造一次失败（可选） | 把内核停掉再问 → 回执 `ok=false` + `Connection refused` 的精确原因；模型明说"按无证据不出结论的规则，本轮不能给出上游表数量" | `docs/evidence/issue-18/dsh-web-failure-receipt.png` |

## 3. 资产清单

| 类型 | 路径 | 说明 |
| --- | --- | --- |
| 平台截图 | `docs/demo/platform-answer.png`、`docs/demo/platform-views.png` | 本轮新增（M4-04） |
| 视图截图 | `docs/evidence/issue-17/view-{graph,table,sql,diff}.png` | M4-01 的四种视图各一次真渲染 |
| 壳截图 | `docs/evidence/issue-18/dsh-web-{answer,failure-receipt,failure-kernel-down,failure-model}.png` | M4-02 的成功 + 三种失败路径 |
| 演示证据 | `docs/evidence/demo-<时间戳>.txt` | `ops/demo.sh` 的完整输出（7 步 + 每步实际返回） |
| 各工作项证据 | `docs/evidence/issue-*.txt` | 见 `docs/evidence/README.md` 第 3 节的对照表 |
| 演示脚本 | `ops/demo.sh`、`ops/gate.sh --demo` | 一条命令起全栈 + 跑验收 |
| 壳接入脚本 | `ops/dsh-connect.sh`、`ops/dsh-web.ps1` | skill 的 HTTP 传输 + Windows 侧起壳 |

**录屏**：本仓库不做自动录屏（要交互式操作 + 录屏工具，且录屏文件体积不适合进仓）。
需要录屏时按上面两张点击路径表走一遍即可；截图与演示脚本覆盖了"可复现"的要求。

## 4. 边界（Issue 明说不要做）

- 不做宣传物料：这里只有"怎么演示 + 看哪里 + 资产在哪"，没有包装文案与效果图。
- 不替人做判断：演示脚本打印真实返回（包括失败），**不美化**；被拒的动作就显示被拒。

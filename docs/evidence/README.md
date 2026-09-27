# 证据目录约定（`docs/evidence/`）

> 依据：《v2 项目规划 v1》§5「每条都要有存证，存 `docs/evidence/`」+
> 存证要求「命令 + 原始输出（不截断关键行）+ 截图；**反例必须留**」。

## 1. 命名与结构

```
docs/evidence/
├─ README.md                    ← 本文件（约定）
├─ issue-<编号>-<短名>.txt       ← 一个工作项一份（如 issue-12-candidates.txt、issue-18-dsh-shell.txt）
├─ issue-<编号>/                ← 该工作项的界面截图（如 issue-17/view-graph.png、issue-18/dsh-web-answer.png）
├─ demo-<YYYYmmdd-HHMMSS>.txt   ← 整条演示路径（ops/demo.sh 每次重新生成一份）
├─ m1-<YYYYmmdd-HHMMSS>.txt     ← M1 垂直切片的历史留档（ops/demo-m1.sh）
└─ refine/ doc-channel/         ← 某个工作项产出的**数据文件**（报告、示例文档等）
```

规则：

| 项 | 约定 | 为什么 |
| --- | --- | --- |
| 文本证据 | 一个工作项一个 `.txt`，**脚本真实输出原样粘贴**（不编辑、不美化、不截断关键行） | 出了争议要能对着原文查 |
| 截图 | 放 `issue-<编号>/` 子目录，文件名说清是什么（`view-graph.png`、`dsh-web-failure-model.png`） | 扁平放一起会重名 |
| 数据产物 | 报告/示例文档放对应子目录（`refine/`、`doc-channel/`） | 与"证据文本"区分开 |
| 演示证据 | 每次跑 `ops/demo.sh` 生成一份带时间戳的；**入库保留最近一次全绿的** | 演示会重跑，覆盖旧的会丢历史 |

## 2. 内容要求

1. **命令 + 原始输出**：证据文件里能看出"跑了什么命令、返回了什么"（脚本用 `set -x` 或显式打印命令）。
2. **不截断关键行**：长 JSON 可以只贴前 N 行，但要在文件里写明"截断"和完整产物的路径。
3. **反例必须留**：被拒的、失败的、报错的，比成功的有价值 —— 例如
   `issue-18-dsh-shell.txt` 里三种失败路径（模型不可用 / 传输失败 / 内核不可达）全留；
   `issue-12-candidates.txt` 里"缺来源脚本被拒"的原文也在。
4. **不落密钥**：口令、令牌、API key **一律不写进证据**。示例：
   WeKnora 的 MCP 端点只打印 URL 与 kb id（令牌长度可以写，值不写）；`.env` 里只引用变量名。
5. **可复现**：证据文件抬头写清日期、分支/commit、环境（哪台机、什么服务在跑）。

## 3. 每个工作项的证据由什么生成

| 工作项 | 证据文件 | 生成方式 |
| --- | --- | --- |
| M1 各步 | `issue-1…issue-4`、`m1-*.txt` | 各自的脚本（`ops/demo-m1.sh` 等） |
| M2 防火墙/令牌/审计 | `issue-7`、`issue-8`、`issue-9`、`issue-10`、`issue-11` | `bash ops/start-firewall.sh` + curl |
| M3 知识/口径/文档通道/提炼 | `issue-12…issue-16` | 对应工作项的 curl + pytest 输出 |
| M4 视图/壳/全栈 | `issue-17*`、`issue-18*`、`demo-*.txt` | 前端真渲染截图 + `ops/dsh-connect.sh --check` + `ops/demo.sh` |

## 4. 复现一条证据

```bash
# 知识类（M3）：门禁 + 对应用例
bash ops/gate.sh

# 壳（M4-02）：起 skill 的 HTTP 传输并自检
bash ops/dsh-connect.sh --check

# 演示路径（M4-03）：一条命令起全栈 + 跑完 7 步，自动生成新的 demo-<时间戳>.txt
bash ops/demo.sh
```

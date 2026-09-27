# 知识候选池与审核流（M3-01 / Issue #12）

一句话：**口径不许直接改知识库**。想改，只能

```
提交候选（带来源脚本 + 公式 + 依赖字段）
   → 责任人审核（approve / reject，拒绝必须写原因）
   → 入库（入库前再校验一遍，库层还有一道 CHECK）
```

依据：《双人分工与 Windows 数据开发约定》§3.3 的流程与质量门禁（**没有来源脚本的口径一律不许入库**）、
《v2 项目规划 v1》§3 的 M3-01 行。

---

## 1. 接口一览

| 方法与路径 | 干什么 | 关键规则 |
| --- | --- | --- |
| `POST /api/knowledge/candidates` | 提交候选 | 缺来源脚本 / 缺公式 / 缺依赖字段 / 格式非法 → `400` + 逐条 `problems`（每条带错误码） |
| `GET /api/knowledge/candidates` | 候选台账 | 可 `?status=pending\|approved\|rejected\|ingested`、`?subject=` 过滤；库不在 → `success=false` + "未落库" |
| `GET /api/knowledge/candidates/{id}` | 一条候选 | 含审核人、审核意见、"值不值得留下"、入库时间 |
| `POST /api/knowledge/candidates/{id}/review` | 责任人审核 | `reviewer` 必填；`reject` 必带 `reason`；`approve` 必须显式给 `worth_keeping`（`false` 配 `approve` 视为自相矛盾） |
| `POST /api/knowledge/candidates/{id}/ingest` | 入库 | 只有 `approved` 能入库；入库前**再校验一遍**；无来源 → `409` + `problems` |
| `GET /api/knowledge/metrics` | 已入库的口径 | 验收就看这个："到底进没进去" |

`problems` 里的错误码：`missing_source_script` / `invalid_source_script` / `missing_formula` /
`invalid_formula` / `missing_depends_on` / `invalid_depends_on` / `missing_subject` / `invalid_subject` /
`invalid_source_line` / `missing_submitter` / `unsupported_kind` / `unknown_kind`。

审核与入库的错误码：`missing_reviewer` / `missing_reason` / `missing_worth_keeping` /
`contradictory_decision` / `status_conflict` / `not_approved` / `already_ingested` / `ingest_rejected`。

## 2. 为什么把门禁放在**库层**

三层，各管一段 —— 这不是重复劳动：

| 层 | 文件 | 管什么 |
| --- | --- | --- |
| 契约层 | `packages/dip-contracts/src/dip_contracts/knowledge.py` | 把"为什么不行"讲清楚（逐条 `Problem`）。纯 pydantic，不碰库、不碰 HTTP |
| 接口层 | `apps/portal-api/src/portal_api/routers/knowledge.py` | 管顺序与语义：没审核不许入库、重复审核不覆盖前一次结论、拒绝必须有原因 |
| **库层** | `packages/dip-pg/src/dip_pg/knowledge.py` | **绕过接口直接改库也进不去**：`CHECK` 约束 |

前两层能被绕过（`psql` 直接写库、以后新写的脚本忘了调校验），第三层不能。本 Issue 的验收说的就是这件事：
"无来源脚本的候选**入不了库**"—— 所以它必须是数据库的性质，而不是某个函数的自觉。
证据里第 5 节就是拿 `psql` 直接 `update ... set status='ingested'` 撞约束报错。

两条库层约束：

```
knowledge_candidates_ingest_requires_source_chk
  check (status <> 'ingested' or (来源脚本非空 and 公式非空 and depends_on <> '[]'))
knowledge_metrics_source_required_chk
  check (来源脚本非空 and 公式非空 and depends_on <> '[]')     -- 口径库里不可能存在无来源的一行
```

还有一条"说不清原因的拒绝不是审核"：

```
knowledge_candidates_reject_reason_chk
  check (status <> 'rejected' or 拒绝原因非空)
```

## 3. 为什么"入库"要跟"审核"分成两步

- 审核回答的是**价值问题**（"这条口径值不值得留下"）—— 只有人能回答，所以 `worth_keeping` 没有默认值。
- 入库回答的是**机器能核的问题**（来源在不在、格式对不对、冲突不冲突）—— 所以入库前再跑一遍校验。
- 分开还有一个直接好处：**M3-02（#13 版本与回滚）就接在 `ingest` 这一步**，不用回头改审核语义。

入库是**一个事务**（`conn.transaction()`）：写口径行 + 改候选状态要么都成、要么都不成。
不允许出现"口径进去了、候选还停在 approved"这种对不上账的中间态 —— 有测试专门钉住
（把第二步替换成抛异常，断言第一步也被回滚）。

## 4. 表结构要点

`knowledge_candidates`（候选池，20 列）：`kind / subject / chinese_name / formula / depends_on(jsonb) /
source_script / source_line / note / submitted_by / status / reviewer / worth_keeping /
rejected_reason / ingested_by / ingested_at / problems(jsonb)` + 时间戳。

`knowledge_metrics`（入库落点，13 列）：多一个 `candidate_id`（外键指回候选，**入库的口径永远能追回它的候选与审核人**）
和 `version`（先固定 1；版本与回滚是 #13）。

字段形状对齐内核 `/kb/metric` 的口径记录（`metric_name`/`formula`/`depends_on[{table,column}]`/`source_file`），
这样将来把平台侧口径喂回内核的离线 `kb build` 时不用做字段翻译。

## 5. 怎么验

```bash
bash ops/start-pg.sh && bash ops/start-portal.sh      # 起库与平台
.venv/bin/python -m pytest -q tests/test_knowledge_candidates.py -rs   # 23 条（其中 8 条要真库）
bash ops/gate.sh                                      # 必须 GATE: ALL_PASS
```

完整过程与原始输出（含三条反例、库层约束报错、门禁输出）：`docs/evidence/issue-12-candidates.txt`。

手边一条命令走一遍（真 HTTP）：

```bash
curl -G --noproxy '*' --data-urlencode "subject=ads.ads_产销存月报.output_qty" \
  http://127.0.0.1:18100/api/knowledge/metrics
```

## 6. 踩过的坑（写下来免得再踩）

| 坑 | 现象 | 正确做法 |
| --- | --- | --- |
| 中文进 URL query 不编码 | uvicorn 直接回 `Invalid HTTP request received.` + `400` —— 看起来像应用报错，其实是 HTTP 请求行本身非法 | `curl -G --data-urlencode "subject=..."`，别手拼 `?subject=ads.ads_产销存月报.x` |
| `JSONResponse` 直接装 ORM 行 | `TypeError: Object of type datetime is not JSON serializable`（候选行里有 `timestamptz`） | 统一过 `fastapi.encoders.jsonable_encoder` |
| 口径主体的字符集 | 只允许 字母/数字/下划线/点/中文（对齐内核里的标识符形状） | 测试标记别用 `-`；被拦是**正确行为**，不是 bug |
| 依赖注入点不清理 | 假 store 会泄漏到后面的真库用例（#9 踩过） | fixture 里 `app.dependency_overrides.pop(...)` |
| 库层约束与外键的报错顺序 | 用不存在的 `candidate_id` 造数据，可能先撞外键而不是 `CHECK`，测试断言就跑偏 | 造数据先建真候选，再撞要验的那条约束 |

## 7. 当前不支持的事（别指望它）

- **不做审核台 UI**（Issue #12 的边界）—— 现在只有接口，`/app/` 前端没有候选页面。
- **不做同字段冲突仲裁**（#14）：同 `subject` 提两条不同公式，目前两条都能入库。
- **不做版本与回滚**（#13）：`version` 恒为 1，没有"回到上一版"。
- **`term` / `rule` 两类候选显式拒绝**（`unsupported_kind`）—— 不是悄悄当口径处理。
- **审核不加身份认证**：`reviewer` 是调用方自报的字符串（`auth_mode=dev` 阶段就是这样，身份体系不在本轮范围）。
- 入库**不写回内核知识库**：这一版先落平台侧口径库（`knowledge_metrics`），
  喂回内核的离线构建要另开 Issue（内核是冻结资产，只按接口调用）。

## 8. 谁负责

- 流程与口径质量：**责任人（么慌）** —— `worth_keeping` 与拒绝原因都出自这里。
- 代码与测试：AI 起草（Hermes Agent），提交人逐行复核后才算完成。
  按 `AGENTS.md` §6，合并由人来做。

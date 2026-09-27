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
| `GET /api/knowledge/metrics/active` | **当前按哪一版算** | 平台侧的 `/kb/metric`；没有生效版本时明说没有 |
| `GET /api/knowledge/metrics/versions` | 某口径的全部版本 | 历史不删（含被取代、被回滚的） |
| `GET /api/knowledge/metrics/history` | 版本事件流水 | 谁、何时、从哪版到哪版、为什么 |
| `POST /api/knowledge/metrics/rollback` | 回滚到指定版本 | 必须记名 + 写原因；不存在 → 404，已生效 → 409 |
| `GET /api/knowledge/conflicts` | 冲突全景 | 生效口径 + 未决候选逐条列出（谁、什么公式、什么来源、什么时候） |
| `POST /api/knowledge/conflicts/resolve` | **仲裁入口** | 留哪条由人指定（`keep_candidate_id` 或 `keep_version`），必须记名 + 写理由 |

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

`knowledge_candidates`（候选池，22 列）：`kind / subject / chinese_name / formula / depends_on(jsonb) /
source_script / source_line / intent / conflicts(jsonb) / note / submitted_by / status / reviewer / worth_keeping /
rejected_reason / ingested_by / ingested_at / problems(jsonb)` + 时间戳。

`knowledge_metrics`（入库落点，16 列）：多一个 `candidate_id`（外键指回候选，**入库的口径永远能追回它的候选与审核人**）、
`version`（同一 `subject` 内自增）与 `status`（`active` / `superseded` / `rolled_back`），
以及回滚留痕三列 `rolled_back_at` / `rolled_back_by` / `rollback_reason`（#13 加）。

`knowledge_metric_history`（版本事件流水，9 列）：`metric_id / subject / version / event /
related_version / actor / reason / created_at`（#13 加，就是 Issue 说的「历史表」）。

字段形状对齐内核 `/kb/metric` 的口径记录（`metric_name`/`formula`/`depends_on[{table,column}]`/`source_file`），
这样将来把平台侧口径喂回内核的离线 `kb build` 时不用做字段翻译。

## 5. 版本与回滚（M3-02 / #13）

### 版本号规则

- **同一口径主体内自增**：第 n 次入库就是第 n 版（`version = max(version) + 1`，按 `subject` 分组）。
- **同一时刻只有一个生效版本**：`create unique index uq_kb_metrics_one_active on knowledge_metrics(subject) where status = 'active'`
  —— 库层不变量。任何"两条同时生效"的写法（含直接 `psql`）都会当场被拒。
- **不删历史**：旧版本降级为 `superseded`，被回滚下来的版本标 `rolled_back`。

### 回滚是"状态翻转"，不是"新增一版"

```
v1（superseded） ← 曾生效
v2（active）     ← 现在生效
        ↓ POST /api/knowledge/metrics/rollback {subject, to_version: 1, operated_by, reason}
v1（active）     ← 生效的仍是原来那条 v1（不是"内容等于 v1 的 v3"）
v2（rolled_back，带 rolled_back_at / rolled_back_by / rollback_reason）
```

理由：版本号是给人看的"这是第几版口径"，插一个内容重复的新版本会让版本号失去意义；
"什么时候回滚的"由历史表回答，信息一点没少。而**入库**（每次都是新口径）与**回滚**（换回旧口径）
是两种不同的变更，所以用两种机制表达。

回滚必须写明"谁、为什么" —— 接口层拦一道，库层 `knowledge_metrics_rollback_trace_chk` 再钉一道
（与"拒绝必须说明原因"同一条道理）。

### 历史表 `knowledge_metric_history`

每次版本状态变化一行：`entered`（进版生效）/ `superseded`（被新版本取代）/
`rolled_back`（被回滚降级）/ `reactivated`（因回滚重新生效），带 `actor`、`related_version`、`reason`。

```
$ psql -c "select version, event, related_version, actor, reason from knowledge_metric_history \
           where subject='ads.ads_产销存月报.xxx' order by id"
 1 | entered       |   | 么慌 |                       ← 第 1 版入库
 2 | entered       |   | 么慌 |                       ← 第 2 版入库
 1 | superseded    | 2 | 么慌 |                       ← 第 1 版被第 2 版取代
 2 | rolled_back   | 1 | 么慌 | 新公式与脚本对不上      ← 第 2 版被回滚（为什么）
 1 | reactivated   | 2 | 么慌 |                       ← 第 1 版重新生效
```

### 来源精度（ADR-0003）

每条口径都带 `source: {script, line, precision, label}`：

- `precision = "line"` → `examples/warehouse/ads/ads_产销存月报.sql 第 1 条语句`
- `precision = "file"` → `examples/warehouse/ads/ads_产销存月报.sql（文件级：行号待补）`

内核只给 `source_script`（没有行号）时就只能是**文件级**，标签里明说"行号待补"，
不许含糊成"有来源"。

### 与内核 `/kb/metric` 的联合验证

平台侧的版本化口径不是自说自话：拿内核真实口径（`/kb/metric` 查 `chanliang_qty`）当基准，
把同一条口径按同一公式与来源入库到平台侧，断言两边**公式、来源脚本、依赖字段**一致
（`tests/test_metric_versions.py::test_与内核_kb_metric_对得上`，标 `smoke`；证据里也有一次真 HTTP 的并排输出）。

差别也说清楚：内核侧是**只读基线**（无版本、无回滚），版本与回滚只发生在平台侧 ——
把平台侧口径喂回内核的离线 `kb build` 要另开 Issue（内核是冻结资产，只按接口调用）。

## 6. 冲突仲裁（M3-03 / #14）

依据：《双人分工与 Windows 数据开发约定》§3.3 —— **同字段两条公式冲突时必须择一，不允许并存**。

### 什么算冲突（判定口径，可测）

| 情形 | 算不算 | 错误码 |
| --- | --- | --- |
| 公式**归一化后不同**（去掉所有空格再比） | **算** | — |
| 与**生效口径**公式不同，且没声明 `intent=replace` | **算** | `conflict_with_active_metric` |
| 与**其他未决候选**（pending/approved）公式不同 | **算**（声明 `replace` 也拦） | `conflict_with_pending_candidate` |
| 只是空格写法不同（`产量 = A + B` vs `产量=A+B`） | 不算 —— 同一条口径重复提交而已 | — |
| 与已入库但**已失效**的历史版本公式不同 | 不算 —— 历史版本本来就该被取代 | — |

**为什么"未决候选之间冲突"声明 replace 也不行**：`replace` 的语义是"替换现有生效口径"，
不是"我可以和别人并存"。两条竞争候选同时待审，就是没择一。

### 提交与审批两道关

```
提交候选 → 与现存口径冲突？
              有 → 409 + 冲突项清单（哪一条、什么公式、来源、谁提的、什么时候）
                      出路一：先仲裁（POST /conflicts/resolve）
                      出路二：如果是替换现有口径，重提并声明 intent=replace
              无 → 落库（pending）
审批 approve → **再查一次**（提交到审批之间，别处可能已经把口径改了）→ 有冲突同样 409
```

声明 `replace` 时，冲突清单仍会写进候选行的 `conflicts` 列 ——
**"这次替换是谁认的账"比"替换了"更重要**。

### 仲裁入口（人决定，这里只执行）

```
GET  /api/knowledge/conflicts?subject=...     冲突全景：生效口径 + 未决候选，逐条列出（不解释成对错）
POST /api/knowledge/conflicts/resolve
     {subject, keep_candidate_id 或 keep_version（二选一）, operated_by, reason}
```

- **留候选**：其余未决候选全部驳回（`rejected` + 理由写"冲突仲裁：…"，记名仲裁人）
- **留某一版**：该版本重新生效（复用 #13 的状态语义：当前版本 → `rolled_back`，目标版本 → `active`），未决候选全部驳回
- 执行完之后：该口径**只有一条生效版本**（库层不变量保证），**历史一行不删**
  （被驳回的候选行还在，被换下的版本行还在，版本事件流水也在）

**不做自动仲裁**（Issue 边界）：这里不给"哪条更对"打分，也不替人选 —— 必须由人指定留哪条 + 写明理由。

### 职责切分（照分层铁律）

| 谁 | 干什么 |
| --- | --- |
| `dip_contracts.knowledge` | **判定**：公式归一化、谁和谁冲突、按 `intent` 决定拦不拦（纯函数，好测） |
| `dip_pg/conflicts.py` | **取数**（未决候选）与**落地仲裁结果**（驳回候选、让指定版本重新生效） |
| `portal_api` 路由 | 粘合：取数 → 判定 → 落库，并把冲突项整理成可读响应 |

`dip_pg` 不许 import 契约层（分层守卫有测试），所以比较逻辑不能写在数据层 —— 这不是洁癖，
是这条守卫真的会红。

## 7. 怎么验

```bash
bash ops/start-pg.sh && bash ops/start-portal.sh      # 起库与平台
.venv/bin/python -m pytest -q tests/test_knowledge_candidates.py -rs   # 23 条（其中 8 条要真库）
.venv/bin/python -m pytest -q tests/test_metric_versions.py -rs        # 12 条（其中 7 条要真库、1 条要内核）
.venv/bin/python -m pytest -q tests/test_conflicts.py -rs              # 19 条（其中 6 条要真库）
bash ops/gate.sh                                      # 必须 GATE: ALL_PASS
bash ops/gate.sh --with-smoke                         # 带上真内核的冒烟（含与 /kb/metric 的联合验证）
```

完整过程与原始输出：`docs/evidence/issue-12-candidates.txt`（候选与入库）、
`docs/evidence/issue-13-versions.txt`（版本与回滚）、`docs/evidence/issue-14-conflicts.txt`（冲突与仲裁）。

手边一条命令走一遍（真 HTTP）：

```bash
# 当前按哪一版算（平台侧的 /kb/metric）
curl -G --noproxy '*' --data-urlencode "subject=ads.ads_产销存月报.output_qty" \
  http://127.0.0.1:18100/api/knowledge/metrics/active
# 版本历史与事件流水
curl -G --noproxy '*' --data-urlencode "subject=ads.ads_产销存月报.output_qty" \
  http://127.0.0.1:18100/api/knowledge/metrics/versions
curl -G --noproxy '*' --data-urlencode "subject=ads.ads_产销存月报.output_qty" \
  http://127.0.0.1:18100/api/knowledge/metrics/history
```

## 8. 踩过的坑（写下来免得再踩）

| 坑 | 现象 | 正确做法 |
| --- | --- | --- |
| 中文进 URL query 不编码 | uvicorn 直接回 `Invalid HTTP request received.` + `400` —— 看起来像应用报错，其实是 HTTP 请求行本身非法 | `curl -G --data-urlencode "subject=..."`，别手拼 `?subject=ads.ads_产销存月报.x` |
| `JSONResponse` 直接装 ORM 行 | `TypeError: Object of type datetime is not JSON serializable`（候选行里有 `timestamptz`） | 统一过 `fastapi.encoders.jsonable_encoder` |
| 口径主体的字符集 | 只允许 字母/数字/下划线/点/中文（对齐内核里的标识符形状） | 测试标记别用 `-`；被拦是**正确行为**，不是 bug |
| 依赖注入点不清理 | 假 store 会泄漏到后面的真库用例（#9 踩过） | fixture 里 `app.dependency_overrides.pop(...)` |
| 库层约束与外键的报错顺序 | 用不存在的 `candidate_id` 造数据，可能先撞外键而不是 `CHECK`，测试断言就跑偏 | 造数据先建真候选，再撞要验的那条约束 |
| **依赖注入点被绕过** | 测试里换掉假 store，代码却直接引用了模块级的 `version_store` → **偷偷打到真库**，用例假绿 | 注入对象**一路传参**到底（`_gather_conflicts(vs, cs, ...)`），别在函数体里直接引用模块 |
| 新增注入点后没补老用例 | 老用例只替换了 `store`，新增的 `versions_store` / `conflicts_store` 落到真模块上 → **CI 没有数据库，直接红**（本地有库时反而看不出来） | 加注入点的那个 PR 里，把所有"用假 store 的用例"一起补上；本地用 `DIP_PG_DSN=...127.0.0.1:1/dip pytest` 模拟无库环境跑一遍 |
| 冲突项写进 jsonb 时带上 `datetime` | `TypeError: Object of type datetime is not JSON serializable`（口径行有 `created_at`） | 整理成冲突项时把时间转 ISO 字符串（`_as_text`） |
| SQL 里 `%s is null` 的占位符 | `IndeterminateDatatype: could not determine data type of parameter $3`（`None` 推断不出类型） | 显式转型：`%s::bigint is null or id <> %s::bigint` |

## 9. 当前不支持的事（别指望它）

- **不做审核台 UI**（Issue #12 的边界）—— 现在只有接口，`/app/` 前端没有候选页面。
- **不做同字段冲突仲裁的自动化**（#14 的边界）：不给"哪条更对"打分、不按规则自动选一条 ——
  冲突只被**检测出来并拦住**，留哪条必须由人指定（`POST /conflicts/resolve`）。
- **不做公式的语义等价判断**：`A + B` 与 `B + A` 会被当成两条不同公式（只做"去空格后字面相同"的归一化）。
- **不做全量知识库迁移**（Issue #13 的边界）：只有人工/流水线提交的候选会进库，内核的 75 条口径不自动搬过来。
- **回滚只回"哪一版生效"，不回滚候选**：候选池是按次提交的事实，不因为口径回滚而改变状态。
- **`term` / `rule` 两类候选显式拒绝**（`unsupported_kind`）—— 不是悄悄当口径处理。
- **审核与回滚不加身份认证**：`reviewer` / `operated_by` 是调用方自报的字符串（`auth_mode=dev` 阶段就是这样）。
- 入库**不写回内核知识库**：先落平台侧口径库（`knowledge_metrics`），
  喂回内核的离线构建要另开 Issue（内核是冻结资产，只按接口调用）。

## 10. 谁负责

- 流程与口径质量：**责任人（么慌）** —— `worth_keeping` 与拒绝原因都出自这里。
- 代码与测试：AI 起草（Hermes Agent），提交人逐行复核后才算完成。
  按 `AGENTS.md` §6，合并由人来做。

## 11. 口径分级：低等级免仲裁（ADR-0006 / Issue #38）

需求方原话：「以后这些口径可能是分级的，低等级的不需要仲裁。」规矩写在
[`docs/adr/0006-metric-tier.md`](../../adr/0006-metric-tier.md)，这里只讲怎么用、以及免的到底是哪一段。

### 11.1 三个等级

| 等级 | 谁该用 | 与仲裁的关系 |
| --- | --- | --- |
| `p0` | 进生产报表 / 对外口径 / 被下游引用 | 最严（与 `p1` 同，冲突一律先仲裁） |
| `p1` | **默认**（不写就是它） | 与生效口径公式不同 → 必须 `intent=replace`；未决候选之间冲突 → 拦（#14 的老规矩） |
| `p2` | 只影响临时分析、无下游引用 | **两类冲突都不拦**，但冲突清单照样写进候选行（不拦 ≠ 不记） |

标 `p2` 必须同时给 `tier_reason`（凭什么说它低风险）与 `tier_set_by`（谁定的）——
契约层与库层**双重把关**（`knowledge_candidates_tier_trace_chk`），绕过接口直接写库也拿不到免仲裁。

### 11.2 免的只是"冲突拦截"，质量门禁一条没放松

`p2` 仍然要过：来源脚本、依赖字段、审核人必填、reject 必写原因、approve 必答 `worth_keeping`、
入库时的库层 `CHECK`。也就是说 **`p2` 不是"提交即入库"**，它免的是"先仲裁"，不是"免审核"
（选这个取舍的理由：入库是个动作，要有责任人 `ingested_by` 与审核记录）。

### 11.3 等级是人的判断，不版本化

- 接口**没有**改等级的入口：`ReviewRequest` 里没有 `tier`（有用例钉住），审核/入库都不会动它；
- 等级只在**提交候选**时由人写入，入库时作为**快照**落到口径行（`knowledge_metrics.tier`）；
- 要改等级 = 重新提交一条候选（带新等级 + 依据 + 人）→ 自然留痕；
- 等级变化**不改版本号**：版本描述的是口径内容（公式/依赖/来源），等级是运维元数据。

### 11.4 怎么用（一句话）

```bash
# 临时分析用的派生指标：标 p2 就可以绕过"必须先仲裁"，但仍要走审核 + 入库
curl -X POST http://127.0.0.1:18100/api/knowledge/candidates -H 'content-type: application/json' -d '{
  "subject":"ads.ads_临时看板.x","chinese_name":"临时指标","formula":"x = a + b",
  "depends_on":[{"table":"t","column":"c"}],
  "source_script":"examples/warehouse/ads/ads_产销存月报.sql",
  "tier":"p2","tier_reason":"只影响临时分析，无下游引用","tier_set_by":"么慌","submitted_by":"么慌"}'
```

证据：`docs/evidence/issue-38-tiers.txt`（真 HTTP + 真库：p1 拦、p2 放行并留痕、p2 缺依据被拒、
库层两种绕过都拦下、等级随入库落到口径行）。

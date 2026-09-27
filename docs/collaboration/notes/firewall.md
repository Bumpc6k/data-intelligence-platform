# 防火墙服务（M2-01）：三档判定与策略表怎么改

> 服务：`apps/firewall`（独立进程，默认 `127.0.0.1:18210`）
> 起停：`bash ops/start-firewall.sh` / `bash ops/start-firewall.sh stop`
> 策略表：`apps/firewall/policies/default.yaml`（改它不用改代码）
> 相关 Issue：#7（M2-01）。令牌时效见 #8（M2-02），判定落库见 #9（M2-03）。

---

## 1. 三档是什么

| 档位 | 含义 | 处置（`disposition`） | 靠什么决定 |
| --- | --- | --- | --- |
| `auto` | 只读/无副作用，自动通过 | `auto_pass` | 命中 `read-only` 这类规则 |
| `approval` | 有副作用，需要人确认一次 | `require_approval` | 命中生产写规则，**或未命中任何规则** |
| `deny` | 破坏性操作，连审批机会都不给 | `reject` | 命中 `destructive-ddl` |

档位从宽到严是 `auto < approval < deny`，这个顺序写在代码里（`policy.SEVERITY`），
不是靠约定。

**为什么 `deny` 不给审批机会**：`truncate` 掉的生产表，审批也救不回来。能被"批一次"变安全的
是"有副作用的操作"，不是"不可逆的破坏"。

---

## 2. 判定怎么算的（三条规则，写在 `policy.py` 里）

1. **一次动作可以命中多条规则，取最严的那一档。**
   这是 Issue 里「拿不准按上一档处理（fail-closed）」的落地方式：规则写重了、写漏了、
   有人往策略表里加了一条宽松规则，都**不会**把本该判严的动作放松。
2. **一条都不命中 → 按策略表显式声明的 `default_tier` 处理，并把 `matched` 标成 `false`。**
3. **策略表写错就起不来**：不认识的档位、规则重名、缺 `default_tier`、规则没有 `actions`
   —— 加载即报错。宁可服务起不来，也不要"静默按默认值继续跑"。

### `matched` 这个字段为什么重要

`matched: false` 和 `matched: true` 可能给出**同一个档位**，但含义完全不同：

- `matched: true` + `approval` = "命中了生产写规则，判它需要审批"；
- `matched: false` + `approval` = "没人管这个动作，走了兜底档"。

后者是**静默失效**的温床：看起来判对了，其实是策略表漏了它。M2-04（中文动作名）
与 M2-05（未命中默认档显式化）治的就是这个 —— 调用方因此必须看 `matched`，不能只看 `tier`。

### 未命中会写进日志（M2-05）

只有 `matched=false` 还不够：响应随请求一起消失，事后查不到。所以**每次未命中都会写一条
WARNING**，带上 actor / action / target / 策略指纹：

```
判定未命中任何策略规则，按 default_tier=approval 处理（fail-closed：拿不准不放松）
｜actor=么慌｜action=没人定义过这个动作｜target=ads.t｜policy_digest=6bfcaadc1263
```

**命中不会**刷这条日志 —— 全是噪音的日志等于没有日志。想看它：
`tmux capture-pane -pJ -S -100 -t firewall | grep 未命中`（或 `tmux attach -t firewall`）。

---

## 3. 接口

| 方法 | 路径 | 入参 | 出参 |
| --- | --- | --- | --- |
| GET | `/health` | — | 策略版本、指纹、默认档、规则清单 |
| POST | `/judge` | `actor` / `action` / `roles` / `target` / `sql` | `tier` `disposition` `matched` `matched_rules` `reason` `requires_token` `fingerprint` + 策略版本与指纹 |
| POST | `/tokens/issue` | `approver` / `actor` / `action` / `target` / `sql` | `token` `fingerprint` |
| POST | `/verify` | `token` / `action` / `target` / `sql` | 通过 → `{"ok": true}`；不通过 → **403** + 原因 |

**两个刻意的设计决定：**

1. **校验不通过返回 403，不是 `200 + {"ok": false}`。** 调用方漏看响应体时，状态码本身还能
   拦住一半。安全边界上的接口不该把"没过"表达成一个成功的响应。
2. **`/judge` 永远返回 200，包括 `deny`。** 拒绝是**判定结论**，不是接口错误 ——
   做成 4xx 会让调用方以为可以重试。

### 令牌的三条性质（M2-02）

| 性质 | 防的是什么 | 实现 |
| --- | --- | --- |
| **一次性** | 同一张令牌被重放（日志里翻出来、消息里被转发） | 校验通过即置 `used_at`，之后一律拒 |
| **绑指纹** | 审批被"挪用"（批的是 A 表，去改 B 表） | 指纹含动作 + 目标 + SQL；改一个字符即失效 |
| **时效** | 令牌被搁置后翻出来用（半年前的审批） | 默认 **300 秒**过期；`FIREWALL_TOKEN_TTL_SECONDS` 可调 |

指纹的**规范化规则**（既要让"复制粘贴的差异"不算改动，又不能宽容到把授权内容改掉）：

| 部分 | 怎么规范化 | 为什么 |
| --- | --- | --- |
| `action` | 去空白 + **转小写** | 动作是词汇（`INSERT`/`insert` 同一件事） |
| `target` | 只去首尾空白，**保留大小写** | 表名可能大小写敏感，宁严勿松 |
| `sql` | 去首尾空白 + 去结尾分号 + **折引号外的连续空白** | 换行/缩进不算改动；**引号内一个字符都不碰**（`'a  b'` 折成 `'a b'` 就是改了语义） |

两条容易忽略的细节：

- **指纹不符的拒绝不消费令牌**：挪用的尝试不该把审批人对**原动作**的授权弄没。
- **拒绝原因的检查顺序是有意的**：不存在 → 已用过 → 已过期 → 指纹不符。一张既用过又过期的
  令牌，拒它的真正原因是"用过"。

`FIREWALL_TOKEN_TTL_SECONDS` 写错（非整数、0、负数）会让**服务起不来**，不会静默按默认值跑。

### 一条命令看三档

```bash
for action in select insert truncate; do
  curl -s --noproxy '*' -X POST http://127.0.0.1:18210/judge \
    -H 'content-type: application/json' \
    -d "{\"actor\":\"么慌\",\"action\":\"$action\",\"target\":\"ads.ads_产销存月报\"}"
  echo
done
```

期望：`auto/auto_pass` → `approval/require_approval` → `deny/reject`。

---

## 4. 策略表怎么改

文件是 `apps/firewall/policies/default.yaml`。**改完不用重启服务**：服务按文件 mtime 判断，
内容变了下次请求即生效，`/health` 与每次 `/judge` 都会带回新的**指纹（digest）**——
这就是「策略表可改且改动有记录」的那条记录：事后能回答"当时用的是哪一版策略"。

```yaml
version: 1                 # 改策略时请同时 +1（给人看的版本号；指纹自动跟着内容变）
default_tier: approval     # 必须显式声明。未命中任何规则时按它处理
rules:
  - name: destructive-ddl  # 必须唯一（日志与审计靠它定位）
    tier: deny             # auto | approval | deny
    actions: ["truncate*", "drop*"]   # 大小写无关通配；动作名要匹配上
    targets: []            # 可选；空 = 任何目标。写了就按通配匹配表名
    require_any_role: []   # 可选；写了则调用方必须带其中一个角色，这条规则才参与
    reason: 破坏性操作不可审批，直接拒绝   # 会出现在判定结果里
```

**加一个新动作的推荐姿势**：给它写一条**专属规则**（`actions` 精确写这个词），
而不是去调 `default_tier`。`default_tier` 是兜底，放开它等于把所有没写过的动作一起放开。

### 为什么模板用 `truncate*` 而不是 `truncate`

动作名可能带着宾语（`truncate table ads.x`、`drop table prod.y`），
精确匹配 `truncate` 会漏掉这些变体 —— 而漏掉破坏性操作的代价最大。

### 为什么不用 ASCII 正则

动作名与表名都可能是中文（`ads.ads_产销存月报`、将来的"上线工作流到生产"）。
按 ASCII 字符类写正则会**整个漏掉中文标识符**（M1 实测过这个坑），所以这里用
大小写无关的 `fnmatch` 通配。

---

## 5. 当前不支持的事（别指望它）

| 项 | 状态 |
| --- | --- |
| 审批流转引擎、多人会签 | **不做**（Issue 边界：P1 只保留"上一级确认一次"）→ `/tokens/issue` 就是那个"确认一次"的程序化形式，它**不判断**该不该批，只记录谁批的 |
| 接真实调度平台写操作 | **不做**（Issue 边界） |
| 令牌时效（5 分钟过期） | **已有**（M2-02 / #8）：默认 300 秒，`FIREWALL_TOKEN_TTL_SECONDS` 可调；测试用注入的假时钟验，不靠 `sleep(300)` |
| 判定落库 / `/api/audit` 可查 | **没有** → M2-03（#9）。目前判定结果只在响应里，不落库 |
| 令牌持久化 | **没有**（进程内）。防火墙服务重启 → 已签发未使用的令牌全部失效，审批人要重新确认一次。这是 fail-closed 的方向，可以接受 |

---

## 6. 踩过的坑（写下来免得再踩）

**指纹一开始没算 SQL。** 第一版 `judge()` 只把 `action + target` 拼进指纹，
于是"审批了这句 SQL"实际绑定的只是"insert 这个动作"——换掉 SQL 照样能用。
`test_token_does_not_transfer_to_another_action` 抓到了它（第二次用不同 SQL 校验竟然 200）。
修法是让 `judge()` 接收 `sql` 并计入指纹，并补了回归测试
`test_verdict_fingerprint_covers_the_sql`。**这说明"绑动作指纹"这条要求必须在接口层测，
只在单元里测 `action_fingerprint` 是测不出来的。**

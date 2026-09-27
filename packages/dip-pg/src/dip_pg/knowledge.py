"""知识候选池与入库落点的访问层（工作项 M3-01 / Issue #12；版本与回滚在 M3-02 / #13）。

**只新增三张表**，`store.py`（判定留痕）与既有 `sessions` / `messages` / `audit_log` 一个字不动：

- `knowledge_candidates`：候选池。谁提的、提了什么、谁审的、审成什么、什么时候进的库。
- `knowledge_metrics`：**入库落点**（平台侧结构化通道的口径库）。只有过了质量门禁的候选才能在这里出现一行；
  同一 `subject` 按 `version` 留多行，**同一时刻只有一行为 `active`**（partial unique index 保证）。
- `knowledge_metric_history`：版本事件流水（进版 / 被取代 / 被回滚 / 重新生效），#13 的「历史表」。

本模块最要紧的一条设计：**质量门禁落在库层，不只是接口层**。
`knowledge_candidates` 与 `knowledge_metrics` 都带 `CHECK` 约束 ——
`ingested` 的行必须带齐「来源脚本 + 公式 + 依赖字段」，`knowledge_metrics` 里也**不可能**
存在一行没有来源脚本的口径。这样"绕过接口直接写库"（验收真正要防的那条路）也堵死了，
而不是靠每个调用点自觉。接口层的校验只负责把"为什么进不去"讲清楚。

第二条：**入库是事务**（`conn.transaction()`）。取版本号、降级旧版本、写口径行、改候选状态、写历史
要么都成、要么都不成 —— 不允许出现"口径进去了但候选还停在 approved"或"版本号取了却没入库"这种对不上账的中间态。
`psycopg` 的 `Connection.transaction()` 在 autocommit 连接上也能开显式事务，
所以这里继续复用 `store.connect()`（DSN 规则与连接超时只维护一份）。

第三条：`record_*` 沿用 #9 的约定 —— **落库失败不抛异常**，返回 `Persisted(ok=False, error=...)`，
让接口把"没落上"如实告诉用户。

第四条（#13）：建表语句里带的 `alter table ... add column if not exists` / `drop constraint if exists` 是
**给已经存在的库补列与换约束**用的（ADR-0005：表少、字段稳定，先不上迁移工具）。
`create table if not exists` 对老库是 no-op，光靠它补不出新列。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .metric_versions import EVENT_ENTERED, EVENT_SUPERSEDED, record_event
from .store import Persisted, connect
from .store import available as _db_available

#: 错误码（接口层据此给中文说明；测试直接断言码，不匹配文案）
E_NOT_FOUND = "candidate_not_found"
E_STATUS_CONFLICT = "status_conflict"
E_NOT_APPROVED = "not_approved"
E_ALREADY_INGESTED = "already_ingested"
E_BAD_REQUEST = "bad_request"

CANDIDATE_STATUSES = ("pending", "approved", "rejected", "ingested")

CANDIDATE_SCHEMA = """
create table if not exists knowledge_candidates (
  id             bigserial primary key,
  kind           text not null default 'metric',      -- 本轮只有 metric；term/rule 显式拒绝
  subject        text not null,                       -- 口径主体：表.字段/指标，如 ads.ads_产销存月报.output_qty
  chinese_name   text,
  formula        text,                                -- 公式
  depends_on     jsonb not null default '[]'::jsonb,   -- [{"table": "...", "column": "..."}]
  source_script  text,                                -- 来源脚本路径（入库的硬门槛）
  source_line    int,
  intent         text not null default 'new',          -- new / replace（M3-03 / #14：改口径要有人认账）
  conflicts      jsonb not null default '[]'::jsonb,   -- 提交时与谁冲突（留痕，含"声明替换"的那次）
  note           text,
  submitted_by   text not null,
  submitted_at   timestamptz not null default now(),
  status         text not null default 'pending',      -- pending / approved / rejected / ingested
  reviewer       text,
  reviewed_at    timestamptz,
  review_reason  text,                                 -- 审核人写的话
  worth_keeping  boolean,                              -- 「值得留下」由责任人确认（价值判断入口）
  rejected_reason text,                                -- 拒绝的原因（拒绝态必填，库层约束）
  ingested_at    timestamptz,
  ingested_by    text,
  problems       jsonb not null default '[]'::jsonb,   -- 被拒时逐条原因（码 + 说明），供审计回溯
  constraint knowledge_candidates_status_chk
    check (status in ('pending', 'approved', 'rejected', 'ingested')),
  constraint knowledge_candidates_kind_chk
    check (kind in ('metric', 'term', 'rule')),
  -- 拒绝必须说明原因：说不清原因的拒绝不是审核，是拍脑袋
  constraint knowledge_candidates_reject_reason_chk
    check (status <> 'rejected' or coalesce(btrim(rejected_reason), '') <> ''),
  -- 质量门禁（Issue #12 验收）：入库态必须带齐 来源脚本 + 公式 + 依赖字段。
  -- 放在这里的意思是：**绕过接口直接 update 也进不去库**。
  constraint knowledge_candidates_ingest_requires_source_chk
    check (
      status <> 'ingested' or (
        coalesce(btrim(source_script), '') <> ''
        and coalesce(btrim(formula), '') <> ''
        and depends_on <> '[]'::jsonb
      )
    )
);
create index if not exists idx_kb_candidates_status on knowledge_candidates(status, submitted_at desc);
create index if not exists idx_kb_candidates_subject on knowledge_candidates(subject);

-- 补列（已存在的库）：M3-01 建的候选表没有 intent / conflicts
alter table knowledge_candidates add column if not exists intent    text not null default 'new';
alter table knowledge_candidates add column if not exists conflicts jsonb not null default '[]'::jsonb;
alter table knowledge_candidates drop constraint if exists knowledge_candidates_intent_chk;
alter table knowledge_candidates add  constraint knowledge_candidates_intent_chk
  check (intent in ('new', 'replace'));
"""

METRIC_SCHEMA = """
create table if not exists knowledge_metrics (
  id            bigserial primary key,
  candidate_id  bigint not null references knowledge_candidates(id),  -- 每个入库口径都能追回它的候选与审核人
  subject       text not null,
  chinese_name  text,
  formula       text not null,
  depends_on    jsonb not null default '[]'::jsonb,
  source_script text not null,
  source_line   int,
  version       int not null default 1,              -- 同一 subject 内自增（M3-02 / #13 的版本号规则）
  status        text not null default 'active',      -- active / superseded / rolled_back
  approved_by   text not null,
  note          text,
  created_at    timestamptz not null default now(),
  rolled_back_at   timestamptz,                      -- 被回滚降级的时间（谁、为什么见下面两列）
  rolled_back_by   text,
  rollback_reason  text,
  -- 库里不可能存在「没有来源脚本」的口径 —— 这是 M3-01 的验收，钉在库层
  constraint knowledge_metrics_source_required_chk
    check (
      coalesce(btrim(source_script), '') <> ''
      and coalesce(btrim(formula), '') <> ''
      and depends_on <> '[]'::jsonb
    ),
  constraint knowledge_metrics_version_chk check (version >= 1),
  constraint knowledge_metrics_status_chk check (status in ('active', 'superseded', 'rolled_back')),
  -- 回滚也要说清楚：谁把它撤下来的、为什么（说不清的回滚与说不清的拒绝同样不可接受）
  constraint knowledge_metrics_rollback_trace_chk
    check (
      status <> 'rolled_back' or (
        rolled_back_at is not null
        and coalesce(btrim(rolled_back_by), '') <> ''
        and coalesce(btrim(rollback_reason), '') <> ''
      )
    )
);
create index if not exists idx_kb_metrics_subject on knowledge_metrics(subject, version desc);
create index if not exists idx_kb_metrics_candidate on knowledge_metrics(candidate_id);

-- 补列（已存在的库）：M3-01 建的表没有回滚留痕三列
alter table knowledge_metrics add column if not exists rolled_back_at  timestamptz;
alter table knowledge_metrics add column if not exists rolled_back_by  text;
alter table knowledge_metrics add column if not exists rollback_reason text;
-- 状态值域扩容（M3-01 只有 active/rolled_back）：先删后加，保证幂等
alter table knowledge_metrics drop constraint if exists knowledge_metrics_status_chk;
alter table knowledge_metrics add  constraint knowledge_metrics_status_chk
  check (status in ('active', 'superseded', 'rolled_back'));
alter table knowledge_metrics drop constraint if exists knowledge_metrics_rollback_trace_chk;
alter table knowledge_metrics add  constraint knowledge_metrics_rollback_trace_chk
  check (
    status <> 'rolled_back' or (
      rolled_back_at is not null
      and coalesce(btrim(rolled_back_by), '') <> ''
      and coalesce(btrim(rollback_reason), '') <> ''
    )
  );
-- **同一口径同时只能有一个生效版本**：库层不变量，partial unique index 兜住
create unique index if not exists uq_kb_metrics_one_active
  on knowledge_metrics(subject) where status = 'active';
"""

HISTORY_SCHEMA = """
-- 版本历史（M3-02 / #13 的「历史表」）：每次版本状态变化一行，谁、什么时候、从哪版到哪版、为什么
create table if not exists knowledge_metric_history (
  id              bigserial primary key,
  metric_id       bigint not null references knowledge_metrics(id),
  subject         text not null,
  version         int not null,
  event           text not null,        -- entered / superseded / rolled_back / reactivated
  related_version int,                  -- 因为哪一版（取代它的 / 回到的那版）
  actor           text not null,
  reason          text,
  created_at      timestamptz not null default now(),
  constraint knowledge_metric_history_event_chk
    check (event in ('entered', 'superseded', 'rolled_back', 'reactivated')),
  -- 回滚必须写明原因（与"拒绝必须说明原因"同一条道理）
  constraint knowledge_metric_history_reason_chk
    check (event <> 'rolled_back' or coalesce(btrim(reason), '') <> '')
);
create index if not exists idx_kb_metric_history_subject on knowledge_metric_history(subject, id desc);
"""

CANDIDATE_COLUMNS = (
    "id, kind, subject, chinese_name, formula, depends_on, source_script, source_line, intent, conflicts, "
    "note, submitted_by, submitted_at, status, reviewer, reviewed_at, review_reason, worth_keeping, "
    "rejected_reason, ingested_at, ingested_by, problems"
)

METRIC_COLUMNS = (
    "id, candidate_id, subject, chinese_name, formula, depends_on, source_script, source_line, "
    "version, status, approved_by, note, created_at, rolled_back_at, rolled_back_by, rollback_reason"
)


@dataclass(frozen=True)
class ReviewOutcome:
    """审核结果。`status` 是审核后的候选状态；失败时带上错误码。"""

    ok: bool
    status: str | None = None
    error: str | None = None
    detail: str | None = None


def init_knowledge_schema() -> None:
    """幂等建表（三张）+ 给老库补列换约束。**只在 startup 调**，别放 import 期（P0-3：会把测试收集也拖挂）。"""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(CANDIDATE_SCHEMA)
        cur.execute(METRIC_SCHEMA)
        cur.execute(HISTORY_SCHEMA)


def available() -> bool:
    """数据库是否可用。复用 `store` 的那一份探测，不重复实现 ——
    不可用时接口照实说"未落库"，而不是返回一个看起来正常的空结果。"""
    return _db_available()


# ---------------------------------------------------------------- 候选池：提交 / 查询


def record_candidate(
    *,
    subject: str,
    submitted_by: str,
    kind: str = "metric",
    chinese_name: str | None = None,
    formula: str | None = None,
    depends_on: list[dict[str, str]] | None = None,
    source_script: str | None = None,
    source_line: int | None = None,
    intent: str = "new",
    conflicts: list[dict[str, Any]] | None = None,
    note: str = "",
    problems: list[dict[str, str]] | None = None,
) -> Persisted:
    """写入一条候选（`status='pending'`）。校验不通过的行**不该走到这里**（接口先拦），
    真进来了也不影响：库层的门槛只认 `ingested` 态。

    ``intent`` / ``conflicts``（M3-03 / #14）：候选自己声明的意图，以及提交那一刻"和谁冲突"的清单 ——
    声明 `replace` 时冲突清单照样要留痕，因为"这次替换是谁认的账"比"替换了"更重要。
    """
    return _write(
        """insert into knowledge_candidates
              (kind, subject, chinese_name, formula, depends_on, source_script, source_line,
               intent, conflicts, note, submitted_by, problems)
            values (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb) returning id""",
        (
            kind, subject, chinese_name, formula,
            json.dumps(depends_on or [], ensure_ascii=False),
            source_script, source_line, intent,
            json.dumps(conflicts or [], ensure_ascii=False),
            note, submitted_by,
            json.dumps(problems or [], ensure_ascii=False),
        ),
    )


def get_candidate(candidate_id: int) -> dict[str, Any] | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"select {CANDIDATE_COLUMNS} from knowledge_candidates where id = %s", (candidate_id,))
        row = cur.fetchone()
        return _row(cur, row) if row else None


def list_candidates(
    *,
    limit: int = 50,
    status: str | None = None,
    subject: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        if status not in CANDIDATE_STATUSES:
            raise ValueError(f"不认识的状态：{status!r}（可选 {'/'.join(CANDIDATE_STATUSES)}）")
        clauses.append("status = %s")
        params.append(status)
    if subject:
        clauses.append("subject = %s")
        params.append(subject)
    where = f"where {' and '.join(clauses)}" if clauses else ""
    params.append(limit)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"select {CANDIDATE_COLUMNS} from knowledge_candidates {where} "  # noqa: S608 - 条件来自本模块白名单
            "order by id desc limit %s",
            params,
        )
        return [_row(cur, row) for row in cur.fetchall()]


# ---------------------------------------------------------------- 候选池：审核


def mark_reviewed(
    *,
    candidate_id: int,
    decision: str,
    reviewer: str,
    reason: str = "",
    worth_keeping: bool | None = None,
) -> ReviewOutcome:
    """审核一次（`pending` → `approved` / `rejected`）。

    - 只认 `pending` 的候选：重复审核返回 `status_conflict`，不覆盖前一次结论（审计要的是"第一次谁审的"）。
    - `rejected` 必须带原因（库层也钉了同一条件）。
    - 返回受影响行数，**0 行 = 状态已被别人改过**，如实报冲突，不假装成功。
    """
    if decision not in ("approve", "reject"):
        return ReviewOutcome(ok=False, error=E_BAD_REQUEST, detail=f"不认识的审核决定：{decision!r}")
    status = "approved" if decision == "approve" else "rejected"
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """update knowledge_candidates
                      set status = %s, reviewer = %s, reviewed_at = now(),
                          review_reason = %s, worth_keeping = %s, rejected_reason = %s
                    where id = %s and status = 'pending'
                  returning status""",
                (
                    status, reviewer, reason, worth_keeping,
                    reason if decision == "reject" else None,
                    candidate_id,
                ),
            )
            row = cur.fetchone()
            if row is None:
                return ReviewOutcome(
                    ok=False,
                    error=E_STATUS_CONFLICT,
                    detail=f"候选 #{candidate_id} 不是 pending：可能已被审过；状态没被改动",
                )
            return ReviewOutcome(ok=True, status=row[0])
    except Exception as exc:  # noqa: BLE001 - 落库失败不该把接口带崩（#9 的约定）
        return ReviewOutcome(ok=False, error=E_BAD_REQUEST, detail=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- 候选池：入库


def ingest(*, candidate_id: int, ingested_by: str, problems: list[dict[str, str]] | None = None) -> Persisted:
    """把已批准的候选写进口径库（`knowledge_metrics`），并把候选置为 `ingested`。

    一个事务里做五件事：**锁行 → 校验状态 → 取号（版本自增）→ 降级旧版本 → 写口径行 + 改候选状态 + 写历史**。
    任何一步失败都整体回滚，不会留下"口径进了库、候选还停在 approved"或"版本号取了但没入库"的错账。

    **版本号规则（M3-02 / #13）**：同一 `subject` 内 `version = max(version) + 1`；
    新版本入库时把该主体的旧生效版本降级为 `superseded`（先降级再插入 ——
    否则会同时存在两条 `active`，被 `uq_kb_metrics_one_active` 当场拒绝）。
    顺带说明：**降级必须发生在插入之前**，这是那个 partial unique index 要求的顺序，不是随手写的。

    ``problems`` 只用于把"接口判定的拒绝原因"落进候选行做留痕（例如缺来源脚本），
    真正的拒绝在事务外由接口层决定；这里仍会**再确认一次状态**，因为锁行之前的状态可能已经变了。
    """
    try:
        with connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "select status, subject from knowledge_candidates where id = %s for update",
                (candidate_id,),
            )
            row = cur.fetchone()
            if row is None:
                return Persisted(ok=False, error=E_NOT_FOUND)
            status, subject = row[0], row[1]
            if status == "ingested":
                return Persisted(ok=False, error=E_ALREADY_INGESTED)
            if status != "approved":
                return Persisted(ok=False, error=E_NOT_APPROVED)

            # 取号：这一版是第几版
            cur.execute("select coalesce(max(version), 0) + 1 from knowledge_metrics where subject = %s",
                        (subject,))
            version = int(cur.fetchone()[0])

            # 降级旧版本（先降级，再插入 —— 见上面 docstring）
            cur.execute(
                """update knowledge_metrics set status = 'superseded'
                    where subject = %s and status = 'active'
                  returning id, version""",
                (subject,),
            )
            for prev_id, prev_version in cur.fetchall():
                record_event(cur, metric_id=int(prev_id), subject=subject, version=int(prev_version),
                             event=EVENT_SUPERSEDED, actor=ingested_by, related_version=version)

            cur.execute(
                """insert into knowledge_metrics
                      (candidate_id, subject, chinese_name, formula, depends_on, source_script,
                       source_line, version, status, approved_by, note)
                    select id, subject, chinese_name, formula, depends_on, source_script,
                           source_line, %s, 'active', %s, note
                      from knowledge_candidates where id = %s
                    returning id""",
                (version, ingested_by, candidate_id),
            )
            metric_row = cur.fetchone()
            if metric_row is None:
                return Persisted(ok=False, error=E_NOT_FOUND)

            record_event(cur, metric_id=int(metric_row[0]), subject=subject, version=version,
                         event=EVENT_ENTERED, actor=ingested_by)

            _mark_ingested(cur, candidate_id=candidate_id, ingested_by=ingested_by, problems=problems)
            return Persisted(ok=True, id=int(metric_row[0]))
    except Exception as exc:  # noqa: BLE001 - 同上：失败要如实回报，而不是抛给调用方
        return Persisted(ok=False, error=f"{type(exc).__name__}: {exc}")


def _mark_ingested(cur: Any, *, candidate_id: int, ingested_by: str, problems: list[dict[str, str]] | None) -> None:
    """把候选置为 `ingested`。单独拆出来是为了让"事务原子性"这条能被测试钉住
    （测试 monkeypatch 本函数抛异常，断言口径行也被回滚掉）。"""
    cur.execute(
        """update knowledge_candidates
              set status = 'ingested', ingested_at = now(), ingested_by = %s, problems = %s::jsonb
            where id = %s""",
        (ingested_by, json.dumps(problems or [], ensure_ascii=False), candidate_id),
    )


def list_metrics(*, limit: int = 50, subject: str | None = None) -> list[dict[str, Any]]:
    """已入库的口径（验收要看"到底进没进去"）。"""
    where = "where subject = %s" if subject else ""
    params: list[Any] = [subject, limit] if subject else [limit]
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"select {METRIC_COLUMNS} from knowledge_metrics {where} "  # noqa: S608 - 同上，白名单条件
            "order by id desc limit %s",
            params,
        )
        return [_row(cur, row) for row in cur.fetchall()]


# ---------------------------------------------------------------- 内部工具


def _row(cur: Any, row: tuple) -> dict[str, Any]:
    return dict(zip([c.name for c in cur.description], row, strict=False))


def _write(sql: str, params: tuple) -> Persisted:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return Persisted(ok=True, id=int(row[0]) if row else None)
    except Exception as exc:  # noqa: BLE001 - 同 #9：落库失败返回失败结果，不抛异常
        return Persisted(ok=False, error=f"{type(exc).__name__}: {exc}")

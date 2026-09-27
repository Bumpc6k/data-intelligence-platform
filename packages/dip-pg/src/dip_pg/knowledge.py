"""知识候选池与入库落点的访问层（工作项 M3-01 / Issue #12）。

**只新增两张表**，`store.py`（判定留痕）与既有 `sessions` / `messages` / `audit_log` 一个字不动：

- `knowledge_candidates`：候选池。谁提的、提了什么、谁审的、审成什么、什么时候进的库。
- `knowledge_metrics`：**入库落点**（平台侧结构化通道的口径库）。只有过了质量门禁的候选才能在这里出现一行。

本模块最要紧的一条设计：**质量门禁落在库层，不只是接口层**。
`knowledge_candidates` 与 `knowledge_metrics` 都带 `CHECK` 约束 ——
`ingested` 的行必须带齐「来源脚本 + 公式 + 依赖字段」，`knowledge_metrics` 里也**不可能**
存在一行没有来源脚本的口径。这样"绕过接口直接写库"（验收真正要防的那条路）也堵死了，
而不是靠每个调用点自觉。接口层的校验只负责把"为什么进不去"讲清楚。

第二条：**入库是事务**（`conn.transaction()`）。写口径行与改候选状态要么都成、要么都不成 ——
不允许出现"口径进去了但候选还停在 approved"这种对不上账的中间态。
`psycopg` 的 `Connection.transaction()` 在 autocommit 连接上也能开显式事务，
所以这里继续复用 `store.connect()`（DSN 规则与连接超时只维护一份）。

第三条：`record_*` 沿用 #9 的约定 —— **落库失败不抛异常**，返回 `Persisted(ok=False, error=...)`，
让接口把"没落上"如实告诉用户。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

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
  version       int not null default 1,              -- 版本与回滚是 M3-02（#13）；这里只落地第 1 版
  status        text not null default 'active',
  approved_by   text not null,
  note          text,
  created_at    timestamptz not null default now(),
  -- 库里不可能存在「没有来源脚本」的口径 —— 这是本 Issue 的验收，钉在库层
  constraint knowledge_metrics_source_required_chk
    check (
      coalesce(btrim(source_script), '') <> ''
      and coalesce(btrim(formula), '') <> ''
      and depends_on <> '[]'::jsonb
    ),
  constraint knowledge_metrics_version_chk check (version >= 1),
  constraint knowledge_metrics_status_chk check (status in ('active', 'rolled_back'))
);
create index if not exists idx_kb_metrics_subject on knowledge_metrics(subject, version desc);
create index if not exists idx_kb_metrics_candidate on knowledge_metrics(candidate_id);
"""

CANDIDATE_COLUMNS = (
    "id, kind, subject, chinese_name, formula, depends_on, source_script, source_line, note, "
    "submitted_by, submitted_at, status, reviewer, reviewed_at, review_reason, worth_keeping, "
    "rejected_reason, ingested_at, ingested_by, problems"
)

METRIC_COLUMNS = (
    "id, candidate_id, subject, chinese_name, formula, depends_on, source_script, source_line, "
    "version, status, approved_by, note, created_at"
)


@dataclass(frozen=True)
class ReviewOutcome:
    """审核结果。`status` 是审核后的候选状态；失败时带上错误码。"""

    ok: bool
    status: str | None = None
    error: str | None = None
    detail: str | None = None


def init_knowledge_schema() -> None:
    """幂等建表（两张）。**只在 startup 调**，别放 import 期（P0-3：会把测试收集也拖挂）。"""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(CANDIDATE_SCHEMA)
        cur.execute(METRIC_SCHEMA)


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
    note: str = "",
    problems: list[dict[str, str]] | None = None,
) -> Persisted:
    """写入一条候选（`status='pending'`）。校验不通过的行**不该走到这里**（接口先拦），
    真进来了也不影响：库层的门槛只认 `ingested` 态。"""
    return _write(
        """insert into knowledge_candidates
              (kind, subject, chinese_name, formula, depends_on, source_script, source_line,
               note, submitted_by, problems)
            values (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::jsonb) returning id""",
        (
            kind, subject, chinese_name, formula,
            json.dumps(depends_on or [], ensure_ascii=False),
            source_script, source_line, note, submitted_by,
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

    一个事务里做三件事：**锁行 → 校验状态 → 写口径行 + 改候选状态**。
    任何一步失败都整体回滚，不会留下"口径进了库、候选还停在 approved"的错账。

    ``problems`` 只用于把"接口判定的拒绝原因"落进候选行做留痕（例如缺来源脚本），
    真正的拒绝在事务外由接口层决定；这里仍会**再确认一次状态**，因为锁行之前的状态可能已经变了。
    """
    try:
        with connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "select status from knowledge_candidates where id = %s for update", (candidate_id,)
            )
            row = cur.fetchone()
            if row is None:
                return Persisted(ok=False, error=E_NOT_FOUND)
            status = row[0]
            if status == "ingested":
                return Persisted(ok=False, error=E_ALREADY_INGESTED)
            if status != "approved":
                return Persisted(ok=False, error=E_NOT_APPROVED)

            cur.execute(
                """insert into knowledge_metrics
                      (candidate_id, subject, chinese_name, formula, depends_on, source_script,
                       source_line, approved_by, note)
                    select id, subject, chinese_name, formula, depends_on, source_script,
                           source_line, %s, note
                      from knowledge_candidates where id = %s
                    returning id""",
                (ingested_by, candidate_id),
            )
            metric_row = cur.fetchone()
            if metric_row is None:
                return Persisted(ok=False, error=E_NOT_FOUND)

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

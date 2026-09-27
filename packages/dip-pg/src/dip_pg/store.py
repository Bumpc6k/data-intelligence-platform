"""PostgreSQL 访问层：防火墙判定的留痕表（Issue #9 / M2-03）。

沿用 `portal_api.store` 已经踩出来的两条约定，别另起一套：

1. **一定要带 `connect_timeout`**：libpq 默认无限等待，数据库不可达时会**静默挂死**
   （实测：裸 socket 2 秒就 ConnectionRefused，psycopg 却能挂 60 秒以上 —— 验证报告 P0-3）。
2. **不用 ORM / Alembic**，`CREATE TABLE IF NOT EXISTS` 幂等建表（ADR-0005：表少、字段稳定，
   等出现第二个消费者或字段变更再引迁移工具）。

本模块最要紧的一条设计：**落库失败绝不影响判定**（Issue #9 的验收）——
`record_*` 永远不抛异常，失败时返回 `Persisted(ok=False, error=...)`，让调用方能把
"未落库"显式告诉用户，而不是让一次数据库抖动把安全判定带崩。
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psycopg

DEFAULT_DSN = "postgresql://dip:dip@127.0.0.1:15432/dip"
CONNECT_TIMEOUT = int(os.environ.get("DIP_PG_CONNECT_TIMEOUT", "3"))

#: 只新增这一张表。既有 sessions / messages / audit_log 不在这里定义、也不在这里改。
JUDGMENT_SCHEMA = """
create table if not exists firewall_judgments (
  id             bigserial primary key,
  kind           text not null,                       -- 'judge' = 一次判定；'token' = 一次令牌操作
  actor          text not null,                       -- 谁
  action         text not null,                       -- 什么动作
  target         text,
  sql_text       text,
  tier           text,                                -- 判成哪一档（auto/approval/deny）
  disposition    text,                                -- 处置（auto_pass/require_approval/reject）
  matched        boolean,                             -- 命中策略（false = 走的默认档）
  matched_rules  jsonb not null default '[]'::jsonb,
  reason         text,                                -- 依据
  fingerprint    text,                                -- 动作指纹（绑的到底是哪一件事）
  policy_version int,
  policy_digest  text,                                -- 当时用的是哪一版策略
  token_event    text,                                -- 'issued' | 'verified' | 'rejected'
  approver       text,                                -- 审批人（"上一级确认一次"的那个人）
  created_at     timestamptz not null default now()
);
create index if not exists idx_fw_judgments_kind   on firewall_judgments(kind, created_at desc);
create index if not exists idx_fw_judgments_action on firewall_judgments(action, created_at desc);
create index if not exists idx_fw_judgments_tier   on firewall_judgments(tier);
"""


@dataclass(frozen=True)
class Persisted:
    """落库结果。**不是异常** —— 判定照常返回，只是把"没落上"如实告诉调用方。"""

    ok: bool
    id: int | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"persisted": self.ok, "persist_error": self.error}


def _dsn() -> str:
    dsn = os.environ.get("DIP_PG_DSN", DEFAULT_DSN)
    if "connect_timeout" not in dsn:
        dsn += ("&" if "?" in dsn else "?") + f"connect_timeout={CONNECT_TIMEOUT}"
    return dsn


@contextmanager
def connect():
    conn = psycopg.connect(_dsn(), autocommit=True, connect_timeout=CONNECT_TIMEOUT)
    try:
        yield conn
    finally:
        conn.close()


def available() -> bool:
    """数据库是否可用。不可用时判定照做，只是不留痕。"""
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("select 1")
        return True
    except Exception:  # noqa: BLE001 - 探测可用性不该把异常漏给调用方
        return False


def init_schema() -> None:
    """幂等建表。**只在 startup 调**，别放 import 期（P0-3：会把测试收集也拖挂）。"""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(JUDGMENT_SCHEMA)


def record_judgment(
    *,
    actor: str,
    action: str,
    tier: str,
    disposition: str,
    matched: bool,
    target: str | None = None,
    sql: str | None = None,
    matched_rules: tuple[str, ...] | list[str] = (),
    reason: str = "",
    fingerprint: str = "",
    policy_version: int | None = None,
    policy_digest: str | None = None,
) -> Persisted:
    """记一次判定。失败**不抛异常**，返回 `Persisted(ok=False, error=...)`。"""
    return _insert(
        """insert into firewall_judgments(kind, actor, action, target, sql_text, tier, disposition,
                                          matched, matched_rules, reason, fingerprint,
                                          policy_version, policy_digest)
           values ('judge', %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
        (
            actor, action, target, sql, tier, disposition, matched,
            json.dumps(list(matched_rules), ensure_ascii=False), reason, fingerprint,
            policy_version, policy_digest,
        ),
    )


def record_token_event(
    *,
    event: str,
    actor: str,
    action: str,
    ok: bool,
    target: str | None = None,
    sql: str | None = None,
    approver: str | None = None,
    reason: str = "",
    fingerprint: str = "",
) -> Persisted:
    """记一次令牌操作（签发 / 校验通过 / 校验被拒）。

    校验没通过时把 `event` 从 `verified` 改写成 `rejected` —— **被拒的那次也要留痕**，
    否则"谁试图用一张不合法的令牌"这件事就查不到了。

    令牌行**不填 tier/disposition**：那两列的含义是"判成哪一档"，令牌操作没有档位，
    塞个 'token' 进去只会污染语义。要看结果就看 `token_event`。
    """
    if event == "verified" and not ok:
        event = "rejected"
    if event not in {"issued", "verified", "rejected"}:
        raise ValueError(f"不认识的令牌事件：{event!r}")
    return _insert(
        """insert into firewall_judgments(kind, actor, action, target, sql_text, tier, disposition,
                                          matched_rules, reason, fingerprint, token_event, approver)
           values ('token', %s,%s,%s,%s, null, null, '[]'::jsonb, %s,%s,%s,%s) returning id""",
        (actor, action, target, sql, reason, fingerprint, event, approver),
    )


def _insert(sql: str, params: tuple) -> Persisted:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return Persisted(ok=True, id=int(row[0]) if row else None)
    except Exception as exc:  # noqa: BLE001 - 落库失败不该影响判定（Issue #9 的验收）
        return Persisted(ok=False, error=f"{type(exc).__name__}: {exc}")


def list_judgments(
    *,
    limit: int = 50,
    kind: str | None = None,
    action: str | None = None,
) -> list[dict[str, Any]]:
    """按时间倒序取判定记录。给 `/api/audit` 的判定视图用。"""
    clauses: list[str] = []
    params: list[Any] = []
    if kind:
        clauses.append("kind = %s")
        params.append(kind)
    if action:
        clauses.append("action = %s")
        params.append(action)
    where = f"where {' and '.join(clauses)}" if clauses else ""
    params.append(limit)

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""select id, kind, actor, action, target, sql_text, tier, disposition, matched,
                       matched_rules, reason, fingerprint, policy_version, policy_digest,
                       token_event, approver, created_at
                from firewall_judgments {where} order by id desc limit %s""",  # noqa: S608 - 条件来自本模块的白名单拼装
            params,
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]

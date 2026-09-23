"""审计与会话存储（PostgreSQL，工作项 W-117 / W-112）。

分层原则：**只有 API 层知道数据库**——`dip-agent` 保持纯粹（它不该知道 PG 或 SQL），
所以审计写入发生在 `routers/agent.py` 拿到 `Answer` 之后，而不是编排过程里。

P1 取舍：不用 ORM/Alembic，用 psycopg3 + 启动时幂等建表（`CREATE TABLE IF NOT EXISTS`）。
理由：表少、字段稳定、迁移需求还没出现；等出现第二个消费者或字段变更再引 Alembic（见 ADR-0005）。
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any

import psycopg

DSN = os.environ.get("DIP_PG_DSN", "postgresql://dip:dip@127.0.0.1:15432/dip")

SCHEMA = """
create table if not exists sessions (
  id            text primary key,
  title         text,
  turns         int not null default 0,
  first_seen_at timestamptz not null default now(),
  last_seen_at  timestamptz not null default now()
);

create table if not exists messages (
  id          bigserial primary key,
  session_id  text not null references sessions(id) on delete cascade,
  role        text not null,
  text        text not null,
  answer      jsonb,
  created_at  timestamptz not null default now()
);
create index if not exists idx_messages_session on messages(session_id, created_at desc);

create table if not exists audit_log (
  id             bigserial primary key,
  session_id     text not null,
  question       text not null,
  mode           text not null default 'rule',
  status         text not null,
  confidence     real,
  version        text,
  value_display  text,
  evidence_count int  not null default 0,
  evidence       jsonb not null default '[]'::jsonb,
  tool_calls     jsonb not null default '[]'::jsonb,
  kernel_calls   int  not null default 0,
  total_ms       int  not null default 0,
  created_at     timestamptz not null default now()
);
create index if not exists idx_audit_session on audit_log(session_id, created_at desc);
create index if not exists idx_audit_status  on audit_log(status);
"""


def available() -> bool:
    """数据库是否可用（不可用时平台仍要能跑，只是不留痕——界面会提示）。"""
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("select 1")
        return True
    except Exception:
        return False


@contextmanager
def connect():
    conn = psycopg.connect(DSN, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def init_schema() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA)


def record_ask(session_id: str, question: str, answer: dict[str, Any], *, title: str | None = None) -> int:
    """把一次问答落库：会话 → 消息 → 审计（含工具调用与证据明细）。返回 audit id。"""
    result = answer.get("result") or {}
    evidence = result.get("evidence") or []
    tool_calls = answer.get("tool_calls") or []
    total_ms = sum(int(c.get("ms") or 0) for c in tool_calls)

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """insert into sessions(id, title, turns) values (%s, %s, 1)
               on conflict (id) do update set turns = sessions.turns + 1, last_seen_at = now(),
                                              title = coalesce(sessions.title, excluded.title)""",
            (session_id, title or question[:60]),
        )
        cur.execute(
            "insert into messages(session_id, role, text) values (%s, 'user', %s)",
            (session_id, question),
        )
        cur.execute(
            "insert into messages(session_id, role, text, answer) values (%s, 'assistant', %s, %s)",
            (session_id, answer.get("text") or "", json.dumps(answer, ensure_ascii=False)),
        )
        cur.execute(
            """insert into audit_log(session_id, question, mode, status, confidence, version,
                                     value_display, evidence_count, evidence, tool_calls, kernel_calls, total_ms)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (
                session_id,
                question,
                answer.get("mode") or "rule",
                result.get("status") or "unresolved",
                result.get("confidence"),
                result.get("version"),
                (result.get("value") or {}).get("display"),
                len(evidence),
                json.dumps(evidence, ensure_ascii=False),
                json.dumps(tool_calls, ensure_ascii=False),
                len(tool_calls),
                total_ms,
            ),
        )
        return int(cur.fetchone()[0])


def list_audit(session_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    sql = """select id, session_id, question, mode, status, confidence, version, value_display,
                    evidence_count, kernel_calls, total_ms, created_at
             from audit_log {where} order by id desc limit %s"""
    where, params = ("where session_id = %s", [session_id, limit]) if session_id else ("", [limit])
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql.format(where=where), params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def list_sessions(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """select id, title, turns, first_seen_at, last_seen_at from sessions
               order by last_seen_at desc limit %s""",
            (limit,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def session_history(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """select role, text, answer, created_at from messages
               where session_id = %s order by id asc limit %s""",
            (session_id, limit),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def last_tables(session_id: str) -> list[str]:
    """从最近一次回答的证据里取回表名（供追问时回填上下文——刷新页面也不丢）。"""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """select evidence from audit_log where session_id = %s order by id desc limit 1""",
            (session_id,),
        )
        row = cur.fetchone()
    if not row:
        return []
    tables: list[str] = []
    for ev in row[0] or []:
        ref = str(ev.get("ref") or "")
        if ref.startswith("graph:"):
            tables.append(ref.replace("graph:", "").replace("#downstream", ""))
        elif ref.startswith("dict:"):
            tables.append(ref.replace("dict:", "").rsplit(".", 1)[0])
    return list(dict.fromkeys(tables))

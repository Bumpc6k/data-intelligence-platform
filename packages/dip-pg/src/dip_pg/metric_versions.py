"""口径的版本与回滚（工作项 M3-02 / Issue #13）。

入库的口径要能追溯历史、也能回滚。三条规则，都落在库层而不是靠调用方自觉：

1. **版本号按口径主体独立自增**：同一 `subject` 的第 n 次入库就是第 n 版（`version = max+1`）。
2. **同一时刻只有一个生效版本**：`create unique index ... on knowledge_metrics(subject) where status = 'active'`
   —— 这是库层的不变量，任何"两条同时生效"的写法都会当场被拒。
3. **不删历史**：旧版本降级为 `superseded`，被回滚下来的版本标 `rolled_back`（并必须留下
   谁、什么时候、为什么），生效版本回到目标版本时标 `active`。每一次状态变化都往
   `knowledge_metric_history` 写一条事件 —— "这口径被谁回滚过"这类问题要能一句话查出来。

**回滚是状态翻转，不是新增一版**（`v2 → 回滚到 v1` 之后生效的仍是 v1，不会冒出个"内容等于 v1 的 v3"）。
理由：版本号是给人看的"这是第几版口径"，插一个内容重复的新版本会让版本号失去意义；
而"什么时候回滚的"由历史表回答，信息一点没少。这一点与 `ingest`（每次入库**必**新增版本）是两种不同的变更，
所以分别用两种机制表达。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .store import available as _db_available
from .store import connect

#: 错误码（接口层据此给中文说明；测试断言码，不匹配文案）
E_SUBJECT_NOT_FOUND = "subject_not_found"
E_VERSION_NOT_FOUND = "version_not_found"
E_ALREADY_ACTIVE = "already_active"
E_NO_ACTIVE = "no_active_version"
E_BAD_REQUEST = "bad_request"

#: 版本事件（历史表）
EVENT_ENTERED = "entered"           # 某版本入库生效
EVENT_SUPERSEDED = "superseded"     # 某版本被新版本取代
EVENT_ROLLED_BACK = "rolled_back"   # 某版本被回滚降级
EVENT_REACTIVATED = "reactivated"   # 某版本因回滚重新生效
EVENTS = (EVENT_ENTERED, EVENT_SUPERSEDED, EVENT_ROLLED_BACK, EVENT_REACTIVATED)

METRIC_COLUMNS = (
    "id, candidate_id, subject, chinese_name, formula, depends_on, source_script, source_line, "
    "version, status, approved_by, note, created_at, rolled_back_at, rolled_back_by, rollback_reason"
)

HISTORY_COLUMNS = "id, subject, version, event, related_version, actor, reason, created_at, metric_id"


@dataclass(frozen=True)
class RollbackOutcome:
    """回滚结果。`from_version` / `to_version` 都填上，接口层照着写响应。"""

    ok: bool
    from_version: int | None = None
    to_version: int | None = None
    error: str | None = None
    detail: str | None = None


# ---------------------------------------------------------------- 事件留痕


def available() -> bool:
    """数据库是否可用。复用 `store` 的那一份探测，不重复实现。"""
    return _db_available()


def record_event(
    cur: Any,
    *,
    metric_id: int,
    subject: str,
    version: int,
    event: str,
    actor: str,
    related_version: int | None = None,
    reason: str | None = None,
) -> None:
    """写一条版本事件。**由调用方在自己事务里的游标上执行** —— 事件与状态变更必须同一事务，
    否则会出现"版本状态变了但历史里没记"。"""
    if event not in EVENTS:
        raise ValueError(f"不认识的版本事件：{event!r}")
    cur.execute(
        """insert into knowledge_metric_history
             (metric_id, subject, version, event, related_version, actor, reason)
           values (%s,%s,%s,%s,%s,%s,%s)""",
        (metric_id, subject, version, event, related_version, actor, reason),
    )


# ---------------------------------------------------------------- 查询


def active_metric(subject: str) -> dict[str, Any] | None:
    """当前生效版本（平台侧的 `/kb/metric`：问一句"这个口径现在按哪一版算"）。"""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"select {METRIC_COLUMNS} from knowledge_metrics where subject = %s and status = 'active'",
            (subject,),
        )
        row = cur.fetchone()
        return _row(cur, row) if row else None


def list_versions(subject: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """某口径的全部版本（含已降级/已回滚的），版本号从大到小。"""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"select {METRIC_COLUMNS} from knowledge_metrics where subject = %s order by version desc limit %s",
            (subject, limit),
        )
        return [_row(cur, row) for row in cur.fetchall()]


def history(subject: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """版本事件流水（谁、什么时候、从哪版到哪版、为什么）。"""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"select {HISTORY_COLUMNS} from knowledge_metric_history where subject = %s "
            "order by id desc limit %s",
            (subject, limit),
        )
        return [_row(cur, row) for row in cur.fetchall()]


# ---------------------------------------------------------------- 回滚


def rollback(*, subject: str, to_version: int, operated_by: str, reason: str) -> RollbackOutcome:
    """把某口径回滚到指定版本（**事务**：降级当前 + 恢复目标 + 两条事件，同生同死）。

    四种拒绝都有明确错误码，不猜：
    - 该主体没有任何版本 → `subject_not_found`
    - 目标版本不在这个主体下 → `version_not_found`（顺带防住"拿 A 的版本号回滚 B"）
    - 目标版本已经是生效版本 → `already_active`（不做无意义的变更，也不写脏历史）
    - 库里没有生效版本（数据被人手工改坏）→ `no_active_version`（如实报，不猜该用哪版）
    """
    try:
        with connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "select id, version, status from knowledge_metrics where subject = %s and version = %s",
                (subject, to_version),
            )
            target = cur.fetchone()
            if target is None:
                cur.execute("select 1 from knowledge_metrics where subject = %s limit 1", (subject,))
                if cur.fetchone() is None:
                    return RollbackOutcome(ok=False, error=E_SUBJECT_NOT_FOUND,
                                           detail=f"口径 {subject} 没有任何版本，无从回滚")
                return RollbackOutcome(ok=False, error=E_VERSION_NOT_FOUND,
                                       detail=f"口径 {subject} 没有第 {to_version} 版")

            cur.execute(
                "select id, version from knowledge_metrics where subject = %s and status = 'active' for update",
                (subject,),
            )
            current = cur.fetchone()
            if current is None:
                return RollbackOutcome(ok=False, error=E_NO_ACTIVE,
                                       detail=f"口径 {subject} 没有生效版本（数据被手工改过？）")
            current_id, current_version = int(current[0]), int(current[1])
            if current_version == to_version:
                return RollbackOutcome(ok=False, error=E_ALREADY_ACTIVE,
                                       detail=f"第 {to_version} 版已经是生效版本，无需回滚")

            # ① 降级当前版本，并说明为什么
            cur.execute(
                """update knowledge_metrics
                      set status = 'rolled_back', rolled_back_at = now(), rolled_back_by = %s,
                          rollback_reason = %s
                    where id = %s""",
                (operated_by, reason, current_id),
            )
            record_event(cur, metric_id=current_id, subject=subject, version=current_version,
                         event=EVENT_ROLLED_BACK, actor=operated_by, related_version=to_version,
                         reason=reason)

            # ② 目标版本重新生效
            cur.execute("update knowledge_metrics set status = 'active' where id = %s",
                        (int(target[0]),))
            record_event(cur, metric_id=int(target[0]), subject=subject, version=to_version,
                         event=EVENT_REACTIVATED, actor=operated_by, related_version=current_version)

            return RollbackOutcome(ok=True, from_version=current_version, to_version=to_version)
    except Exception as exc:  # noqa: BLE001 - 落库失败如实回报，不把接口带崩（#9 起的约定）
        return RollbackOutcome(ok=False, error=E_BAD_REQUEST,
                              detail=f"{type(exc).__name__}: {exc}")


def _row(cur: Any, row: tuple) -> dict[str, Any]:
    return dict(zip([c.name for c in cur.description], row, strict=False))

"""口径冲突的**数据访问**与**仲裁落地**（工作项 M3-03 / Issue #14）。

规则（《双人分工与 Windows 数据开发约定》§3.3 的质量门禁）：
**同字段两条公式冲突时必须择一，不允许并存**。

职责切分（照分层铁律：`dip_pg` 只依赖 psycopg，不许 import 契约层）：

| 谁 | 干什么 |
| --- | --- |
| `dip_contracts.knowledge` | **判定**：公式归一化、谁和谁冲突、按 `intent` 决定拦不拦（纯函数，好测） |
| 本模块 | **取数**（未决候选 / 版本）与**执行仲裁结果**（驳回候选、让指定版本重新生效） |

**不做自动仲裁**（Issue #14 的边界）：这里不给"哪条更对"打分，也不替人选 ——
`resolve()` 必须由调用方指定"留哪条"，且必须写明谁决定的、为什么。

`resolve()` 一个事务做完三件事：**驳回未决候选 + 改生效版本 + 写版本事件**。
**历史一行不删**：被驳回的候选行还留着（`rejected` + 理由），被回滚的版本还留着（`rolled_back`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .metric_versions import record_event
from .store import connect

#: 错误码（接口层据此给中文说明；测试断言码，不匹配文案）
E_CONFLICT_ACTIVE = "conflict_with_active_metric"
E_CONFLICT_PENDING = "conflict_with_pending_candidate"
E_NO_CONFLICT = "no_conflict"
E_CANDIDATE_NOT_FOUND = "candidate_not_found"
E_VERSION_NOT_FOUND = "version_not_found"
E_BAD_REQUEST = "bad_request"

#: 参与冲突判定的候选状态（未决 = 还没入库）
PENDING_STATUSES = ("pending", "approved")

CANDIDATE_COLUMNS = (
    "id, subject, formula, source_script, source_line, status, submitted_by, submitted_at, intent"
)


@dataclass(frozen=True)
class ResolveOutcome:
    """仲裁结果：留了谁、驳回了谁、现在生效的是哪一版。"""

    ok: bool
    kept: dict[str, Any] | None = None
    rejected_candidate_ids: list[int] = field(default_factory=list)
    active_version: int | None = None
    error: str | None = None
    detail: str | None = None


def available() -> bool:
    from .store import available as _db_available

    return _db_available()


# ---------------------------------------------------------------- 取数


def pending_candidates(subject: str, *, exclude_candidate_id: int | None = None) -> list[dict[str, Any]]:
    """某口径主体下**未决**的候选（pending / approved），按提交顺序。"""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""select {CANDIDATE_COLUMNS} from knowledge_candidates
                 where subject = %s and status = any(%s)
                   and (%s::bigint is null or id <> %s::bigint)
                 order by id""",
            (subject, list(PENDING_STATUSES), exclude_candidate_id, exclude_candidate_id),
        )
        return [_row(cur, row) for row in cur.fetchall()]


# ---------------------------------------------------------------- 仲裁（人决定，这里只执行）


def resolve(
    *,
    subject: str,
    operated_by: str,
    reason: str,
    keep_candidate_id: int | None = None,
    keep_version: int | None = None,
) -> ResolveOutcome:
    """执行一次仲裁：**留哪条由调用方指定**，其余未决候选驳回并写明理由。

    - `keep_candidate_id`：保留这条候选（其余未决候选全部驳回）。保留的候选仍需走正常审核 + 入库。
    - `keep_version`：让指定版本重新生效（等于 #13 的回滚），并把未决候选全部驳回。

    两种落选项必须且只能给一个 —— 两个都不给等于没做决定，两个都给等于决定不清。
    """
    if (keep_candidate_id is None) == (keep_version is None):
        return ResolveOutcome(ok=False, error=E_BAD_REQUEST,
                              detail="必须且只能指定一个落选项：keep_candidate_id 或 keep_version")

    try:
        with connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """select id, status, formula from knowledge_candidates
                    where subject = %s and status = any(%s) for update""",
                (subject, list(PENDING_STATUSES)),
            )
            candidates = [{"id": int(r[0]), "status": r[1], "formula": r[2]} for r in cur.fetchall()]

            cur.execute("select id, version from knowledge_metrics where subject = %s and status = 'active'",
                        (subject,))
            active_row = cur.fetchone()
            active_version = int(active_row[1]) if active_row else None

            kept: dict[str, Any] = {}
            to_reject: list[int] = []

            if keep_candidate_id is not None:
                target = next((c for c in candidates if c["id"] == keep_candidate_id), None)
                if target is None:
                    return ResolveOutcome(ok=False, error=E_CANDIDATE_NOT_FOUND,
                                          detail=f"候选 #{keep_candidate_id} 不在 {subject} 的未决候选里")
                to_reject = [c["id"] for c in candidates if c["id"] != keep_candidate_id]
                kept = {"kind": "candidate", "id": keep_candidate_id, "formula": target["formula"]}
            else:
                cur.execute(
                    "select id, version, formula from knowledge_metrics where subject = %s and version = %s",
                    (subject, keep_version),
                )
                target_metric = cur.fetchone()
                if target_metric is None:
                    return ResolveOutcome(ok=False, error=E_VERSION_NOT_FOUND,
                                          detail=f"口径 {subject} 没有第 {keep_version} 版")
                to_reject = [c["id"] for c in candidates]
                kept = {"kind": "metric", "version": int(target_metric[1]), "formula": target_metric[2]}
                if active_version != int(target_metric[1]):
                    if active_row is not None:
                        # 让指定版本重新生效 —— 与 metric_versions.rollback 同一套状态语义，
                        # 只是这里不另开事务（仲裁动作要么全成、要么全不成）
                        cur.execute(
                            """update knowledge_metrics
                                  set status = 'rolled_back', rolled_back_at = now(), rolled_back_by = %s,
                                      rollback_reason = %s
                                where id = %s""",
                            (operated_by, f"冲突仲裁：{reason}", int(active_row[0])),
                        )
                        record_event(cur, metric_id=int(active_row[0]), subject=subject,
                                     version=int(active_row[1]), event="rolled_back", actor=operated_by,
                                     related_version=int(target_metric[1]), reason=f"冲突仲裁：{reason}")
                    cur.execute("update knowledge_metrics set status = 'active' where id = %s",
                                (int(target_metric[0]),))
                    record_event(cur, metric_id=int(target_metric[0]), subject=subject,
                                 version=int(target_metric[1]), event="reactivated", actor=operated_by,
                                 related_version=active_version)
                    active_version = int(target_metric[1])

            if to_reject:
                cur.execute(
                    """update knowledge_candidates
                          set status = 'rejected', reviewer = %s, reviewed_at = now(),
                              review_reason = %s, rejected_reason = %s
                        where id = any(%s)""",
                    (operated_by, f"冲突仲裁：{reason}", f"冲突仲裁：{reason}", to_reject),
                )

            return ResolveOutcome(ok=True, kept=kept, rejected_candidate_ids=to_reject,
                                  active_version=active_version)
    except Exception as exc:  # noqa: BLE001 - 落库失败如实回报，不把接口带崩（#9 起的约定）
        return ResolveOutcome(ok=False, error=E_BAD_REQUEST, detail=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- 内部工具


def _row(cur: Any, row: tuple) -> dict[str, Any]:
    return dict(zip([c.name for c in cur.description], row, strict=False))

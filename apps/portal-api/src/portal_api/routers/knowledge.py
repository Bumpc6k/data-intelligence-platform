"""知识候选池接口（工作项 M3-01 / Issue #12；版本与回滚是 M3-02 / #13）：候选 → 审核 → 入库。

一条口径想进知识库，只能走这三步：

```
POST /api/knowledge/candidates             提交候选（必须带 来源脚本 + 公式 + 依赖字段）
POST /api/knowledge/candidates/{id}/review 责任人审核（approve / reject，reject 必须说明原因）
POST /api/knowledge/candidates/{id}/ingest 入库（只有已批准、且**再校验一遍**通过的才进得去）
GET  /api/knowledge/metrics                看得见"到底进没进去"
```

入库之后（M3-02 / #13）还能查版本、回滚：

```
GET  /api/knowledge/metrics/active?subject=...   当前按哪一版算（平台侧的 /kb/metric）
GET  /api/knowledge/metrics/versions?subject=... 全部版本（历史不删）
GET  /api/knowledge/metrics/history?subject=...  版本事件流水（谁、何时、从哪版到哪版、为什么）
POST /api/knowledge/metrics/rollback            回滚到指定版本（必须记名 + 写原因）
```

同一口径不允许两条不同公式并存（M3-03 / #14）：

```
GET  /api/knowledge/conflicts?subject=...        冲突全景（生效口径 + 未决候选，逐条列出）
POST /api/knowledge/conflicts/resolve            仲裁：留哪条由人指定（必须记名 + 写理由）
提交 / 审批时若与现存口径冲突 → 409 + 冲突项清单；要替换现有口径须显式声明 intent=replace
```

三层防护，各管一段（这是本 Issue 的设计要点，不是重复劳动）：

1. **契约层**（`dip_contracts.knowledge`）—— 把"为什么不行"讲清楚：逐条 `problems`，带错误码。
2. **接口层**（本文件）—— 管顺序与权限语义：没审核不许入库、重复审核不覆盖前一次结论、拒绝必须有原因。
3. **库层**（`dip_pg.knowledge` 的 `CHECK` 约束）—— **绕过接口直接改库也进不去**。
   前两层能被人绕过（直接连数据库写），第三层不能。

边界（Issue #12 明文）：**不做审核台 UI**；同字段冲突仲裁是 #14；版本号与回滚是 #13。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from dip_contracts.knowledge import (
    CandidateDraft,
    blocking_conflicts,
    conflict_entry,
    detect_conflicts,
    draft_from_row,
    has_conflict,
    problems_as_dicts,
    source_ref,
    validate_draft,
)
from dip_pg import conflicts as conflict_store
from dip_pg import knowledge as knowledge_store
from dip_pg import metric_versions as version_store
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(tags=["knowledge"])


def _json(status_code: int, content: dict) -> JSONResponse:
    """带状态码的 JSON 响应。

    必须先过 `jsonable_encoder`：候选行里有 `timestamptz`（`datetime`）与 `jsonb`（`dict/list`），
    直接塞给 `JSONResponse` 会在渲染时 `TypeError: Object of type datetime is not JSON serializable`。
    """
    return JSONResponse(status_code=status_code, content=jsonable_encoder(content))


def store() -> Any:
    """依赖注入点：测试里用 dependency_overrides 覆盖，即可不连数据库。"""
    return knowledge_store


def conflicts_store() -> Any:
    """依赖注入点：冲突取数与仲裁（同上，测试可覆盖）。"""
    return conflict_store


def _gather_conflicts(versions: Any, candidates_source: Any, subject: str, formula: str | None,
                      *, exclude_candidate_id: int | None = None) -> dict:
    """取"与这条公式冲突"的现存记录：生效口径 + 其他未决候选。

    判定（谁和谁冲突）在契约层的纯函数里，这里只负责取数与喂参数 ——
    分层铁律不许 `dip_pg` 依赖契约层，所以比较逻辑不能写在数据层。

    **两个取数对象是入参，不是模块级引用**：依赖注入点必须一路传到底，
    否则测试里替换掉的假 store 会被绕过、直接打到真库（这个坑真踩过）。
    """
    active = versions.active_metric(subject)
    active_rows = [active] if active else []
    pending_rows = candidates_source.pending_candidates(subject, exclude_candidate_id=exclude_candidate_id)
    return detect_conflicts(formula, active_rows=active_rows, pending_rows=pending_rows)


def _reject_on_conflict(error: str | None, items: list[dict], subject: str) -> Any:
    """冲突被拦时的统一响应：**列出冲突项**（验收明确要求），并说清两条出路。"""
    if not error:
        return None
    return _json(status_code=409, content={
        "success": False,
        "error": error,
        "message": ("同一口径不能并存两条不同公式（《双人分工》§3.3）——必须择一："
                    "先把冲突那条处理掉（审核驳回），或者用 POST /api/knowledge/conflicts/resolve 仲裁；"
                    "若是要替换现有口径，重新提交时声明 intent=replace。"),
        "subject": subject,
        "conflicts": items,
    })


# ---------------------------------------------------------------- 请求体


class ReviewRequest(BaseModel):
    """审核。`worth_keeping` 是交付物里的「价值判断入口」—— 由**人**回答"值不值得留下"。"""

    decision: Literal["approve", "reject"]
    reviewer: str = ""
    reason: str = ""
    worth_keeping: bool | None = None


class IngestRequest(BaseModel):
    ingested_by: str = ""


# ---------------------------------------------------------------- 提交 / 查询


@router.post("/knowledge/candidates")
def submit_candidate(
    draft: CandidateDraft,
    st: Annotated[Any, Depends(store)] = None,
    vs: Annotated[Any, Depends(versions_store)] = None,
    cs: Annotated[Any, Depends(conflicts_store)] = None,
) -> Any:
    """提交一条候选。校验不过 → `400`；与现存口径冲突 → `409` **并列出冲突项**（M3-03 / #14）。

    `intent=replace` 表示提交人明说"这是替换现有生效口径"——冲突清单照样记在候选行上，
    但不再拦。其余情况的冲突一律拦：**同一口径不允许两条不同公式并存**。
    """
    problems = validate_draft(draft)
    if problems:
        return _json(
            status_code=400,
            content={
                "success": False,
                "error": "invalid_candidate",
                "problems": problems_as_dicts(problems),
            },
        )
    if not st.available():
        return _json(
            status_code=503, content={"success": False, "error": "数据库不可用（未落库）"}
        )

    conflicts = _gather_conflicts(vs, cs, draft.subject, draft.formula)
    error, items = blocking_conflicts(draft.intent, conflicts, draft.tier)
    blocked = _reject_on_conflict(error, items, draft.subject)
    if blocked is not None:
        return blocked

    persisted = st.record_candidate(
        kind=draft.kind,
        subject=draft.subject,
        chinese_name=draft.chinese_name,
        formula=draft.formula,
        depends_on=[d.model_dump() for d in draft.depends_on],
        source_script=draft.source_script,
        source_line=draft.source_line,
        intent=draft.intent,
        # 声明替换时把"和谁冲突"留痕（谁认的账比"替换了"更重要）
        conflicts=[*conflicts["active"], *conflicts["pending"]],
        # 等级与定级依据（ADR-0006 / #38）：**p2 免仲裁也要留痕**——不拦 ≠ 不记
        tier=draft.tier,
        tier_reason=draft.tier_reason,
        tier_set_by=draft.tier_set_by,
        note=draft.note,
        submitted_by=draft.submitted_by,
    )
    if not persisted.ok:
        return _json(status_code=503, content={"success": False, "error": persisted.error})
    return _json(
        status_code=201,
        content={"success": True, "candidate": st.get_candidate(persisted.id)},
    )


@router.get("/knowledge/candidates")
def list_candidates(
    status: Annotated[str | None, Query(max_length=16)] = None,
    subject: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    st: Annotated[Any, Depends(store)] = None,
) -> dict:
    """候选列表。数据库不可用时**明说未落库**，不返回一个看起来正常的空列表（沿用 `/api/audit` 的约定）。"""
    if not st.available():
        return {"success": False, "error": "数据库不可用（未落库）", "items": []}
    try:
        items = st.list_candidates(status=status, subject=subject, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "items": items}


@router.get("/knowledge/candidates/{candidate_id}")
def candidate_detail(candidate_id: int, st: Annotated[Any, Depends(store)] = None) -> dict:
    if not st.available():
        raise HTTPException(status_code=503, detail="数据库不可用（未落库）")
    row = st.get_candidate(candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"候选 #{candidate_id} 不存在")
    return {"success": True, "candidate": row}


# ---------------------------------------------------------------- 审核


@router.post("/knowledge/candidates/{candidate_id}/review")
def review_candidate(
    candidate_id: int,
    req: ReviewRequest,
    st: Annotated[Any, Depends(store)] = None,
    vs: Annotated[Any, Depends(versions_store)] = None,
    cs: Annotated[Any, Depends(conflicts_store)] = None,
) -> Any:
    """责任人审核一次。三条规则都是"必须说清楚"，没有默认值：

    - 谁审的（`reviewer`）必填 —— 审计要的是人，不是"系统通过"；
    - `reject` 必须写原因 —— 说不清原因的拒绝不是审核；
    - `approve` 必须显式回答"值不值得留下"；`worth_keeping=false` 配 `approve` 是自相矛盾，直接拒。
    """
    if not req.reviewer.strip():
        return _json(status_code=400, content={"success": False, "error": "missing_reviewer",
                                                      "message": "缺审核人：审核要记名"})
    if req.decision == "reject" and not req.reason.strip():
        return _json(status_code=400, content={"success": False, "error": "missing_reason",
                                                      "message": "拒绝必须说明原因（缺来源 / 格式非法 / 价值不够）"})
    if req.decision == "approve":
        if req.worth_keeping is None:
            return _json(status_code=400, content={
                "success": False, "error": "missing_worth_keeping",
                "message": "请显式回答「值得留下」——这一栏由责任人确认，不给默认值",
            })
        if not req.worth_keeping:
            return _json(status_code=400, content={
                "success": False, "error": "contradictory_decision",
                "message": "worth_keeping=false 与 approve 矛盾：值不得留下就应当 reject 并写明原因",
            })
    if not st.available():
        return _json(status_code=503, content={"success": False, "error": "数据库不可用（未落库）"})

    # 审批是"择一"的现场：提交到审批之间可能有人又提交/入库了别的公式（M3-03 / #14）
    if req.decision == "approve":
        row = st.get_candidate(candidate_id)
        if row is None:
            return _json(status_code=404, content={"success": False, "error": "candidate_not_found",
                                                   "message": f"候选 #{candidate_id} 不存在"})
        conflicts = _gather_conflicts(vs, cs, row["subject"], row.get("formula"), exclude_candidate_id=candidate_id)
        error, items = blocking_conflicts(row.get("intent"), conflicts, row.get("tier"))
        blocked = _reject_on_conflict(error, items, row["subject"])
        if blocked is not None:
            return blocked

    outcome = st.mark_reviewed(
        candidate_id=candidate_id,
        decision=req.decision,
        reviewer=req.reviewer,
        reason=req.reason,
        worth_keeping=req.worth_keeping,
    )
    if not outcome.ok:
        code = 409 if outcome.error == knowledge_store.E_STATUS_CONFLICT else 400
        return _json(status_code=code, content={
            "success": False, "error": outcome.error, "message": outcome.detail,
        })
    return {"success": True, "candidate": st.get_candidate(candidate_id)}


# ---------------------------------------------------------------- 入库


@router.post("/knowledge/candidates/{candidate_id}/ingest")
def ingest_candidate(
    candidate_id: int,
    req: IngestRequest,
    st: Annotated[Any, Depends(store)] = None,
) -> Any:
    """入库。**审核通过 ≠ 入库**：这一步会把候选再校验一遍，再交给库层的事务写入。

    无来源脚本 / 格式非法 → `409` + 逐条 `problems`（说明"为什么进不去"）；
    状态不对（未批准、已入库）→ `409` + 错误码；库层 `CHECK` 拦下时也如实回报，不假装成功。
    """
    if not req.ingested_by.strip():
        return _json(status_code=400, content={"success": False, "error": "missing_operator",
                                                      "message": "缺入库操作人：入库要记名"})
    if not st.available():
        return _json(status_code=503, content={"success": False, "error": "数据库不可用（未落库）"})

    row = st.get_candidate(candidate_id)
    if row is None:
        return _json(status_code=404, content={"success": False, "error": "candidate_not_found",
                                                      "message": f"候选 #{candidate_id} 不存在"})

    status = row.get("status")
    if status != "approved":
        return _json(status_code=409, content={
            "success": False,
            "error": "already_ingested" if status == "ingested" else "not_approved",
            "message": f"候选当前状态 {status}：只有 approved 才谈入库（未审批或被拒的候选不进库）",
        })

    # 入库前再校验一遍：候选从提交到入库之间可能有人直接改过库（本项目明确要防的那条路）
    problems = validate_draft(draft_from_row(row))
    if problems:
        return _json(status_code=409, content={
            "success": False, "error": "ingest_rejected",
            "message": "候选没通过入库前的校验，未写入口径库",
            "problems": problems_as_dicts(problems),
        })

    persisted = st.ingest(candidate_id=candidate_id, ingested_by=req.ingested_by)
    if not persisted.ok:
        error = persisted.error or ""
        code = 409 if error in (knowledge_store.E_NOT_APPROVED, knowledge_store.E_ALREADY_INGESTED) else 400
        return _json(status_code=code, content={
            "success": False, "error": error or "ingest_failed",
            "message": "库层拒绝写入（可能被 CHECK 约束拦下）：口径库里不允许存在没有来源的条目",
        })
    return {
        "success": True,
        "metric_id": persisted.id,
        "candidate": st.get_candidate(candidate_id),
    }


# ---------------------------------------------------------------- 口径库（看"进没进去"）


def _with_source(row: dict) -> dict:
    """给口径行补上**来源展示**（ADR-0003：有行号说行号，没有就标"文件级"）。"""
    return {**row, "source": source_ref(row.get("source_script"), row.get("source_line")).model_dump()}


@router.get("/knowledge/metrics")
def list_metrics(
    subject: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    st: Annotated[Any, Depends(store)] = None,
) -> dict:
    if not st.available():
        return {"success": False, "error": "数据库不可用（未落库）", "items": []}
    return {"success": True, "items": [_with_source(row) for row in st.list_metrics(subject=subject, limit=limit)]}


# ---------------------------------------------------------------- 冲突检测与仲裁（M3-03 / #14）


class ResolveRequest(BaseModel):
    """一次仲裁：**留哪条由人指定**（不给默认值），且必须记名 + 写理由。"""

    subject: str = ""
    keep_candidate_id: int | None = None
    keep_version: int | None = None
    operated_by: str = ""
    reason: str = ""


@router.get("/knowledge/conflicts")
def list_conflicts(
    subject: Annotated[str, Query(min_length=1, max_length=200)],
    st: Annotated[Any, Depends(versions_store)] = None,
    cs: Annotated[Any, Depends(conflicts_store)] = None,
) -> dict:
    """某口径主体上的**冲突全景**：生效口径 + 未决候选，逐条列出来。

    只陈述事实（谁、什么公式、什么来源、什么时候），**不解释成对错** —— 判对错是人的事。
    """
    if not st.available():
        return {"success": False, "error": "数据库不可用（未落库）", "conflict": False,
                "active": [], "pending": []}
    active = st.active_metric(subject)
    active_rows = [active] if active else []
    pending_rows = cs.pending_candidates(subject)
    return {
        "success": True,
        "subject": subject,
        # 归一化后不止一条公式 = 有冲突（"同样的公式重复提交"不算冲突）
        "conflict": has_conflict(*active_rows, *pending_rows),
        # 与"被拒时的冲突项"用同一个形状（conflict_entry），前端只需要认一种结构
        "active": [conflict_entry("metric", row) for row in active_rows],
        "pending": [conflict_entry("candidate", row) for row in pending_rows],
    }


@router.post("/knowledge/conflicts/resolve")
def resolve_conflicts(
    req: ResolveRequest,
    cs: Annotated[Any, Depends(conflicts_store)] = None,
) -> Any:
    """**仲裁入口**：人决定留哪一条，这里只执行，**不做自动仲裁**（Issue #14 的边界）。

    - 留候选（`keep_candidate_id`）：其余未决候选全部驳回（写明理由），保留的那条仍需正常审核 + 入库。
    - 留某一版（`keep_version`）：让该版本重新生效（等于 #13 的回滚），未决候选全部驳回。

    执行完之后：该主体**只有一条生效版本**（库层不变量保证），**历史一行不删** ——
    被驳回的候选仍留着（`rejected` + 仲裁理由），被换下的版本仍留着（`rolled_back` + 谁换的、为什么）。
    """
    if not req.subject.strip():
        return _json(status_code=400, content={"success": False, "error": "missing_subject",
                                               "message": "缺口径主体"})
    if (req.keep_candidate_id is None) == (req.keep_version is None):
        return _json(status_code=400, content={
            "success": False, "error": "missing_decision",
            "message": "必须且只能指定一个落选项：keep_candidate_id（留候选）或 keep_version（留某一版）",
        })
    if not req.operated_by.strip():
        return _json(status_code=400, content={"success": False, "error": "missing_operator",
                                               "message": "缺仲裁人：是人在做这个决定"})
    if not req.reason.strip():
        return _json(status_code=400, content={"success": False, "error": "missing_reason",
                                               "message": "仲裁必须写明理由（为什么留这条、为什么驳回那条）"})
    if not cs.available():
        return _json(status_code=503, content={"success": False, "error": "数据库不可用（未落库）"})

    outcome = cs.resolve(
        subject=req.subject.strip(),
        operated_by=req.operated_by,
        reason=req.reason,
        keep_candidate_id=req.keep_candidate_id,
        keep_version=req.keep_version,
    )
    if not outcome.ok:
        code = 400
        if outcome.error in (conflict_store.E_CANDIDATE_NOT_FOUND, conflict_store.E_VERSION_NOT_FOUND):
            code = 404
        return _json(status_code=code, content={"success": False, "error": outcome.error,
                                                "message": outcome.detail})
    return {
        "success": True,
        "kept": outcome.kept,
        "rejected_candidate_ids": outcome.rejected_candidate_ids,
        "active_version": outcome.active_version,
    }


# ---------------------------------------------------------------- 口径版本与回滚（M3-02 / #13）


def versions_store() -> Any:
    """依赖注入点：版本与回滚（测试可覆盖，不连数据库）。"""
    return version_store


@router.get("/knowledge/metrics/active")
def active_metric(
    subject: Annotated[str, Query(min_length=1, max_length=200)],
    st: Annotated[Any, Depends(versions_store)] = None,
) -> dict:
    """当前生效版本 —— 平台侧的 `/kb/metric`：问一句"这个口径现在按哪一版算"。

    没有生效版本时**明说没有**，不返回一个空壳让人以为"口径是空的"。
    """
    if not st.available():
        return {"success": False, "error": "数据库不可用（未落库）", "metric": None}
    row = st.active_metric(subject)
    if row is None:
        return {"success": True, "metric": None, "message": f"口径 {subject} 没有生效版本"}
    return {"success": True, "metric": _with_source(row)}


@router.get("/knowledge/metrics/versions")
def list_versions(
    subject: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    st: Annotated[Any, Depends(versions_store)] = None,
) -> dict:
    """某口径的全部版本（含被取代、被回滚的）—— 历史不删。"""
    if not st.available():
        return {"success": False, "error": "数据库不可用（未落库）", "items": []}
    return {"success": True, "items": [_with_source(row) for row in st.list_versions(subject, limit=limit)]}


@router.get("/knowledge/metrics/history")
def metric_history(
    subject: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    st: Annotated[Any, Depends(versions_store)] = None,
) -> dict:
    """版本事件流水：谁在什么时候把哪一版换成了哪一版、为什么。"""
    if not st.available():
        return {"success": False, "error": "数据库不可用（未落库）", "items": []}
    return {"success": True, "items": st.history(subject, limit=limit)}


class RollbackRequest(BaseModel):
    """回滚。`reason` 必填 —— 说不清原因的回滚与说不清原因的拒绝同样不可接受。"""

    subject: str = ""
    to_version: int = 0
    operated_by: str = ""
    reason: str = ""


@router.post("/knowledge/metrics/rollback")
def rollback_metric(
    req: RollbackRequest,
    st: Annotated[Any, Depends(versions_store)] = None,
) -> Any:
    """把某口径回滚到指定版本：**降级当前版本 + 目标版本重新生效 + 写两条历史**，同一个事务。

    这一步只改"当前按哪一版算"，历史一行不删。回滚后 `GET /api/knowledge/metrics/active`
    返回的就是目标版本（验收说的"回滚后 `/kb/metric` 返回上一版本"）。
    """
    if not req.subject.strip():
        return _json(status_code=400, content={"success": False, "error": "missing_subject",
                                               "message": "缺口径主体（回滚要说清是哪条口径）"})
    if req.to_version < 1:
        return _json(status_code=400, content={"success": False, "error": "invalid_version",
                                               "message": f"目标版本号必须 ≥ 1，实际 {req.to_version}"})
    if not req.operated_by.strip():
        return _json(status_code=400, content={"success": False, "error": "missing_operator",
                                               "message": "缺操作人：回滚要记名"})
    if not req.reason.strip():
        return _json(status_code=400, content={"success": False, "error": "missing_reason",
                                               "message": "回滚必须写明原因（为什么退回这一版）"})
    if not st.available():
        return _json(status_code=503, content={"success": False, "error": "数据库不可用（未落库）"})

    outcome = st.rollback(subject=req.subject.strip(), to_version=req.to_version,
                          operated_by=req.operated_by, reason=req.reason)
    if not outcome.ok:
        code = 404 if outcome.error in (version_store.E_SUBJECT_NOT_FOUND, version_store.E_VERSION_NOT_FOUND) else 409
        return _json(status_code=code, content={"success": False, "error": outcome.error,
                                                "message": outcome.detail})
    return {
        "success": True,
        "from_version": outcome.from_version,
        "to_version": outcome.to_version,
        "metric": _with_source(st.active_metric(req.subject.strip()) or {}),
    }

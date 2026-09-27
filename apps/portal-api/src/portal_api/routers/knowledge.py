"""知识候选池接口（工作项 M3-01 / Issue #12）：候选 → 审核 → 入库。

一条口径想进知识库，只能走这三步：

```
POST /api/knowledge/candidates             提交候选（必须带 来源脚本 + 公式 + 依赖字段）
POST /api/knowledge/candidates/{id}/review 责任人审核（approve / reject，reject 必须说明原因）
POST /api/knowledge/candidates/{id}/ingest 入库（只有已批准、且**再校验一遍**通过的才进得去）
GET  /api/knowledge/metrics                看得见"到底进没进去"
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
    draft_from_row,
    problems_as_dicts,
    validate_draft,
)
from dip_pg import knowledge as knowledge_store
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
def submit_candidate(draft: CandidateDraft, st: Annotated[Any, Depends(store)] = None) -> Any:
    """提交一条候选。校验不过 → `400`，并把**每一条**原因列出来（缺来源 / 格式非法…）。"""
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

    persisted = st.record_candidate(
        kind=draft.kind,
        subject=draft.subject,
        chinese_name=draft.chinese_name,
        formula=draft.formula,
        depends_on=[d.model_dump() for d in draft.depends_on],
        source_script=draft.source_script,
        source_line=draft.source_line,
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


@router.get("/knowledge/metrics")
def list_metrics(
    subject: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    st: Annotated[Any, Depends(store)] = None,
) -> dict:
    if not st.available():
        return {"success": False, "error": "数据库不可用（未落库）", "items": []}
    return {"success": True, "items": st.list_metrics(subject=subject, limit=limit)}

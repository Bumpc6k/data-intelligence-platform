"""口径分级：低等级免仲裁（ADR-0006 / Issue #38）。

分三段，和 #12/#14 的用例组织方式一致：

- **契约层**（不需要库）：等级取值、`p2` 必须有依据与定级人、`blocking_conflicts` 的分级语义；
- **接口层**（假 store）：同一句"与生效口径公式不同"，`p1` 被 409 拦、`p2` 放行且冲突留痕；
- **库层**（真库，标 `pg`）：等级落库、绕过接口直接写 `p2` 也被 `CHECK` 拦、入库把等级带进口径行。

这组用例最想钉住的两句话：
1. **免仲裁只是"不拦冲突"，任何质量门禁都没放松**（来源脚本/依赖字段/审核人/库层 CHECK 全部照旧）；
2. **等级是人的判断**：没有依据与定级人就别想拿 `p2`，也没有任何接口能"顺手"改等级。
"""

from __future__ import annotations

import uuid
from typing import Any

import dip_pg
import pytest
from dip_contracts.knowledge import (
    DEFAULT_TIER,
    FREE_ARBITRATION_TIER,
    KNOWN_TIERS,
    CandidateDraft,
    FieldRef,
    blocking_conflicts,
    validate_draft,
)
from fastapi.testclient import TestClient

FORMULA_A = "产量 = 打码量 + 跳码量 - 重码量"
FORMULA_B = "产量 = 打码量 + 跳码量"          # 与 FORMULA_A 不同 → 构成冲突
SUBJECT = "ads.ads_产销存月报.output_qty"


def _draft(**over: Any) -> CandidateDraft:
    base: dict[str, Any] = {
        "kind": "metric",
        "subject": SUBJECT,
        "chinese_name": "产量",
        "formula": FORMULA_A,
        "depends_on": [FieldRef(table="cdw.dwd_卷烟产量码段明细", column="dama_qty")],
        "source_script": "examples/warehouse/ads/ads_产销存月报.sql",
        "source_line": 1,
        "submitted_by": "么慌",
    }
    base.update(over)
    return CandidateDraft(**base)


def _codes(draft: CandidateDraft) -> set[str]:
    return {p.code for p in validate_draft(draft)}


# ------------------------------------------------------------------ 契约层


def test_等级词汇表与_ADR_一致():
    """ADR-0006 定的是 p0/p1/p2，默认 p1（默认从严），免仲裁档是 p2。"""
    assert KNOWN_TIERS == ("p0", "p1", "p2")
    assert DEFAULT_TIER == "p1"
    assert FREE_ARBITRATION_TIER == "p2"


def test_不写等级就是_p1():
    assert _draft().tier == "p1"
    assert validate_draft(_draft()) == []


def test_认不出的等级被拒():
    assert "invalid_tier" in _codes(_draft(tier="p9"))
    assert "invalid_tier" in _codes(_draft(tier=""))


@pytest.mark.parametrize(
    ("over", "expect"),
    [
        ({"tier": "p2"}, {"missing_tier_reason", "missing_tier_setter"}),
        ({"tier": "p2", "tier_reason": "只影响临时分析，无下游引用"}, {"missing_tier_setter"}),
        ({"tier": "p2", "tier_set_by": "么慌"}, {"missing_tier_reason"}),
        ({"tier": "p2", "tier_reason": "只影响临时分析", "tier_set_by": "么慌"}, set()),
    ],
)
def test_标_p2_必须写依据与定级人(over, expect):
    """免仲裁不是白给的：**谁说的 + 凭什么**，缺一样都不给过。"""
    assert _codes(_draft(**over)) == expect


def test_p2_与生效口径冲突也不拦():
    conflicts = {"active": [{"kind": "metric", "formula": FORMULA_B}], "pending": []}
    assert blocking_conflicts("new", conflicts, "p2") == (None, [])


def test_p2_与未决候选冲突也不拦():
    conflicts = {"active": [], "pending": [{"kind": "candidate", "id": 9, "formula": FORMULA_B}]}
    assert blocking_conflicts("new", conflicts, "p2") == (None, [])


def test_p0_p1_维持从严_既有行为一个字没改():
    active = {"active": [{"kind": "metric", "formula": FORMULA_B}], "pending": []}
    pending = {"active": [], "pending": [{"kind": "candidate", "id": 9, "formula": FORMULA_B}]}
    for tier in ("p0", "p1", None):          # None = 老调用方没传等级
        error, items = blocking_conflicts("new", active, tier)
        assert error == "conflict_with_active_metric" and items
    for tier in ("p0", "p1"):
        error, _ = blocking_conflicts("new", pending, tier)
        assert error == "conflict_with_pending_candidate"
    # 声明 replace 时，p1 与生效口径冲突是老行为：放行
    assert blocking_conflicts("replace", active, "p1") == (None, [])


def test_分级只管冲突不管质量门禁():
    """把 p2 拿到"没来源脚本"上去试：该拦还是拦 —— 免仲裁不是免质量门禁。"""
    codes = _codes(_draft(tier="p2", tier_reason="临时用", tier_set_by="么慌", source_script=""))
    assert "missing_source_script" in codes
    codes = _codes(_draft(tier="p2", tier_reason="临时用", tier_set_by="么慌", depends_on=[]))
    assert "missing_depends_on" in codes


def test_draft_from_row_把等级带回来_入库前复校验才不会漏():
    from dip_contracts.knowledge import draft_from_row

    row = {
        "kind": "metric", "subject": SUBJECT, "formula": FORMULA_A,
        "depends_on": [{"table": "t", "column": "c"}], "source_script": "a.sql",
        "tier": "p2", "tier_reason": "临时用", "tier_set_by": "么慌", "submitted_by": "么慌",
    }
    draft = draft_from_row(row)
    assert (draft.tier, draft.tier_reason, draft.tier_set_by) == ("p2", "临时用", "么慌")
    assert validate_draft(draft) == []


# ------------------------------------------------------------------ 接口层（假 store）


class _FakeKnowledge:
    E_STATUS_CONFLICT = dip_pg.knowledge.E_STATUS_CONFLICT

    def __init__(self) -> None:
        self.recorded: dict[str, Any] = {}

    def available(self) -> bool:
        return True

    def record_candidate(self, **kwargs: Any) -> dip_pg.Persisted:
        self.recorded = kwargs
        return dip_pg.Persisted(ok=True, id=2)

    def get_candidate(self, candidate_id: int) -> dict | None:
        return {"id": 2, "status": "pending", "subject": SUBJECT, "formula": FORMULA_B}


class _FakeVersions:
    """已经有一条生效口径，公式与本次提交**不同** —— 冲突现场。"""

    def __init__(self) -> None:
        self._active = {
            "id": 7, "subject": SUBJECT, "formula": FORMULA_B, "version": 1, "status": "active",
            "approved_by": "么慌", "source_script": "a.sql", "source_line": 1, "created_at": None,
        }

    def available(self) -> bool:
        return True

    def active_metric(self, subject: str) -> dict | None:
        return self._active

    def list_metrics(self, **_: Any) -> list[dict]:
        return [self._active]


class _NoPending:
    def available(self) -> bool:
        return True

    def pending_candidates(self, subject: str, **_: Any) -> list[dict]:
        return []


@pytest.fixture()
def client_and_fakes():
    from portal_api.main import app
    from portal_api.routers import knowledge as knowledge_router

    fake = _FakeKnowledge()
    app.dependency_overrides[knowledge_router.store] = lambda: fake
    app.dependency_overrides[knowledge_router.versions_store] = _FakeVersions
    app.dependency_overrides[knowledge_router.conflicts_store] = _NoPending
    try:
        yield TestClient(app), fake
    finally:
        for dep in (knowledge_router.store, knowledge_router.versions_store,
                    knowledge_router.conflicts_store):
            app.dependency_overrides.pop(dep, None)


def _body(**over: Any) -> dict[str, Any]:
    base = {
        "subject": SUBJECT, "chinese_name": "产量", "formula": FORMULA_A,
        "depends_on": [{"table": "cdw.dwd_卷烟产量码段明细", "column": "dama_qty"}],
        "source_script": "examples/warehouse/ads/ads_产销存月报.sql", "source_line": 1,
        "submitted_by": "么慌",
    }
    base.update(over)
    return base


def test_接口_p1_与生效口径冲突仍然_409(client_and_fakes):
    client, _ = client_and_fakes
    r = client.post("/api/knowledge/candidates", json=_body())
    assert r.status_code == 409, r.text
    assert r.json()["error"] == "conflict_with_active_metric"


def test_接口_p2_免仲裁_放行并留痕(client_and_fakes):
    client, fake = client_and_fakes
    r = client.post("/api/knowledge/candidates", json=_body(
        tier="p2", tier_reason="只影响临时分析，无下游引用", tier_set_by="么慌",
    ))
    assert r.status_code == 201, r.text
    assert fake.recorded["tier"] == "p2"
    assert fake.recorded["tier_reason"] and fake.recorded["tier_set_by"]
    # 不拦 ≠ 不记：冲突清单照样落在候选行上（谁看都能发现"它和谁公式不同"）
    assert fake.recorded["conflicts"], "p2 放行也要把冲突清单留痕"


def test_接口_p2_缺依据直接_400(client_and_fakes):
    client, _ = client_and_fakes
    r = client.post("/api/knowledge/candidates", json=_body(tier="p2"))
    assert r.status_code == 400
    codes = {p["code"] for p in r.json()["problems"]}
    assert {"missing_tier_reason", "missing_tier_setter"} <= codes


def test_接口认不出的等级_400(client_and_fakes):
    client, _ = client_and_fakes
    r = client.post("/api/knowledge/candidates", json=_body(tier="p3"))
    assert r.status_code == 400
    assert "invalid_tier" in {p["code"] for p in r.json()["problems"]}


def test_审核接口没有改等级的入口_等级只能提交时由人定():
    from portal_api.routers.knowledge import ReviewRequest

    assert "tier" not in ReviewRequest.model_fields
    assert "tier" not in ReviewRequest.model_json_schema()["properties"]


# ------------------------------------------------------------------ 库层（真库）


def _pg_up() -> bool:
    try:
        return dip_pg.available()
    except Exception:  # noqa: BLE001
        return False


requires_pg = pytest.mark.skipif(not _pg_up(), reason="PostgreSQL 不在（127.0.0.1:15432），跳过")


def pg_only(func):
    return requires_pg(pytest.mark.pg(func))


@pg_only
def test_真库_p2_等级随入库落到口径行():
    tag = uuid.uuid4().hex[:8]
    subject = f"ads.ads_产销存月报.tier_{tag}"
    dip_pg.init_knowledge_schema()
    rec = dip_pg.knowledge.record_candidate(
        subject=subject, submitted_by="么慌", chinese_name="临时口径", formula=f"x = {tag}",
        depends_on=[{"table": "t", "column": "c"}],
        source_script="examples/warehouse/ads/ads_产销存月报.sql", source_line=1,
        tier="p2", tier_reason="只影响临时分析，无下游引用", tier_set_by="么慌",
    )
    assert rec.ok, rec.error
    assert dip_pg.knowledge.mark_reviewed(
        candidate_id=rec.id, decision="approve", reviewer="么慌",
        reason="抽检用例", worth_keeping=True,
    ).ok
    out = dip_pg.knowledge.ingest(candidate_id=rec.id, ingested_by="么慌")
    assert out.ok, out.error

    row = dip_pg.metric_versions.list_versions(subject)[0]
    assert row["tier"] == "p2"
    cand = dip_pg.knowledge.get_candidate(rec.id)
    assert (cand["tier"], cand["tier_reason"], cand["tier_set_by"]) == (
        "p2", "只影响临时分析，无下游引用", "么慌",
    )


@pg_only
def test_真库_绕过接口写_p2_没依据会被库层_CHECK_拦():
    tag = uuid.uuid4().hex[:8]
    # 库层的写入口不抛异常，它**如实回报失败**（error 里带约束名）—— 这正是我们要的"不假装成功"
    out = dip_pg.knowledge.record_candidate(
        subject=f"ads.ads_产销存月报.tier_direct_{tag}", submitted_by="么慌",
        chinese_name="绕过接口的 p2", formula="x = 2",
        depends_on=[{"table": "t", "column": "c"}],
        source_script="examples/warehouse/ads/ads_产销存月报.sql",
        tier="p2", tier_reason="", tier_set_by="",
    )
    assert out.ok is False
    assert "knowledge_candidates_tier_trace_chk" in (out.error or "")


@pg_only
def test_真库_等级值域由库层守_认不出的等级进不去():
    tag = uuid.uuid4().hex[:8]
    out = dip_pg.knowledge.record_candidate(
        subject=f"ads.ads_产销存月报.tier_bad_{tag}", submitted_by="么慌",
        chinese_name="乱填等级", formula="x = 3",
        depends_on=[{"table": "t", "column": "c"}],
        source_script="examples/warehouse/ads/ads_产销存月报.sql", tier="p7",
    )
    assert out.ok is False
    assert "knowledge_candidates_tier_chk" in (out.error or "")

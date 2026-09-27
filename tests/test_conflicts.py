"""口径冲突检测与仲裁（M3-03 / Issue #14）：**同一口径不允许两条不同公式并存**。

三段：

- **不需要真库**：公式归一化与"该不该拦"的判定（纯函数）、接口的说话方式（缺决定/缺人/缺理由）。
- **需要真库**（标 `pg`）：提交冲突候选被拒并列出冲突项、声明替换可入库并留痕、
  两条未决候选并存被拦、审批现场再查一次、仲裁后只剩一条生效版本且历史保留。
- 边界：**不做自动仲裁** —— 这里没有"哪条更对"的判断，只有"谁和谁冲突"的事实与人的决定。
"""

from __future__ import annotations

import uuid
from typing import Any

import dip_pg
import pytest
from dip_contracts.knowledge import (
    CandidateDraft,
    FieldRef,
    blocking_conflicts,
    detect_conflicts,
    has_conflict,
    normalize_formula,
    validate_draft,
)
from fastapi.testclient import TestClient
from portal_api.routers import knowledge as knowledge_router

SUBJECT_PREFIX = "ads.ads_产销存月报."
FORMULA_A = "产量 = 打码量 + 跳码量 - 重码量"
FORMULA_B = "产量 = SUM(chanliang_qty)"


def _pg_up() -> bool:
    try:
        return dip_pg.available()
    except Exception:  # noqa: BLE001
        return False


requires_pg = pytest.mark.skipif(not _pg_up(), reason="PostgreSQL 不在（127.0.0.1:15432），跳过")


def pg_only(func):
    return requires_pg(pytest.mark.pg(func))


# ================================================================ 判定（不需要真库）


def test_公式只差空格不算冲突():
    """`产量 = A + B` 与 `产量=A+B` 是同一条公式 —— 换个空格写法不该走一遍仲裁。"""
    assert normalize_formula("产量 = A + B") == normalize_formula("产量=A+B")
    rows = [{"id": 1, "formula": FORMULA_A, "status": "active"}]
    conflicts = detect_conflicts(FORMULA_A.replace(" = ", "="), active_rows=rows, pending_rows=[])
    assert conflicts == {"active": [], "pending": []}, "归一化后相同 → 不冲突"


def test_与生效口径冲突时_只有声明替换才放行():
    """与生效口径公式不同：得有人明说"这是替换"（`intent=replace`），否则拦。"""
    active_rows = [{"id": 7, "subject": "ads.t.c", "formula": FORMULA_A, "version": 1,
                    "status": "active", "approved_by": "么慌", "source_script": "a.sql", "source_line": 1}]
    conflicts = detect_conflicts(FORMULA_B, active_rows=active_rows, pending_rows=[])
    assert len(conflicts["active"]) == 1
    entry = conflicts["active"][0]
    assert entry["kind"] == "metric" and entry["version"] == 1
    assert entry["formula"] == FORMULA_A and entry["who"] == "么慌"

    code, items = blocking_conflicts("new", conflicts)
    assert code == "conflict_with_active_metric" and items == conflicts["active"]

    code, items = blocking_conflicts("replace", conflicts)
    assert code is None and items == [], "声明替换就放行（但冲突清单仍要留痕）"


def test_两条未决候选并存一律拦_声明替换也不行():
    """`replace` 只解决"与生效口径"的冲突；两条竞争候选同时待审，就是没择一。"""
    pending_rows = [{"id": 9, "subject": "ads.t.c", "formula": FORMULA_A, "status": "pending",
                     "submitted_by": "小王", "intent": "new", "source_script": "a.sql"}]
    conflicts = detect_conflicts(FORMULA_B, active_rows=[], pending_rows=pending_rows)
    assert len(conflicts["pending"]) == 1

    for intent in ("new", "replace"):
        code, items = blocking_conflicts(intent, conflicts)
        assert code == "conflict_with_pending_candidate" and items[0]["id"] == 9


def test_同样的公式重复提交不算冲突():
    active_rows = [{"id": 1, "formula": FORMULA_A, "version": 1, "status": "active"}]
    conflicts = detect_conflicts(FORMULA_A, active_rows=active_rows, pending_rows=[])
    assert conflicts == {"active": [], "pending": []}
    assert has_conflict(*active_rows) is False


def test_冲突全景的判定_存在两种公式才算冲突():
    assert has_conflict({"formula": FORMULA_A}, {"formula": FORMULA_B}) is True
    assert has_conflict({"formula": FORMULA_A}, {"formula": FORMULA_A.replace(" ", "")}) is False


def test_认不出的提交意图被拒():
    draft = CandidateDraft(subject="a.b", formula=FORMULA_A, source_script="a.sql",
                           depends_on=[FieldRef(table="t", column="c")], submitted_by="么慌",
                           intent="whatever")
    codes = {p.code for p in validate_draft(draft)}
    assert "invalid_intent" in codes


# ================================================================ 接口（假 store，不需要真库）


class _FakeKnowledge:
    E_STATUS_CONFLICT = dip_pg.knowledge.E_STATUS_CONFLICT

    def __init__(self, *, candidate: dict | None = None) -> None:
        self._candidate = candidate or {"id": 1, "status": "pending", "subject": "ads.t.c", "formula": FORMULA_B,
                                        "intent": "new"}

    def available(self) -> bool:
        return True

    def record_candidate(self, **kwargs: Any) -> dip_pg.Persisted:
        self.recorded = kwargs
        return dip_pg.Persisted(ok=True, id=2)

    def get_candidate(self, candidate_id: int) -> dict | None:
        return self._candidate

    def mark_reviewed(self, **_: Any) -> Any:
        return dip_pg.knowledge.ReviewOutcome(ok=True, status="approved")


class _FakeVersions:
    def __init__(self, *, active: dict | None = None) -> None:
        self._active = active

    def available(self) -> bool:
        return True

    def active_metric(self, subject: str) -> dict | None:
        return self._active

    def list_metrics(self, **_: Any) -> list[dict]:
        return []


class _FakeConflicts:
    E_CANDIDATE_NOT_FOUND = dip_pg.conflicts.E_CANDIDATE_NOT_FOUND
    E_VERSION_NOT_FOUND = dip_pg.conflicts.E_VERSION_NOT_FOUND

    def __init__(self, *, pending: list[dict] | None = None, resolve_ok: bool = True) -> None:
        self._pending = pending or []
        self._resolve_ok = resolve_ok

    def available(self) -> bool:
        return True

    def pending_candidates(self, subject: str, **_: Any) -> list[dict]:
        return self._pending

    def resolve(self, **_: Any) -> Any:
        if not self._resolve_ok:
            return dip_pg.conflicts.ResolveOutcome(ok=False, error="candidate_not_found", detail="没有这条候选")
        return dip_pg.conflicts.ResolveOutcome(ok=True, kept={"kind": "candidate", "id": 2},
                                               rejected_candidate_ids=[3], active_version=1)


_ACTIVE_A = {"id": 7, "subject": "ads.t.c", "formula": FORMULA_A, "version": 1, "status": "active",
             "approved_by": "么慌", "source_script": "a.sql", "source_line": 1, "created_at": None}


@pytest.fixture()
def fake_api():
    """三个依赖注入点都换掉（用完清理，避免泄漏到真库用例）。"""
    from portal_api.main import app

    def _make(*, active: dict | None = _ACTIVE_A, pending: list[dict] | None = None,
              candidate: dict | None = None, resolve_ok: bool = True) -> TestClient:
        app.dependency_overrides[knowledge_router.store] = lambda: _FakeKnowledge(candidate=candidate)
        app.dependency_overrides[knowledge_router.versions_store] = lambda: _FakeVersions(active=active)
        app.dependency_overrides[knowledge_router.conflicts_store] = lambda: _FakeConflicts(
            pending=pending, resolve_ok=resolve_ok)
        return TestClient(app)

    yield _make
    for dep in (knowledge_router.store, knowledge_router.versions_store, knowledge_router.conflicts_store):
        app.dependency_overrides.pop(dep, None)


def _draft(**over: Any) -> CandidateDraft:
    base: dict[str, Any] = {
        "kind": "metric", "subject": "ads.t.c", "chinese_name": "产量", "formula": FORMULA_B,
        "depends_on": [FieldRef(table="cdw.dwd_卷烟产量码段明细", column="dama_qty")],
        "source_script": "examples/warehouse/ads/ads_产销存月报.sql", "source_line": 1,
        "note": "验收用例", "submitted_by": "么慌",
    }
    base.update(over)
    return CandidateDraft(**base)


def test_提交冲突候选被拒并列出冲突项(fake_api):
    """**本 Issue 的核心验收**：冲突候选被拒，响应里把冲突项逐条列出来。"""
    r = fake_api().post("/api/knowledge/candidates", json=_draft().model_dump())
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "conflict_with_active_metric"
    assert body["subject"] == "ads.t.c"
    conflicts = body["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["kind"] == "metric"
    assert conflicts[0]["formula"] == FORMULA_A, "要说清楚冲突的那条公式是什么"
    assert conflicts[0]["version"] == 1 and conflicts[0]["who"] == "么慌"
    assert "intent=replace" in body["message"], "要给出路：要么仲裁，要么声明替换"


def test_声明替换则放行并留痕(fake_api):
    client = fake_api()
    r = client.post("/api/knowledge/candidates", json=_draft(intent="replace").model_dump())
    assert r.status_code == 201


def test_与未决候选冲突被拦并列出候选(fake_api):
    pending = [{"id": 9, "subject": "ads.t.c", "formula": FORMULA_A, "status": "pending",
                "submitted_by": "小王", "submitted_at": None, "intent": "new",
                "source_script": "a.sql", "source_line": 1}]
    r = fake_api(pending=pending).post("/api/knowledge/candidates", json=_draft(intent="replace").model_dump())
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "conflict_with_pending_candidate"
    assert [(c["kind"], c["id"]) for c in body["conflicts"]] == [("candidate", 9)]
    assert body["conflicts"][0]["who"] == "小王"


def test_审批时也要过冲突这道门(fake_api):
    """审批是"择一"的现场：审批时冲突 → 拒绝 approve，并列出冲突项。"""
    r = fake_api().post("/api/knowledge/candidates/1/review",
                        json={"decision": "approve", "reviewer": "么慌", "worth_keeping": True})
    assert r.status_code == 409
    assert r.json()["error"] == "conflict_with_active_metric"


def test_冲突全景接口(fake_api):
    pending = [{"id": 9, "subject": "ads.t.c", "formula": FORMULA_B, "status": "pending",
                "submitted_by": "小王", "submitted_at": None, "intent": "new",
                "source_script": "a.sql", "source_line": 1}]
    body = fake_api(pending=pending).get("/api/knowledge/conflicts", params={"subject": "ads.t.c"}).json()
    assert body["success"] is True and body["conflict"] is True
    assert [r["kind"] for r in body["active"]] == ["metric"]
    assert body["active"][0]["formula"] == FORMULA_A
    assert [r["kind"] for r in body["pending"]] == ["candidate"]
    assert body["pending"][0]["formula"] == FORMULA_B

    # 只有"同一条公式的记录"时不算冲突（同样的公式重复提交而已）
    same = [{"id": 9, "subject": "ads.t.c", "formula": FORMULA_A, "status": "pending",
             "submitted_by": "小王", "submitted_at": None, "intent": "new",
             "source_script": "a.sql", "source_line": 1}]
    body = fake_api(pending=same).get("/api/knowledge/conflicts", params={"subject": "ads.t.c"}).json()
    assert body["conflict"] is False


def test_仲裁的四条硬规则(fake_api):
    url = "/api/knowledge/conflicts/resolve"
    base = {"subject": "ads.t.c", "keep_candidate_id": 2, "operated_by": "么慌", "reason": "按业务口径留这条"}

    r = fake_api().post(url, json={**base, "subject": " "})
    assert r.status_code == 400 and r.json()["error"] == "missing_subject"

    r = fake_api().post(url, json={**base, "keep_candidate_id": None})
    assert r.status_code == 400 and r.json()["error"] == "missing_decision"

    r = fake_api().post(url, json={**base, "keep_version": 1})
    assert r.status_code == 400 and r.json()["error"] == "missing_decision", "两个都给我也是决定不清"

    r = fake_api().post(url, json={**base, "operated_by": ""})
    assert r.status_code == 400 and r.json()["error"] == "missing_operator"

    r = fake_api().post(url, json={**base, "reason": "  "})
    assert r.status_code == 400 and r.json()["error"] == "missing_reason"


def test_仲裁成功与失败的状态码(fake_api):
    body = fake_api().post("/api/knowledge/conflicts/resolve", json={
        "subject": "ads.t.c", "keep_candidate_id": 2, "operated_by": "么慌", "reason": "留这条",
    }).json()
    assert body["success"] is True
    assert body["kept"]["id"] == 2 and body["rejected_candidate_ids"] == [3]

    r = fake_api(resolve_ok=False).post("/api/knowledge/conflicts/resolve", json={
        "subject": "ads.t.c", "keep_candidate_id": 2, "operated_by": "么慌", "reason": "留这条",
    })
    assert r.status_code == 404


# ================================================================ 真库（验收本体）


@pytest.fixture()
def pg():
    dip_pg.knowledge.init_knowledge_schema()
    return f"kb{uuid.uuid4().hex[:8]}"


def _submit(client: TestClient, subject: str, formula: str, *, mark: str, intent: str = "new") -> Any:
    draft = CandidateDraft(
        kind="metric", subject=subject, chinese_name="产量", formula=formula,
        depends_on=[FieldRef(table="cdw.dwd_卷烟产量码段明细", column="dama_qty")],
        source_script="examples/warehouse/ads/ads_产销存月报.sql", source_line=1,
        intent=intent, note=f"验收-{mark}", submitted_by=f"提交人-{mark}",
    )
    return client.post("/api/knowledge/candidates", json=draft.model_dump())


def _approve(client: TestClient, candidate_id: int) -> Any:
    return client.post(f"/api/knowledge/candidates/{candidate_id}/review",
                       json={"decision": "approve", "reviewer": "么慌", "reason": "核过", "worth_keeping": True})


def _ingest(client: TestClient, candidate_id: int) -> Any:
    return client.post(f"/api/knowledge/candidates/{candidate_id}/ingest", json={"ingested_by": "么慌"})


def _ingest_formula(client: TestClient, subject: str, formula: str, *, mark: str, intent: str = "replace") -> int:
    candidate_id = _submit(client, subject, formula, mark=mark, intent=intent).json()["candidate"]["id"]
    _approve(client, candidate_id)
    return int(_ingest(client, candidate_id).json()["metric_id"])


@pg_only
def test_提交冲突候选被拒并列出冲突项_真库(pg):
    """**核心验收（真库版）**：口径库里已有 v1（公式 A）时，提交公式 B 会被拒并列出 v1 作为冲突项，
    而且这条候选**根本没进候选池**（拦在写库之前）。"""
    from portal_api.main import app

    client = TestClient(app)
    subject = f"{SUBJECT_PREFIX}{pg}"
    _ingest_formula(client, subject, FORMULA_A, mark=f"{pg}a", intent="new")

    r = _submit(client, subject, FORMULA_B, mark=f"{pg}b")
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error"] == "conflict_with_active_metric"
    conflict = body["conflicts"][0]
    assert conflict["kind"] == "metric" and conflict["version"] == 1
    assert conflict["formula"] == FORMULA_A
    assert conflict["source_script"].endswith(".sql")
    assert conflict["who"] == "么慌"

    # 候选池里只有那条已入库的候选（冲突的那次没有留下 pending 残渣）
    rows = client.get("/api/knowledge/candidates", params={"subject": subject}).json()["items"]
    assert [row["status"] for row in rows] == ["ingested"], rows

    # 口径库也没被改：仍然只有一版，且是公式 A
    active = client.get("/api/knowledge/metrics/active", params={"subject": subject}).json()["metric"]
    assert active["version"] == 1 and active["formula"] == FORMULA_A


@pg_only
def test_声明替换可以入库_冲突清单留在候选上(pg):
    """声明 `intent=replace` 是"人认账"：候选行上留下当时和谁冲突，入库后成为新版本。"""
    from portal_api.main import app

    client = TestClient(app)
    subject = f"{SUBJECT_PREFIX}{pg}"
    _ingest_formula(client, subject, FORMULA_A, mark=f"{pg}a", intent="new")

    candidate = _submit(client, subject, FORMULA_B, mark=f"{pg}b", intent="replace").json()["candidate"]
    assert candidate["intent"] == "replace"
    assert [c["version"] for c in candidate["conflicts"]] == [1], "替换的冲突清单要留痕"

    _approve(client, candidate["id"])
    assert _ingest(client, candidate["id"]).json()["success"] is True

    active = client.get("/api/knowledge/metrics/active", params={"subject": subject}).json()["metric"]
    assert active["version"] == 2 and active["formula"] == FORMULA_B
    versions = {v["version"]: v["status"] for v in
                client.get("/api/knowledge/metrics/versions", params={"subject": subject}).json()["items"]}
    assert versions == {2: "active", 1: "superseded"}


@pg_only
def test_两条未决候选并存被拦(pg):
    """两条竞争候选同时待审就是"没择一" —— 第二条（哪怕声明 replace）也要被拦，并列出第一条。"""
    from portal_api.main import app

    client = TestClient(app)
    subject = f"{SUBJECT_PREFIX}{pg}"
    first = _submit(client, subject, FORMULA_A, mark=f"{pg}a").json()["candidate"]

    r = _submit(client, subject, FORMULA_B, mark=f"{pg}b", intent="replace")
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "conflict_with_pending_candidate"
    assert body["conflicts"][0]["id"] == first["id"]
    assert body["conflicts"][0]["formula"] == FORMULA_A
    assert body["conflicts"][0]["who"] == f"提交人-{pg}a"

    # 承诺的出路：先仲裁 → 未决候选只剩一条
    resolved = client.post("/api/knowledge/conflicts/resolve", json={
        "subject": subject, "keep_candidate_id": first["id"], "operated_by": "么慌",
        "reason": "两条都对得上脚本，按业务口径以「打码量+跳码量-重码量」为准",
    }).json()
    assert resolved["success"] is True
    assert resolved["rejected_candidate_ids"] == []

    # "不许并存"是硬的：留下的候选还在待审，别的公式仍然提不进来（哪怕声明 replace）
    again = _submit(client, subject, FORMULA_B, mark=f"{pg}c", intent="replace")
    assert again.status_code == 409
    assert again.json()["error"] == "conflict_with_pending_candidate"
    assert again.json()["conflicts"][0]["id"] == first["id"]

    # 把留下的那条推进入库后，再提不同公式就是"替换生效口径"——声明 replace 即可
    _approve(client, first["id"])
    assert _ingest(client, first["id"]).json()["success"] is True
    with_replace = _submit(client, subject, FORMULA_B, mark=f"{pg}d", intent="replace")
    assert with_replace.status_code == 201
    assert with_replace.json()["candidate"]["conflicts"][0]["kind"] == "metric"


@pg_only
def test_审批现场再查一次冲突(pg):
    """提交时没有生效口径，审批时有了（别的路径先入了库）—— 审批这一关必须拦住。"""
    from portal_api.main import app

    client = TestClient(app)
    subject = f"{SUBJECT_PREFIX}{pg}"
    candidate = _submit(client, subject, FORMULA_B, mark=f"{pg}a").json()["candidate"]

    # 绕过接口，直接造一条生效口径（公式 A）—— 模拟"提交之后、审批之前，别处先把口径改掉了"
    with dip_pg.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """insert into knowledge_metrics
                 (candidate_id, subject, formula, depends_on, source_script, source_line, version, status, approved_by)
               values (%s, %s, %s, '[{"table": "t", "column": "c"}]'::jsonb, 'a.sql', 1, 1, 'active', '别人')""",
            (candidate["id"], subject, FORMULA_A),
        )

    r = _approve(client, candidate["id"])
    assert r.status_code == 409
    assert r.json()["error"] == "conflict_with_active_metric"
    assert r.json()["conflicts"][0]["formula"] == FORMULA_A
    rows = client.get("/api/knowledge/candidates", params={"subject": subject}).json()["items"]
    assert rows[0]["status"] == "pending", "被拦下的审批不该改动候选状态"


@pg_only
def test_仲裁后只剩一条生效版本_历史保留(pg):
    """**验收②**：仲裁之后该口径只有一条生效版本，而历史（版本行、候选行）一行不删。"""
    from portal_api.main import app

    client = TestClient(app)
    subject = f"{SUBJECT_PREFIX}{pg}"
    _ingest_formula(client, subject, FORMULA_A, mark=f"{pg}a", intent="new")
    candidate = _submit(client, subject, FORMULA_B, mark=f"{pg}b", intent="replace").json()["candidate"]
    _approve(client, candidate["id"])
    _ingest(client, candidate["id"])
    assert client.get("/api/knowledge/metrics/active",
                      params={"subject": subject}).json()["metric"]["version"] == 2

    # 仲裁：留第 1 版（公式 A），把候选（已入库成 v2）换下来
    body = client.post("/api/knowledge/conflicts/resolve", json={
        "subject": subject, "keep_version": 1, "operated_by": "么慌",
        "reason": "复核脚本后确认第 2 版的 SUM 写法与口径不符，回到第 1 版",
    }).json()
    assert body["success"] is True and body["active_version"] == 1

    active = client.get("/api/knowledge/metrics/active", params={"subject": subject}).json()["metric"]
    assert active["version"] == 1 and active["formula"] == FORMULA_A

    versions = client.get("/api/knowledge/metrics/versions", params={"subject": subject}).json()["items"]
    assert sorted((v["version"], v["status"]) for v in versions) == [(1, "active"), (2, "rolled_back")], versions
    assert len(versions) == 2, "历史版本一行都不能少"

    events = client.get("/api/knowledge/metrics/history", params={"subject": subject}).json()["items"]
    assert {"entered", "superseded", "rolled_back", "reactivated"} <= {e["event"] for e in events}

    # 候选池也一行没删：被驳回的候选还留着，带仲裁理由
    rows = client.get("/api/knowledge/candidates", params={"subject": subject}).json()["items"]
    assert len(rows) == 2, "候选历史也要留着"
    # 唯一"生效版本"由库层不变量保证（uq_kb_metrics_one_active），这里再核一次
    with dip_pg.connect() as conn, conn.cursor() as cur:
        cur.execute("select count(*) from knowledge_metrics where subject = %s and status = 'active'", (subject,))
        assert int(cur.fetchone()[0]) == 1


@pg_only
def test_仲裁留候选_其余驳回(pg):
    """留候选那条路：其余未决候选被驳回并写明"冲突仲裁"，保留的那条仍在等审核。"""
    from portal_api.main import app

    client = TestClient(app)
    subject = f"{SUBJECT_PREFIX}{pg}"
    keep = _submit(client, subject, FORMULA_A, mark=f"{pg}a").json()["candidate"]
    # 绕过接口再塞一条竞争候选（模拟并发/别的写入路径）
    with dip_pg.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """insert into knowledge_candidates
                 (kind, subject, formula, depends_on, source_script, source_line, intent, submitted_by)
               values ('metric', %s, %s, '[{"table": "t", "column": "c"}]'::jsonb, 'a.sql', 1, 'new', '另一个人')
               returning id""",
            (subject, FORMULA_B),
        )
        other_id = int(cur.fetchone()[0])

    body = client.post("/api/knowledge/conflicts/resolve", json={
        "subject": subject, "keep_candidate_id": keep["id"], "operated_by": "么慌",
        "reason": "两条都能追到脚本，按业务口径以「打码量+跳码量-重码量」为准",
    }).json()
    assert body["success"] is True
    assert body["rejected_candidate_ids"] == [other_id]
    assert body["kept"]["id"] == keep["id"]

    rows = {r["id"]: r for r in client.get("/api/knowledge/candidates", params={"subject": subject}).json()["items"]}
    assert rows[keep["id"]]["status"] == "pending", "留下的那条还在等审核"
    assert rows[other_id]["status"] == "rejected"
    assert "冲突仲裁" in (rows[other_id]["rejected_reason"] or "")
    assert rows[other_id]["reviewer"] == "么慌"

    # 仲裁之后冲突解除：可以继续把保留的那条推进（这里只验冲突不再拦）
    assert client.get("/api/knowledge/conflicts", params={"subject": subject}).json()["conflict"] is False

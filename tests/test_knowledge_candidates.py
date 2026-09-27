"""知识候选池与审核流（M3-01 / Issue #12）。

分两段：

- **不需要真库**：契约校验（逐条 `Problem`）与接口的"说话方式"（缺原因、缺审核人、状态冲突…）。
  这些是本 Issue 的验收本体，**不该因为没起数据库就被跳过**，所以用假 store 覆盖依赖注入点跑。
- **需要真库**（标 `pg`）：真往返 + **绕过接口直接写库也要被拦**（库层 `CHECK`）+ 入库事务的原子性。

用法与既有约定一致：`bash ops/start-pg.sh` 起了库就真跑，没起就跳过（不假装通过）。
每条用例都带 `uuid4` 标记，避免同库重复跑累积数据造成假红（AGENTS §8 坑表第 2 条）。
"""

from __future__ import annotations

import uuid
from typing import Any

import dip_pg
import psycopg
import pytest
from dip_contracts.knowledge import CandidateDraft, FieldRef, validate_draft
from fastapi.testclient import TestClient
from portal_api.routers import knowledge as knowledge_router


def _pg_up() -> bool:
    try:
        return dip_pg.available()
    except Exception:  # noqa: BLE001
        return False


requires_pg = pytest.mark.skipif(not _pg_up(), reason="PostgreSQL 不在（127.0.0.1:15432），跳过")


def pg_only(func):
    """真库用例：既带 `pg` 标记（可按 `-m pg` 选中），也会在库不在时**跳过而不是假装通过**。"""
    return requires_pg(pytest.mark.pg(func))


def _draft(**over: Any) -> CandidateDraft:
    """一份合法的候选（口径：产量）。用例只覆盖自己要验的那一项。"""
    base: dict[str, Any] = {
        "kind": "metric",
        "subject": "ads.ads_产销存月报.output_qty",
        "chinese_name": "产量",
        "formula": "产量 = 打码量 + 跳码量 - 重码量",
        "depends_on": [
            FieldRef(table="cdw.dwd_卷烟产量码段明细", column="dama_qty"),
            FieldRef(table="cdw.dwd_卷烟产量码段明细", column="tiaoma_qty"),
        ],
        "source_script": "examples/warehouse/ads/ads_产销存月报.sql",
        "source_line": 1,
        "note": "验收用例",
        "submitted_by": "么慌",
    }
    base.update(over)
    return CandidateDraft(**base)


def _codes(draft: CandidateDraft) -> set[str]:
    return {p.code for p in validate_draft(draft)}


# ================================================================ 契约校验（不需要真库）


def test_完整候选没有问题():
    assert validate_draft(_draft()) == []


def test_没有来源脚本的候选被拒_并说明原因():
    """验收①：**没有来源脚本的口径一律不许入库** —— 提交这一关就拦。"""
    problems = validate_draft(_draft(source_script=None))
    assert [p.code for p in problems] == ["missing_source_script"]
    assert problems[0].field == "source_script"
    assert "来源脚本" in problems[0].message

    # 空串与纯空格同样不许（"填了个空格"不是来源）
    assert "missing_source_script" in _codes(_draft(source_script=""))
    assert "missing_source_script" in _codes(_draft(source_script="   "))


def test_来源脚本格式非法被拒():
    """验收②：候选被拒时说明原因（缺来源 / **格式非法**）。"""
    # 不是脚本文件（对着"来源：我脑子里"这类填法）
    codes = _codes(_draft(source_script="我脑子里"))
    assert "invalid_source_script" in codes
    # 带说明文字（含空白）
    codes = _codes(_draft(source_script="examples/warehouse/ads/ads_产销存月报.sql（产量那段）"))
    assert "invalid_source_script" in codes
    # 行号从 1 开始
    assert "invalid_source_line" in _codes(_draft(source_line=0))


def test_中文口径主体合法_ASCII正则的坑():
    """本仓库表名/字段名含中文 —— 用 ASCII 正则会**静默**判成非法（AGENTS §8 第 1 条），
    所以这条中文用例是必须的，不是锦上添花。"""
    assert validate_draft(_draft(subject="ads.ads_产销存月报.output_qty")) == []
    assert validate_draft(_draft(subject="产量")) == []
    assert validate_draft(_draft(subject="cdw.dwd_卷烟产量码段明细.chanliang_qty")) == []
    # 反例：真的带非标识符字符才算非法（不是"含中文"）
    assert "invalid_subject" in _codes(_draft(subject="ads.ads_产销存月报 output_qty"))
    assert "invalid_subject" in _codes(_draft(subject="产量(箱)"))


def test_公式与依赖字段的格式非法被拒():
    assert "missing_formula" in _codes(_draft(formula=None))
    assert "invalid_formula" in _codes(_draft(formula="产量 = A + B;\nD = E"))  # 分号 = 塞了多条
    assert "invalid_formula" in _codes(_draft(formula="产量 = A\n产量 = B"))   # 换行
    assert "missing_depends_on" in _codes(_draft(depends_on=[]))
    # 依赖缺列名
    codes = _codes(_draft(depends_on=[FieldRef(table="cdw.dwd_卷烟产量码段明细", column="")]))
    assert "invalid_depends_on" in codes


def test_认不出的类型必须报错而不是被当成口径():
    """认不出就报错（AGENTS 铁律 1）；**不能静默当作 metric 处理**，
    否则以后 term/rule 通道上线时会发现历史数据里混着错类型的东西。"""
    assert "unsupported_kind" in _codes(_draft(kind="rule"))
    assert "unknown_kind" in _codes(_draft(kind="whatever"))


def test_可以一次报多条问题():
    problems = validate_draft(_draft(subject="", formula=None, depends_on=[], source_script=None,
                                     submitted_by=""))
    assert {p.code for p in problems} == {
        "missing_subject", "missing_formula", "missing_depends_on", "missing_source_script",
        "missing_submitter",
    }


# ================================================================ 接口层（假 store，不需要真库）


class _FakeStore:
    """假数据访问层：用来验"接口怎么说话"，不连数据库。"""

    E_STATUS_CONFLICT = dip_pg.knowledge.E_STATUS_CONFLICT
    E_NOT_APPROVED = dip_pg.knowledge.E_NOT_APPROVED
    E_ALREADY_INGESTED = dip_pg.knowledge.E_ALREADY_INGESTED

    def __init__(self, *, up: bool = True, candidate: dict | None = None,
                 review_ok: bool = True, ingest_ok: bool = True) -> None:
        self._up = up
        self._candidate = candidate if candidate is not None else {
            "id": 1, "status": "pending", "subject": "ads.ads_产销存月报.output_qty",
        }
        self._review_ok = review_ok
        self._ingest_ok = ingest_ok
        self.ingest_calls: list[dict] = []

    def available(self) -> bool:
        return self._up

    def record_candidate(self, **kwargs: Any) -> dip_pg.Persisted:
        self.recorded = kwargs
        return dip_pg.Persisted(ok=True, id=1)

    def get_candidate(self, candidate_id: int) -> dict | None:
        return self._candidate

    def list_candidates(self, **_: Any) -> list[dict]:
        return [self._candidate]

    def mark_reviewed(self, **_: Any) -> Any:
        if not self._review_ok:
            return dip_pg.knowledge.ReviewOutcome(
                ok=False, error=dip_pg.knowledge.E_STATUS_CONFLICT, detail="已被审过"
            )
        return dip_pg.knowledge.ReviewOutcome(ok=True, status="approved")

    def ingest(self, **kwargs: Any) -> dip_pg.Persisted:
        self.ingest_calls.append(kwargs)
        if not self._ingest_ok:
            return dip_pg.Persisted(ok=False, error="IntegrityError: check 约束拦下")
        return dip_pg.Persisted(ok=True, id=99)

    def list_metrics(self, **_: Any) -> list[dict]:
        return [{"id": 99, "subject": "ads.ads_产销存月报.output_qty"}]


@pytest.fixture()
def fake_client():
    """依赖注入点被换成假 store 的客户端。

    **必须清理 `dependency_overrides`**：不清理会泄漏到后面的真库用例上（#9 踩过这个坑）。
    """
    from portal_api.main import app

    def _make(**kwargs: Any) -> TestClient:
        app.dependency_overrides[knowledge_router.store] = lambda: _FakeStore(**kwargs)
        return TestClient(app)

    yield _make
    app.dependency_overrides.pop(knowledge_router.store, None)


def test_提交被拒时逐条说明原因(fake_client):
    """验收②：候选被拒时说明原因（缺来源 / 格式非法）—— 接口层也要给码，不只是抛一句话。"""
    payload = _draft(source_script=None, depends_on=[]).model_dump()
    body = fake_client().post("/api/knowledge/candidates", json=payload)
    assert body.status_code == 400
    detail = body.json()
    assert detail["success"] is False and detail["error"] == "invalid_candidate"
    assert {p["code"] for p in detail["problems"]} == {"missing_source_script", "missing_depends_on"}


def test_合法候选能提交(fake_client):
    r = fake_client().post("/api/knowledge/candidates", json=_draft().model_dump())
    assert r.status_code == 201
    assert r.json()["success"] is True


def test_数据库不可用时不假装成功(fake_client):
    """没有库就明说没落库，不返回一个看起来正常的 200（沿用 /api/audit 的约定）。"""
    r = fake_client(up=False).post("/api/knowledge/candidates", json=_draft().model_dump())
    assert r.status_code == 503
    assert "未落库" in r.json()["error"]

    body = fake_client(up=False).get("/api/knowledge/candidates").json()
    assert body["success"] is False and "未落库" in body["error"] and body["items"] == []


def test_审核三条硬规则(fake_client):
    """谁审的、拒绝的原因、"值不值得留下" —— 这三样都不给默认值。"""
    url = "/api/knowledge/candidates/1/review"
    r = fake_client().post(url, json={"decision": "approve", "reviewer": "  ", "worth_keeping": True})
    assert r.status_code == 400 and r.json()["error"] == "missing_reviewer"

    r = fake_client().post(url, json={"decision": "reject", "reviewer": "组长", "reason": " "})
    assert r.status_code == 400 and r.json()["error"] == "missing_reason"

    r = fake_client().post(url, json={"decision": "approve", "reviewer": "组长"})
    assert r.status_code == 400 and r.json()["error"] == "missing_worth_keeping"

    r = fake_client().post(url, json={"decision": "approve", "reviewer": "组长", "worth_keeping": False})
    assert r.status_code == 400 and r.json()["error"] == "contradictory_decision"


def test_审核通过(fake_client):
    r = fake_client().post("/api/knowledge/candidates/1/review",
                           json={"decision": "approve", "reviewer": "么慌", "reason": "口径对得上",
                                 "worth_keeping": True})
    assert r.status_code == 200 and r.json()["success"] is True


def test_重复审核报状态冲突_不覆盖前一次结论(fake_client):
    """重复审核不覆盖前一次结论（审计要的是"第一次是谁审的"）。"""
    r = fake_client(review_ok=False).post("/api/knowledge/candidates/1/review",
                                         json={"decision": "reject", "reviewer": "么慌", "reason": "缺来源"})
    assert r.status_code == 409
    assert r.json()["error"] == dip_pg.knowledge.E_STATUS_CONFLICT


def test_未批准的候选不许入库(fake_client):
    """审核通过 ≠ 入库，没人审过的候选更不能进库。"""
    client = fake_client(candidate={"id": 1, "status": "pending", "subject": "x.y"})
    r = client.post("/api/knowledge/candidates/1/ingest", json={"ingested_by": "么慌"})
    assert r.status_code == 409 and r.json()["error"] == "not_approved"


def test_入库要记名(fake_client):
    r = fake_client().post("/api/knowledge/candidates/1/ingest", json={"ingested_by": ""})
    assert r.status_code == 400 and r.json()["error"] == "missing_operator"


# ================================================================ 真库（验收本体）


@pytest.fixture()
def pg():
    """真库：建表 + 一个唯一标记，用例之间互不干扰。"""
    dip_pg.knowledge.init_knowledge_schema()
    return f"kb{uuid.uuid4().hex[:8]}"


@pg_only
def test_完整流程_提交到入库(pg):
    """验收：候选 → 审核 → 入库 走通，且**在哪一步都能查、口径库里真的多了一行**。"""
    from portal_api.main import app

    client = TestClient(app)
    subject = f"ads.ads_产销存月报.{pg}"
    draft = _draft(subject=subject, submitted_by=f"提交人-{pg}")

    submitted = client.post("/api/knowledge/candidates", json=draft.model_dump()).json()["candidate"]
    candidate_id = submitted["id"]
    assert submitted["status"] == "pending"
    assert submitted["worth_keeping"] is None, "价值判断此时还没发生，不许给默认值"

    reviewed = client.post(
        f"/api/knowledge/candidates/{candidate_id}/review",
        json={"decision": "approve", "reviewer": "么慌", "reason": "对得上脚本", "worth_keeping": True},
    ).json()["candidate"]
    assert reviewed["status"] == "approved"
    assert reviewed["reviewer"] == "么慌" and reviewed["worth_keeping"] is True

    ingested = client.post(
        f"/api/knowledge/candidates/{candidate_id}/ingest", json={"ingested_by": "么慌"}
    ).json()
    assert ingested["success"] is True
    assert ingested["candidate"]["status"] == "ingested"
    assert ingested["candidate"]["ingested_by"] == "么慌"
    assert ingested["candidate"]["ingested_at"]

    metrics = client.get("/api/knowledge/metrics", params={"subject": subject}).json()["items"]
    assert len(metrics) == 1, f"口径库里应当正好 1 行，实际 {len(metrics)}"
    metric = metrics[0]
    assert metric["subject"] == subject
    assert metric["formula"] == draft.formula
    assert metric["source_script"] == draft.source_script
    assert metric["version"] == 1, "第一版；版本与回滚是 #13"
    assert metric["approved_by"] == "么慌"
    assert metric["candidate_id"] == candidate_id, "入库条目要能追回它的候选"
    assert metric["depends_on"][0]["column"] == "dama_qty", "依赖字段要一起落库（jsonb 往返）"


@pg_only
def test_审核通过但被拒绝的候选不能入库(pg):
    """审核 → reject 之后，入库这一关照样关着，而且拒绝原因必须落库。"""
    from portal_api.main import app

    client = TestClient(app)
    subject = f"ads.ads_产销存月报.{pg}"
    candidate_id = client.post("/api/knowledge/candidates",
                              json=_draft(subject=subject).model_dump()).json()["candidate"]["id"]

    rejected = client.post(
        f"/api/knowledge/candidates/{candidate_id}/review",
        json={"decision": "reject", "reviewer": "么慌", "reason": "缺来源：脚本是我口述的"},
    ).json()["candidate"]
    assert rejected["status"] == "rejected"
    assert rejected["rejected_reason"] == "缺来源：脚本是我口述的"

    r = client.post(f"/api/knowledge/candidates/{candidate_id}/ingest", json={"ingested_by": "么慌"})
    assert r.status_code == 409 and r.json()["error"] == "not_approved"
    assert client.get("/api/knowledge/metrics", params={"subject": subject}).json()["items"] == []


@pg_only
def test_没有来源脚本的候选入不了库_连同绕过接口的写法一起拦(pg):
    """**本 Issue 的核心验收。**

    三种写法都要进不去：

    1. 接口提交 —— 提交那一关就被拒（`problems` 里写明缺来源）；
    2. 直接写库造一条"没有来源脚本"的候选，再走入库接口 —— 入库前的再校验发现缺来源，拒；
    3. 直接 `update ... set status='ingested'`（绕过接口） —— **库层 CHECK 直接报约束冲突**。

    第 3 条是这条用例真正要证明的东西：门禁不能只活在接口里。
    """
    from portal_api.main import app

    client = TestClient(app)
    subject = f"ads.ads_产销存月报.{pg}"

    # ① 接口提交：缺来源 → 400
    r = client.post("/api/knowledge/candidates", json=_draft(subject=subject, source_script=None).model_dump())
    assert r.status_code == 400
    assert "missing_source_script" in {p["code"] for p in r.json()["problems"]}

    # ② 绕过接口造一条"没有来源脚本"的候选（库里允许 pending 无来源；门槛在 ingested 那一步）
    with dip_pg.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """insert into knowledge_candidates
                 (kind, subject, formula, depends_on, source_script, submitted_by)
               values ('metric', %s, '产量 = A + B',
                       '[{"table": "cdw.dwd_卷烟产量码段明细", "column": "dama_qty"}]'::jsonb,
                       null, %s) returning id""",
            (subject, f"偷偷写库的人-{pg}"),
        )
        candidate_id = int(cur.fetchone()[0])

    client.post(f"/api/knowledge/candidates/{candidate_id}/review",
                json={"decision": "approve", "reviewer": "么慌", "reason": "只看价值，来源入库再验",
                      "worth_keeping": True})

    r = client.post(f"/api/knowledge/candidates/{candidate_id}/ingest", json={"ingested_by": "么慌"})
    assert r.status_code == 409 and r.json()["error"] == "ingest_rejected"
    assert "missing_source_script" in {p["code"] for p in r.json()["problems"]}
    assert client.get("/api/knowledge/metrics", params={"subject": subject}).json()["items"] == []

    # ③ 彻底绕过接口：直接改库
    with pytest.raises(psycopg.errors.CheckViolation) as exc:
        with dip_pg.connect() as conn, conn.cursor() as cur:
            cur.execute("update knowledge_candidates set status = 'ingested' where id = %s", (candidate_id,))
    assert "knowledge_candidates_ingest_requires_source_chk" in str(exc.value)
    assert dip_pg.knowledge.get_candidate(candidate_id)["status"] == "approved", "被拦下后状态不变"


@pg_only
def test_口径库里不可能存在没有来源的一行(pg):
    """库层的第二道：`knowledge_metrics` 自己也不接受无来源的写入。"""
    from portal_api.main import app

    candidate_id = TestClient(app).post(
        "/api/knowledge/candidates", json=_draft(subject=f"ads.ads_产销存月报.{pg}").model_dump()
    ).json()["candidate"]["id"]

    with pytest.raises(psycopg.errors.CheckViolation) as exc:
        with dip_pg.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """insert into knowledge_metrics
                     (candidate_id, subject, formula, depends_on, source_script, approved_by)
                   values (%s, %s, '产量 = A + B', '[{"table": "t", "column": "c"}]'::jsonb, '  ', '谁')
                   returning id""",
                (candidate_id, f"ads.x.{pg}"),
            )
    assert "knowledge_metrics_source_required_chk" in str(exc.value)


@pg_only
def test_入库是原子的_中途失败不留错账(pg):
    """写口径行与改候选状态必须同生同死 —— 不允许"口径进去了、候选还停在 approved"。"""
    from portal_api.main import app

    client = TestClient(app)
    subject = f"ads.ads_产销存月报.{pg}"
    candidate_id = client.post("/api/knowledge/candidates",
                              json=_draft(subject=subject).model_dump()).json()["candidate"]["id"]
    client.post(f"/api/knowledge/candidates/{candidate_id}/review",
                json={"decision": "approve", "reviewer": "么慌", "worth_keeping": True})

    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("模拟第二步失败")

    original = dip_pg.knowledge._mark_ingested
    dip_pg.knowledge._mark_ingested = boom
    try:
        result = dip_pg.knowledge.ingest(candidate_id=candidate_id, ingested_by="么慌")
    finally:
        dip_pg.knowledge._mark_ingested = original

    assert result.ok is False and "RuntimeError" in (result.error or "")
    assert dip_pg.knowledge.get_candidate(candidate_id)["status"] == "approved", "候选状态不该被改成 ingested"
    rows = dip_pg.knowledge.list_metrics(subject=subject)
    assert rows == [], "第一步写进去的口径行必须一起回滚掉，否则口径库对不上账"


@pg_only
def test_重复入库被拒(pg):
    from portal_api.main import app

    client = TestClient(app)
    subject = f"ads.ads_产销存月报.{pg}"
    candidate_id = client.post("/api/knowledge/candidates",
                              json=_draft(subject=subject).model_dump()).json()["candidate"]["id"]
    client.post(f"/api/knowledge/candidates/{candidate_id}/review",
                json={"decision": "approve", "reviewer": "么慌", "worth_keeping": True})
    assert client.post(f"/api/knowledge/candidates/{candidate_id}/ingest",
                       json={"ingested_by": "么慌"}).status_code == 200

    r = client.post(f"/api/knowledge/candidates/{candidate_id}/ingest", json={"ingested_by": "么慌"})
    assert r.status_code == 409 and r.json()["error"] == "already_ingested"
    assert len(client.get("/api/knowledge/metrics", params={"subject": subject}).json()["items"]) == 1


@pg_only
def test_已审核的候选不能被第二次审核覆盖(pg):
    from portal_api.main import app

    client = TestClient(app)
    subject = f"ads.ads_产销存月报.{pg}"
    candidate_id = client.post("/api/knowledge/candidates",
                              json=_draft(subject=subject).model_dump()).json()["candidate"]["id"]
    first = client.post(f"/api/knowledge/candidates/{candidate_id}/review",
                        json={"decision": "approve", "reviewer": "么慌", "worth_keeping": True})
    assert first.status_code == 200

    second = client.post(f"/api/knowledge/candidates/{candidate_id}/review",
                         json={"decision": "reject", "reviewer": "另一个人", "reason": "我不同意"})
    assert second.status_code == 409 and second.json()["error"] == dip_pg.knowledge.E_STATUS_CONFLICT
    row = dip_pg.knowledge.get_candidate(candidate_id)
    assert row["status"] == "approved" and row["reviewer"] == "么慌", "第一次的审核结论没被改掉"
    assert row["rejected_reason"] is None


@pg_only
def test_拒绝必须带原因_库层也钉了同一条件(pg):
    """接口层拦一道、库层再钉一道：**说不清原因的拒绝不是审核**。"""
    from portal_api.main import app

    candidate_id = TestClient(app).post(
        "/api/knowledge/candidates", json=_draft(subject=f"ads.ads_产销存月报.{pg}").model_dump()
    ).json()["candidate"]["id"]

    with pytest.raises(psycopg.errors.CheckViolation) as exc:
        with dip_pg.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "update knowledge_candidates set status = 'rejected', reviewer = '谁', rejected_reason = ' ' "
                "where id = %s",
                (candidate_id,),
            )
    assert "knowledge_candidates_reject_reason_chk" in str(exc.value)
    assert dip_pg.knowledge.get_candidate(candidate_id)["status"] == "pending"

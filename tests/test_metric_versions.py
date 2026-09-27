"""口径版本与回滚（M3-02 / Issue #13）。

两段，和 M3-01 一样：

- **不需要真库**：来源精度（ADR-0003 的"文件级"标注）与回滚接口的"说话方式"（缺操作人/缺原因/版本号非法）。
- **需要真库**（标 `pg`）：版本号自增、同一主体只能有一个生效版本、回滚后查得到上一版本、
  历史表留痕、以及"直接改库也造不出不带留痕的回滚"。
- **需要内核**（标 `smoke`）：与 `/kb/metric` 的联合验证（平台侧口径的来源与公式能对上内核同一口径）。

约定同前：库/内核不在就跳过（不假装通过），每条用例用 `uuid4` 标记避免历史数据造成假红。
"""

from __future__ import annotations

import uuid
from typing import Any

import dip_pg
import psycopg
import pytest
from dip_contracts.knowledge import CandidateDraft, FieldRef, source_ref
from fastapi.testclient import TestClient
from portal_api.routers import knowledge as knowledge_router

SUBJECT_PREFIX = "ads.ads_产销存月报."


def _pg_up() -> bool:
    try:
        return dip_pg.available()
    except Exception:  # noqa: BLE001
        return False


requires_pg = pytest.mark.skipif(not _pg_up(), reason="PostgreSQL 不在（127.0.0.1:15432），跳过")


def pg_only(func):
    """真库用例：既带 `pg` 标记，也会在库不在时跳过。"""
    return requires_pg(pytest.mark.pg(func))


def _kernel_up() -> bool:
    try:
        from lineage_client import LineageClient

        with LineageClient("http://127.0.0.1:18080", retries=0) as c:
            return bool(c.health().ok)
    except Exception:  # noqa: BLE001
        return False


requires_kernel = pytest.mark.skipif(not _kernel_up(), reason="内核不在（127.0.0.1:18080），跳过")


# ================================================================ 来源精度（不需要真库）


def test_来源精度_有行号说行号_没有就标文件级():
    """验收②的后半句：**每条口径能指到来源脚本（行号缺失时标注"文件级"）**。"""
    with_line = source_ref("examples/warehouse/ads/ads_产销存月报.sql", 1)
    assert with_line.precision == "line"
    assert "第 1 条语句" in with_line.label

    file_only = source_ref("examples/warehouse/ads/ads_产销存月报.sql", None)
    assert file_only.precision == "file", "内核不给行号时只能是文件级"
    assert "文件级" in file_only.label, "必须显式标出'文件级'，不许含糊成'有来源'"
    assert "行号待补" in file_only.label

    nothing = source_ref(None, None)
    assert nothing.precision == "none" and "无来源" in nothing.label


# ================================================================ 接口层（假 store，不需要真库）


class _FakeVersions:
    E_ALREADY_ACTIVE = dip_pg.metric_versions.E_ALREADY_ACTIVE
    E_VERSION_NOT_FOUND = dip_pg.metric_versions.E_VERSION_NOT_FOUND
    E_SUBJECT_NOT_FOUND = dip_pg.metric_versions.E_SUBJECT_NOT_FOUND

    def __init__(self, *, up: bool = True, rollback_ok: bool = True, rollback_error: str | None = None) -> None:
        self._up = up
        self._rollback_ok = rollback_ok
        self._rollback_error = rollback_error

    def available(self) -> bool:
        return self._up

    def active_metric(self, subject: str) -> dict:
        return {"id": 7, "subject": subject, "version": 1, "status": "active", "formula": "产量 = A + B",
                "source_script": "examples/warehouse/ads/ads_产销存月报.sql", "source_line": None}

    def list_versions(self, subject: str, **_: Any) -> list[dict]:
        return [{"version": 2, "status": "rolled_back"}, {"version": 1, "status": "active"}]

    def history(self, subject: str, **_: Any) -> list[dict]:
        return [{"version": 2, "event": "rolled_back", "actor": "么慌", "reason": "公式抄错了"}]

    def rollback(self, **_: Any) -> Any:
        from dip_pg.metric_versions import RollbackOutcome

        if not self._rollback_ok:
            return RollbackOutcome(ok=False, error=self._rollback_error or "version_not_found",
                                   detail="没有那一版")
        return RollbackOutcome(ok=True, from_version=2, to_version=1)


@pytest.fixture()
def fake_versions():
    """依赖注入点被换成假 store 的客户端（用完必须清理，否则会泄漏到真库用例）。"""
    from portal_api.main import app

    def _make(**kwargs: Any) -> TestClient:
        app.dependency_overrides[knowledge_router.versions_store] = lambda: _FakeVersions(**kwargs)
        return TestClient(app)

    yield _make
    app.dependency_overrides.pop(knowledge_router.versions_store, None)


def test_回滚的四条硬规则(fake_versions):
    """回滚要记名、要写原因、目标版本要合法 —— 一条都不给默认值。"""
    url = "/api/knowledge/metrics/rollback"
    base = {"subject": SUBJECT_PREFIX + "x", "to_version": 1, "operated_by": "么慌", "reason": "退回旧口径"}

    r = fake_versions().post(url, json={**base, "subject": "  "})
    assert r.status_code == 400 and r.json()["error"] == "missing_subject"

    r = fake_versions().post(url, json={**base, "to_version": 0})
    assert r.status_code == 400 and r.json()["error"] == "invalid_version"

    r = fake_versions().post(url, json={**base, "operated_by": " "})
    assert r.status_code == 400 and r.json()["error"] == "missing_operator"

    r = fake_versions().post(url, json={**base, "reason": ""})
    assert r.status_code == 400 and r.json()["error"] == "missing_reason"


def test_回滚成功返回从哪版回哪版(fake_versions):
    r = fake_versions().post("/api/knowledge/metrics/rollback", json={
        "subject": SUBJECT_PREFIX + "x", "to_version": 1, "operated_by": "么慌", "reason": "公式抄错了",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["from_version"] == 2 and body["to_version"] == 1
    assert body["metric"]["status"] == "active"
    assert body["metric"]["source"]["precision"] == "file", "没有行号就该标文件级"


def test_回滚失败按错误码给状态码(fake_versions):
    """版本不存在 → 404；"已经是生效版本" → 409。不把两者混成一个状态码。"""
    r = fake_versions(rollback_ok=False, rollback_error="version_not_found").post(
        "/api/knowledge/metrics/rollback", json={
            "subject": SUBJECT_PREFIX + "x", "to_version": 9, "operated_by": "么慌", "reason": "试",
        })
    assert r.status_code == 404

    r = fake_versions(rollback_ok=False, rollback_error="already_active").post(
        "/api/knowledge/metrics/rollback", json={
            "subject": SUBJECT_PREFIX + "x", "to_version": 1, "operated_by": "么慌", "reason": "试",
        })
    assert r.status_code == 409


def test_版本与历史查询在库不可用时明说(fake_versions):
    client = fake_versions(up=False)
    assert client.get("/api/knowledge/metrics/active", params={"subject": "a.b"}).json()["success"] is False
    assert client.get("/api/knowledge/metrics/versions", params={"subject": "a.b"}).json()["items"] == []
    assert client.get("/api/knowledge/metrics/history", params={"subject": "a.b"}).json()["items"] == []


# ================================================================ 真库（验收本体）


@pytest.fixture()
def pg():
    dip_pg.knowledge.init_knowledge_schema()
    return f"kb{uuid.uuid4().hex[:8]}"


def _draft(subject: str, *, formula: str, source_line: int | None = 1, submitted_by: str,
           source_script: str = "examples/warehouse/ads/ads_产销存月报.sql",
           depends_on: list[FieldRef] | None = None) -> CandidateDraft:
    return CandidateDraft(
        kind="metric",
        subject=subject,
        chinese_name="产量",
        formula=formula,
        depends_on=depends_on or [FieldRef(table="cdw.dwd_卷烟产量码段明细", column="dama_qty")],
        source_script=source_script,
        source_line=source_line,
        note="验收用例",
        submitted_by=submitted_by,
    )


def _ingest_new_version(client: TestClient, subject: str, *, formula: str, mark: str,
                        source_line: int | None = 1,
                        source_script: str = "examples/warehouse/ads/ads_产销存月报.sql",
                        depends_on: list[FieldRef] | None = None) -> int:
    """走完整流程入库一版，返回 metric id。"""
    candidate_id = client.post(
        "/api/knowledge/candidates",
        json=_draft(subject, formula=formula, source_line=source_line, submitted_by=f"提交人-{mark}",
                    source_script=source_script, depends_on=depends_on).model_dump(),
    ).json()["candidate"]["id"]
    client.post(f"/api/knowledge/candidates/{candidate_id}/review",
                json={"decision": "approve", "reviewer": "么慌", "reason": "对得上脚本", "worth_keeping": True})
    body = client.post(f"/api/knowledge/candidates/{candidate_id}/ingest",
                       json={"ingested_by": "么慌"}).json()
    assert body["success"] is True, body
    return int(body["metric_id"])


@pg_only
def test_二次入库是新版本_旧版本降级(pg):
    """版本号规则：同一口径每次入库 = 新版本；旧版本降为 `superseded`（历史不删）。"""
    from portal_api.main import app

    client = TestClient(app)
    subject = f"{SUBJECT_PREFIX}{pg}"

    _ingest_new_version(client, subject, formula="产量 = 打码量 + 跳码量 - 重码量", mark=f"{pg}a")
    _ingest_new_version(client, subject, formula="产量 = SUM(chanliang_qty)", mark=f"{pg}b")

    versions = client.get("/api/knowledge/metrics/versions", params={"subject": subject}).json()["items"]
    assert [(v["version"], v["status"]) for v in versions] == [(2, "active"), (1, "superseded")], versions

    active = client.get("/api/knowledge/metrics/active", params={"subject": subject}).json()["metric"]
    assert active["version"] == 2 and active["formula"] == "产量 = SUM(chanliang_qty)"
    assert active["source"]["precision"] == "line"

    events = client.get("/api/knowledge/metrics/history", params={"subject": subject}).json()["items"]
    kinds = sorted(e["event"] for e in events)
    assert kinds == ["entered", "entered", "superseded"], events
    assert all(e["actor"] == "么慌" for e in events), "每条历史都要有经手人"


@pg_only
def test_回滚后生效的是上一版本(pg):
    """**本 Issue 的核心验收**：回滚后口径查询返回上一版本，且回滚本身留痕。"""
    from portal_api.main import app

    client = TestClient(app)
    subject = f"{SUBJECT_PREFIX}{pg}"
    v1_id = _ingest_new_version(client, subject, formula="产量 = 打码量 + 跳码量 - 重码量", mark=f"{pg}a")
    _ingest_new_version(client, subject, formula="产量 = SUM(chanliang_qty)", mark=f"{pg}b")
    assert client.get("/api/knowledge/metrics/active",
                      params={"subject": subject}).json()["metric"]["version"] == 2

    r = client.post("/api/knowledge/metrics/rollback", json={
        "subject": subject, "to_version": 1, "operated_by": "么慌",
        "reason": "新公式与脚本对不上（脚本是 打码量+跳码量-重码量）",
    })
    assert r.status_code == 200, r.text
    assert r.json()["from_version"] == 2 and r.json()["to_version"] == 1

    active = client.get("/api/knowledge/metrics/active", params={"subject": subject}).json()["metric"]
    assert active["version"] == 1, "回滚后生效的应当是上一版本"
    assert active["formula"] == "产量 = 打码量 + 跳码量 - 重码量"
    assert active["status"] == "active"

    versions = {v["version"]: v for v in
                client.get("/api/knowledge/metrics/versions", params={"subject": subject}).json()["items"]}
    assert versions[2]["status"] == "rolled_back"
    assert versions[2]["rolled_back_by"] == "么慌"
    assert "对不上" in versions[2]["rollback_reason"]
    assert versions[2]["rolled_back_at"], "回滚时间必须落下来"
    assert versions[1]["id"] == v1_id, "回滚不会换一条新行，生效的还是原来那条 v1"

    rollback_events = [e for e in
                       client.get("/api/knowledge/metrics/history", params={"subject": subject}).json()["items"]
                       if e["event"] in ("rolled_back", "reactivated")]
    assert len(rollback_events) == 2, "回滚要留两条：谁被降级、谁被恢复"
    assert {e["related_version"] for e in rollback_events} == {1, 2}

    # 回滚过之后还能再改回来（版本没被"用坏"）
    back = client.post("/api/knowledge/metrics/rollback", json={
        "subject": subject, "to_version": 2, "operated_by": "么慌", "reason": "复核后确认新公式才是对的",
    })
    assert back.status_code == 200
    assert client.get("/api/knowledge/metrics/active",
                      params={"subject": subject}).json()["metric"]["version"] == 2


@pg_only
def test_回滚的三种拒绝(pg):
    """版本不存在 / 拿别的口径的版本号 / 目标已经是生效版本 —— 都拒，且说清是哪一种。"""
    from portal_api.main import app

    client = TestClient(app)
    subject = f"{SUBJECT_PREFIX}{pg}"
    other = f"{SUBJECT_PREFIX}{pg}x"
    _ingest_new_version(client, subject, formula="产量 = A + B", mark=f"{pg}a")
    _ingest_new_version(client, other, formula="产量 = A + B", mark=f"{pg}b")
    _ingest_new_version(client, subject, formula="产量 = A - B", mark=f"{pg}c")

    def roll(to_version: int) -> Any:
        return client.post("/api/knowledge/metrics/rollback", json={
            "subject": subject, "to_version": to_version, "operated_by": "么慌", "reason": "试",
        })

    assert roll(9).status_code == 404                                     # 没有第 9 版
    assert roll(9).json()["error"] == "version_not_found"
    assert roll(2).status_code == 409                                     # 第 2 版已经生效
    assert roll(2).json()["error"] == "already_active"
    # 不存在的口径 → 404 subject_not_found（与"版本不存在"分开，便于排错）
    missing = client.post("/api/knowledge/metrics/rollback", json={
        "subject": f"{SUBJECT_PREFIX}根本没有这条", "to_version": 1, "operated_by": "么慌", "reason": "试",
    })
    assert missing.status_code == 404 and missing.json()["error"] == "subject_not_found"
    # 版本定位是 (subject, version)：上面给 other 也入了库，但 subject 自己的第 1 版还在、能回过去
    assert roll(1).status_code == 200
    assert client.get("/api/knowledge/metrics/active",
                      params={"subject": other}).json()["metric"]["version"] == 1, "另一个口径完全没被碰到"


@pg_only
def test_同一口径同时只能有一个生效版本_库层不变量(pg):
    """绕过接口直接写库，也不可能让两条同时生效（partial unique index）。"""
    from portal_api.main import app

    client = TestClient(app)
    subject = f"{SUBJECT_PREFIX}{pg}"
    _ingest_new_version(client, subject, formula="产量 = A + B", mark=f"{pg}a")
    _ingest_new_version(client, subject, formula="产量 = A - B", mark=f"{pg}b")

    with pytest.raises(psycopg.errors.UniqueViolation) as exc:
        with dip_pg.connect() as conn, conn.cursor() as cur:
            cur.execute("update knowledge_metrics set status = 'active' where subject = %s and version = 1",
                        (subject,))
    assert "uq_kb_metrics_one_active" in str(exc.value)


@pg_only
def test_回滚也必须留痕_直接改库造不出没有留痕的回滚(pg):
    """说不清"谁把它撤下来的、为什么"就不是回滚（库层 CHECK）。"""
    from portal_api.main import app

    client = TestClient(app)
    subject = f"{SUBJECT_PREFIX}{pg}"
    _ingest_new_version(client, subject, formula="产量 = A + B", mark=f"{pg}a")

    with pytest.raises(psycopg.errors.CheckViolation) as exc:
        with dip_pg.connect() as conn, conn.cursor() as cur:
            cur.execute("update knowledge_metrics set status = 'rolled_back' where subject = %s", (subject,))
    assert "knowledge_metrics_rollback_trace_chk" in str(exc.value)
    assert dip_pg.metric_versions.active_metric(subject)["version"] == 1, "被拦下后状态不变"


@pg_only
def test_老库补列与约束扩容是幂等的(pg):
    """`init_knowledge_schema` 可以被反复调用（startup 每次都会跑）—— 补列/换约束不许报错。"""
    for _ in range(3):
        dip_pg.knowledge.init_knowledge_schema()
    with dip_pg.connect() as conn, conn.cursor() as cur:
        cur.execute("select column_name from information_schema.columns where table_name = 'knowledge_metrics'")
        columns = {row[0] for row in cur.fetchall()}
    assert {"rolled_back_at", "rolled_back_by", "rollback_reason"} <= columns
    assert "version" in columns


# ================================================================ 与内核 /kb/metric 的联合验证


@pytest.mark.smoke
@pg_only
@requires_kernel
def test_与内核_kb_metric_对得上(pg):
    """验收①的"与 `/kb/metric` 的联合验证"。

    做法：拿内核真实口径（`/kb/metric` 查 `chanliang_qty`）当基准，把同一条口径按同一公式与来源
    入库到平台侧，再断言**平台侧生效版本与内核返回的公式/来源一致** ——
    证明平台侧的版本化口径不是自说自话，指得到同一份来源脚本。
    """
    from lineage_client import LineageClient
    from portal_api.main import app

    with LineageClient("http://127.0.0.1:18080", retries=0) as client:
        r = client.metric("chanliang_qty")
    assert r.ok, r.error
    kernel_metric = next(m for m in r.data["metrics"] if m["table_name"] == "cdw.dwd_卷烟产量码段明细")

    subject = f"{kernel_metric['table_name']}.{kernel_metric['metric_name']}"
    platform = TestClient(app)
    _ingest_new_version(platform, subject, formula=kernel_metric["formula"],
                        source_line=kernel_metric.get("source_stmt"), mark=pg,
                        source_script=kernel_metric["source_file"],
                        depends_on=[FieldRef(table=d["table"], column=d["column"])
                                    for d in kernel_metric["depends_on"]])

    active = platform.get("/api/knowledge/metrics/active", params={"subject": subject}).json()["metric"]
    assert active["formula"] == kernel_metric["formula"], "平台侧口径与内核口径必须是同一条公式"
    assert active["source_script"] == kernel_metric["source_file"], "来源脚本要指到内核同一份文件"
    assert active["source"]["precision"] == "line", "内核给了 source_stmt，这里就该是行级来源"
    deps = {(d["table"], d["column"]) for d in active["depends_on"]}
    assert {(d["table"], d["column"]) for d in kernel_metric["depends_on"]} <= deps

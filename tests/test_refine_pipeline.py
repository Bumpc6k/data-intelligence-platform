"""知识提炼流水线（M3-05 / Issue #16）。

这些用例**不连内核、不连模型**：解析器与初筛器都是注入的假实现（协议很窄，一个方法就够）。
要钉住的是四件事：

1. 解析结果**决定白名单** —— 后面两步都不许脱离它（模型编了要被抓出来）；
2. LLM 产出**必须过两道校验**（契约 + 出口事实），没过的单独列出来；
3. 报告的每条候选**都能指到出处**（脚本 + 语句序号 + 置信度），且不完整时**明说不完整**；
4. **只出报告不落库**：整条流水线不做任何写操作（边界，Issue 明文）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from dip_contracts.kernel import ToolResult
from dip_contracts.knowledge import CandidateDraft, FieldRef
from dip_refine import (
    GatewayLlm,
    parse_llm_json,
    parse_project,
    run,
    to_dict,
    write_report,
)
from dip_refine.report import render_markdown
from dip_refine.screen import PIPELINE_SUBMITTER, build_prompt
from dip_refine.validate import check_candidate, check_identifiers

SQL_TEXT = "INSERT OVERWRITE TABLE ads.ads_产销存月报 SELECT SUM(s.output_qty) AS output_qty FROM cdw.dws_产销存汇总 s;"

PARSE_RESULT = {
    "success": True,
    "dialect": "hive",
    "statement_count": 1,
    "input_tables": ["cdw.dws_产销存汇总"],
    "output_tables": ["ads.ads_产销存月报"],
    "table_lineage": [{"source": "cdw.dws_产销存汇总", "target": "ads.ads_产销存月报"}],
    "statements": [
        {
            "statement_index": 1,
            "task_type": "INSERT_SELECT",
            "sql": SQL_TEXT,
            "input_table_names": ["cdw.dws_产销存汇总"],
            "output_table_names": ["ads.ads_产销存月报"],
            "column_lineage": [
                {"target_table": "ads.ads_产销存月报", "target_column": "output_qty",
                 "source_table": "cdw.dws_产销存汇总", "source_column": "output_qty",
                 "expression": "SUM(s.output_qty) AS output_qty", "resolved": True},
            ],
        }
    ],
}


class FakeParser:
    """假内核：按脚本内容返回预设解析结果（可按文件名制造失败）。"""

    def __init__(self, *, fail_on: str | None = None, payload: dict[str, Any] | None = None) -> None:
        self.fail_on = fail_on
        self.payload = payload or PARSE_RESULT
        self.calls: list[str] = []

    def parse(self, sql: str, dialect: str = "hive") -> ToolResult:
        self.calls.append(sql)
        if self.fail_on and self.fail_on in sql:
            return ToolResult(ok=False, endpoint="/parse", ms=1, data={}, error="内核：SQL 解析失败")
        return ToolResult(ok=True, endpoint="/parse", ms=5, data=self.payload)


class FakeScreener:
    """假模型：返回预设 JSON 文本（可指定每个目录返回什么、或直接抛错）。"""

    model = "fake-llm"

    def __init__(self, payload: dict[str, Any] | None = None, *, raw: str | None = None,
                 error: Exception | None = None) -> None:
        self.payload = payload if payload is not None else {"candidates": [CANDIDATE_JSON]}
        self.raw = raw
        self.error = error
        self.prompts: list[str] = []

    def complete(self, *, system: str, user: str) -> str:
        self.prompts.append(user)
        if self.error:
            raise self.error
        return self.raw if self.raw is not None else json.dumps(self.payload, ensure_ascii=False)


CANDIDATE_JSON = {
    "subject": "ads.ads_产销存月报.output_qty",
    "chinese_name": "产量",
    "formula": "产量 = SUM(产量)",
    "depends_on": [{"table": "cdw.dws_产销存汇总", "column": "output_qty"}],
    "confidence": 0.82,
    "reason": "对 cdw.dws_产销存汇总.output_qty 做了 SUM 聚合，有中文名「产量」",
}


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """一个小项目：一个正常脚本 + 一个坏脚本（内核解析失败）。"""
    ads = tmp_path / "examples" / "warehouse" / "ads"
    ads.mkdir(parents=True)
    (ads / "ads_产销存月报.sql").write_text(SQL_TEXT, encoding="utf-8")
    bad = tmp_path / "examples" / "warehouse" / "broken"
    bad.mkdir(parents=True)
    (bad / "broken.sql").write_text("-- 坏脚本 BROKEN", encoding="utf-8")
    return tmp_path


# ================================================================ 解析


def test_解析结果与来源路径(project: Path):
    parser = FakeParser()
    scripts = parse_project(project / "examples", parser=parser, project_root=project)

    assert len(scripts) == 2
    by_name = {s.source_script: s for s in scripts}
    assert by_name["examples/warehouse/broken/broken.sql"].ok is True, "假解析器不看内容，两个都能过"
    ok = by_name["examples/warehouse/ads/ads_产销存月报.sql"]
    assert ok.source_script == "examples/warehouse/ads/ads_产销存月报.sql", "来源要相对于项目根"
    assert ok.tables == ("cdw.dws_产销存汇总", "ads.ads_产销存月报")
    assert ok.fields == ("output_qty",)
    st = ok.statements[0]
    assert st.index == 1 and st.task_type == "INSERT_SELECT"
    assert st.columns[0][0] == "output_qty"


def test_解析失败的脚本不拖停整条流水线(project: Path):
    parser = FakeParser(fail_on="BROKEN")
    scripts = parse_project(project / "examples", parser=parser, project_root=project)
    broken = [s for s in scripts if not s.ok]
    assert len(broken) == 1 and "内核解析失败" in (broken[0].error or "")
    assert [s.ok for s in scripts].count(True) == 1, "另一个脚本照常解析"


def test_读不到的文件也如实记(project: Path, tmp_path: Path):
    from dip_refine import parse_script

    ghost = parse_script(tmp_path / "没有这个.sql", project_root=tmp_path, parser=FakeParser())
    assert ghost.ok is False and "读取失败" in (ghost.error or "")


# ================================================================ 初筛


def test_模型返回的_json_能被解析_围栏也认():
    fenced = "```json\n" + json.dumps({"candidates": [CANDIDATE_JSON]}, ensure_ascii=False) + "\n```"
    assert parse_llm_json(fenced) == [CANDIDATE_JSON]
    assert parse_llm_json(json.dumps({"candidates": []})) == []
    with pytest.raises(ValueError):
        parse_llm_json("我觉得这条口径挺好的（没有 JSON）")


def test_来源由流水线填_不听模型的(project: Path):
    screener = FakeScreener(raw=json.dumps({"candidates": [{
        **CANDIDATE_JSON,
        "source_script": "模型编的路径.sql",     # 模型给的一律忽略
        "source_line": 99,
    }]}, ensure_ascii=False))
    report = run(project / "examples", parser=FakeParser(), screener=screener, project_root=project)

    candidate = report.candidates[0]
    assert candidate.source_script == "examples/warehouse/ads/ads_产销存月报.sql"
    assert candidate.statement_index == 1, "语句序号来自内核解析，不是模型说的 99"
    assert candidate.draft.submitted_by == PIPELINE_SUBMITTER, "流水线标记，不是真人"


def test_提示词里必须有白名单(project: Path):
    scripts = parse_project(project / "examples", parser=FakeParser(), project_root=project)
    from dip_refine.parse import whitelist_of

    prompt = build_prompt(scripts[1], scripts[1].statements[0], whitelist_of(scripts))
    assert "cdw.dws_产销存汇总" in prompt and "output_qty" in prompt
    assert "只能用这些" in prompt


# ================================================================ 校验


def _draft(**over: Any) -> CandidateDraft:
    base: dict[str, Any] = {
        "kind": "metric", "subject": "ads.ads_产销存月报.output_qty", "formula": "产量 = SUM(产量)",
        "depends_on": [FieldRef(table="cdw.dws_产销存汇总", column="output_qty")],
        "source_script": "examples/warehouse/ads/ads_产销存月报.sql", "source_line": 1,
        "note": "对 cdw.dws_产销存汇总.output_qty 做了 SUM", "submitted_by": PIPELINE_SUBMITTER,
    }
    base.update(over)
    return CandidateDraft(**base)


def test_合格候选通过校验():
    whitelist = {"tables": {"ads.ads_产销存月报", "cdw.dws_产销存汇总"}, "fields": {"output_qty"},
                 "numbers": {"0", "4"}}
    assert check_candidate(_draft(), whitelist).ok is True


def test_数字白名单来自脚本_不误伤也不漏放():
    """SQL 里本来就有的数字（`NULLIF(x, 0)` 的 0、`ROUND(..., 4)` 的 4）不算编造；
    凭空冒出来的数字才算（试跑时真踩过这个假红）。"""
    base = {"tables": {"ads.ads_产销存月报", "cdw.dws_产销存汇总"}, "fields": {"output_qty"}}

    ok_note = _draft(note="对 cdw.dws_产销存汇总.output_qty 做 SUM，分母为 0 时返回空值")
    assert check_candidate(ok_note, {**base, "numbers": {"0"}}).ok is True

    fabricated = _draft(note="这个口径覆盖了 37 张表")
    check = check_candidate(fabricated, {**base, "numbers": {"0"}})
    assert check.ok is False
    assert any(getattr(v.kind, "value", "") == "number" and v.normalized == "37" for v in check.violations)


def test_模型复述_SQL_别名不算编造():
    """模型在理由里照抄 SQL 片段是很自然的写法（`s.output_qty - s.sale_qty`），
    只要这两个写法在脚本里出现过，就不该被判"编造表名" —— 试跑时这一条误伤了 11 条候选。"""
    whitelist = {
        "tables": {"ads.ads_产销存月报", "cdw.dws_产销存汇总"},
        "fields": {"output_qty", "sale_qty"},
        "numbers": {"0", "4"},
        "quote_ok": {"ads.ads_产销存月报", "cdw.dws_产销存汇总", "s.output_qty", "s.sale_qty"},
    }
    quoted = _draft(note="目标字段由 s.output_qty - s.sale_qty 计算得出")
    assert check_candidate(quoted, whitelist).ok is True

    # 但"别名"是白名单里的具体写法，凭空造一个 x.made_up 照样拦
    invented = _draft(note="这条口径来自 x.made_up 的换算")
    check = check_candidate(invented, whitelist)
    assert check.ok is False
    assert any(getattr(v.kind, "value", "") == "table" for v in check.violations)


def test_quote_ok_包含脚本里出现的别名写法(project: Path):
    from dip_refine.parse import whitelist_of

    payload = {
        **PARSE_RESULT,
        "statements": [{**PARSE_RESULT["statements"][0],
                        "sql": "SELECT SUM(s.output_qty) AS output_qty FROM cdw.dws_产销存汇总 s"}],
    }
    scripts = parse_project(project / "examples", parser=FakeParser(payload=payload), project_root=project)
    whitelist = whitelist_of(scripts)
    assert "s.output_qty" in whitelist["quote_ok"]
    assert "s.output_qty" not in whitelist["tables"], "别名写法不进精确核对用的 tables"


def test_解析结果里的数字会被收进白名单(project: Path):
    """脚本里出现过的数字（`NULLIF(x, 0)`、`ROUND(..., 4)`）才允许在理由里复述。"""
    from dip_refine.parse import whitelist_of

    payload = {
        **PARSE_RESULT,
        "statements": [{**PARSE_RESULT["statements"][0],
                        "sql": "SELECT ROUND(s.sale_qty / NULLIF(s.output_qty, 0), 4) AS ratio FROM t",
                        "column_lineage": [{**PARSE_RESULT["statements"][0]["column_lineage"][0],
                                            "expression": "ROUND(sale_qty / NULLIF(output_qty, 0), 4)"}]}],
    }
    scripts = parse_project(project / "examples", parser=FakeParser(payload=payload), project_root=project)
    numbers = whitelist_of(scripts)["numbers"]
    assert {"0", "4"} <= numbers, numbers


def test_脚本里没有的数字不会进白名单(project: Path):
    from dip_refine.parse import whitelist_of

    scripts = parse_project(project / "examples", parser=FakeParser(), project_root=project)
    assert whitelist_of(scripts)["numbers"] == set(), "SQL_TEXT 里没有数字字面量"


def test_缺来源或公式的候选被契约层拦():
    """与 #12 的提交接口**同一套规则**（复用 `validate_draft`），不是流水线自己定一套。"""
    whitelist = {"tables": {"ads.ads_产销存月报", "cdw.dws_产销存汇总"}, "fields": {"output_qty"}}

    check = check_candidate(_draft(source_script=None), whitelist)
    assert check.ok is False
    assert [p.code for p in check.problem_list] == ["missing_source_script"]

    check = check_candidate(_draft(formula=None, depends_on=[]), whitelist)
    assert {p.code for p in check.problem_list} == {"missing_formula", "missing_depends_on"}


def test_编造的表名字段名会被抓出来():
    whitelist = {"tables": {"ads.ads_产销存月报", "cdw.dws_产销存汇总"}, "fields": {"output_qty"}}

    # subject 的表不在解析结果里
    notes = check_identifiers(_draft(subject="ads.ads_根本不存在的表.output_qty"), whitelist)
    assert any("不在解析结果里" in n for n in notes)

    # 依赖字段编的
    notes = check_identifiers(_draft(depends_on=[FieldRef(table="cdw.dws_产销存汇总", column="made_up_qty")]),
                              whitelist)
    assert any("made_up_qty" in n for n in notes)

    # 模型理由里出现了编造的表名 → 出口事实校验（#5 的 guards）点名
    check = check_candidate(_draft(note="这条口径来自 ods.ods_编造明细 的 dama_qty"), whitelist)
    kinds = {getattr(v.kind, "value", str(v.kind)) for v in check.violations}
    tokens = {v.token for v in check.violations}
    assert check.ok is False and "table" in kinds
    assert any("ods.ods_编造明细" == t or "dama_qty" == t for t in tokens)


# ================================================================ 报告


def test_报告列出处与置信度(project: Path):
    report = run(project / "examples", parser=FakeParser(), screener=FakeScreener(), project_root=project)
    md = render_markdown(report)

    assert "只出报告，未写入候选池" in md
    assert "examples/warehouse/ads/ads_产销存月报.sql" in md
    assert "第 1 条语句" in md
    assert "置信度（模型自评）：**0.82**" in md
    assert "templates/人工抽检记录.md" in md
    assert "## 4. 人工抽检建议" in md


def test_报告不完整时必须明说(project: Path):
    report = run(project / "examples", parser=FakeParser(fail_on="BROKEN"), screener=FakeScreener(),
                 project_root=project)
    assert report.incomplete is True                     # broken.sql 解析失败
    md = render_markdown(report)
    assert "本报告不完整" in md and "别据此入库" in md
    assert "## 5. 失败与错误" in md

    data = to_dict(report)
    assert data["incomplete"] is True
    assert data["wrote_to_candidate_pool"] is False, "报告自己声明边界（Issue #16：只出报告不落库）"
    assert data["counts"]["candidates"] == len(report.candidates)


def test_写出的报告有两份(project: Path, tmp_path: Path):
    report = run(project / "examples", parser=FakeParser(), screener=FakeScreener(), project_root=project)
    md_path, json_path = write_report(report, tmp_path / "out")
    assert md_path.exists() and json_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["counts"]["candidates"] == len(report.candidates)


# ================================================================ 边界：只出报告不落库


def test_流水线不做任何写操作():
    """Issue #16 的边界是硬的：**整包只有初筛模块能发 HTTP**，而且只发向模型网关的 `/v1/chat/completions`。

    这样写比"扫关键字"结实：要写候选池就得发 HTTP，而这一刻只有一处能发；
    报告里出现 `POST /api/knowledge/candidates` 这类**说明文字**不算违规（那是给人看的下一步指引）。
    """
    package = Path(__file__).resolve().parents[1] / "packages" / "dip-refine" / "src" / "dip_refine"
    http_users = [p.name for p in sorted(package.rglob("*.py"))
                  if "httpx" in p.read_text(encoding="utf-8")]
    assert http_users == ["screen.py"], f"只有初筛模块该碰 HTTP，实际：{http_users}"

    screen_src = (package / "screen.py").read_text(encoding="utf-8")
    assert screen_src.count("client.post(") == 1, "模型网关只能有一个出口"
    assert "/v1/chat/completions" in screen_src
    assert "/api/knowledge" not in screen_src, "初筛模块不许出现知识库接口"


def test_注入假实现时完全不碰网络(project: Path, monkeypatch: pytest.MonkeyPatch):
    import httpx

    def boom(*_: Any, **__: Any) -> Any:
        raise AssertionError("流水线不该发起任何真实 HTTP 请求")

    monkeypatch.setattr(httpx, "Client", boom)
    report = run(project / "examples", parser=FakeParser(fail_on="BROKEN"), screener=FakeScreener(),
                 project_root=project)
    assert report.llm_calls == 1, "只有那个能解析的脚本被初筛一次"


# ================================================================ 失败策略


def test_模型整体不可用时不产出报告(project: Path):
    """宁可没有报告，也不给一份"看起来完整"的东西（AGENTS 铁律 1）。"""
    screener = FakeScreener(error=RuntimeError("模型调用失败（2 次）：Connection refused"))
    with pytest.raises(RuntimeError) as exc:
        run(project / "examples", parser=FakeParser(), screener=screener, project_root=project)
    assert "一条候选都没筛出来" in str(exc.value)


def test_单条语句初筛失败记下来但不停工(project: Path):
    """一条语句初筛失败不该拖停别人 —— 记进 `errors`，报告标"不完整"，其余候选照常列。"""
    two_statements = {
        **PARSE_RESULT,
        "statement_count": 2,
        "statements": [
            PARSE_RESULT["statements"][0],
            {**PARSE_RESULT["statements"][0], "statement_index": 2, "sql": "SELECT 2"},
        ],
    }

    class Flaky(FakeScreener):
        """第一次超时，第二次成功。"""

        def __init__(self) -> None:
            super().__init__()
            self.seen = 0

        def complete(self, *, system: str, user: str) -> str:
            self.seen += 1
            if self.seen == 1:
                raise RuntimeError("第一次超时")
            return json.dumps(self.payload, ensure_ascii=False)

    report = run(project / "examples", parser=FakeParser(payload=two_statements, fail_on="BROKEN"),
                 screener=Flaky(), project_root=project)

    assert report.incomplete is True
    assert [e for e in report.errors if "初筛失败" in e], report.errors
    assert len(report.candidates) == 1, "第二次成功了，失败的那条不影响它"
    assert "本报告不完整" in render_markdown(report)


def test_全都没筛出来时不产出报告(project: Path):
    """模型返回空 + 有脚本解析失败 → 判为环境不可用，抛异常（不产出半成品报告）。"""
    with pytest.raises(RuntimeError) as exc:
        run(project / "examples", parser=FakeParser(fail_on="BROKEN"),
            screener=FakeScreener({"candidates": []}), project_root=project)
    assert "一条候选都没筛出来" in str(exc.value)


def test_抽检样本含最低最高置信度且可复现(project: Path):
    payload = {"candidates": [
        {**CANDIDATE_JSON, "subject": "ads.ads_产销存月报.output_qty", "confidence": 0.1},
        {**CANDIDATE_JSON, "subject": "ads.ads_产销存月报.sale_qty", "confidence": 0.9},
        {**CANDIDATE_JSON, "subject": "ads.ads_产销存月报.stock_qty", "confidence": 0.5},
    ]}
    report = run(project / "examples", parser=FakeParser(), screener=FakeScreener(payload), project_root=project)
    confidences = [c.confidence for c in report.sample]
    assert min(confidences) == 0.1 and max(confidences) == 0.9, "最低和最高的都要进抽检样本"

    again = run(project / "examples", parser=FakeParser(), screener=FakeScreener(payload), project_root=project)
    assert [c.subject for c in again.sample] == [c.subject for c in report.sample], "固定种子 → 样本可复现"


# ================================================================ 模型网关客户端


def test_网关请求形状正确(monkeypatch: pytest.MonkeyPatch):
    """只走平台网关的 OpenAI 兼容入口（`/v1/chat/completions`），不直连模型厂商。"""
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": "{}"}}]}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured["client_kwargs"] = kwargs

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> FakeResponse:
            captured.update(url=url, payload=json, headers=headers)
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)
    llm = GatewayLlm("http://127.0.0.1:18200/", model="deepseek-v4-flash", api_key="k", max_attempts=1)
    assert llm.complete(system="sys", user="usr") == "{}"

    assert captured["url"] == "http://127.0.0.1:18200/v1/chat/completions"
    assert captured["payload"]["model"] == "deepseek-v4-flash"
    assert captured["payload"]["messages"][0] == {"role": "system", "content": "sys"}
    assert captured["payload"]["temperature"] == 0, "提炼要可复现，温度固定 0"
    assert captured["headers"]["Authorization"] == "Bearer k"
    assert captured["client_kwargs"]["trust_env"] is False, "本机调用别被系统代理带崩"


def test_网关报错时抛出而不是返回空文本(monkeypatch: pytest.MonkeyPatch):
    class FakeResponse:
        status_code = 502
        text = "upstream_error"

    class FakeClient:
        def __init__(self, **_: Any) -> None: ...
        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def post(self, *_: Any, **__: Any) -> FakeResponse:
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(RuntimeError) as exc:
        GatewayLlm("http://127.0.0.1:18200", model="m", max_attempts=1).complete(system="s", user="u")
    assert "502" in str(exc.value)


def test_cli_缺模型名时明确报错(monkeypatch: pytest.MonkeyPatch, project: Path, capsys: pytest.CaptureFixture):
    from dip_refine.__main__ import main

    monkeypatch.setattr("dip_core.load_settings", lambda *_: type("S", (), {"llm_model": None,
                                                                          "llm_api_key": None,
                                                                          "llm_timeout": 30.0,
                                                                          "llm_max_attempts": 2,
                                                                          "kernel_base_url": "http://127.0.0.1:18080",
                                                                          "kernel_retries": 0})())
    code = main(["--project", str(project / "examples"), "--out", str(project / "out")])
    assert code == 2
    assert "缺少模型名" in capsys.readouterr().err

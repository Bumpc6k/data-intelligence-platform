"""视图分派的后端侧（M4-01 / Issue #17）—— 全离线，用录制的真内核响应。

要钉四件事：

1. **词汇表是一份**：契约层的 `RENDERERS` 与 skill 契约（`dip_skills.Renderer`）必须一致，
   前端注册表再用同一套名字 —— 三处不一致就等于"声明的 renderer 渲染不出来"；
2. **视图归属可由 skill 声明改**：把 `lineage` 的 renderer 覆盖成 `table`，
   血缘结果就得交给表格视图（这是"前端按 skill 声明分派"的落点）；
3. **不许画空图**：失败的调用、没有数据的返回，都不产出视图块；
4. **宿主不认识具体视图**：`index.html` 里不许出现"按视图名分支"的写法（有测试盯着）。
"""

from __future__ import annotations

import pathlib

import pytest
from dip_agent import Agent
from dip_agent.agent.views import (
    KIND_BY_TOOL,
    MAX_ROWS,
    block_for,
    renderer_for,
    view_map,
    views_from_results,
)
from dip_contracts import RENDERERS, ViewBlock
from dip_skills.spec import Renderer
from fakes import FakeKernel
from portal_api.view_support import diff_block, diff_for_answer, metric_name_of, subjects_for, with_platform_views

WEB = pathlib.Path(__file__).parents[1] / "apps/portal-web"


class _Step:
    def __init__(self, tool: str, **args: object) -> None:
        self.tool = tool
        self.args = args


class _Result:
    def __init__(self, data: object, *, ok: bool = True, endpoint: str | None = "POST /x") -> None:
        self.data = data
        self.ok = ok
        self.endpoint = endpoint


# 真内核的响应形状（与 tests/fakes.py 用的是同一份录制 fixture）
REAL_UPSTREAM = {
    "success": True,
    "direction": "upstream",
    "start_table": "ads.ads_产销存月报",
    "tables": ["cdw.dws_产销存汇总", "cdw.dwd_卷烟销量明细", "dim.dim_brand"],
    "paths": [["ads.ads_产销存月报", "cdw.dws_产销存汇总", "cdw.dwd_卷烟销量明细"]],
    "levels": [{"level": 1, "tables": ["cdw.dws_产销存汇总"]}],
    "upstream_count": 3,
    "edge_count": 2,
}

REAL_SEARCH = {
    "success": True,
    "query": "产量",
    "total": 2,
    "groups": {
        "metrics": [
            {"metric_name": "chanliang_qty", "table_name": "cdw.dwd_卷烟产量码段明细", "chinese_name": "产量",
             "layer": "dwd", "formula": "产量 = 打码量 + 跳码量 - 重码量", "score": 130.0,
             "expression_raw": "SUM(b.dama_qty) + SUM(b.tiaoma_qty) - SUM(b.chongma_qty) AS chanliang_qty",
             "source_file": "examples/knowledge_demo/cdw/dwd_卷烟产量码段明细.sql", "source_stmt": 1},
        ],
        "fields": [
            {"column": "output_qty", "chinese_name": "产量", "table_name": "ads.ads_产销存月报", "layer": "ads",
             "score": 42.0},
        ],
    },
}


# ---------------------------------------------------------------- 词汇表一致


def test_视图词汇表与_skill_契约一致():
    """契约层的 RENDERERS、skill 契约的 Renderer、前端注册表，说的是同一套名字。"""
    assert RENDERERS == tuple(r.value for r in Renderer)


def test_未知视图名在契约层就被拦():
    with pytest.raises(ValueError):
        ViewBlock(renderer="pie", title="饼图")  # 词汇表里没有它，不许偷偷通过


def test_每个工具都有明确的默认归属或明确没有():
    for tool, kind in KIND_BY_TOOL.items():
        assert renderer_for(tool), f"{tool}（{kind}）应该有默认视图"
    assert renderer_for("不存在的方法") is None      # 认不出就是没有，不硬猜


# ---------------------------------------------------------------- 内核视图


def test_血缘结果给图视图():
    views = views_from_results([(_Step("upstream", table="ads.ads_产销存月报"), _Result(REAL_UPSTREAM))])
    assert [v.renderer for v in views] == ["graph"]
    data = views[0].data
    assert data["start_table"] == "ads.ads_产销存月报"
    # 视图拿到的是**原始形状**（tables/paths），怎么画是前端视图的事
    assert data["paths"] == REAL_UPSTREAM["paths"]
    assert data["upstream_count"] == 3


def test_检索结果给表格加SQL两块():
    views = views_from_results([(_Step("search", keyword="产量"), _Result(REAL_SEARCH))])
    assert [v.renderer for v in views] == ["table", "sql"]

    table, sql = views
    rows = table.data["rows"]
    assert {r["name"] for r in rows} == {"chanliang_qty", "output_qty"}
    assert rows[0]["score"] >= rows[-1]["score"]                 # 按相关度倒序
    assert table.data["total"] == 2
    assert sql.data["statements"][0]["source_file"].endswith(".sql")
    assert sql.data["statements"][0]["source_stmt"] == 1


def test_失败的调用不产出视图():
    views = views_from_results([
        (_Step("upstream", table="x"), _Result({}, ok=False)),
        (_Step("search", keyword="产量"), _Result({"groups": {}})),
    ])
    assert views == []


def test_同种视图合并成一块():
    views = views_from_results([
        (_Step("search", keyword="产量"), _Result(REAL_SEARCH)),
        (_Step("search", keyword="销量"), _Result(REAL_SEARCH)),
    ])
    assert [v.renderer for v in views] == ["table", "sql"]
    assert len(views[0].data["rows"]) == 4                       # 两轮检索的行接在一起
    assert len(views[1].data["statements"]) == 1                 # SQL 去重


def test_行数超上限会截断并写明():
    big = {"groups": {"metrics": [
        {"metric_name": f"m{i}", "table_name": "t", "score": float(1000 - i)} for i in range(MAX_ROWS + 20)
    ]}}
    block = block_for("search", big)
    assert len(block.data["rows"]) == MAX_ROWS
    assert block.data["truncated"] is True
    assert "截断" in (block.note or "")


# ---------------------------------------------------------------- skill 声明驱动


def test_skill_声明能把血缘的视图换成表格():
    """声明里写 table，血缘结果就交给表格视图 —— 这就是"按声明分派"的落点。"""
    declared = {"lineage": "table"}
    views = views_from_results([(_Step("upstream", table="t"), _Result(REAL_UPSTREAM))],
                               renderer_by_kind=declared)
    assert [v.renderer for v in views] == ["table"]
    assert views[0].data["tables"] == REAL_UPSTREAM["tables"]


def test_每步的视图名会标在步骤条上():
    results = [(_Step("search", keyword="产量"), _Result(REAL_SEARCH)),
               (_Step("upstream", table="t"), _Result(REAL_UPSTREAM)),
               (_Step("不存在的方法", ), _Result({"x": 1}))]
    views, by_result = view_map(results)
    assert [v.renderer for v in views] == ["table", "sql", "graph"]
    assert by_result[id(results[0][1])] == "table"
    assert by_result.get(id(results[2][1])) is None                # 认不出的工具没有视图名
    assert by_result.get(id(results[1][1])) == "graph" or by_result.get(id(results[1][1])) == "table"


def test_agent_会把视图带进回答():
    answer = Agent(FakeKernel()).ask("ads.ads_产销存月报 的产量怎么来的？")
    assert answer.views, "真内核响应里既有血缘也有检索，应当带视图"
    names = [v.renderer for v in answer.views]
    assert "graph" in names and "table" in names
    # 步骤条上标了视图名的那些调用，名字必须在词汇表里
    for call in answer.tool_calls:
        assert call.renderer is None or call.renderer in RENDERERS


# ---------------------------------------------------------------- 平台侧 diff 视图


class _FakeMetricsStore:
    def __init__(self, subjects: list[str]) -> None:
        self.subjects = subjects

    def available(self) -> bool:
        return True

    def list_metrics(self, *, limit: int = 50, subject: str | None = None) -> list[dict]:
        return [{"subject": s} for s in self.subjects[:limit]]


class _FakeVersionsStore:
    def __init__(self, versions: dict[str, list[dict]], *, ok: bool = True) -> None:
        self.versions = versions
        self.ok = ok

    def available(self) -> bool:
        return self.ok

    def list_versions(self, subject: str, *, limit: int = 50) -> list[dict]:
        return self.versions.get(subject, [])[:limit]


def _answer_with_metric_ref(ref: str = "metric:产量@chanliang_qty"):
    answer = Agent(FakeKernel()).ask("ads.ads_产销存月报 的产量怎么来的？")
    evidence = [answer.result.evidence[0].model_copy(update={"type": "metric", "ref": ref})]
    return answer.model_copy(update={"result": answer.result.model_copy(update={"evidence": evidence})})


def test_从证据里取口径名():
    assert metric_name_of(_answer_with_metric_ref()) == "chanliang_qty"
    assert metric_name_of(_answer_with_metric_ref("metric:表.chanliang_qty")) == "chanliang_qty"
    plain = Agent(FakeKernel()).ask("ads.ads_产销存月报 的产量怎么来的？")
    assert metric_name_of(plain.model_copy(update={
        "result": plain.result.model_copy(update={"evidence": plain.result.evidence[:1]})})) is None


def test_按口径名找主体是结尾匹配():
    store = _FakeMetricsStore(["cdw.dwd_卷烟产量码段明细.chanliang_qty", "ads.ads_产销存月报.output_qty"])
    assert subjects_for("chanliang_qty", metrics_store=store) == ["cdw.dwd_卷烟产量码段明细.chanliang_qty"]
    assert subjects_for("不存在", metrics_store=store) == []


def test_两版有差异时给_diff_视图():
    subject = "cdw.dwd_卷烟产量码段明细.chanliang_qty"
    versions = [
        {"version": 2, "status": "active", "formula": "产量 = 打码量 + 跳码量", "chinese_name": "产量"},
        {"version": 1, "status": "superseded", "formula": "产量 = 打码量 + 跳码量 - 重码量", "chinese_name": "产量"},
    ]
    block = diff_block(subject, versions)
    assert block.renderer == "diff"
    assert block.data["from"]["version"] == 1 and block.data["to"]["version"] == 2
    assert block.data["changed"][0]["field"] == "formula"
    assert block.data["changed"][0]["before"].endswith("重码量")
    assert "口径库" in (block.source or "")            # 出处写明不是内核


def test_两版一致时明写一致():
    subject = "t.chanliang_qty"
    same = [{"version": 2, "status": "active", "formula": "产量 = 打码量"}, {"version": 1, "formula": "产量 = 打码量"}]
    block = diff_block(subject, same)
    assert block.data["changed"] == []
    assert "一致" in (block.note or "")


def test_只有一个版本就不给_diff():
    subject = "cdw.dwd_卷烟产量码段明细.chanliang_qty"
    answer = _answer_with_metric_ref()
    block = diff_for_answer(
        answer,
        metrics_store=_FakeMetricsStore([subject]),
        versions_store=_FakeVersionsStore({subject: [{"version": 1, "formula": "x"}]}),
    )
    assert block is None


def test_库不可用就不给_diff_也不报错():
    answer = _answer_with_metric_ref()
    merged = with_platform_views(
        answer,
        metrics_store=_FakeMetricsStore(["t.chanliang_qty"]),
        versions_store=_FakeVersionsStore({}, ok=False),
    )
    assert merged.views == answer.views


def test_平台视图接在内核视图之后():
    subject = "cdw.dwd_卷烟产量码段明细.chanliang_qty"
    answer = _answer_with_metric_ref()
    merged = with_platform_views(
        answer,
        metrics_store=_FakeMetricsStore([subject]),
        versions_store=_FakeVersionsStore({subject: [
            {"version": 3, "formula": "新"}, {"version": 2, "formula": "旧"}, {"version": 1, "formula": "更旧"}]}),
    )
    assert [v.renderer for v in merged.views][:-1] == [v.renderer for v in answer.views]
    assert merged.views[-1].renderer == "diff"
    assert merged.views[-1].data["version_count"] == 3
    # 视图只加视图，不碰结论与证据链
    assert merged.result == answer.result


# ---------------------------------------------------------------- 宿主不认识具体视图


def test_前端宿主没有按视图名分支():
    """验收②的实现面：宿主只调 `renderView(block, mount)`，不许出现 `renderer === 'graph'` 这类写法。"""
    host = (WEB / "index.html").read_text(encoding="utf-8")
    for name in RENDERERS:
        for pattern in (f"=== '{name}'", f'=== "{name}"', f"case '{name}'", f"case \"{name}\""):
            assert pattern not in host, f"宿主里出现了按视图名分支的写法：{pattern}"
    assert "=== 'renderer'" not in host
    assert "views/index.js" in host                      # 只通过注册表入口装配
    assert "window.renderView" in host


def test_注册表入口只做装配():
    entry = (WEB / "views" / "index.js").read_text(encoding="utf-8")
    for name in RENDERERS:
        assert f"./{name}.js" in entry, f"注册表入口应当 import 四个视图：缺 {name}"
    # 入口本身不许有渲染逻辑：它只负责 import + 再导出
    assert "render(" not in entry

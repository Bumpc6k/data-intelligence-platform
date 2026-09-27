"""文档通道的「仅背景」约束（M3-04 / Issue #15）。

这些用例**全离线**：不连 WeKnora、不连模型，只测契约层的规则。要钉住三件事：

1. **引用必须标注来源** —— 缺来源的材料直接被拒收，而不是"过滤掉"（验收②）；
2. **双通道同时命中时，结论只来自结构化通道** —— 挂文档材料不许动 `result` 一个字节（验收①）；
3. **文档里的数字不许变成结论** —— 拿结构化通道说过的数字当白名单再扫一遍结论（反例测试）。

第 3 条反例最容易被"改坏还看不出来"：把文档摘录当成结论的依据，界面上一模一样，
只有出口校验能分出真假，所以这里用 #5 的校验器当尺子，不另写一套。
"""

from __future__ import annotations

import pytest
from dip_contracts.doc_channel import (
    CHANNEL_DOCUMENTS,
    CitationMissing,
    ConclusionContaminated,
    attach_background,
    background_of,
    check_citations,
    conclusion_contamination,
    empty_background,
    render_background,
    structured_numbers,
)
from dip_contracts.models import (
    Answer,
    DocCitation,
    DocHit,
    Evidence,
    EvidenceType,
    Result,
    ResultValue,
    Source,
    Status,
)

# ---------------------------------------------------------------- 造数据


def _metric_answer(text: str = "产量 = 打码量 + 跳码量 - 重码量（来源：ads.ads_产销存月报.sql 第 12 行）") -> Answer:
    """一个**结构化通道**给的答案：有口径、有证据、有来源。"""
    return Answer(
        answer_id="ans-1",
        text=text,
        result=Result(
            value=ResultValue(type="formula", display="产量 = 打码量 + 跳码量 - 重码量",
                              expr="SUM(dama_qty) + SUM(tiaoma_qty) - SUM(chongma_qty)"),
            confidence=0.9,
            status=Status.VERIFIED,
            evidence=[
                Evidence(
                    type=EvidenceType.METRIC,
                    ref="metric:产量@v1",
                    summary="产量 = 打码量 + 跳码量 - 重码量",
                    source=Source(kind="metric", file="ads/ads_产销存月报.sql", line=12),
                )
            ],
            source=Source(kind="metric", file="ads/ads_产销存月报.sql", line=12),
        ),
    )


def _empty_answer() -> Answer:
    """结构化通道**没命中**：没有结论值、状态是 unresolved。"""
    return Answer(
        answer_id="ans-2",
        text="没有找到对应的口径（结构化通道未命中）。",
        result=Result(value=None, confidence=0.0, status=Status.UNRESOLVED, evidence=[]),
    )


def _hit(text: str = "本厂产销存月报里的库存增量，口径为「产量 − 销量」。",
         *, document: str = "产销存月报口径说明.md", chunk: str = "chunk-7",
         kb: str = "kb-1", score: float | None = 0.83) -> DocHit:
    return DocHit(
        text=text,
        citation=DocCitation(document_name=document, chunk_id=chunk, knowledge_id="doc-1",
                             knowledge_base_id=kb, url="http://127.0.0.1:18380/doc/doc-1",
                             position="第 3 页"),
        score=score,
    )


# ---------------------------------------------------------------- 验收②：引用必须标来源


def test_引用齐全才合格():
    assert check_citations([_hit(), _hit(document="产量口径.md", chunk="chunk-2")]) == ()


def test_缺文档名或缺切片都不合格():
    problems = check_citations([
        _hit(document="", chunk="chunk-1"),
        _hit(document="产量口径.md", chunk=""),
        _hit(text="   "),
    ])
    codes = [p.code for p in problems]
    assert codes == ["citation_missing_document", "citation_missing_chunk", "citation_empty_excerpt"]
    # 每条问题都要指到具体哪一条材料（否则人不知道去修哪条）
    assert problems[0].field == "hits[0].citation.document_name"
    assert "第 1 条" in problems[0].message


def test_缺来源的材料是被拒收而不是被丢掉():
    with pytest.raises(CitationMissing) as exc:
        background_of([_hit(), _hit(chunk="")])
    assert [p.code for p in exc.value.problems] == ["citation_missing_chunk"]


def test_放宽开关是显式的():
    """确实需要收下"没来源"的材料时，得显式关掉 —— 默认必须是严格。"""
    bg = background_of([_hit(chunk="")], require_citations=False)
    assert len(bg.hits) == 1
    assert bg.hits[0].citation.label == "产销存月报口径说明.md"


# ---------------------------------------------------------------- 验收①：结论只来自结构化通道


def test_双通道同时命中时结论只来自结构化通道():
    structured = _metric_answer()
    merged = attach_background(structured, [_hit()])

    assert merged.result == structured.result                     # 结论一字未变
    assert merged.text == structured.text                         # 给人看的话也没动
    assert len(merged.background.hits) == 1                       # 文档材料在，但只在 background
    assert merged.background.channel == CHANNEL_DOCUMENTS
    assert merged.background.usable_for_conclusion is False
    assert conclusion_contamination(structured, merged) == ()     # 校验器也认这条合格


def test_结构化通道没命中时文档也不许顶上():
    """最容易出错的一条：结构化没答案、文档有话说 —— 结论必须仍然是"未命中"。"""
    empty = _empty_answer()
    merged = attach_background(empty, [_hit("（文档里写着）库存增量 = 产量 − 销量")])

    assert merged.result.value is None
    assert merged.result.status is Status.UNRESOLVED
    assert merged.text == empty.text
    assert merged.background.hits                                # 材料还在，只是当背景


def test_文档材料不许进证据链():
    """反例：有人把文档摘录塞进 `Result.evidence` 给结论背书 —— 必须被抓出来。"""
    structured = _metric_answer()
    merged = attach_background(structured, [_hit()])
    tampered = merged.model_copy(update={
        "result": merged.result.model_copy(update={
            "evidence": [
                *merged.result.evidence,
                Evidence(type=EvidenceType.DICT, ref="doc:产销存月报口径说明.md#chunk-7",
                         summary="文档里也这么说"),
            ]
        })
    })
    problems = conclusion_contamination(structured, tampered)
    assert [p.code for p in problems] == ["result_changed"]
    assert "证据链" in problems[0].message


def test_文档里的数字不许当结论():
    """反例：文档写"占比 37%"，平台把它写进结论 —— 结构化通道没说过 37，必须拦。"""
    structured = _metric_answer()
    merged = attach_background(structured, [_hit("2025 年库存增量占产量 37%")])
    dirty = merged.model_copy(update={"text": structured.text + "；库存增量占产量 37%"})

    problems = conclusion_contamination(structured, dirty)
    assert [p.code for p in problems] == ["conclusion_number_not_structured"]
    assert "37" in problems[0].message


def test_结构化说了的数字照样能用():
    structured = _metric_answer("产量（2025 年口径），产量 = 打码量 + 跳码量 - 重码量")
    merged = attach_background(structured, [_hit()])
    assert conclusion_contamination(structured, merged) == ()
    assert "2025" in structured_numbers(structured)


def test_严格模式下结论被污染就直接抛():
    structured = _metric_answer()

    # 合格路径：挂背景不抛，且结论一字未动
    clean = attach_background(structured, [_hit()], strict=True)
    assert clean.result == structured.result

    # 反例：有人拿文档材料另写了一段结论文字（37% 是文档里的，结构化通道没说过）→ 直接抛
    with pytest.raises(ConclusionContaminated) as exc:
        attach_background(structured, [_hit("2025 年库存增量占产量 37%")],
                          compose="库存增量占产量 37%，口径为产量 − 销量", strict=True)
    assert [p.code for p in exc.value.problems] == ["conclusion_number_not_structured"]

    # 只用到结构化通道自己的数字 → 放行（2025 是结构化通道自己标的口径年份）
    structured_2025 = _metric_answer("产量（2025 年口径）= 打码量 + 跳码量 - 重码量")
    ok = attach_background(structured_2025, [_hit()],
                           compose="产量（2025 年口径）= 打码量 + 跳码量 - 重码量", strict=True)
    assert ok.background.hits


def test_没有文档材料时形状明确():
    merged = attach_background(_metric_answer(), None)
    assert merged.background is not None and merged.background.hits == []
    assert merged.background.usable_for_conclusion is False
    assert empty_background().hits == []
    assert render_background(None) == ""


# ---------------------------------------------------------------- 规则 4：渲染必带来源


def test_背景渲染每条都带来源():
    text = render_background(background_of([_hit(), _hit(document="产量口径.md", chunk="chunk-9", score=None)]))
    lines = text.splitlines()
    assert lines[0].startswith("背景（来自文档通道，仅作参考，不作为结论）")
    assert "产销存月报口径说明.md#chunk-7" in lines[1]
    assert "相关度 0.83" in lines[1]
    assert "产量口径.md#chunk-9" in lines[2]
    assert "相关度" not in lines[2]  # 没给分数就别编一个


def test_长摘录截断要留痕():
    long_text = "库存增量的口径说明。" * 200
    text = render_background(background_of([_hit(long_text)]))
    assert "…（已截断）" in text
    assert len(text) < len(long_text)

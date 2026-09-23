"""契约层测试：`status` 判定表、confidence 归并、模型硬约束。

判定表的每一行都来自《B2 接口设计与评审》§4.2，并用**录制的内核真实响应**做锚点，
避免"规则写完没人验"。
"""

from __future__ import annotations

import json
import pathlib

import pytest
from dip_contracts import (
    Answer,
    Evidence,
    EvidenceType,
    Result,
    ResultValue,
    Source,
    Status,
    ToolCall,
    derive_confidence,
    derive_status,
    derive_version,
    needs_human_check,
)
from pydantic import ValidationError

FIXTURE = pathlib.Path(__file__).parents[1] / "integrations/lineage-client/tests/fixtures/kernel-probe-2026-09-23.json"
REAL = json.loads(FIXTURE.read_text(encoding="utf-8"))["endpoints"]


# ------------------------------------------------------------------ status 判定表
@pytest.mark.parametrize(
    "signs,expected,why",
    [
        ({"kernel_ok": False}, Status.UNRESOLVED, "内核调用失败"),
        ({"resolved": False}, Status.UNRESOLVED, "字段血缘没解析出来"),
        ({"kb_version_matches": False}, Status.STALE, "知识库快照与平台记录不一致"),
        ({"chinese_source": "pending"}, Status.CANDIDATE, "待审核术语"),
        ({"fuzzy_match": True}, Status.CANDIDATE, "模糊命中"),
        ({"chinese_source": "exact_glossary", "confidence": 1.0}, Status.VERIFIED, "人工维护的词表"),
        (
            {"has_formula": True, "has_source_script": True, "confidence": 0.9},
            Status.VERIFIED,
            "脚本推导 + 来源明确",
        ),
        ({"has_lineage": True}, Status.INFERRED, "有链路没口径"),
        ({}, Status.UNRESOLVED, "什么都没有 → 证据不足"),
    ],
)
def test_derive_status_table(signs, expected, why):
    assert derive_status(**signs) is expected, why


def test_status_priority_failure_beats_glossary():
    """硬失败优先级最高：即使命中了词表，只要内核挂了也不能说 verified。"""
    assert derive_status(kernel_ok=False, chinese_source="exact_glossary", confidence=1.0) is Status.UNRESOLVED


def test_status_uses_real_recorded_values():
    """用真实响应里的字段值验证判定：两个口径命中都应为 verified，但理由不同。"""
    metrics = REAL["analyze"]["response"]["knowledge"]["metrics"]
    assert metrics, "录制响应里应有口径命中"
    statuses = [
        derive_status(
            chinese_source=m.get("chinese_source") or REAL["kb_search"]["response"]["groups"]["fields"][0]["chinese_source"],
            confidence=m.get("confidence"),
            has_formula=bool(m.get("formula")),
            has_source_script=bool(m.get("source_script")),
            has_lineage=True,
        )
        for m in metrics
    ]
    assert set(statuses) == {Status.VERIFIED}

    fields = REAL["kb_search"]["response"]["groups"]["fields"]
    ads_field = next(f for f in fields if f["table_name"] == "ads.ads_产销存月报" and f["column_name"] == "output_qty")
    assert ads_field["chinese_source"] == "exact_glossary" and ads_field["confidence"] == 1.0
    assert derive_status(chinese_source=ads_field["chinese_source"], confidence=ads_field["confidence"]) is Status.VERIFIED


# ------------------------------------------------------------- confidence / version
def test_confidence_takes_minimum_not_average():
    assert derive_confidence([1.0, 0.9, 0.4]) == 0.4, "链上最弱的一环决定整体可信度"
    assert derive_confidence([None, 0.8]) == 0.8
    assert derive_confidence([], fallback=0.0) == 0.0


def test_version_from_kb_snapshot():
    assert derive_version("1.0.0", "2026-09-20 20:17:25+0800") == "kb:1.0.0@2026-09-20"
    assert derive_version("1.0.0", None) == "kb:1.0.0"
    assert derive_version(None, "2026-09-20 20:17:25+0800") is None


def test_needs_human_check():
    assert needs_human_check(Status.VERIFIED, 0.95) is False
    assert needs_human_check(Status.VERIFIED, 0.6) is True, "置信度低于 0.7 必须提示人工确认"
    assert needs_human_check(Status.CANDIDATE, 1.0) is True
    assert needs_human_check(Status.UNRESOLVED, 0.0) is True


# ------------------------------------------------------------------ 模型硬约束
def test_no_evidence_means_no_conclusion():
    """铁律 1：无凭证不发布。"""
    with pytest.raises(ValidationError, match="铁律 1"):
        Result(value=ResultValue(type="formula", display="产量 = …"), status=Status.INFERRED, evidence=[])


def test_unresolved_cannot_carry_value():
    with pytest.raises(ValidationError, match="unresolved"):
        Result(
            value=ResultValue(type="formula", display="产量 = …"),
            status=Status.UNRESOLVED,
            evidence=[Evidence(type=EvidenceType.LINEAGE, ref="graph:ads.x", summary="上游 3 张")],
        )


def test_valid_result_and_answer():
    ev = Evidence(
        type=EvidenceType.METRIC,
        ref="metric:产量@kb:1.0.0",
        summary="v3 · verified · 分解 3 项",
        source=Source(kind="script", file="examples/warehouse/ads/ads_产销存月报.sql"),
        endpoint="POST /kb/search",
    )
    r = Result(
        value=ResultValue(type="formula", display="产量 = 打码量 + 跳码量 − 重码量"),
        confidence=0.93,
        status=Status.VERIFIED,
        evidence=[ev],
        source=ev.source,
        version="kb:1.0.0@2026-09-20",
    )
    a = Answer(
        answer_id="ans_1",
        text="产量来自 dwd 层口径……",
        result=r,
        suggestions=["改 output_qty 会砸哪些下游？", "dws 层的产量口径是什么？"],
        tool_calls=[ToolCall(name="upstream", args={"table": "ads.ads_产销存月报"}, ms=412, endpoint="POST /upstream")],
    )
    assert a.result.status is Status.VERIFIED
    assert a.mode == "rule"  # 默认规则模式（ADR-0004）
    assert a.result.source is not None and a.result.source.precise is False, "内核暂无行号 → 文件级（ADR-0003）"
    dumped = a.model_dump(mode="json")
    assert dumped["result"]["status"] == "verified", "序列化给前端的就是这个结构（设计说明书 §8）"


def test_too_many_suggestions_rejected():
    with pytest.raises(ValidationError, match="最多 3 条"):
        Answer(
            answer_id="ans_2",
            text="…",
            result=Result(status=Status.UNRESOLVED),
            suggestions=["1", "2", "3", "4"],
        )

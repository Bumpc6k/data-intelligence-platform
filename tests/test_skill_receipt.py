"""skill 回执（M1-03）的测试 —— 重点是 `usable` 必须出现在**序列化结果**里。

这条是回归测试：最初 `usable` 写成了普通 property，`model_dump_json()` 里根本没有它，
外壳拿到的 JSON 少一个判断依据，只能自己猜"什么算有证据"。后来端到端演示在断言
`payload["receipt"]["usable"]` 时直接 KeyError，才暴露出来。
"""

from __future__ import annotations

import json

import pydantic
import pytest
from dip_skills import SkillReceipt


def make(**overrides) -> SkillReceipt:
    base = {
        "skill": "lineage.analyze@1",
        "endpoint": "POST /upstream",
        "ms": 41,
        "evidence_count": 9,
        "ok": True,
        "attempts": 1,
        "http_status": 200,
    }
    base.update(overrides)
    return SkillReceipt(**base)


def test_usable_when_success_with_evidence():
    assert make().usable is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"ok": False, "error": "graph not found"},
        {"ok": False, "evidence_count": 9, "error": "内核报错但计数没清零"},
        {"evidence_count": 0},
        {"ok": False, "evidence_count": 0, "error": "连不上"},
    ],
)
def test_not_usable(overrides):
    """只要"没成功"或"没证据"，就不能拿去支撑结论（铁律 2）。"""
    assert make(**overrides).usable is False


def test_usable_is_present_in_serialized_json():
    """回归测试：`usable` 必须出现在外壳真正拿到的 JSON 里。"""
    payload = json.loads(make().model_dump_json())
    assert payload["usable"] is True
    assert json.loads(make(evidence_count=0).model_dump_json())["usable"] is False
    assert payload["evidence_count"] == 9
    assert payload["endpoint"] == "POST /upstream"


def test_receipt_is_read_only():
    receipt = make()
    with pytest.raises(pydantic.ValidationError):
        receipt.ms = 999


def test_negative_evidence_count_is_rejected():
    with pytest.raises(pydantic.ValidationError):
        make(evidence_count=-1)

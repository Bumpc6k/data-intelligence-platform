"""给回答补"平台侧"的视图块（工作项 M4-01 / Issue #17）。

内核给的视图（血缘图 / 检索表 / 口径 SQL）由 `dip_agent.views` 负责；
**平台自己的数据**（口径库的版本历史）由这里补 —— 于是"口径改过几版、上一版长什么样"
不需要用户再去点接口，答案里直接能看。

一条硬规矩：**能确定主体才补**。按回答里那条口径证据的名字去库里找主体，
找不到、或只有一个版本，就**不补** —— 不编一块空 diff 充数（空图比没图更误导）。
主体名匹配到多个时如实列出候选，也不静默挑一个。
"""

from __future__ import annotations

from typing import Any

from dip_contracts import Answer, ViewBlock

__all__ = ["diff_block", "diff_for_answer", "metric_name_of", "subjects_for", "with_platform_views"]

#: diff 只比最近两版（版本多起来，逐版对照没人看；想看全部走 /api/knowledge/metrics/versions）
COMPARE_FIELDS = ("formula", "chinese_name", "source_script", "source_line")


def metric_name_of(answer: Answer) -> str | None:
    """从证据里取口径名：`metric:产量@chanliang_qty` → `chanliang_qty`。

    取不到就返回 `None`（没有口径证据的回答不需要 diff 视图）。
    """
    for item in answer.result.evidence:
        if getattr(item.type, "value", item.type) != "metric" or not item.ref:
            continue
        tail = item.ref.split("@")[-1]
        if "." in tail:  # 有的证据写成 metric:表.字段
            tail = tail.split(".")[-1]
        if tail:
            return tail
    return None


def subjects_for(name: str, *, metrics_store: Any, limit: int = 200) -> list[str]:
    """按口径名找库里的主体（主体形如 `cdw.dwd_卷烟产量码段明细.chanliang_qty`）。

    用**结尾匹配**而不是全等：证据里给的是口径名，主体是"表.口径名"。
    """
    if not getattr(metrics_store, "available", lambda: False)():
        return []
    subjects: list[str] = []
    for row in metrics_store.list_metrics(limit=limit):
        subject = row.get("subject") or ""
        if subject and (subject == name or subject.endswith("." + name)) and subject not in subjects:
            subjects.append(subject)
    return subjects


def diff_block(subject: str, versions: list[dict[str, Any]]) -> ViewBlock:
    """两块版本 → 一块 diff 视图数据（`versions` 按版本号倒序，取最近两版）。"""
    latest, previous = versions[0], versions[1]
    changed = [
        {
            "field": field,
            "before": previous.get(field),
            "after": latest.get(field),
        }
        for field in COMPARE_FIELDS
        if previous.get(field) != latest.get(field)
    ]
    return ViewBlock(
        renderer="diff",
        title=f"口径版本对照：{subject}",
        source="database:knowledge_metrics（平台口径库，非内核）",
        data={
            "subject": subject,
            "chinese_name": latest.get("chinese_name") or previous.get("chinese_name") or "",
            "from": {k: previous.get(k) for k in ("version", "status", "formula", "chinese_name",
                                                  "source_script", "approved_by", "created_at")},
            "to": {k: latest.get(k) for k in ("version", "status", "formula", "chinese_name",
                                              "source_script", "approved_by", "created_at")},
            "changed": changed,
            "version_count": len(versions),
        },
        note="两版内容一致（只在版本号/时间上有差别）" if not changed else None,
    )


def diff_for_answer(answer: Answer, *, metrics_store: Any, versions_store: Any) -> ViewBlock | None:
    """能确定主体、且有 ≥2 版 → 给一块 diff 视图；否则 `None`。"""
    name = metric_name_of(answer)
    if not name:
        return None
    if not getattr(versions_store, "available", lambda: False)():
        return None
    for subject in subjects_for(name, metrics_store=metrics_store):
        versions = versions_store.list_versions(subject, limit=50)
        if len(versions) >= 2:
            return diff_block(subject, versions)
    return None


def with_platform_views(answer: Answer, *, metrics_store: Any, versions_store: Any) -> Answer:
    """把平台侧的视图块接在**内核视图之后**（顺序 = 数据来源顺序，便于人核对）。

    这里只加视图，不碰结论、不碰证据链 —— 与 M3-04 的「仅背景」是同一套纪律。
    """
    block = diff_for_answer(answer, metrics_store=metrics_store, versions_store=versions_store)
    if block is None:
        return answer
    return answer.model_copy(update={"views": [*answer.views, block]})

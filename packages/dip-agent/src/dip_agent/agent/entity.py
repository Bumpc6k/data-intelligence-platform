"""实体识别（W-114）——**平台自己做**，因为内核的 `/kb/ask` 会把整句当实体。

实测证据（`integrations/lineage-client/tests/fixtures/kernel-probe-2026-09-23.json`）：
问「ads.ads_产销存月报 的产量怎么来的」时内核返回 `entity="ads.ads_产销存月报 产量怎么来"`、`evidence.metrics=[]`。
所以这里实现四步（顺序即优先级）：

  1. 精确匹配 `schema.table` 与 `schema.table.column`（正则，零成本、确定）
  2. 中文名/业务词：先用内置词表剥离，再由上层（编排）用内核 `/kb/search` 反查确认
  3. 会话上下文回填：本句没提表时，沿用上一轮的表/字段
  4. 兜底：整句当关键词（保留内核的稳退行为，但标记 `confidence` 低）

不做模糊字符串匹配（同音、错字）——那属于 P2 的"实体消歧"，P1 宁可反问也不猜。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 表名：schema.table 或裸 table（要求含下划线或中文，避免把普通英文词当表）
TABLE_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_\u4e00-\u9fa5][\w\u4e00-\u9fa5]*)\b")
COLUMN_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b")

# 问题里的"废话词"：抽关键词时先去掉，剩下的才可能是业务名
STOPWORDS = (
    "怎么来的", "怎么算", "怎么算的", "从哪里来", "来自哪里", "的", "是", "请问", "帮我", "看一下", "查一下",
    "会砸谁", "影响", "下游", "上游", "血缘", "口径", "公式", "定义", "来源", "链路", "报告", "导出", "呢", "吗",
    "什么", "哪些", "如何", "为什么", "请", "下", "个", "这张表", "这个字段", "有", "和", "与", "以及", "？", "。",
    "还有", "一下", "看看", "告诉我", "这个", "那个", "它们", "一共", "总共", "几个", "多少", "具体", "相关",
    "情况", "数据", "问题", "帮忙", "可能", "是否", "可以", "能不能", "怎么样", "什么样",
)


@dataclass(frozen=True)
class Entities:
    """识别结果。`ambiguity` 非空时表示需要反问澄清，**不允许猜**。"""

    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    words: list[str] = field(default_factory=list)
    sql: str | None = None
    from_context: bool = False
    ambiguity: list[str] = field(default_factory=list)

    @property
    def table(self) -> str | None:
        return self.tables[0] if len(self.tables) == 1 else None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def looks_like_sql(text: str) -> bool:
    t = text.lower()
    return bool(re.search(r"\b(select|insert\s+(overwrite|into)|create\s+table|with\s+\w+\s+as)\b", t)) and " from " in t


def keywords(text: str) -> list[str]:
    """抽业务关键词：去掉废话词与标点，保留长度 ≥2 的片段。"""
    s = text
    for w in sorted(STOPWORDS, key=len, reverse=True):
        s = s.replace(w, " ")
    s = re.sub(r"[^\w\u4e00-\u9fa5]+", " ", s)
    parts = [p.strip() for p in s.split() if len(p.strip()) >= 2]
    return _dedupe(parts)


def extract(text: str, *, context: dict | None = None) -> Entities:
    """从一句话里抽出表/字段/关键词；`context` 为上一轮结果（会话上下文回填）。"""
    ctx = context or {}
    if looks_like_sql(text):
        return Entities(sql=text, words=keywords(text))

    columns = _dedupe(COLUMN_RE.findall(text))
    fully_qualified = {c.rsplit(".", 1)[0] for c in columns}
    tables = _dedupe([t for t in TABLE_RE.findall(text) if t not in columns and t not in fully_qualified])

    # 上下文回填：本句没有表，但上一轮有 → 沿用（"它/这个字段"类追问）
    from_context = False
    if not tables and not columns and ctx.get("tables"):
        tables = list(ctx["tables"])
        from_context = True
        if ctx.get("columns") and not columns:
            columns = list(ctx["columns"])

    ent = Entities(
        tables=tables,
        columns=columns,
        words=keywords(text),
        from_context=from_context,
    )
    if len(tables) > 1:
        ent = Entities(**{**ent.__dict__, "ambiguity": [f"提到多张表：{'、'.join(tables)}，请指明一张"]})
    return ent

def pick_topic(ent: Entities) -> str | None:
    """挑「业务主题词」——口诀：优先中文业务词，并排除表名的组成部分。

    实测：`ads.ads_产销存月报 的产量怎么来的？` 的分词是 ["ads", "ads_产销存月报", "产量"]，
    取 words[0] 会得到 "ads"（schema 名），于是检索词变成 "ads"，命中一堆无关口径。
    """
    def inside_table(w: str) -> bool:
        return any(w in t for t in ent.tables)

    def has_cjk(w: str) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in w)

    cjk = [w for w in ent.words if has_cjk(w) and not inside_table(w)]
    if cjk:
        return cjk[0]
    rest = [w for w in ent.words if not inside_table(w)]
    return rest[0] if rest else None


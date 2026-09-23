"""证据装配 + status 判定（W-103）——**含金量最高的一步**。

三件事（《B2 接口设计与评审》§4.3）：

1. **打分挑选**：一次检索可能回来 72 条命中，必须挑出能支撑本次结论的那几条。
2. **跨层拼装**：``ads.ads_产销存月报.output_qty`` 只有中文名与"透传"血缘，**公式在 dwd 层**。
   只取"本表命中"就会答成"产量就是上游透传"——错。这里负责把三层证据串成一条链。
3. **保守定级**：多来源时 `status` 取**最坏**的一个（一条弱证据足以让整句结论不该被深信），
   `confidence` 取链上**最小值**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dip_contracts import Evidence, EvidenceType, Source, Status, derive_confidence, derive_status
from dip_contracts.kernel import ToolResult

from .entity import Entities

LAYER_WEIGHT = {"ads": 15, "dws": 10, "dwd": 8, "ods": 5, "dim": 3}
SOURCE_WEIGHT = {"exact_glossary": 30, "glossary": 30, "rule": 15, "builtin": 5, "pending": 0}
PASSTHROUGH_RE = re.compile(r"^\s*\w+\.(\w+)\s+AS\s+\w+\s*$", re.IGNORECASE)
SEVERITY = {Status.VERIFIED: 0, Status.INFERRED: 1, Status.CANDIDATE: 2, Status.STALE: 3, Status.UNRESOLVED: 4}


@dataclass
class Findings:
    topic: str | None = None
    table: str | None = None
    formula: str | None = None
    formula_full: str | None = None
    formula_table: str | None = None
    formula_confidence: float | None = None
    glossary: dict | None = None
    passthrough: str | None = None
    layers: list[str] = field(default_factory=list)
    lineage: dict | None = None
    report: dict | None = None
    evidence: list[Evidence] = field(default_factory=list)
    status: Status = Status.UNRESOLVED
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------- 打分


def score_field(hit: dict, entities: Entities, topic: str | None) -> int:
    s = 0
    if entities.table and hit.get("table_name") == entities.table:
        s += 100
    if topic and hit.get("chinese_name") == topic:
        s += 40
    s += LAYER_WEIGHT.get(hit.get("layer") or "", 0)
    s += SOURCE_WEIGHT.get(hit.get("chinese_source") or "", 0)
    return s


def business_tokens(formula: str | None) -> set[str]:
    """公式右侧的"业务字段"集合（去掉聚合函数、括号、运算符、数字）。"""
    if not formula or "=" not in formula:
        return set()
    rhs = formula.split("=", 1)[1]
    for ch in "()[],+-*/":
        rhs = rhs.replace(ch, " ")
    out: set[str] = set()
    for token in rhs.split():
        low = token.lower()
        if low in DEGENERATE_TOKENS or token.replace(".", "").isdigit():
            continue
        out.add(token)
    return out


def is_wrapper_formula(formula: str | None, name: str | None) -> bool:
    """「包装口径」：右侧只有一个字段、且它自己就带着指标名（`产量 = SUM(总产量)`）。

    这类不是业务定义，而是把同一个量再聚合一次；实测 ads 层有若干条，排序必须让它靠后。
    """
    tokens = business_tokens(formula)
    return len(tokens) == 1 and any(name and name in t for t in tokens)


def score_metric(hit: dict, entities: Entities, topic: str | None) -> int:
    s = 0
    if entities.table and hit.get("table_name") == entities.table:
        s += 100
    if topic and hit.get("chinese_name") == topic:
        s += 40
    if hit.get("formula"):
        s += 25  # "怎么算"类问题必需
    # 口径"信息量"加权：真正做计算的公式（≥2 个业务字段）优先于单字段包装
    n = len(business_tokens(hit.get("formula")))
    if n >= 2:
        s += 20
    elif is_wrapper_formula(hit.get("formula"), hit.get("chinese_name")):
        s -= 10
    s += LAYER_WEIGHT.get(hit.get("layer") or "", 0)
    return s


DEGENERATE_TOKENS = {"sum", "round", "nullif", "cast", "as", "distinct"}


def is_degenerate_formula(formula: str | None, name: str | None) -> bool:
    """剔除「同义反复」口径：`产量 = SUM(产量)` 这类只把指标自己再包一层，
    不是业务口径（实测 ads 层有若干条这样的），当作答案会让用户以为查到了计算逻辑。"""
    tokens = business_tokens(formula)
    return bool(tokens) and all(t == name for t in tokens)


def qualifies(hit: dict, entities: Entities) -> bool:
    """命中是否**真的对得上**本次提问的实体。

    这是防「编答案」的关键闸门：内核检索对任何字符串都会返回一堆相似命中
    （实测「产量」返回 72 条），不过滤的话用户问「你好」也可能被凑出一个像模像样的结论。
    """
    words = {w for w in entities.words if len(w) >= 2}
    names = {hit.get("chinese_name"), hit.get("column_name"), hit.get("metric_name"), hit.get("term")}
    name_match = bool(words & {n for n in names if n}) or any(
        w and w in (hit.get("chinese_name") or "") for w in words
    )
    if name_match:
        return True
    # 只有表名匹配、但业务名对不上（如问「产量」命中的却是「产销率」）→ 不算证据
    if entities.table and hit.get("table_name") == entities.table and not words:
        return True
    return any(c.endswith(f".{hit.get('column_name')}") for c in entities.columns)


def _groups(results: list[tuple[object, ToolResult]], entities: Entities) -> list[dict]:
    """把多次 /kb/search 的命中合并成一个池子（带来源工具便于审计）。"""
    pool: list[dict] = []
    for step, res in results:
        if not res.ok or not str(getattr(step, "tool", "")) == "search":
            continue
        groups = (res.data or {}).get("groups") or {}
        for kind in ("fields", "metrics", "tables", "terms", "rules"):
            for hit in groups.get(kind) or []:
                if kind in {"fields", "metrics"} and not qualifies(hit, entities):
                    continue  # 与提问实体对不上的命中不作为证据
                pool.append({**hit, "_kind": kind, "_endpoint": res.endpoint})
    return pool


def _structural_evidence(results: list[tuple[object, ToolResult]], findings: Findings) -> None:
    """结构性证据（链路 / 报告）总是保留——它们是"可点开看得见"的凭证。"""
    for step, res in results:
        tool = str(getattr(step, "tool", ""))
        if not res.ok:
            continue
        if tool == "upstream":
            findings.lineage = res.data
            tables = res.data.get("tables") or []
            findings.layers = _layers(tables)
            findings.evidence.append(
                Evidence(
                    type=EvidenceType.LINEAGE,
                    ref=f"graph:{res.data.get('start_table')}",
                    summary=f"上游 {res.data.get('upstream_count')} 张 / {res.data.get('edge_count')} 条边",
                    endpoint=res.endpoint,
                )
            )
        elif tool == "impact":
            findings.lineage = res.data
            findings.evidence.append(
                Evidence(
                    type=EvidenceType.LINEAGE,
                    ref=f"graph:{res.data.get('start_table')}#downstream",
                    summary=f"下游 {res.data.get('downstream_count')} 张 / {res.data.get('edge_count')} 条边",
                    endpoint=res.endpoint,
                )
            )
        elif tool == "analyze":
            findings.report = res.data.get("report")
            if findings.report:
                findings.evidence.append(
                    Evidence(
                        type=EvidenceType.REPORT,
                        ref=f"report:{findings.report.get('report_id')}",
                        summary=f"HTML 报告 · {round((findings.report.get('size_bytes') or 0) / 1024)} KB",
                        source=Source(kind="report", report_id=findings.report.get("report_id")),
                        endpoint=res.endpoint,
                    )
                )
            findings.passthrough = _passthrough(res.data, findings)


def _layers(tables: list) -> list[str]:
    """从上游/下游清单里推导出层次链（元素可能是字符串，也可能是 {"name": ...} 字典）。"""
    order = ["ods", "dwd", "dws", "ads", "dim"]
    names: set[str] = set()
    for t in tables or []:
        raw = t.get("name") if isinstance(t, dict) else t
        if not raw:
            continue
        # 真实数据里 schema 是 cdw/dim 这类，层次藏在表名里：cdw.dws_产销存汇总 → dws
        leaf = str(raw).split(".")[-1].strip().lower()
        head = leaf.split("_")[0]
        if head in order:
            names.add(head)
        else:
            schema = str(raw).split(".")[0].strip().lower()
            if schema in order:
                names.add(schema)
    return [lvl for lvl in order if lvl in names]


def _passthrough(analyze_data: dict, findings: Findings) -> str | None:
    """识别"本表只是透传"的字段（表达式形如 s.output_qty AS output_qty），并记录上游口径位置。"""
    for entry in analyze_data.get("column_lineage") or []:
        if findings.table and entry.get("target_table") != findings.table:
            continue
        m = PASSTHROUGH_RE.match(entry.get("expression") or "")
        if m and m.group(1) == entry.get("target_column"):
            return (
                f"本表 {entry['target_table']}.{entry['target_column']} 是上游透传"
                f"（表达式 {entry['expression'].strip()}），计算逻辑不在本脚本里"
            )
    return None


# --------------------------------------------------------------- 主装配


def assemble(
    results: list[tuple[object, ToolResult]],
    entities: Entities,
    *,
    intents: list[str] | None = None,
    topic: str | None = None,
    content_limit: int = 3,
) -> Findings:
    """把若干工具结果装配成 `Findings`（含 evidence / status / confidence）。"""
    f = Findings(table=entities.table, topic=topic)
    intents = intents or []
    _structural_evidence(results, f)

    pool = _groups(results, entities)
    fields = sorted([h for h in pool if h["_kind"] == "fields"], key=lambda h: -score_field(h, entities, topic))
    metrics = sorted(
        [
            h
            for h in pool
            if h["_kind"] == "metrics"
            and h.get("formula")
            and not is_degenerate_formula(h.get("formula"), h.get("chinese_name"))
        ],
        key=lambda h: -score_metric(h, entities, topic),
    )

    # ① 本表字段的词表命中（提供中文名与来源）
    if fields:
        best = fields[0]
        f.glossary = best
        f.topic = f.topic or best.get("chinese_name")
        f.evidence.append(
            Evidence(
                type=EvidenceType.DICT,
                ref=f"dict:{best.get('table_name')}.{best.get('column_name')}",
                summary=f"{best.get('chinese_name')}（词表命中 · 置信度 {best.get('confidence')}）",
                source=Source(kind="kernel", file=(best.get("source_files") or [None])[0]),
                endpoint=best.get("_endpoint"),
            )
        )

    # ② 公式口径：**允许来自别的层**（跨层拼装的关键）
    for cand in metrics:
        if f.table and cand.get("table_name") == f.table and f.glossary and not f.formula:
            pass  # 本表若有公式也接受，但优先级低于下面的外部公式（先到先得，见排序）
        if f.formula is None:
            f.formula = cand.get("formula")
            f.formula_full = cand.get("formula_full")
            f.formula_table = cand.get("table_name")
            f.formula_confidence = cand.get("confidence")
            f.evidence.append(
                Evidence(
                    type=EvidenceType.METRIC,
                    ref=f"metric:{cand.get('chinese_name')}@{cand.get('metric_name')}",
                    summary=f"{cand.get('formula')} · 口径在 {cand.get('table_name')}",
                    source=Source(kind="script", file=cand.get("source_script")),
                    endpoint=cand.get("_endpoint"),
                )
            )
            if cand.get("table_name") != f.table and f.table:
                f.notes.append(f"公式不在 {f.table} 本层，而在 {cand.get('table_name')}（{cand.get('layer')} 层）")
            break

    if f.passthrough:
        f.notes.append(f.passthrough)
    if f.layers:
        f.notes.append("上游层次：" + " → ".join(f.layers))

    # ③ 证据裁剪：内容类证据最多 content_limit 条（结构性证据不受限）
    structural = [e for e in f.evidence if e.type in {EvidenceType.LINEAGE, EvidenceType.REPORT}]
    content = [e for e in f.evidence if e.type not in {EvidenceType.LINEAGE, EvidenceType.REPORT}][:content_limit]
    f.evidence = structural + content

    # ④ status / confidence：保守定级
    statuses = [
        derive_status(
            chinese_source=(f.glossary or {}).get("chinese_source"),
            confidence=(f.glossary or {}).get("confidence"),
        )
        if f.glossary
        else None
    ]
    if f.formula:
        statuses.append(
            derive_status(has_formula=True, has_source_script=True, confidence=f.formula_confidence, has_lineage=bool(f.lineage))
        )
    if f.lineage and not f.formula and not f.glossary:
        # 纯血缘图事实（/upstream、/impact）：图由真实脚本生成 → verified（不是推理）。
        # 若命中过字段/指标却拿不到公式，仍按 §4.2 记 inferred（口径未定）。
        statuses.append(Status.VERIFIED if not f.glossary else derive_status(has_lineage=True))
    valid = [s for s in statuses if s is not None]
    # 补 §4.2 的一行（真机演示后补）：只有血缘图事实、没有任何口径/词表参与时，
    # 它是从真实脚本生成的确定事实，不是推理 → verified。
    if not valid and f.lineage and all(e.type is EvidenceType.LINEAGE for e in f.evidence):
        valid = [Status.VERIFIED]
        f.notes.append("结论直接来自血缘图（由真实脚本生成），非模型推理")
    f.status = combine_status(valid)
    f.confidence = derive_confidence(
        [(f.glossary or {}).get("confidence"), f.formula_confidence, 0.9 if f.lineage else None]
    )
    return f


def combine_status(statuses: list[Status]) -> Status:
    """保守合并：取**最坏**的一个（顺序 verified < inferred < candidate < stale < unresolved）。"""
    if not statuses:
        return Status.UNRESOLVED
    return max(statuses, key=lambda s: SEVERITY[s])

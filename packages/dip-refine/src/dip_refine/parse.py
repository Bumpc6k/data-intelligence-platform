"""解析：把项目里的脚本交给**内核**解析，拿到结构（表 / 字段 / 语句），作为后续两步的地基。

工作项 M3-05 / Issue #16。这一步刻意只做一件事：**拿到"这个脚本里真实存在什么"**，
后面的 LLM 初筛与规则校验都以此为白名单 —— 模型不许编，规则也不许。

三条约定：

1. **只按接口调用**（AGENTS 铁律 4）：解析一律走内核 `/parse`，不自己写 SQL 解析器。
   为什么用 `/parse` 而不是 `/analyze`：`/analyze` 会顺手往内核落盘一份 HTML 报告（AGENTS §8 提过），
   提炼要跑几十个脚本，没必要留几十份报告。
2. **协议只声明用到的那一个方法**：`dip-contracts` 是冻结资产，不为这个 Issue 往里加接口；
   这里定义一个只含 `parse` 的窄协议，`lineage_client.LineageClient` 天然满足它（结构化类型）。
3. **来源要能追**：每条候选的来源是「脚本路径 + 语句序号」，路径统一记录成**相对项目根**的形式，
   与内核自己的 `source_file` 形状一致（`examples/warehouse/ads/ads_产销存月报.sql`），
   这样将来入库时两边指得到同一份文件。
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from dip_contracts.guards import NUMBER_RE, normalize_number
from dip_contracts.kernel import ToolResult

#: `别名.字段` / `库.表` 这类"点号连接"的写法（Unicode 感知；中文表名一样认）
ALIAS_RE = re.compile(r"[\w\u4e00-\u9fff]+\.[\w\u4e00-\u9fff]+", re.UNICODE)


@runtime_checkable
class SqlParser(Protocol):
    """只需要一个方法：把 SQL 文本解析成结构。实现者是 `lineage_client.LineageClient`。"""

    def parse(self, sql: str, dialect: str = "hive") -> ToolResult: ...


@dataclass(frozen=True)
class ParsedStatement:
    """一条语句的结构（沿用内核的 `statement_index` 作为"来源行"，从 1 数起）。"""

    index: int
    task_type: str
    sql: str
    input_tables: tuple[str, ...] = ()
    output_tables: tuple[str, ...] = ()
    columns: tuple[tuple[str, str, str], ...] = ()      # (target_column, expression, source_table)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(sorted({c[0] for c in self.columns if c[0]}))


@dataclass(frozen=True)
class ParsedScript:
    """一个脚本的解析结果。`source_script` 是**相对项目根**的路径（与内核 `source_file` 同形状）。"""

    source_script: str
    path: str
    dialect: str = "hive"
    statements: tuple[ParsedStatement, ...] = ()
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def tables(self) -> tuple[str, ...]:
        found: list[str] = []
        for st in self.statements:
            for table in (*st.input_tables, *st.output_tables):
                if table not in found:
                    found.append(table)
        return tuple(found)

    @property
    def fields(self) -> tuple[str, ...]:
        found: list[str] = []
        for st in self.statements:
            for name in st.column_names:
                if name not in found:
                    found.append(name)
        return tuple(found)


def sql_files(project: pathlib.Path, *, limit: int | None = None) -> list[pathlib.Path]:
    """项目下的 SQL 脚本（按路径排序，保证报告可复现）。"""
    files = sorted(p for p in project.rglob("*.sql") if p.is_file())
    return files[:limit] if limit else files


def relative_source(path: pathlib.Path, project_root: pathlib.Path) -> str:
    """来源脚本的标识：相对项目根的路径（尽量与内核 `source_file` 一致）。"""
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def parse_script(
    path: pathlib.Path,
    *,
    project_root: pathlib.Path,
    parser: SqlParser,
    dialect: str = "hive",
) -> ParsedScript:
    """解析单个脚本。**解析失败也是结果**（记下 error，继续跑别的脚本 —— 一个坏文件不该拖停整条流水线）。"""
    source = relative_source(path, project_root)
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - 读不到就如实记，不中断
        return ParsedScript(source_script=source, path=str(path), dialect=dialect, error=f"读取失败：{exc}")

    result = parser.parse(text, dialect=dialect)
    if not result.ok:
        return ParsedScript(source_script=source, path=str(path), dialect=dialect,
                            error=f"内核解析失败：{result.error}", raw=result.data)

    raw = result.data
    statements: list[ParsedStatement] = []
    for item in raw.get("statements") or []:
        columns: list[tuple[str, str, str]] = []
        for col in item.get("column_lineage") or []:
            columns.append((
                str(col.get("target_column") or ""),
                str(col.get("expression") or ""),
                str(col.get("source_table") or ""),
            ))
        statements.append(
            ParsedStatement(
                index=int(item.get("statement_index") or len(statements) + 1),
                task_type=str(item.get("task_type") or ""),
                sql=str(item.get("sql") or ""),
                input_tables=tuple(str(t) for t in (item.get("input_table_names") or item.get("input_tables") or [])),
                output_tables=tuple(str(t) for t in (item.get("output_table_names") or item.get("output_tables") or [])),
                columns=tuple(columns),
            )
        )
    return ParsedScript(source_script=source, path=str(path), dialect=dialect,
                        statements=tuple(statements), raw=raw)


def parse_project(
    project: pathlib.Path,
    *,
    parser: SqlParser,
    project_root: pathlib.Path | None = None,
    dialect: str = "hive",
    limit: int | None = None,
) -> list[ParsedScript]:
    root = project_root or project
    return [parse_script(p, project_root=root, parser=parser, dialect=dialect)
            for p in sql_files(project, limit=limit)]


def whitelist_of(scripts: Iterable[ParsedScript]) -> dict[str, set[str]]:
    """把解析结果收成"这个项目里真实存在的表、字段、数字，以及允许被引用的写法"。

    四个集合，用途不同（别混）：

    | 集合 | 用途 | 为什么单独一份 |
    | --- | --- | --- |
    | `tables` / `fields` | 核对 `subject` 与 `depends_on`（**要入库的字段，必须精确**） | 只认真实表与真实字段 |
    | `numbers` | 核对自由文本里的数字 | SQL 里本来就有的 `NULLIF(x, 0)` 的 0 不算编造 |
    | `quote_ok` | 核对模型**自由文本**（理由）里引用的标识符 | 模型复述 SQL 原文会写成 `s.sale_qty` 这种「别名.字段」，那不是编造；凭空造出来的 `x.y` 才算 |

    试跑时真踩过两个假红：数字白名单为空导致 `NULLIF(x, 0)` 被判编造；
    只给真实表名导致模型复述 `s.output_qty` 被判编造表名。两份白名单分别治这两个。
    """
    tables: set[str] = set()
    fields: set[str] = set()
    numbers: set[str] = set()
    quoted: set[str] = set()
    for script in scripts:
        tables.update(script.tables)
        fields.update(script.fields)
        for statement in script.statements:
            for text in (statement.sql, *(expr for _, expr, _ in statement.columns)):
                quoted.update(ALIAS_RE.findall(text))
                for match in NUMBER_RE.findall(text):
                    numbers.add(normalize_number(match))
    return {"tables": tables, "fields": fields, "numbers": numbers,
            "quote_ok": tables | quoted}

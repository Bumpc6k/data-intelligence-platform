"""数据库访问层（PostgreSQL）：防火墙判定留痕 + 知识候选池 + 口径版本与回滚 + 既有审计。

**为什么单独一个包**：判定留痕有两个消费者 —— 防火墙服务要写、portal-api 的 `/api/audit`
要读。表结构定义在两处会立刻分叉，所以放在这里，两边都只 import 它。
（M3-01 的候选池与入库落点同理：接口层写、验收与后续流水线读。）

**按模块分文件**：`store.py` 判定留痕（#9）、`knowledge.py` 候选池与口径库（#12）、
`metric_versions.py` 版本与回滚（#13）。
按 Issue 分文件而不是按表分，是为了让"后来者只新增一个文件"成为默认做法
（#13 也改了 `knowledge.py`，因为版本号规则就长在 `ingest` 那一步，硬拆成两个文件反而要跨文件读）。

**本包只新增表，不改既有表**（Issue #9 / #12 / #13 的边界）：`sessions` / `messages` / `audit_log`
仍由 `portal_api.store` 负责，这里一个字都不动。
"""

from . import knowledge, metric_versions  # noqa: F401  —— 知识（M3-01 / #12）与版本回滚（M3-02 / #13）
from .knowledge import init_knowledge_schema  # noqa: F401
from .store import (  # noqa: F401
    JUDGMENT_SCHEMA,
    Persisted,
    available,
    connect,
    init_schema,
    list_judgments,
    record_judgment,
    record_token_event,
)

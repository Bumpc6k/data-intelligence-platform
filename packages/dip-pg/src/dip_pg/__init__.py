"""数据库访问层（PostgreSQL）：防火墙判定留痕 + 既有审计。

**为什么单独一个包**：判定留痕有两个消费者 —— 防火墙服务要写、portal-api 的 `/api/audit`
要读。表结构定义在两处会立刻分叉，所以放在这里，两边都只 import 它。

**本包只新增表，不改既有表**（Issue #9 的边界）：`sessions` / `messages` / `audit_log`
仍由 `portal_api.store` 负责，这里一个字都不动。
"""

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

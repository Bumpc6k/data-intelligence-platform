"""审批令牌（M2-01 的最小实现）。

只做"能校验"这件事：**签发 → 绑动作指纹 → 一次一用**。

刻意没做的（留给 M2-02，别拿这个当完整实现）：
- **时效**：这里没有 5 分钟过期；令牌签发后一直有效，直到被用掉。
- **指纹规范化**：指纹算法就 `policy.action_fingerprint` 那个 strip + 拼接版本。

没有时效的令牌是**不完整**的，M2-02 补上之前不要把它当安全边界之外的东西用。
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

#: 令牌长度（URL 安全字符）。32 字节熵足够，且短到能贴进审批消息里。
_TOKEN_BYTES = 32


@dataclass
class ApprovalToken:
    """一张已签发的审批令牌。"""

    value: str
    fingerprint: str
    actor: str
    issued_at: float
    used_at: float | None = None

    @property
    def used(self) -> bool:
        return self.used_at is not None


class TokenStore:
    """进程内的令牌表。

    放内存里是有意的：令牌状态**不该**跟着业务服务重启一起丢，但也不该在 P1
    阶段就塞进数据库（那是 M2-03 落库要解决的事，而且落的是**判定**不是令牌）。
    代价说清楚：防火墙服务重启 → 已签发未使用的令牌全部失效（审批人需要重新确认一次）。
    这是 fail-closed 的方向，可以接受。
    """

    def __init__(self) -> None:
        self._tokens: dict[str, ApprovalToken] = {}

    def issue(self, *, actor: str, fingerprint: str) -> ApprovalToken:
        """审批通过后签发一张令牌。这一步对应"上一级确认一次"。"""
        token = ApprovalToken(
            value=secrets.token_urlsafe(_TOKEN_BYTES),
            fingerprint=fingerprint,
            actor=actor,
            issued_at=time.time(),
        )
        self._tokens[token.value] = token
        return token

    def verify(self, *, value: str, fingerprint: str) -> tuple[bool, str]:
        """校验一张令牌能不能用于当前这个动作。校验通过**即消费掉**（一次性）。

        返回 `(是否放行, 原因)`。原因串会进日志与审计，所以写成人能看懂的话。
        """
        token = self._tokens.get(value)
        if token is None:
            return False, "令牌不存在（可能已用过，或不是本服务签发的）"
        if token.used:
            return False, f"令牌已使用过（一次性，不允许复用；上次使用 actor={token.actor}）"
        if token.fingerprint != fingerprint:
            return False, "令牌与当前动作不匹配（指纹不一致：令牌是给另一个动作签的）"

        token.used_at = time.time()
        return True, "ok"

    def __len__(self) -> int:
        return len(self._tokens)

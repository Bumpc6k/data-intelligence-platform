"""审批令牌（M2-02）：**一次性 + 绑动作指纹 + 限时**。

三件事缺一不可，各自的理由：

| 性质 | 防的是什么 | 实现 |
| --- | --- | --- |
| 一次性 | 同一张令牌被重放（日志里翻出来、消息里被转发） | 校验通过即置 `used_at`，之后一律拒 |
| 绑指纹 | 审批被"挪用"（批的是 A 表，去改 B 表） | 指纹含动作 + 目标 + SQL；改一个字符即失效 |
| 时效 | 令牌被搁置后翻出来用（半年前的审批） | 默认 5 分钟过期，过期即作废 |

**时钟是注入的**：`clock` 参数默认真实时钟，测试里换成假的 —— 否则验"5 分钟过期"就得
`sleep(300)`，那种测试没人会跑第二遍。
"""

from __future__ import annotations

import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

#: 令牌长度（URL 安全字符）。32 字节熵足够，且短到能贴进审批消息里。
_TOKEN_BYTES = 32

#: 默认时效：5 分钟（Issue #8 明确要求）。
DEFAULT_TTL_SECONDS = 300


def ttl_seconds() -> int:
    """从环境读时效，默认 5 分钟。改大它等于削弱令牌，别随手调。"""
    raw = os.environ.get("FIREWALL_TOKEN_TTL_SECONDS")
    if raw is None:
        return DEFAULT_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"FIREWALL_TOKEN_TTL_SECONDS 必须是整数，得到 {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"FIREWALL_TOKEN_TTL_SECONDS 必须为正数，得到 {value}")
    return value


@dataclass
class ApprovalToken:
    """一张已签发的审批令牌。"""

    value: str
    fingerprint: str
    actor: str
    issued_at: float
    ttl_seconds: int
    used_at: float | None = None

    @property
    def used(self) -> bool:
        return self.used_at is not None

    def expired_at(self, now: float) -> bool:
        return now - self.issued_at >= self.ttl_seconds

    def remaining_seconds(self, now: float) -> float:
        return max(0.0, self.ttl_seconds - (now - self.issued_at))


class TokenStore:
    """进程内的令牌表。

    放内存里是有意的：令牌状态**不该**跟着业务服务重启一起丢，但也不该在 M2 阶段就塞进
    数据库（M2-03 落的是**判定**，不是令牌）。代价说清楚：防火墙服务重启 → 已签发未使用的
    令牌全部失效（审批人需要重新确认一次）。这是 fail-closed 的方向，可以接受。
    """

    def __init__(self, *, ttl_seconds: int | None = None, clock: Callable[[], float] = time.time) -> None:
        self._tokens: dict[str, ApprovalToken] = {}
        self._ttl = DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        self._clock = clock

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def issue(self, *, actor: str, fingerprint: str) -> ApprovalToken:
        """审批通过后签发一张令牌。这一步对应"上一级确认一次"。"""
        token = ApprovalToken(
            value=secrets.token_urlsafe(_TOKEN_BYTES),
            fingerprint=fingerprint,
            actor=actor,
            issued_at=self._clock(),
            ttl_seconds=self._ttl,
        )
        self._tokens[token.value] = token
        return token

    def verify(self, *, value: str, fingerprint: str) -> tuple[bool, str]:
        """校验一张令牌能不能用于当前这个动作。校验通过**即消费掉**（一次性）。

        返回 `(是否放行, 原因)`。原因串会进日志与审计，所以写成人能看懂的话。

        **检查顺序是有意的**：不存在 → 已用过 → 已过期 → 指纹不符。
        一张已经用过的令牌，无论后来是否过期，都该报"已用过"（那才是它被拒的真正原因）；
        而"指纹不符"的拒绝**不消费**令牌 —— 挪用的尝试不该把审批人对原动作的授权弄没。
        """
        now = self._clock()
        token = self._tokens.get(value)
        if token is None:
            return False, "令牌不存在（可能已用过，或不是本服务签发的）"
        if token.used:
            return False, f"令牌已使用过（一次性，不允许复用；上次使用 actor={token.actor}）"
        if token.expired_at(now):
            elapsed = int(now - token.issued_at)
            return False, f"令牌已过期（签发于 {elapsed} 秒前，时效 {token.ttl_seconds} 秒）"
        if token.fingerprint != fingerprint:
            return False, "令牌与当前动作不匹配（指纹不一致：令牌是给另一个动作签的）"

        token.used_at = now
        return True, "ok"

    def info(self, *, value: str) -> ApprovalToken | None:
        """查一张令牌的状态（不消费）。给审计与排障用。"""
        return self._tokens.get(value)

    def __len__(self) -> int:
        return len(self._tokens)

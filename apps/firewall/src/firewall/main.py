"""防火墙服务（M2-01）：判定 + 令牌校验，独立的 HTTP 进程。

起法：`bash ops/start-firewall.sh`（默认 `127.0.0.1:18210`）

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET  | `/health`        | 策略表版本/指纹/默认档/规则清单 |
| POST | `/judge`         | 判定：身份 + 权限 + 动作 + 目标 → 档位 + 处置 |
| POST | `/tokens/issue`  | 审批通过后签发令牌（"上一级确认一次"） |
| POST | `/verify`        | 校验令牌能否用于当前动作（校验通过即消费掉） |

两个刻意的设计决定，都写在这里免得后面有人改掉：

1. **校验不通过返回 403，不是 `200 + {"ok": false}`。** 调用方漏看响应体时，
   状态码本身还能拦住一半；安全边界上的接口不该把"没过"表达成一个成功的响应。
2. **`/judge` 永远返回 200**（包括 `deny`）。拒绝是**判定结论**，不是接口错误 ——
   把它做成 4xx 会让调用方想重试。
"""

from __future__ import annotations

import contextlib
import os
import pathlib
from dataclasses import dataclass

import dip_pg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .policy import Policy, PolicyError
from .tokens import TokenStore, ttl_seconds

_DEFAULT_POLICY = pathlib.Path(__file__).parents[2] / "policies" / "default.yaml"


@dataclass(frozen=True)
class LoadedPolicy:
    """当前生效的策略表 + 它的指纹。"""

    policy: Policy
    digest: str
    path: str


_cache: dict[str, tuple[float, LoadedPolicy]] = {}


def policy_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("FIREWALL_POLICY", str(_DEFAULT_POLICY)))


def load_policy() -> LoadedPolicy:
    """读策略表；**文件变了就自动重载**（改策略不用重启进程）。

    指纹（digest）跟着内容走：判定结果里带着它，事后能回答"当时用的是哪一版策略"。
    """
    path = policy_path()
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f"策略表不存在：{path}") from exc

    cached = _cache.get(str(path))
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        policy = Policy.load(path)
    except PolicyError as exc:
        raise HTTPException(status_code=500, detail=f"策略表不可用：{exc}") from exc

    loaded = LoadedPolicy(
        policy=policy,
        digest=policy.digest,  # 指纹由 Policy 自己算（单一真相，别在两处各算一遍）
        path=str(path),
    )
    _cache[str(path)] = (mtime, loaded)
    return loaded


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    # 策略表坏了就别起来 —— 一个"判定不了但活着"的防火墙比没有防火墙更危险。
    load_policy()
    # 数据库坏了**不影响服务起来**：判定照做，只是不留痕（Issue #9 的验收）。
    try:
        dip_pg.init_schema()
    except Exception as exc:  # noqa: BLE001
        print(f"[firewall] 数据库不可用，判定将不落库：{exc}")
    yield


app = FastAPI(title="数据智能平台 · firewall", version="0.1.0", lifespan=lifespan)
# 时效从环境读：环境写错就直接抛（ValueError），假装按默认值跑等于把时效悄悄换掉。
tokens = TokenStore(ttl_seconds=ttl_seconds())


class JudgeRequest(BaseModel):
    actor: str = Field(min_length=1, description="谁要做这件事")
    action: str = Field(min_length=1, description="动作名，如 select / insert / 上线工作流到生产")
    roles: list[str] = Field(default_factory=list, description="调用方声明的角色（用于带角色的规则）")
    target: str | None = Field(default=None, description="目标对象，如表名 prod.fact_sales")
    sql: str | None = Field(default=None, description="具体 SQL（进指纹，绑定「授权的是这一句」）")


class JudgeResponse(BaseModel):
    tier: str
    disposition: str
    matched: bool
    matched_rules: list[str]
    reason: str
    requires_token: bool
    fingerprint: str
    policy_version: int
    policy_digest: str
    policy_path: str
    #: 是否已落库。**判定结果不受落库成败影响** —— 这里如实告诉调用方有没有留痕。
    persisted: bool
    persist_error: str | None = None


class TokenIssueRequest(BaseModel):
    approver: str = Field(min_length=1, description="批准人（审计要）")
    actor: str = Field(min_length=1, description="被批准的执行人")
    action: str = Field(min_length=1)
    target: str | None = None
    sql: str | None = None


class TokenIssueResponse(BaseModel):
    token: str
    fingerprint: str
    approver: str
    actor: str
    persisted: bool
    persist_error: str | None = None


class VerifyRequest(BaseModel):
    token: str = Field(min_length=1)
    action: str = Field(min_length=1)
    target: str | None = None
    sql: str | None = None


class VerifyResponse(BaseModel):
    ok: bool
    reason: str
    persisted: bool
    persist_error: str | None = None


class HealthResponse(BaseModel):
    status: str
    policy_version: int
    policy_digest: str
    policy_path: str
    default_tier: str
    rules: list[dict]
    token_ttl_seconds: int
    issued_token_count: int
    #: 数据库是否可用。不可用时判定照做，只是不留痕。
    db_available: bool


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    loaded = load_policy()
    return HealthResponse(
        status="ok",
        policy_version=loaded.policy.version,
        policy_digest=loaded.digest,
        policy_path=loaded.path,
        default_tier=loaded.policy.default_tier.value,
        rules=[{"name": rule.name, "tier": rule.tier.value} for rule in loaded.policy.rules],
        token_ttl_seconds=tokens.ttl_seconds,
        issued_token_count=len(tokens),
        db_available=dip_pg.available(),
    )


@app.post("/judge", response_model=JudgeResponse, tags=["judge"])
def judge(request: JudgeRequest) -> JudgeResponse:
    loaded = load_policy()
    verdict = loaded.policy.judge(
        actor=request.actor,
        action=request.action,
        roles=tuple(request.roles),
        target=request.target,
        sql=request.sql,
    )
    # 留痕放在判定**之后**，且失败不影响返回值 —— 数据库抖动不该把安全判定带崩。
    persisted = dip_pg.record_judgment(
        actor=request.actor,
        action=request.action,
        target=request.target,
        sql=request.sql,
        tier=verdict.tier.value,
        disposition=verdict.disposition,
        matched=verdict.matched,
        matched_rules=verdict.matched_rules,
        reason=verdict.reason,
        fingerprint=verdict.fingerprint,
        policy_version=loaded.policy.version,
        policy_digest=loaded.digest,
    )
    return JudgeResponse(
        **verdict.as_dict(),
        policy_version=loaded.policy.version,
        policy_digest=loaded.digest,
        policy_path=loaded.path,
        persisted=persisted.ok,
        persist_error=persisted.error,
    )


@app.post("/tokens/issue", response_model=TokenIssueResponse, tags=["tokens"])
def issue_token(request: TokenIssueRequest) -> TokenIssueResponse:
    """审批通过后签发一张令牌。

    Issue 的边界写的是「不做审批流转引擎与多人会签（P1 只保留"上一级确认一次"）」——
    这个接口就是"上一级确认一次"的程序化形式：人（或上游流程）确认过了，来换一张令牌。
    它**不判断**该不该批，只记录谁批的。
    """
    loaded = load_policy()
    fingerprint = loaded.policy.judge(
        actor=request.actor, action=request.action, target=request.target, sql=request.sql
    ).fingerprint
    token = tokens.issue(actor=request.actor, fingerprint=fingerprint)
    persisted = dip_pg.record_token_event(
        event="issued",
        actor=request.actor,
        action=request.action,
        target=request.target,
        sql=request.sql,
        ok=True,
        approver=request.approver,
        reason=f"审批通过，签发令牌（审批人={request.approver}）",
        fingerprint=fingerprint,
    )
    return TokenIssueResponse(
        token=token.value,
        fingerprint=fingerprint,
        approver=request.approver,
        actor=request.actor,
        persisted=persisted.ok,
        persist_error=persisted.error,
    )


@app.post("/verify", response_model=VerifyResponse, tags=["tokens"])
def verify(request: VerifyRequest) -> VerifyResponse:
    """校验令牌能否用于当前动作；通过即消费（一次性）。不通过 → 403。"""
    loaded = load_policy()
    fingerprint = loaded.policy.judge(
        actor="", action=request.action, target=request.target, sql=request.sql
    ).fingerprint
    ok, reason = tokens.verify(value=request.token, fingerprint=fingerprint)

    # 被拒的那次也要留痕：否则"谁试图用一张不合法的令牌"就查不到了。
    known = tokens.info(value=request.token)
    persisted = dip_pg.record_token_event(
        event="verified",
        actor=known.actor if known else "(未知令牌)",
        action=request.action,
        target=request.target,
        sql=request.sql,
        ok=ok,
        reason=reason,
        fingerprint=fingerprint,
    )
    if not ok:
        # 403 的响应体只有 detail，所以把"未落库"直接缀在原因里 —— 别让调用方以为留痕了。
        hint = "" if persisted.ok else f"（且未落库：{persisted.error}）"
        raise HTTPException(status_code=403, detail=f"{reason}{hint}")
    return VerifyResponse(ok=True, reason=reason, persisted=persisted.ok, persist_error=persisted.error)

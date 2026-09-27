"""风险判定策略（M2-01 · 防火墙服务）。

判定规则用代码固定下来，不靠自觉：

1. **三档从宽到严**：`auto` < `approval` < `deny`。
2. **一次动作可以命中多条规则时，取最严的那一档**。这就是 Issue 里
   「拿不准按上一档处理（fail-closed）」的落地方式 —— 规则写重了、写漏了、
   或者有人往策略表里加了一条宽松规则，都**不会**把本该判严的动作放松。
3. **一条都不命中** → 按策略表**显式声明**的 `default_tier` 处理，并把 `matched`
   标成 `False`。调用方因此能区分「命中了策略」与「没人管、走了默认档」——
   这两种情况可能给出同一个档位，但含义完全不同（M2-04 / M2-05 治的就是这个）。

策略表是 YAML，改策略不需要改代码；加载时严格校验，**写错就起不来**，
不允许"静默按默认值继续跑"。
"""

from __future__ import annotations

import fnmatch
import hashlib
import pathlib
from dataclasses import dataclass
from enum import Enum

import yaml


class Tier(str, Enum):
    """三个档位。值会出现在接口与日志里，所以用稳定的短字符串。"""

    AUTO = "auto"
    APPROVAL = "approval"
    DENY = "deny"


#: 从宽到严的顺序 —— 取最严那一档时就靠它的下标。
SEVERITY: dict[Tier, int] = {Tier.AUTO: 0, Tier.APPROVAL: 1, Tier.DENY: 2}

#: 档位 → 处置。档位是"风险判成什么"，处置是"下一步干什么"。
DISPOSITION: dict[Tier, str] = {
    Tier.AUTO: "auto_pass",
    Tier.APPROVAL: "require_approval",
    Tier.DENY: "reject",
}


class PolicyError(ValueError):
    """策略表写错。加载即报错，绝不放行。"""


def stricter(left: Tier, right: Tier) -> Tier:
    """取更严的一档。"""
    return left if SEVERITY[left] >= SEVERITY[right] else right


def action_fingerprint(*, action: str, target: str | None = None, sql: str | None = None) -> str:
    """动作指纹：动作 + 目标 + SQL 规范化后取 sha256 前 16 位。

    绑指纹意味着：审批人对「这条 SQL 打这张表」的授权**不能挪用到别的动作上** ——
    改一个字符即失效。

    注意：M2-01 这里只做 strip + 拼接（够用、可断言）；指纹的规范化规则
    （大小写、空白、参数化）留给 M2-02 收紧。
    """
    parts = [action.strip(), (target or "").strip(), (sql or "").strip()]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def _matches_any(patterns: tuple[str, ...], text: str) -> bool:
    """大小写无关的通配匹配。

    用 fnmatch 的 `*`/`?`，**不用 ASCII 正则** —— 动作名与表名都可能是中文，
    按 ASCII 字符类写正则会漏掉整个中文标识符（M1 实测过这个坑）。
    """
    lowered = text.lower()
    return any(fnmatch.fnmatchcase(lowered, pattern.lower()) for pattern in patterns)


@dataclass(frozen=True)
class Rule:
    """一条策略规则。命中即参与"取最严"的比较。"""

    name: str
    tier: Tier
    actions: tuple[str, ...]
    targets: tuple[str, ...] = ()
    require_any_role: tuple[str, ...] = ()
    reason: str = ""

    def matches(self, *, action: str, target: str | None, roles: tuple[str, ...]) -> bool:
        if not _matches_any(self.actions, action):
            return False
        if self.targets and not (target and _matches_any(self.targets, target)):
            return False
        if self.require_any_role and not (set(self.require_any_role) & set(roles)):
            return False
        return True


@dataclass(frozen=True)
class Verdict:
    """判定结果。字段名即接口字段名。"""

    tier: Tier
    disposition: str
    matched: bool
    matched_rules: tuple[str, ...] = ()
    reason: str = ""
    requires_token: bool = False
    fingerprint: str = ""

    def as_dict(self) -> dict:
        return {
            "tier": self.tier.value,
            "disposition": self.disposition,
            "matched": self.matched,
            "matched_rules": list(self.matched_rules),
            "reason": self.reason,
            "requires_token": self.requires_token,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class Policy:
    """一份策略表：默认档 + 若干规则。只读、不可变。"""

    default_tier: Tier
    rules: tuple[Rule, ...]
    version: int = 1
    source: str = ""

    @classmethod
    def load(cls, path: str | pathlib.Path) -> Policy:
        """从 YAML 读策略表。任何结构性错误都抛 `PolicyError`（fail-closed：宁可起不来）。"""
        location = pathlib.Path(path)
        try:
            raw = yaml.safe_load(location.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise PolicyError(f"策略表不存在：{location}") from exc
        except yaml.YAMLError as exc:
            raise PolicyError(f"策略表不是合法 YAML：{location}（{exc}）") from exc

        if not isinstance(raw, dict):
            raise PolicyError(f"策略表根节点必须是映射：{location}")

        if "default_tier" not in raw:
            raise PolicyError("策略表必须显式声明 `default_tier`（未命中任何规则时按它处理）")
        default_tier = _parse_tier(raw["default_tier"], where="default_tier")

        raw_rules = raw.get("rules")
        if not isinstance(raw_rules, list) or not raw_rules:
            raise PolicyError("策略表至少要有一条 `rules` 规则")

        rules: list[Rule] = []
        seen: set[str] = set()
        for index, item in enumerate(raw_rules):
            where = f"rules[{index}]"
            if not isinstance(item, dict):
                raise PolicyError(f"{where} 必须是映射")
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                raise PolicyError(f"{where} 必须有非空 `name`")
            if name in seen:
                raise PolicyError(f"规则名重复：{name}（日志与审计靠它定位，必须唯一）")
            seen.add(name)

            actions = _parse_str_list(item.get("actions"), where=f"{where}.actions", required=True)
            rules.append(
                Rule(
                    name=name,
                    tier=_parse_tier(item.get("tier"), where=f"{where}.tier"),
                    actions=actions,
                    targets=_parse_str_list(item.get("targets"), where=f"{where}.targets"),
                    require_any_role=_parse_str_list(
                        item.get("require_any_role"), where=f"{where}.require_any_role"
                    ),
                    reason=str(item.get("reason", "")).strip(),
                )
            )

        version = raw.get("version", 1)
        if not isinstance(version, int):
            raise PolicyError("`version` 必须是整数")
        return cls(default_tier=default_tier, rules=tuple(rules), version=version, source=str(location))

    def judge(
        self,
        *,
        actor: str,
        action: str,
        roles: tuple[str, ...] = (),
        target: str | None = None,
        sql: str | None = None,
    ) -> Verdict:
        """判定一件事：命中哪些规则、最终哪个档、下一步干什么。

        `sql` 不参与档位判定（档位由动作与目标决定），但**要进指纹** ——
        令牌绑的是"这一句 SQL"，不是"insert 这个动作"。
        """
        hits = [rule for rule in self.rules if rule.matches(action=action, target=target, roles=roles)]
        fingerprint = action_fingerprint(action=action, target=target, sql=sql)

        if not hits:
            return Verdict(
                tier=self.default_tier,
                disposition=DISPOSITION[self.default_tier],
                matched=False,
                reason=(
                    f"未命中任何策略规则，按 default_tier={self.default_tier.value} 处理"
                    f"（fail-closed：拿不准不放松）"
                ),
                requires_token=self.default_tier is Tier.APPROVAL,
                fingerprint=fingerprint,
            )

        tier = Tier.AUTO
        for rule in hits:
            tier = stricter(tier, rule.tier)
        detail = "；".join(f"{rule.name}({rule.tier.value})：{rule.reason}" for rule in hits if rule.reason)
        return Verdict(
            tier=tier,
            disposition=DISPOSITION[tier],
            matched=True,
            matched_rules=tuple(rule.name for rule in hits),
            reason=detail or f"命中 {len(hits)} 条规则，取最严的一档",
            requires_token=tier is Tier.APPROVAL,
            fingerprint=fingerprint,
        )


def _parse_tier(value: object, *, where: str) -> Tier:
    if not isinstance(value, str):
        raise PolicyError(f"{where} 必须是字符串（{Tier.AUTO.value}/{Tier.APPROVAL.value}/{Tier.DENY.value}）")
    try:
        return Tier(value.strip().lower())
    except ValueError as exc:
        allowed = "/".join(tier.value for tier in Tier)
        raise PolicyError(f"{where} 是不认识的档位 {value!r}（只允许 {allowed}）") from exc


def _parse_str_list(value: object, *, where: str, required: bool = False) -> tuple[str, ...]:
    if value is None:
        if required:
            raise PolicyError(f"{where} 不能为空")
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise PolicyError(f"{where} 必须是非空字符串列表")
    return tuple(item.strip() for item in value)

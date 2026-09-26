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
import logging
import pathlib
from dataclasses import dataclass
from enum import Enum

import yaml

logger = logging.getLogger("firewall.policy")


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


def _collapse_whitespace_outside_quotes(text: str) -> str:
    """把引号**外面**的连续空白折成一个空格；引号**里面**的原样保留。

    为什么不能直接 `re.sub(r"\\s+", " ", sql)`：SQL 字符串字面量里的空白是有语义的，
    `where name = '张 三'` 折成 `'张 三'` 看着一样，但 `'a  b'` 折成 `'a b'` 就**改了语义**。
    审批人复制粘贴 SQL 时换行/缩进变来变去是常态，所以这里既要宽容（折外面的空白），
    又不能宽容到把授权内容改掉（引号里一个字符都不碰）。

    SQL 里 `''` 是转义的引号，也一并处理。
    """
    out: list[str] = []
    in_quote = False
    pending_space = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_quote:
            out.append(char)
            if char == "'":
                if index + 1 < len(text) and text[index + 1] == "'":
                    out.append("'")
                    index += 2
                    continue
                in_quote = False
            index += 1
            continue
        if char == "'":
            in_quote = True
            if pending_space and out:
                out.append(" ")
            pending_space = False
            out.append(char)
            index += 1
            continue
        if char.isspace():
            pending_space = True
            index += 1
            continue
        if pending_space and out:
            out.append(" ")
        pending_space = False
        out.append(char)
        index += 1
    return "".join(out)


def normalize_sql(sql: str) -> str:
    """SQL 的规范化形式（进指纹前先过这里）。

    - 去掉首尾空白与**结尾的分号**（`...;` 与 `...` 是同一句话）；
    - 把引号外的连续空白折成一个空格（换行、缩进、Tab 都不算改动）；
    - **不改大小写、不动引号内的任何字符** —— 标识符与字面量是大小写敏感的，
      这里宁严勿松。
    """
    collapsed = _collapse_whitespace_outside_quotes(sql.strip())
    return collapsed.rstrip(";").strip()


def action_fingerprint(*, action: str, target: str | None = None, sql: str | None = None) -> str:
    """动作指纹：动作 + 目标 + SQL 规范化后取 sha256 前 16 位。

    绑指纹意味着：审批人对「这条 SQL 打这张表」的授权**不能挪用到别的动作上** ——
    改一个字符即失效。

    规范化规则（M2-02 收紧，每一条都是为了让"人复制粘贴的差异"不算改动，
    同时保证"内容的差异"一定算改动）：

    | 部分 | 怎么规范化 | 为什么 |
    | --- | --- | --- |
    | `action` | 去空白 + **转小写** | 动作是词汇（`INSERT`/`insert` 同一件事） |
    | `target` | 只去首尾空白，**保留大小写** | 表名可能大小写敏感，宁严勿松 |
    | `sql` | `normalize_sql`（见上） | 换行/缩进/结尾分号不算改动；引号内一个字符都不碰 |

    三段之间用 `\\x1f`（单元分隔符）拼接，避免"动作 + 目标"边界蹭在一起产生歧义。
    """
    parts = [action.strip().lower(), (target or "").strip(), normalize_sql(sql or "")]
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
    #: 内容指纹（sha256 前 12 位）。判定日志与接口都带它 —— 事后能回答"当时用的是哪一版策略"。
    digest: str = ""

    @classmethod
    def load(cls, path: str | pathlib.Path) -> Policy:
        """从 YAML 读策略表。任何结构性错误都抛 `PolicyError`（fail-closed：宁可起不来）。"""
        location = pathlib.Path(path)
        try:
            text = location.read_text(encoding="utf-8")
            raw = yaml.safe_load(text)
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
        return cls(
            default_tier=default_tier,
            rules=tuple(rules),
            version=version,
            source=str(location),
            digest=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        )

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
            # 「未命中」必须在日志里写明 —— 否则它和"命中了策略"在事后完全分不出来：
            # 响应里的 matched=false 随请求一起消失了，日志是唯一的长期记录（M2-05）。
            logger.warning(
                "判定未命中任何策略规则，按 default_tier=%s 处理（fail-closed：拿不准不放松）"
                "｜actor=%s｜action=%s｜target=%s｜policy_digest=%s",
                self.default_tier.value,
                actor or "(空)",
                action,
                target or "(空)",
                self.digest or "(未计算)",
            )
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

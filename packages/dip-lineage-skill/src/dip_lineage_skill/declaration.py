"""本 skill 的契约声明读取（M1-02 规范 → M1-03 实现）。

声明的唯一落点是 `packages/dip-lineage-skill/skill.yaml`，由 `dip_skills.load_skill_spec`
校验；测试会拿它和实现里写死的常量比对，防止"声明改了实现没改"。
"""

from __future__ import annotations

import pathlib

from dip_skills import SkillSpec, load_skill_spec

SKILL_YAML = pathlib.Path(__file__).parents[2] / "skill.yaml"


def load_declaration() -> SkillSpec:
    """读入并校验本 skill 的契约声明。"""
    return load_skill_spec(SKILL_YAML)

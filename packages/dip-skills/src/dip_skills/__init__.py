"""skill 契约层：声明规范与校验器（工作项 M1-02）+ 调用回执（工作项 M1-03）。"""

from .receipt import SkillReceipt
from .spec import (
    CONTRACT_VERSION,
    SkillSpec,
    SkillSpecError,
    load_skill_spec,
    validate_skill_spec,
)

__all__ = [
    "CONTRACT_VERSION",
    "SkillReceipt",
    "SkillSpec",
    "SkillSpecError",
    "load_skill_spec",
    "validate_skill_spec",
]

"""分层守卫（最小版，工作项 W-104）：依赖只能自上而下。

规则（与《项目规划》§4.3 一致）：

    portal_api  →  dip_agent  →  dip_core / dip_contracts
    portal_api  →  lineage_client            （集成适配器，谁也不依赖它）
    dip_contracts / dip_core / lineage_client  →  **不得**依赖上面任何一层

由测试强制，而不是靠自觉：一旦有人在契约层里 import 内核客户端，这里立刻红。
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).parents[1]

PKG_DIRS = {
    "dip_contracts": ROOT / "packages/dip-contracts/src/dip_contracts",
    "dip_core": ROOT / "packages/dip-core/src/dip_core",
    "dip_agent": ROOT / "packages/dip-agent/src/dip_agent",
    "dip_skills": ROOT / "packages/dip-skills/src/dip_skills",
    "model_gateway": ROOT / "apps/model-gateway/src/model_gateway",
    "lineage_client": ROOT / "integrations/lineage-client/src/lineage_client",
    "portal_api": ROOT / "apps/portal-api/src/portal_api",
}

ALLOWED: dict[str, set[str]] = {
    "dip_contracts": set(),
    "dip_core": set(),
    "dip_skills": set(),                          # 声明层：不依赖任何其它单元（标准库 + pydantic + YAML）
    "model_gateway": {"dip_core"},                # 网关只吃配置；不依赖契约层与上层
    "lineage_client": {"dip_contracts"},          # 适配器实现契约层的 KernelToolkit 协议
    "dip_agent": {"dip_contracts", "dip_core"},   # 只依赖协议，不依赖 httpx 适配器
    "portal_api": {"dip_contracts", "dip_core", "dip_agent", "lineage_client"},
}


def first_party_imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_all_units_exist():
    for name, path in PKG_DIRS.items():
        assert path.is_dir(), f"部署单元缺失：{name}（{path}）"
        assert (path / "__init__.py").exists(), f"{name} 缺 __init__.py"


def test_dependencies_flow_downwards_only():
    violations: list[str] = []
    for pkg, directory in PKG_DIRS.items():
        for py in sorted(directory.rglob("*.py")):
            for imported in first_party_imports(py) & set(PKG_DIRS):
                if imported not in ALLOWED[pkg]:
                    violations.append(f"{py.relative_to(ROOT)} 里 import 了 {imported}（{pkg} 不允许依赖 {imported}）")
    assert not violations, "分层违规：\n  " + "\n  ".join(violations)


def test_contracts_layer_stays_pure():
    """契约层只允许 pydantic/标准库——它是前后端共享的最小真相。"""
    allowed = {"dip_contracts", "__future__", "enum", "typing", "collections", "pydantic", "dataclasses", "re"}
    for py in sorted(PKG_DIRS["dip_contracts"].rglob("*.py")):
        unknown = {i for i in first_party_imports(py) if i not in allowed and not i.startswith("_")}
        third_party = {i for i in unknown if i in {"httpx", "fastapi", "uvicorn", "requests"}}
        assert not third_party, f"{py.relative_to(ROOT)} 引入了重量级依赖 {third_party}（契约层必须保持纯净）"


def test_skills_layer_stays_pure():
    """skill 契约声明层只允许 标准库 + pydantic + YAML——解析一份声明不该拖进运行时依赖。"""
    allowed = {
        "dip_skills",
        "__future__",
        "enum",
        "typing",
        "pathlib",
        "collections",
        "dataclasses",
        "re",
        "pydantic",
        "yaml",
    }
    for py in sorted(PKG_DIRS["dip_skills"].rglob("*.py")):
        unknown = {i for i in first_party_imports(py) if i not in allowed and not i.startswith("_")}
        heavy = {i for i in unknown if i in {"httpx", "fastapi", "uvicorn", "requests", "psycopg", "sqlalchemy"}}
        assert not heavy, f"{py.relative_to(ROOT)} 引入了重量级依赖 {heavy}（skill 契约层必须保持纯净）"
        assert unknown <= allowed, f"{py.relative_to(ROOT)} 引入了 {unknown - allowed}（该层只允许 {sorted(allowed)}）"

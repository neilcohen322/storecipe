"""Architecture guardrails for the standalone MCP gateway boundary."""

import ast
import tomllib
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_SOURCE_DIR = GATEWAY_ROOT / "src" / "storecipe_mcp"
GATEWAY_PROJECT_FILE = GATEWAY_ROOT / "pyproject.toml"
FORBIDDEN_IMPORT_ROOTS = {"catalog", "ingestion", "sqlalchemy", "asyncpg", "redis"}


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def test_gateway_source_does_not_import_catalog_or_persistence_code() -> None:
    offenders: dict[str, list[str]] = {}
    for path in sorted(GATEWAY_SOURCE_DIR.rglob("*.py")):
        forbidden = sorted(
            module
            for module in _imported_modules(path.read_text())
            if module.split(".", 1)[0] in FORBIDDEN_IMPORT_ROOTS
        )
        if forbidden:
            offenders[str(path.relative_to(GATEWAY_SOURCE_DIR))] = forbidden

    assert not offenders, f"Gateway must not import Catalog or persistence code: {offenders}"


def test_gateway_dependency_manifest_has_no_catalog_or_persistence_dependencies() -> None:
    project = tomllib.loads(GATEWAY_PROJECT_FILE.read_text())
    dependencies = project["project"]["dependencies"]
    dependency_names = {dependency.split("[", 1)[0].split(">", 1)[0] for dependency in dependencies}

    assert dependency_names.isdisjoint(FORBIDDEN_IMPORT_ROOTS)

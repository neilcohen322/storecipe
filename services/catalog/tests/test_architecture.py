"""Architecture guardrails for the routes/services layering.

Route modules own HTTP concerns only. They must not reach into persistence:
importing SQLAlchemy or the ORM models directly is a layering violation that
this test prevents for any future route module.
"""

import ast
import tomllib
from collections.abc import Iterator
from pathlib import Path

import catalog

ROUTES_DIR = Path(catalog.__file__).parent / "routes"
CATALOG_SOURCE_DIR = Path(catalog.__file__).parent
CATALOG_PROJECT_FILE = CATALOG_SOURCE_DIR.parents[1] / "pyproject.toml"


def _imported_modules(source: str) -> Iterator[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module


def _is_forbidden(module: str) -> bool:
    return module == "sqlalchemy" or module.startswith("sqlalchemy.") or module == "catalog.models"


def test_route_modules_do_not_import_sqlalchemy_or_models() -> None:
    offenders: dict[str, list[str]] = {}
    for path in sorted(ROUTES_DIR.rglob("*.py")):
        forbidden = sorted(
            {module for module in _imported_modules(path.read_text()) if _is_forbidden(module)}
        )
        if forbidden:
            offenders[path.name] = forbidden
    assert not offenders, f"Route modules must not import SQLAlchemy or models: {offenders}"


def test_catalog_source_does_not_import_mcp_sdk() -> None:
    offenders: dict[str, list[str]] = {}
    for path in sorted(CATALOG_SOURCE_DIR.rglob("*.py")):
        forbidden = sorted(
            {
                module
                for module in _imported_modules(path.read_text())
                if module == "mcp" or module.startswith("mcp.")
            }
        )
        if forbidden:
            offenders[str(path.relative_to(CATALOG_SOURCE_DIR))] = forbidden

    assert not offenders, f"Catalog REST code must not import the MCP SDK: {offenders}"


def test_catalog_has_no_mcp_runtime_modules_or_dependency() -> None:
    legacy_modules = [
        CATALOG_SOURCE_DIR / "mcp_server.py",
        CATALOG_SOURCE_DIR / "mcp_auth.py",
        CATALOG_SOURCE_DIR / "mcp_runtime.py",
    ]
    assert [str(path) for path in legacy_modules if path.exists()] == []

    project = tomllib.loads(CATALOG_PROJECT_FILE.read_text())
    dependencies = project["project"]["dependencies"]
    assert not any(dependency.startswith("mcp") for dependency in dependencies)

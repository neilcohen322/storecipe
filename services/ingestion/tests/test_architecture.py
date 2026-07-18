"""Architecture guardrails for the routes/services layering.

Route modules own HTTP concerns only. They must not import SQLAlchemy or the
persistence models directly; this test prevents that for future route modules.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import ingestion

ROUTES_DIR = Path(ingestion.__file__).parent / "routes"


def _imported_modules(source: str) -> Iterator[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module


def _is_forbidden(module: str) -> bool:
    return (
        module == "sqlalchemy"
        or module.startswith("sqlalchemy.")
        or module == "ingestion.import_models"
    )


def test_route_modules_do_not_import_sqlalchemy_or_models() -> None:
    offenders: dict[str, list[str]] = {}
    for path in sorted(ROUTES_DIR.rglob("*.py")):
        forbidden = sorted(
            {module for module in _imported_modules(path.read_text()) if _is_forbidden(module)}
        )
        if forbidden:
            offenders[path.name] = forbidden
    assert not offenders, f"Route modules must not import SQLAlchemy or models: {offenders}"

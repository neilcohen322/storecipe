"""Architecture guardrails for the routes/services layering.

Route modules own HTTP concerns only. They must not import SQLAlchemy or the
persistence models directly; this test prevents that for future route modules.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import ingestion
from ingestion.models import AiDailyUsage, LlmInvocation

ROUTES_DIR = Path(ingestion.__file__).parent / "routes"
REPO_ROOT = Path(__file__).resolve().parents[3]
ROOT = REPO_ROOT


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


def test_operations_docs_describe_split_redis_and_opt_in_recovery_checks() -> None:
    readme = (REPO_ROOT / "README.md").read_text()
    compose = (REPO_ROOT / "compose.yaml").read_text()
    verifier = (REPO_ROOT / "scripts" / "verify.ps1").read_text()

    assert "dedicated persistent Celery broker Redis" in readme
    assert "ingestion-dispatcher" in compose
    assert "ingestion-reconciler" in compose
    assert "Celery broker uses redis-broker" in compose
    assert "INGESTION_IMPORT_BURST_REQUESTS" in compose
    assert "INGESTION_AI_DAILY_TOKEN_LIMIT" in compose
    assert "OPENROUTER_API_KEY" in compose
    # Secret stays on the worker; API/dispatcher/reconciler share ingestion-common only.
    api_block = compose.split("ingestion-api:")[1].split("ingestion-worker:")[0]
    worker_block = compose.split("ingestion-worker:")[1].split("ingestion-dispatcher:")[0]
    assert "OPENROUTER_API_KEY" not in api_block
    assert "OPENROUTER_API_KEY" in worker_block
    assert "UNVERIFIED" in verifier
    assert "INGESTION_TEST_DATABASE_URL" in verifier
    assert "RUN_DOCKER_INTEGRATION" in verifier


def test_duplicate_source_contract_is_explicit() -> None:
    openapi = (ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8")
    errors = (ROOT / "contracts" / "errors.md").read_text(encoding="utf-8")
    ownership = (ROOT / "contracts" / "ownership.md").read_text(encoding="utf-8")

    assert "duplicatePolicy" in openapi
    assert "existingJobId" in openapi
    assert "existingRecipeId" in openapi
    assert "active_url_import_exists" in errors
    assert "recipe_source_exists" in errors
    assert "Active URL duplicate invariant" in ownership


def test_usage_governance_environment_and_rate_limit_contracts_are_explicit() -> None:
    environment = (ROOT / "contracts" / "environment.md").read_text(encoding="utf-8")
    openapi = (ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8")
    errors = (ROOT / "contracts" / "errors.md").read_text(encoding="utf-8")

    for variable in (
        "INGESTION_IMPORT_BURST_REQUESTS",
        "INGESTION_IMPORT_BURST_WINDOW_SECONDS",
        "INGESTION_AI_DAILY_TOKEN_LIMIT",
        "INGESTION_AI_INVOCATION_RESERVATION_TOKENS",
        "STORECIPE_TEST_REDIS_URL",
    ):
        assert variable in environment

    assert '"429"' in openapi
    for header in ("Retry-After", "RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset"):
        assert header in openapi
    assert "import_burst_exceeded" in errors


def test_ai_budget_models_have_durable_keys() -> None:
    assert tuple(column.name for column in AiDailyUsage.__table__.primary_key) == (
        "owner_subject",
        "budget_date_utc",
    )
    assert any(
        constraint.name == "uq_llm_invocations_provider_operation"
        for constraint in LlmInvocation.__table__.constraints
    )
    constraint_sql = {
        str(constraint.sqltext)
        for constraint in (
            *AiDailyUsage.__table__.constraints,
            *LlmInvocation.__table__.constraints,
        )
        if hasattr(constraint, "sqltext")
    }
    assert "reserved_tokens >= 0 AND consumed_tokens >= 0" in constraint_sql
    assert "reserved_tokens >= 0" in constraint_sql
    assert "input_tokens >= 0" in constraint_sql
    assert "output_tokens >= 0" in constraint_sql
    assert "total_tokens >= 0" in constraint_sql
    assert "cost_microunits >= 0" in constraint_sql
    assert "latency_ms >= 0" in constraint_sql

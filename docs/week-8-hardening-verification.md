# Import Reliability Hardening Verification

Date: 2026-07-29

## Normal verifier

Command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

Result:

- `uv sync --frozen`: passed
- Ruff lint: passed
- Ruff format: passed (`85 files already formatted`)
- mypy: passed (`50 source files`)
- pytest: **236 passed, 9 skipped**
- OpenAPI validation: passed
- pnpm frozen install: passed
- TypeScript type-check: passed

The nine normal-run skips are the seven disposable-PostgreSQL tests and two isolated
Docker tests. Both opt-in groups were then run explicitly.

## PostgreSQL integration

A disposable `postgres:17-alpine` container was initialized with the repository's
local roles, migrated to the current ingestion Alembic head, and exposed only on an
ephemeral loopback port.

Command:

```powershell
$env:INGESTION_TEST_DATABASE_URL = 'postgresql+asyncpg://ingestion_app:...@127.0.0.1:<ephemeral>/storecipe'
uv run pytest services/ingestion/tests/integration/test_import_postgres.py -q
```

Result: **7 passed**.

This includes independent-session receipt recovery, concurrent same-generation claims,
heartbeat renewal, stale-lease fencing, cancellation after lease loss, and the
cancellation/Catalog-intent race.

## Isolated Docker recovery stack

Command:

```powershell
$env:RUN_DOCKER_INTEGRATION = '1'
uv run pytest services/ingestion/tests/integration/test_import_stack.py -q
```

Result: **2 passed**.

The tests built a unique Compose project with local JWT/JWKS and Catalog substitutes.
They verified:

- a worker killed during an in-flight Catalog request is recovered and retried without
  a duplicate recipe creation;
- an import accepted into PostgreSQL while the broker is stopped completes after the
  broker returns, with exactly one Catalog creation.

The harness allocated ephemeral API/Auth ports, emitted Compose logs on failure, and
removed its containers and volumes afterward. No external access token or provider
account was used.

## Configuration and repository checks

Commands:

```powershell
docker compose config --quiet
docker compose -f compose.yaml -f services/ingestion/tests/integration/compose.week8.yaml config --quiet
git diff --check
```

These configuration and whitespace checks are part of the final pre-commit gate.

## Deterministic recovery evidence

The verified suite covers:

- commit-boundary lifecycle telemetry with rollback suppression;
- truthful completed, deferred, retry, terminal, and stale stage outcomes;
- provider and Catalog success, failure, retry, and ambiguity outcomes;
- queue-delay and Catalog-pending-age metrics;
- receipt-deadline recovery into exactly one current dispatch generation;
- concurrent duplicate-delivery rejection under PostgreSQL row locking;
- stale-worker fencing and heartbeat renewal;
- cancellation terminalization after lease loss without redispatch;
- checkpoint survival after worker failure;
- provider-success adoption without a second paid call;
- durable broker-loss recovery and idempotent Catalog retry after worker death.

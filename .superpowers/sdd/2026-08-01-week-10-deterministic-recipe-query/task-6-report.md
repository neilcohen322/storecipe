# Task 6 Report: Rename and integrate the fail-open recipe query cache

## Status

DONE_WITH_CONCERNS

Implemented on top of 90804db. The requested commit is created after final verification.

## RED / GREEN

RED:

- `uv run pytest services/catalog/tests/test_recipe_query_cache.py services/catalog/tests/test_health.py -q` initially failed with 13 failures: the renamed cache tests reported `ModuleNotFoundError: catalog.recipe_query_cache`, the service test reported the missing `catalog.services.recipe_queries` module, and the migrated health expectations targeted the absent recipe-query setting/state.

GREEN:

- The focused cache/health run passed 28 tests after implementation.
- The cache miss orchestration test verifies cursor validation, `limit + 1` fetch sizing, page construction, and cache write-back; the hit test verifies no fetch/write on a cached page.

## Cache and orchestration behavior

- Renamed `RecommendationCache` to `RecipeQueryCache`, with `recipe_queries:{user_id}:{catalog_version}:{recipe_query_hash(request)}` keys.
- Preserved bounded async Redis get/set/delete operations, fail-open Redis/timeout behavior, invalid-envelope deletion, schema version `1`, TTL default `1800`, and telemetry under `recipe_query_cache.*`.
- The cache envelope now carries the request and catalog version because `RecipeQueryPage` does not; reads validate both against the key/read arguments before returning a page.
- Renamed the setting to `recipe_query_cache_ttl_seconds`, which maps to `CATALOG_RECIPE_QUERY_CACHE_TTL_SECONDS`, retaining bounds 60 through 86,400.
- `app.state.recipe_query_cache` is created during lifespan; optional Redis readiness remains degraded-but-healthy while PostgreSQL remains required.
- `query_recipes` follows the required resolve-user, cursor-validation, cache-read, bounded repository fetch, page-build, and cache-write sequence.
- `build_query_page` limits input processing to `request.limit + 1`, returns only the first `limit` candidates, derives sorted missing ingredients and preferred-tag match sets from loaded recipe relations, and emits a cursor from the last returned candidate only when an extra candidate exists.

## Rename and deletion list

- `services/catalog/src/catalog/recommendation_cache.py` -> `services/catalog/src/catalog/recipe_query_cache.py` via `git mv`.
- `services/catalog/tests/test_recommendation_cache.py` -> `services/catalog/tests/test_recipe_query_cache.py` via `git mv`.
- `services/catalog/tests/integration/test_recommendation_cache_redis.py` -> `services/catalog/tests/integration/test_recipe_query_cache_redis.py` via `git mv`.
- Deleted `services/catalog/src/catalog/recommendations.py` after consumer migration.
- Deleted `services/catalog/tests/test_recommendations.py` after contract coverage was already provided by `recipe_queries.py` tests.
- Created `services/catalog/src/catalog/services/recipe_queries.py`.

## Verification commands and results

- `uv run pytest services/catalog/tests -ra` -> 119 passed, 5 skipped.
- `uv run pytest services/catalog/tests/integration -ra` -> 5 skipped: 1 PostgreSQL skip because `CATALOG_TEST_DATABASE_URL` was not configured and 4 Redis skips because `STORECIPE_TEST_REDIS_URL` was not configured.
- `uv run pytest services/catalog/tests/test_recipe_query_cache.py services/catalog/tests/test_health.py services/catalog/tests/integration/test_catalog_version_postgres.py -q` -> 28 passed, 1 skipped.
- `uv run ruff check` on all 10 touched Python files -> all checks passed.
- `uv run ruff format --check` on all 10 touched Python files -> 10 files already formatted.
- `uv run mypy --strict` on all 10 touched Python files -> success, no issues found.
- `git diff --check` -> passed; Git emitted only normal LF/CRLF conversion warnings.

## Self-review

- No Task 7 HTTP route or route parameters were added.
- Catalog-version keying and envelope validation prevent reuse across durable catalog versions.
- Cursor values are built from the same effective sort fields used by the repository, including the internal `recipeId:asc` tie-breaker.
- The final diff is limited to the brief's owned files plus this requested report; no unrelated prior changes were reverted.

## Concerns

- PostgreSQL concurrency and real Redis behavior were not exercised because their opt-in environment variables were unavailable; the test modules skip exactly as designed.
- `services/catalog/src/catalog/services/users.py` retains an older recommendation-cache phrase in a docstring. It was not modified because it is outside the Task 6 ownership list and is not a code consumer.

## Commit

`feat: cache deterministic recipe query pages`

## Round 1 review fixes

### RED evidence

- Updated the asymmetric page-match regression to use recipe ingredients `basil` and `salt` with available ingredients `basil` and `garlic`, expecting only `salt`. Before the production fix, the focused run failed with `['garlic'] == ['salt']`.
- Added `test_recipe_query_cache_ttl_environment_name_is_consistent`, which checks `.env.example`, `compose.yaml`, and `contracts/environment.md`. Before the contract fix, it failed because the new variable occurred zero times in `.env.example`.

### GREEN evidence and changes

- Changed `missing_ingredients` to `recipe ingredient names - available ingredient names`.
- Renamed the TTL variable in `.env.example`, `compose.yaml`, and `contracts/environment.md` to `CATALOG_RECIPE_QUERY_CACHE_TTL_SECONDS`; default 1,800 and bounds 60–86,400 remain documented/configured.
- Focused command `uv run pytest services/catalog/tests/test_recipe_query_cache.py services/catalog/tests/test_health.py services/catalog/tests/test_recipe_queries.py -q` -> 67 passed.
- Full Catalog command `uv run pytest services/catalog/tests -ra` -> 120 passed, 5 skipped (1 PostgreSQL and 4 Redis opt-in integrations not configured).
- Ruff check -> all checks passed; Ruff format check -> 10 files already formatted; strict mypy -> no issues in 10 files.
- `docker compose config --quiet` -> passed.
- `git diff --check` -> passed with only normal LF/CRLF conversion warnings.
- OpenAPI validation was not run because no route or OpenAPI contract was touched.

Fix commit: `fix: align recipe query cache contract`

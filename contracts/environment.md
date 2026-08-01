# Environment variables

| Variable | Consumer | Required when | Purpose |
|---|---|---:|---|
| `CATALOG_DATABASE_URL` | Catalog | yes | Catalog-role PostgreSQL DSN |
| `CATALOG_REDIS_URL` | Catalog | no | Optional recipe-query cache Redis URL; defaults to `redis://localhost:6379` |
| `CATALOG_REDIS_TIMEOUT_SECONDS` | Catalog | no | Shared Redis connect/command deadline in seconds; defaults to 1 and is bounded above by 10 |
| `CATALOG_RECIPE_QUERY_CACHE_TTL_SECONDS` | Catalog | no | Recipe-query cache TTL in seconds; defaults to 1,800 and is bounded from 60 to 86,400 |
| `INGESTION_DATABASE_URL` | Ingestion/worker | yes | Ingestion-role PostgreSQL DSN |
| `INGESTION_REDIS_URL` | Ingestion API | yes | Readiness, locks, and rate limiting |
| `INGESTION_CELERY_BROKER_URL` | Worker/dispatcher | yes | Dedicated persistent, `noeviction` Celery Redis broker |
| `INGESTION_IMPORT_DEADLINE_SECONDS` | API/worker/reconciler | no | Overall pre-Catalog import deadline; defaults to 900 seconds |
| `INGESTION_IMPORT_BURST_REQUESTS` | Ingestion API | no | Maximum import submissions accepted in one burst window; defaults to 5 |
| `INGESTION_IMPORT_BURST_WINDOW_SECONDS` | Ingestion API | no | Import submission burst-window duration; defaults to 60 seconds |
| `INGESTION_AI_DAILY_TOKEN_LIMIT` | Ingestion worker | no | Per-user daily AI extraction token budget; defaults to 1,100,000 |
| `INGESTION_AI_INVOCATION_RESERVATION_TOKENS` | Ingestion worker | no | Tokens reserved before one AI invocation; defaults to 275,000 and cannot exceed the daily budget |
| `INGESTION_PAYLOAD_ACTIVE_KEY_ID` | Ingestion/worker | yes | Key ID used for new encrypted payload writes |
| `INGESTION_PAYLOAD_KEYRING` | Ingestion/worker | yes | Secret `key-id=base64-key` mapping for retained payloads |
| `AUTH0_ISSUER` | APIs/MCP | protected routes | Expected JWT issuer |
| `AUTH0_AUDIENCE` | APIs/MCP | protected routes | Canonical Storecipe API resource |
| `AUTH0_JWKS_URL` | APIs/MCP | no | Optional JWKS override; defaults to `<issuer>/.well-known/jwks.json` |
| `MCP_RESOURCE_URL` | Catalog | MCP enabled | Canonical HTTPS MCP resource identifier |
| `OPENROUTER_API_KEY` | Worker | AI enabled | Secret model-provider credential |
| `OPENROUTER_MODEL` | Worker | no | Pinned extraction model; defaults to `openai/gpt-5.6-luna` |
| `AI_EXTRACTION_ENABLED` | Worker | no | AI kill switch; defaults false |
| `CATALOG_API_URL` | Ingestion API/worker | yes | Catalog service base URL |
| `CATALOG_M2M_TOKEN_URL` | Ingestion API/worker | no | OAuth token URL; defaults from `AUTH0_ISSUER` |
| `CATALOG_M2M_CLIENT_ID` | Ingestion API/worker | source lookup/catalog stage | M2M client ID |
| `CATALOG_M2M_CLIENT_SECRET` | Ingestion API/worker | source lookup/catalog stage | M2M client secret |
| `CATALOG_M2M_AUDIENCE` | Ingestion API/worker | source lookup/catalog stage | M2M token audience |
| `INGESTION_TEST_DATABASE_URL` | PostgreSQL integration tests | opt-in | Disposable migrated PostgreSQL DSN for concurrency and transaction checks |
| `CATALOG_TEST_DATABASE_URL` | PostgreSQL integration tests | opt-in | Disposable migrated PostgreSQL DSN for Catalog version-concurrency checks |
| `STORECIPE_TEST_REDIS_URL` | Redis integration tests | opt-in | Disposable Redis DSN for rate-limit and AI usage-governance checks |
| `RUN_DOCKER_INTEGRATION` | Docker integration tests | opt-in | Set to `1` to build an isolated Compose project with deterministic local Auth and Catalog substitutes; the harness issues its own test token |

Production values are injected by the deployment environment. `.env` is local only;
`.env.example` contains names and harmless defaults but no secrets.
Protected endpoints fail closed while the Auth0 issuer or audience is empty.
Payload startup fails closed when the active key is absent or a retained payload references a
missing key. Old keys must remain configured until affected payloads are re-encrypted or expire.

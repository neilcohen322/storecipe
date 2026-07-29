# Environment variables

| Variable | Consumer | Required when | Purpose |
|---|---|---:|---|
| `CATALOG_DATABASE_URL` | Catalog | yes | Catalog-role PostgreSQL DSN |
| `INGESTION_DATABASE_URL` | Ingestion/worker | yes | Ingestion-role PostgreSQL DSN |
| `INGESTION_REDIS_URL` | Ingestion API | yes | Readiness, locks, and rate limiting |
| `INGESTION_CELERY_BROKER_URL` | Worker/dispatcher | yes | Dedicated persistent, `noeviction` Celery Redis broker |
| `INGESTION_IMPORT_DEADLINE_SECONDS` | API/worker/reconciler | no | Overall pre-Catalog import deadline; defaults to 900 seconds |
| `INGESTION_PAYLOAD_ACTIVE_KEY_ID` | Ingestion/worker | yes | Key ID used for new encrypted payload writes |
| `INGESTION_PAYLOAD_KEYRING` | Ingestion/worker | yes | Secret `key-id=base64-key` mapping for retained payloads |
| `AUTH0_ISSUER` | APIs/MCP | protected routes | Expected JWT issuer |
| `AUTH0_AUDIENCE` | APIs/MCP | protected routes | Canonical Storecipe API resource |
| `AUTH0_JWKS_URL` | APIs/MCP | no | Optional JWKS override; defaults to `<issuer>/.well-known/jwks.json` |
| `MCP_RESOURCE_URL` | Catalog | MCP enabled | Canonical HTTPS MCP resource identifier |
| `OPENROUTER_API_KEY` | Worker | AI enabled | Secret model-provider credential |
| `OPENROUTER_MODEL` | Worker | no | Pinned extraction model; defaults to `openai/gpt-5-nano` |
| `AI_EXTRACTION_ENABLED` | Worker | no | AI kill switch; defaults false |
| `CATALOG_API_URL` | Worker | yes | Catalog service base URL |
| `CATALOG_M2M_TOKEN_URL` | Worker | no | OAuth token URL; defaults from `AUTH0_ISSUER` |
| `CATALOG_M2M_CLIENT_ID` | Worker | catalog stage | M2M client ID |
| `CATALOG_M2M_CLIENT_SECRET` | Worker | catalog stage | M2M client secret |
| `CATALOG_M2M_AUDIENCE` | Worker | catalog stage | M2M token audience |
| `INGESTION_TEST_DATABASE_URL` | PostgreSQL integration tests | opt-in | Disposable migrated PostgreSQL DSN for concurrency and transaction checks |
| `RUN_DOCKER_INTEGRATION` | Docker integration tests | opt-in | Set to `1` to build an isolated Compose project with deterministic local Auth and Catalog substitutes; the harness issues its own test token |

Production values are injected by the deployment environment. `.env` is local only;
`.env.example` contains names and harmless defaults but no secrets.
Protected endpoints fail closed while the Auth0 issuer or audience is empty.
Payload startup fails closed when the active key is absent or a retained payload references a
missing key. Old keys must remain configured until affected payloads are re-encrypted or expire.

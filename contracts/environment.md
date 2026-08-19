# Environment variables

| Variable | Consumer | Required when | Purpose |
|---|---|---:|---|
| `CATALOG_DATABASE_URL` | Catalog | yes | Catalog-role PostgreSQL DSN |
| `CATALOG_REDIS_URL` | Catalog | no | Optional recipe-query cache Redis URL; defaults to `redis://localhost:6379` |
| `CATALOG_REDIS_TIMEOUT_SECONDS` | Catalog | no | Shared Redis connect/command deadline in seconds; defaults to 1 and is bounded above by 10 |
| `CATALOG_RECIPE_QUERY_CACHE_TTL_SECONDS` | Catalog | no | Recipe-query cache TTL in seconds; defaults to 1,800 and is bounded from 60 to 86,400 |
| `CATALOG_MEDIA_BUCKET` | Catalog | no | Private GCS bucket for one cover image per recipe; empty disables media uploads and delivery with `503 media_unavailable` |
| `CATALOG_MEDIA_MAX_INPUT_BYTES` | Catalog | no | Maximum uploaded image size in bytes; defaults to 8,388,608 and cannot exceed that bound |
| `CATALOG_MEDIA_MAX_PIXELS` | Catalog | no | Maximum decoded pixel count; defaults to 12,000,000 and cannot exceed that bound |
| `CATALOG_MEDIA_MAX_OUTPUT_BYTES` | Catalog | no | Maximum stored WebP size in bytes; defaults to 1,572,864 and cannot exceed that bound |
| `CATALOG_CORS_ORIGINS` | Catalog | no | Comma-separated browser origins allowed to call Catalog (Expo web); defaults to `http://localhost:8081,http://127.0.0.1:8081` |
| `INGESTION_CORS_ORIGINS` | Ingestion API | no | Comma-separated browser origins allowed to call Ingestion (Expo web); defaults to `http://localhost:8081,http://127.0.0.1:8081` |
| `INGESTION_DATABASE_URL` | Ingestion/worker | yes | Ingestion-role PostgreSQL DSN |
| `INGESTION_REDIS_URL` | Ingestion API | yes | Readiness, locks, and rate limiting |
| `INGESTION_CELERY_BROKER_URL` | Worker/dispatcher | yes | Dedicated persistent, `noeviction` Celery Redis broker |
| `INGESTION_IMPORT_DEADLINE_SECONDS` | API/worker/reconciler | no | Overall pre-Catalog import deadline; defaults to 900 seconds |
| `INGESTION_IMPORT_BURST_REQUESTS` | Ingestion API | no | Maximum import submissions accepted in one burst window; defaults to 5 |
| `INGESTION_IMPORT_BURST_WINDOW_SECONDS` | Ingestion API | no | Import submission burst-window duration; defaults to 60 seconds |
| `INGESTION_AI_DAILY_TOKEN_LIMIT` | Ingestion worker | no | Per-user daily AI extraction token budget; defaults to 1,100,000 |
| `INGESTION_AI_INVOCATION_RESERVATION_TOKENS` | Ingestion worker | no | Tokens reserved before one AI invocation; defaults to 275,000 and cannot exceed the daily budget |
| `INGESTION_INGREDIENT_NORMALIZATION_RESERVATION_TOKENS` | Ingestion API/worker | no | Tokens reserved before one ingredient-normalization invocation; defaults to 64,000 and cannot exceed the daily budget |
| `INGESTION_PAYLOAD_ACTIVE_KEY_ID` | Ingestion/worker | yes | Key ID used for new encrypted payload writes |
| `INGESTION_PAYLOAD_KEYRING` | Ingestion/worker | yes | Secret `key-id=base64-key` mapping for retained payloads |
| `AUTH0_ISSUER` | APIs/MCP | protected routes / complete gateway auth | Expected JWT issuer; required in the gateway auth all-or-none bundle (cannot be replaced by `MCP_OBO_TOKEN_URL`) |
| `AUTH0_AUDIENCE` | Catalog/Ingestion/MCP OBO | protected routes / complete gateway auth | Canonical Storecipe API resource; distinct from the public MCP resource URL |
| `AUTH0_JWKS_URL` | APIs/MCP | no | Optional JWKS override; defaults to `<issuer>/.well-known/jwks.json` |
| `EXPO_PUBLIC_AUTH0_DOMAIN` | Web (Expo) | Auth0 login | Auth0 tenant domain for Universal Login SPA |
| `EXPO_PUBLIC_AUTH0_CLIENT_ID` | Web (Expo) | Auth0 login | Public Auth0 SPA client ID |
| `EXPO_PUBLIC_AUTH0_AUDIENCE` | Web (Expo) | Auth0 login | Storecipe API resource for access tokens; use `AUTH0_AUDIENCE`, not `MCP_RESOURCE_URL` |
| `MCP_PORT` | Compose | no | Optional host port mapped to the gateway's internal port `8002` |
| `MCP_CATALOG_API_URL` | MCP gateway | yes | Trusted Catalog REST base URL; defaults to `http://catalog-api:8000` |
| `MCP_INGESTION_API_URL` | MCP gateway | yes | Trusted Ingestion REST base URL; defaults to `http://ingestion-api:8001` |
| `MCP_CATALOG_MAX_RESPONSE_BYTES` | MCP gateway | no | Maximum Catalog response size; defaults to 2,097,152 bytes |
| `MCP_CONNECT_TIMEOUT_SECONDS` | MCP gateway | no | Catalog connection timeout; defaults to 5 seconds and is bounded from 0.1 to 30 |
| `MCP_POOL_TIMEOUT_SECONDS` | MCP gateway | no | Catalog connection-pool acquisition timeout; defaults to 5 seconds and is bounded from 0.1 to 30 |
| `MCP_READ_TIMEOUT_SECONDS` | MCP gateway | no | Catalog response-read timeout; defaults to 10 seconds and is bounded from 0.1 to 30 |
| `MCP_WRITE_TIMEOUT_SECONDS` | MCP gateway | no | Catalog request-write timeout; defaults to 10 seconds and is bounded from 0.1 to 30 |
| `MCP_RESOURCE_URL` | MCP gateway | protected routes | Canonical public HTTPS MCP gateway resource identifier and inbound token audience, normally the Caddy `/mcp` route |
| `MCP_OBO_CLIENT_ID` | MCP gateway | complete gateway auth | Confidential Auth0 client ID used for RFC 8693 On-Behalf-Of exchange; all-or-none with issuer, audience, and secret |
| `MCP_OBO_CLIENT_SECRET` | MCP gateway | complete gateway auth | Confidential Auth0 client secret used for On-Behalf-Of exchange |
| `MCP_OBO_TOKEN_URL` | MCP gateway | no | OAuth token URL for OBO; defaults to `<AUTH0_ISSUER>/oauth/token` and may override that path, but does not replace the JWT issuer |
| `MCP_OBO_EXPIRY_MARGIN_SECONDS` | MCP gateway | no | Seconds before expiry at which a cached OBO token is refreshed; defaults to 30 |
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
| `CATALOG_TEST_MEDIA_BUCKET` | GCS integration tests | opt-in | Private GCS bucket used only after deployment creates it; skipped when unset |
| `STORECIPE_TEST_REDIS_URL` | Redis integration tests | opt-in | Disposable Redis DSN for rate-limit and AI usage-governance checks |
| `RUN_DOCKER_INTEGRATION` | Docker integration tests | opt-in | Set to `1` to build an isolated Compose project with deterministic local Auth and Catalog substitutes; the harness issues its own test token |

Production values are injected by the deployment environment. `.env` is local only;
`.env.example` contains names and harmless defaults but no secrets. Catalog talks to GCS
with Application Default Credentials when a media bucket is configured; do not commit
ADC files or a JSON service-account key path.
Leaving Auth0/OBO fully unset keeps gateway readiness available for local infrastructure
(`obo_config: not_required`). Any Auth0/OBO value requires the complete gateway auth
bundle. Protected endpoints fail closed while the Auth0 issuer or audience is empty.
Payload startup fails closed when the active key is absent or a retained payload references a
missing key. Old keys must remain configured until affected payloads are re-encrypted or expire.

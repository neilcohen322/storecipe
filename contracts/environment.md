# Environment variables

| Variable | Consumer | Required when | Purpose |
|---|---|---:|---|
| `CATALOG_DATABASE_URL` | Catalog | yes | Catalog-role PostgreSQL DSN |
| `INGESTION_DATABASE_URL` | Ingestion/worker | yes | Ingestion-role PostgreSQL DSN |
| `INGESTION_REDIS_URL` | Ingestion API | yes | Readiness, locks, and rate limiting |
| `INGESTION_CELERY_BROKER_URL` | Ingestion/worker | yes | Celery Redis database 0 |
| `INGESTION_CELERY_RESULT_BACKEND` | Ingestion/worker | yes | Short-lived Celery results in database 1 |
| `AUTH0_ISSUER` | APIs/MCP | protected routes | Expected JWT issuer |
| `AUTH0_AUDIENCE` | APIs/MCP | protected routes | Canonical Storecipe API resource |
| `AUTH0_JWKS_URL` | APIs/MCP | no | Optional JWKS override; defaults to `<issuer>/.well-known/jwks.json` |
| `MCP_RESOURCE_URL` | Catalog | MCP enabled | Canonical HTTPS MCP resource identifier |
| `AI_PROVIDER` | Worker | no | Registered provider adapter; defaults to `openrouter` |
| `AI_API_KEY` | Worker | AI enabled | Secret credential for the selected AI provider |
| `AI_MODEL` | Worker | no | Pinned extraction model; defaults to `openai/gpt-5-nano` |
| `AI_ENDPOINT` | Worker | no | Optional full request endpoint override for the selected provider |
| `AI_TIMEOUT_SECONDS` | Worker | no | Provider request timeout; defaults to 30 seconds |
| `AI_MAX_OUTPUT_TOKENS` | Worker | no | Maximum completion tokens; defaults to 1200 |
| `AI_EXTRACTION_ENABLED` | Worker | no | AI kill switch; defaults false |

Production values are injected by the deployment environment. `.env` is local only;
`.env.example` contains names and harmless defaults but no secrets.
Protected endpoints fail closed while the Auth0 issuer or audience is empty.
`OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, and `OPENROUTER_BASE_URL` remain accepted as
legacy aliases when the generic AI variables are not set. `AI_BASE_URL` is also accepted
as an alias for `AI_ENDPOINT`.

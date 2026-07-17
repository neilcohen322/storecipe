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
| `OPENROUTER_API_KEY` | Worker | AI enabled | Secret model-provider credential |
| `AI_EXTRACTION_ENABLED` | Worker | no | AI kill switch; defaults false |

Production values are injected by the deployment environment. `.env` is local only;
`.env.example` contains names and harmless defaults but no secrets.
Protected endpoints fail closed while the Auth0 issuer or audience is empty.

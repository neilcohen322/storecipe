# Service ownership

| Concern | Owner | Other services interact through |
|---|---|---|
| Recipes, ingredients, steps, tags | Catalog | Catalog REST/application layer |
| Ratings and `catalog_version` | Catalog | Catalog REST/application layer |
| Search and recommendations | Catalog | Catalog REST or MCP |
| MCP endpoint and tools | Catalog | Direct application calls; ingestion REST for imports |
| Import jobs and state machine | Ingestion | Ingestion REST |
| URL/text extraction telemetry | Ingestion | No direct database access by catalog |
| Background work | Ingestion worker | Celery via a dedicated persistent Redis broker |
| Dispatch recovery and retention | Ingestion dispatcher/reconciler | Ingestion PostgreSQL schema |

Both services use one PostgreSQL instance but separate schemas and database roles.
Neither service reads or writes the other service's tables. The worker creates a
recipe only through the M2M-protected catalog endpoint.

## Active URL duplicate invariant

Ingestion owns active URL import-job uniqueness for a user and rejects a duplicate
active job even when the caller sets `duplicatePolicy: allow`. Catalog owns current
recipe-source existence for a user and exposes it only through the M2M-only
`POST /internal/recipes/source-lookup` endpoint; this endpoint is excluded from
the public proxy. When the default Catalog lookup is unavailable, Ingestion returns
`503` instead of bypassing the check.

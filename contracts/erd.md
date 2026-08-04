# Minimal data model and gateway boundary

This is the logical data contract. Physical changes are managed through Alembic
revisions; IDs are UUIDs and timestamps are timezone-aware.

The standalone `services/mcp_gateway` service is deliberately absent from this
diagram. It is stateless and has no PostgreSQL, Redis, ORM, or Catalog implementation
access. It adapts MCP calls to the Catalog REST contract; Catalog remains the data
owner.

```mermaid
erDiagram
  USER ||--o{ RECIPE : owns
  USER ||--o{ RATING : gives
  USER ||--o{ RECIPE_CREATION_IDEMPOTENCY : keys
  USER ||--o{ IMPORT_JOB : starts
  USER ||--o{ AI_DAILY_USAGE : budgets
  USER ||--o{ LLM_INVOCATION : consumes
  IMPORT_JOB ||--o{ LLM_INVOCATION : records
  RECIPE ||--|{ INGREDIENT : contains
  RECIPE ||--|{ INSTRUCTION : contains
  RECIPE ||--o{ RECIPE_TAG : classified
  TAG ||--o{ RECIPE_TAG : applies
  RECIPE ||--o| RATING : receives
  RECIPE ||--o| RECIPE_CREATION_IDEMPOTENCY : replays
  IMPORT_JOB ||--o| RECIPE : creates

  USER {
    uuid id PK
    string auth_subject UK
    bigint catalog_version
  }
  RECIPE {
    uuid id PK
    uuid user_id FK
    uuid import_job_id UK "nullable"
    string title
    string source_url "nullable"
    string source_fingerprint "nullable"
    int servings "nullable"
    int prep_minutes "nullable"
    int cook_minutes "nullable"
    int total_minutes "nullable"
  }
  RECIPE_CREATION_IDEMPOTENCY {
    uuid user_id PK,FK
    string idempotency_key PK
    string payload_hash
    uuid recipe_id UK,FK
    datetime created_at
  }
  INGREDIENT {
    uuid id PK
    uuid recipe_id FK
    int position
    string raw_text
    string name
    string normalized_name
    decimal quantity "nullable"
    string unit "nullable"
  }
  INSTRUCTION {
    uuid id PK
    uuid recipe_id FK
    int position
    string text
  }
  RATING {
    uuid user_id PK,FK
    uuid recipe_id PK,FK
    int value "1..5"
  }
  IMPORT_JOB {
    uuid id PK
    uuid user_id
    string source_type "url|text"
    string status
    int attempt_count
    string source_fingerprint "nullable"
    uuid created_recipe_id "nullable"
    datetime last_heartbeat_at "nullable"
    string error_category "nullable"
  }
  AI_DAILY_USAGE {
    string owner_subject PK
    date budget_date_utc PK
    int reserved_tokens
    int consumed_tokens
  }
  LLM_INVOCATION {
    uuid id PK
    uuid provider_operation_id UK,FK
    string owner_subject
    date budget_date_utc
    uuid import_job_id FK
    string state "reserved|succeeded|failed|ambiguous"
    string model_name
    string provider_name
    string prompt_version
    int reserved_tokens
    int input_tokens "nullable"
    int output_tokens "nullable"
    int total_tokens "nullable"
    int cost_microunits "nullable"
    int latency_ms "nullable"
    datetime request_deadline_at
    datetime settled_at "nullable"
    string safe_error_category "nullable"
  }
```

`catalog_version` is incremented in the same Catalog transaction as recipe/rating
changes. Catalog enforces unique `(user_id, import_job_id)` for import replay safety.

Public recipe creation also writes one `RECIPE_CREATION_IDEMPOTENCY` row in the same
transaction as the recipe and catalog-version increment. Its composite primary key
scopes an `Idempotency-Key` to one user, its SHA-256 `payload_hash` distinguishes an
exact replay from a conflicting reuse, and its unique `recipe_id` prevents one
creation record from being attached to multiple recipes. Deleting the user or recipe
cascades the record. This table is owned by Catalog; the gateway keeps no retry or
idempotency state.

Ingestion owns active URL import-job uniqueness; Catalog owns current recipe-source
existence for each user.

`AI_DAILY_USAGE` is keyed by owner subject and UTC budget date. Reservations increase
`reserved_tokens` before an invocation; settled invocations move actual usage into
`consumed_tokens`. UTC midnight creates the next budget row, keeping daily limits
independent of local time zones. `LLM_INVOCATION` is durable per provider operation so
ambiguous outcomes can be reconciled without spending a second reservation.

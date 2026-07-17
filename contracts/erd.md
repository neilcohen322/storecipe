# Minimal data model

This is the logical data contract. Physical changes are managed through Alembic
revisions; IDs are UUIDs and timestamps are timezone-aware.

```mermaid
erDiagram
  USER ||--o{ RECIPE : owns
  USER ||--o{ RATING : gives
  USER ||--o{ IMPORT_JOB : starts
  USER ||--o{ LLM_INVOCATION : consumes
  RECIPE ||--|{ INGREDIENT : contains
  RECIPE ||--|{ INSTRUCTION : contains
  RECIPE ||--o{ RECIPE_TAG : classified
  TAG ||--o{ RECIPE_TAG : applies
  RECIPE ||--o| RATING : receives
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
  INGREDIENT {
    uuid id PK
    uuid recipe_id FK
    int position
    string raw_text
    string name
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
  LLM_INVOCATION {
    uuid id PK
    uuid user_id
    uuid import_job_id FK
    string model
    string provider
    string prompt_version
    int input_tokens
    int output_tokens
    decimal estimated_cost
    int latency_ms
    string error_category "nullable"
  }
```

`catalog_version` is incremented in the same catalog transaction as recipe/rating
changes. Catalog enforces unique `(user_id, import_job_id)` for replay safety.

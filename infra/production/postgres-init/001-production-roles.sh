#!/bin/sh
set -eu

: "${CATALOG_DB_PASSWORD:?CATALOG_DB_PASSWORD is required}"
: "${INGESTION_DB_PASSWORD:?INGESTION_DB_PASSWORD is required}"

psql --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=catalog_password="$CATALOG_DB_PASSWORD" \
  --set=ingestion_password="$INGESTION_DB_PASSWORD" <<'SQL'
CREATE ROLE catalog_app LOGIN PASSWORD :'catalog_password';
CREATE ROLE ingestion_app LOGIN PASSWORD :'ingestion_password';
CREATE SCHEMA catalog AUTHORIZATION catalog_app;
CREATE SCHEMA ingestion AUTHORIZATION ingestion_app;
GRANT CONNECT ON DATABASE storecipe TO catalog_app, ingestion_app;
GRANT CREATE ON DATABASE storecipe TO catalog_app, ingestion_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL

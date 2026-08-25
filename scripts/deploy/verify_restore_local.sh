#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
TMP_DIR=$(mktemp -d)
SOURCE_CONTAINER="storecipe-backup-source-$$"
cleanup() {
  docker rm -f "$SOURCE_CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

mkdir -p /var/lib/storecipe "$TMP_DIR/fake-gcs/daily" "$TMP_DIR/fake-gcs/account-deletions" "$TMP_DIR/bin"
cp "$ROOT_DIR/services/catalog/tests/fixtures/bin/gcloud" "$TMP_DIR/bin/gcloud"
chmod 0755 "$TMP_DIR/bin/gcloud"

SOURCE_PASSWORD=$(openssl rand -hex 24)
printf 'POSTGRES_PASSWORD=%s\nPOSTGRES_USER=source_admin\nPOSTGRES_DB=storecipe\n' \
  "$SOURCE_PASSWORD" > "$TMP_DIR/source.env"
docker run --detach --name "$SOURCE_CONTAINER" --env-file "$TMP_DIR/source.env" \
  postgres:17-alpine >/dev/null
for _ in $(seq 1 30); do
  if docker exec "$SOURCE_CONTAINER" pg_isready -U source_admin -d storecipe >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$SOURCE_CONTAINER" pg_isready -U source_admin -d storecipe >/dev/null

docker exec -i -e PGPASSWORD="$SOURCE_PASSWORD" "$SOURCE_CONTAINER" \
  psql -U source_admin -d storecipe -v ON_ERROR_STOP=1 <<'SQL' >/dev/null
CREATE SCHEMA catalog;
CREATE SCHEMA ingestion;
CREATE TABLE catalog.alembic_version_catalog (version_num varchar(32) PRIMARY KEY);
CREATE TABLE ingestion.alembic_version_ingestion (version_num varchar(32) PRIMARY KEY);
INSERT INTO catalog.alembic_version_catalog VALUES ('20260824_02');
INSERT INTO ingestion.alembic_version_ingestion VALUES ('20260824_01');
CREATE TABLE catalog.users (
  id uuid PRIMARY KEY,
  auth_subject text UNIQUE NOT NULL
);
CREATE TABLE catalog.recipes (
  id uuid PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES catalog.users(id) ON DELETE CASCADE
);
CREATE TABLE catalog.tags (id uuid PRIMARY KEY);
CREATE TABLE catalog.recipe_tags (
  tag_id uuid NOT NULL REFERENCES catalog.tags(id),
  recipe_id uuid NOT NULL REFERENCES catalog.recipes(id) ON DELETE CASCADE
);
CREATE TABLE catalog.account_deletions (
  id uuid PRIMARY KEY,
  auth_subject text UNIQUE NOT NULL,
  status text NOT NULL,
  request_id text NOT NULL,
  attempts integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  completed_at timestamptz,
  expires_at timestamptz,
  lease_owner text,
  lease_expires_at timestamptz,
  last_error text,
  catalog_user_id uuid,
  media_snapshot json
);
CREATE TABLE ingestion.import_jobs (
  id uuid PRIMARY KEY,
  owner_subject text NOT NULL
);
CREATE TABLE ingestion.llm_invocations (
  id uuid PRIMARY KEY,
  owner_subject text NOT NULL
);
CREATE TABLE ingestion.ingredient_normalization_operations (
  id uuid PRIMARY KEY,
  owner_subject text NOT NULL
);
CREATE TABLE ingestion.ai_daily_usage (
  owner_subject text PRIMARY KEY
);
CREATE TABLE ingestion.account_deletion_tombstones (
  subject text PRIMARY KEY,
  deleted_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  CHECK (expires_at > deleted_at)
);
INSERT INTO catalog.users VALUES
  ('11111111-1111-1111-1111-111111111111', 'auth0|deleted-chef'),
  ('44444444-4444-4444-4444-444444444444', 'auth0|active-chef');
INSERT INTO catalog.recipes VALUES
  ('22222222-2222-2222-2222-222222222222', '11111111-1111-1111-1111-111111111111'),
  ('55555555-5555-5555-5555-555555555555', '44444444-4444-4444-4444-444444444444');
INSERT INTO ingestion.import_jobs VALUES
  ('33333333-3333-3333-3333-333333333333', 'auth0|deleted-chef'),
  ('66666666-6666-6666-6666-666666666666', 'auth0|active-chef');
SQL

OBJECT=storecipe-20260824T000000Z.dump
docker exec -e PGPASSWORD="$SOURCE_PASSWORD" "$SOURCE_CONTAINER" \
  pg_dump -U source_admin -d storecipe --format=custom --file="/tmp/$OBJECT"
docker cp "$SOURCE_CONTAINER:/tmp/$OBJECT" "$TMP_DIR/fake-gcs/daily/$OBJECT" >/dev/null
(cd "$TMP_DIR/fake-gcs/daily" && sha256sum "$OBJECT" > "$OBJECT.sha256")

cat > "$TMP_DIR/fake-gcs/account-deletions/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.json" <<'EOF'
{"deletionId":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa","expiresAt":"2026-11-22T00:00:00Z","requestId":"restore-proof","requestedAt":"2026-08-24T00:00:00Z","subject":"auth0|deleted-chef"}
EOF
cp "$TMP_DIR/fake-gcs/account-deletions/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.json" \
  "$TMP_DIR/fake-gcs/account-deletions/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.committed"

FAKE_GCS_ROOT="$TMP_DIR/fake-gcs" GCLOUD_BIN="$TMP_DIR/bin/gcloud" \
  CATALOG_ACCOUNT_DELETION_JOURNAL_BUCKET=fake-storecipe \
  bash "$ROOT_DIR/scripts/deploy/restore_verify.sh" "gs://fake-storecipe/daily/$OBJECT"

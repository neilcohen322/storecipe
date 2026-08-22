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

mkdir -p /var/lib/storecipe "$TMP_DIR/fake-gcs/daily" "$TMP_DIR/bin"
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

docker exec -e PGPASSWORD="$SOURCE_PASSWORD" "$SOURCE_CONTAINER" \
  psql -U source_admin -d storecipe -v ON_ERROR_STOP=1 -c \
  "CREATE SCHEMA catalog; CREATE SCHEMA ingestion; CREATE TABLE catalog.alembic_version_catalog (version_num varchar(32) PRIMARY KEY); CREATE TABLE ingestion.alembic_version_ingestion (version_num varchar(32) PRIMARY KEY); INSERT INTO catalog.alembic_version_catalog VALUES ('20260812_01'); INSERT INTO ingestion.alembic_version_ingestion VALUES ('20260815_01'); CREATE TABLE catalog.users (id integer PRIMARY KEY); CREATE TABLE catalog.recipes (id integer PRIMARY KEY, user_id integer NOT NULL REFERENCES catalog.users(id)); CREATE TABLE ingestion.import_jobs (id integer PRIMARY KEY); INSERT INTO catalog.users VALUES (1); INSERT INTO catalog.recipes VALUES (1, 1); INSERT INTO ingestion.import_jobs VALUES (1);" >/dev/null

OBJECT=storecipe-20260822T000000Z.dump
docker exec -e PGPASSWORD="$SOURCE_PASSWORD" "$SOURCE_CONTAINER" \
  pg_dump -U source_admin -d storecipe --format=custom --file="/tmp/$OBJECT"
docker cp "$SOURCE_CONTAINER:/tmp/$OBJECT" "$TMP_DIR/fake-gcs/daily/$OBJECT" >/dev/null
(cd "$TMP_DIR/fake-gcs/daily" && sha256sum "$OBJECT" > "$OBJECT.sha256")

FAKE_GCS_ROOT="$TMP_DIR/fake-gcs" GCLOUD_BIN="$TMP_DIR/bin/gcloud" \
  bash "$ROOT_DIR/scripts/deploy/restore_verify.sh" "gs://fake-storecipe/daily/$OBJECT"

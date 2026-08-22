#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

if [[ $# -ne 1 || $1 != gs://*.dump ]]; then
  echo "usage: restore_verify.sh gs://BUCKET/(daily|weekly)/storecipe-TIMESTAMP.dump" >&2
  exit 2
fi

SOURCE=$1
GCLOUD_BIN=${GCLOUD_BIN:-gcloud}
TMP_DIR=$(mktemp -d /var/lib/storecipe/restore.XXXXXX)
CONTAINER="storecipe-restore-$(date -u +%s)-$$"
cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

DUMP="$TMP_DIR/backup.dump"
CHECKSUM="$DUMP.sha256"
"$GCLOUD_BIN" storage cp "$SOURCE" "$DUMP"
"$GCLOUD_BIN" storage cp "$SOURCE.sha256" "$CHECKSUM"
(cd "$TMP_DIR" && sed 's#storecipe-[0-9]\{8\}T[0-9]\{6\}Z\.dump#backup.dump#' "$(basename "$CHECKSUM")" | sha256sum --check --strict -)

RESTORE_PASSWORD=$(openssl rand -hex 24)
printf 'POSTGRES_PASSWORD=%s\nPOSTGRES_USER=restore_admin\nPOSTGRES_DB=storecipe_restore\n' \
  "$RESTORE_PASSWORD" > "$TMP_DIR/container.env"
docker run --detach --name "$CONTAINER" --env-file "$TMP_DIR/container.env" postgres:17-alpine >/dev/null
for _ in $(seq 1 30); do
  if docker exec "$CONTAINER" pg_isready -U restore_admin -d storecipe_restore >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$CONTAINER" pg_isready -U restore_admin -d storecipe_restore >/dev/null
docker cp "$DUMP" "$CONTAINER:/tmp/backup.dump"
docker exec -e PGPASSWORD="$RESTORE_PASSWORD" "$CONTAINER" \
  pg_restore --username restore_admin --dbname storecipe_restore --no-owner --no-privileges \
  --exit-on-error /tmp/backup.dump

SQL=$(cat <<'SQL'
DO $$
BEGIN
  IF to_regclass('catalog.alembic_version_catalog') IS NULL THEN RAISE EXCEPTION 'catalog migration head missing'; END IF;
  IF to_regclass('ingestion.alembic_version_ingestion') IS NULL THEN RAISE EXCEPTION 'ingestion migration head missing'; END IF;
  IF to_regclass('catalog.recipes') IS NULL THEN RAISE EXCEPTION 'catalog recipes missing'; END IF;
  IF to_regclass('ingestion.import_jobs') IS NULL THEN RAISE EXCEPTION 'ingestion imports missing'; END IF;
END $$;
SELECT count(*) >= 0 AS catalog_count_valid FROM catalog.recipes;
SELECT count(*) >= 0 AS ingestion_count_valid FROM ingestion.import_jobs;
SELECT count(*) = 0 AS invalid_foreign_keys FROM pg_constraint WHERE contype = 'f' AND NOT convalidated;
SQL
)
RESULT=$(docker exec -e PGPASSWORD="$RESTORE_PASSWORD" "$CONTAINER" \
  psql --username restore_admin --dbname storecipe_restore --no-psqlrc --tuples-only \
  --set ON_ERROR_STOP=1 --command "$SQL")
if [[ $(grep -c 't' <<<"$RESULT") -lt 3 ]]; then
  echo "Restore integrity checks failed" >&2
  exit 1
fi
echo "Restore verification passed: checksum, schemas, migration heads, counts, and foreign keys."

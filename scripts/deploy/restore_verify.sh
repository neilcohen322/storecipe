#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

if [[ $# -ne 1 || $1 != gs://*.dump ]]; then
  echo "usage: restore_verify.sh gs://BUCKET/(daily|weekly)/storecipe-TIMESTAMP.dump" >&2
  exit 2
fi
if [[ -z ${CATALOG_ACCOUNT_DELETION_JOURNAL_BUCKET:-} ]]; then
  echo "CATALOG_ACCOUNT_DELETION_JOURNAL_BUCKET is required for restore verification" >&2
  exit 2
fi
command -v python3 >/dev/null || {
  echo "python3 is required to replay the account-deletion journal" >&2
  exit 2
}

SOURCE=$1
JOURNAL_BUCKET=$CATALOG_ACCOUNT_DELETION_JOURNAL_BUCKET
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

mkdir -p "$TMP_DIR/journal"
journal_ls_err="$TMP_DIR/journal-ls.err"
set +e
journal_listing=$("$GCLOUD_BIN" storage ls "gs://${JOURNAL_BUCKET}/account-deletions/" 2>"$journal_ls_err")
ls_status=$?
set -e
if (( ls_status != 0 )); then
  if grep -Eqi 'matched no objects' "$journal_ls_err"; then
    journal_listing=""
  else
    echo "Failed to list account-deletion journal objects" >&2
    cat "$journal_ls_err" >&2
    exit 1
  fi
fi
if [[ -n ${journal_listing:-} ]]; then
  "$GCLOUD_BIN" storage cp --recursive "gs://${JOURNAL_BUCKET}/account-deletions/" "$TMP_DIR/journal/"
fi

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

replay_sql() {
  python3 - "$1" <<'PY'
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

path = Path(sys.argv[1])
entry = json.loads(path.read_text(encoding="utf-8"))
required = ("deletionId", "subject", "requestedAt", "expiresAt", "requestId")
if any(not entry.get(key) for key in required):
    raise SystemExit(f"invalid journal object: {path.name}")
completed = path.with_name(f"{path.stem}.completed")
if completed.is_file():
    marker = json.loads(completed.read_text(encoding="utf-8"))
    raw = marker.get("completedAt")
    if raw:
        completed_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > completed_at + timedelta(days=90):
            raise SystemExit(10)
    else:
        expires = datetime.fromisoformat(str(entry["expiresAt"]).replace("Z", "+00:00"))
        if expires <= datetime.now(timezone.utc):
            raise SystemExit(10)

def sql_str(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"

subject = sql_str(entry["subject"])
deletion_id = sql_str(entry["deletionId"])
requested_at = sql_str(entry["requestedAt"])
expires_at = sql_str(entry["expiresAt"])
request_id = sql_str(entry["requestId"])
print(
    f"""
DO $$
DECLARE
  v_user_id uuid;
  v_media json;
BEGIN
  IF to_regclass('catalog.users') IS NOT NULL THEN
    SELECT u.id INTO v_user_id
    FROM catalog.users AS u
    WHERE u.auth_subject = {subject};
    IF v_user_id IS NOT NULL
       AND to_regclass('catalog.recipe_images') IS NOT NULL
       AND to_regclass('catalog.recipes') IS NOT NULL THEN
      SELECT json_agg(json_build_object(
        'key', ri.object_key,
        'generation', ri.object_generation
      ))
      INTO v_media
      FROM catalog.recipe_images AS ri
      JOIN catalog.recipes AS r ON r.id = ri.recipe_id
      WHERE r.user_id = v_user_id;
      v_media := COALESCE(v_media, '[]'::json);
    END IF;
    DELETE FROM catalog.users WHERE auth_subject = {subject};
  END IF;
  IF to_regclass('catalog.tags') IS NOT NULL AND to_regclass('catalog.recipe_tags') IS NOT NULL THEN
    DELETE FROM catalog.tags WHERE NOT EXISTS (
      SELECT 1 FROM catalog.recipe_tags WHERE tag_id = catalog.tags.id
    );
  END IF;
  IF to_regclass('catalog.account_deletions') IS NOT NULL THEN
    INSERT INTO catalog.account_deletions
      (id, auth_subject, status, request_id, attempts, next_attempt_at, created_at,
       updated_at, completed_at, expires_at, lease_owner, lease_expires_at,
       catalog_user_id, media_snapshot, journal_committed)
    VALUES
      ({deletion_id}::uuid, {subject}, 'pending', {request_id}, 0, NOW(),
       {requested_at}::timestamptz, NOW(), NULL,
       {expires_at}::timestamptz, NULL, NULL, v_user_id, v_media, TRUE)
    ON CONFLICT (auth_subject) DO UPDATE SET
      status = CASE
        WHEN catalog.account_deletions.status = 'completed'
         AND catalog.account_deletions.expires_at IS NOT NULL
         AND catalog.account_deletions.expires_at > NOW()
        THEN catalog.account_deletions.status
        ELSE 'pending'
      END,
      completed_at = CASE
        WHEN catalog.account_deletions.status = 'completed'
         AND catalog.account_deletions.expires_at IS NOT NULL
         AND catalog.account_deletions.expires_at > NOW()
        THEN catalog.account_deletions.completed_at
        ELSE NULL
      END,
      request_id = EXCLUDED.request_id,
      expires_at = CASE
        WHEN catalog.account_deletions.status = 'completed'
         AND catalog.account_deletions.expires_at IS NOT NULL
         AND catalog.account_deletions.expires_at > NOW()
        THEN catalog.account_deletions.expires_at
        ELSE EXCLUDED.expires_at
      END,
      catalog_user_id = CASE
        WHEN catalog.account_deletions.status = 'completed'
         AND catalog.account_deletions.expires_at IS NOT NULL
         AND catalog.account_deletions.expires_at > NOW()
        THEN catalog.account_deletions.catalog_user_id
        ELSE COALESCE(EXCLUDED.catalog_user_id, catalog.account_deletions.catalog_user_id)
      END,
      media_snapshot = CASE
        WHEN catalog.account_deletions.status = 'completed'
         AND catalog.account_deletions.expires_at IS NOT NULL
         AND catalog.account_deletions.expires_at > NOW()
        THEN catalog.account_deletions.media_snapshot
        ELSE COALESCE(EXCLUDED.media_snapshot, catalog.account_deletions.media_snapshot)
      END,
      journal_committed = CASE
        WHEN catalog.account_deletions.status = 'completed'
         AND catalog.account_deletions.expires_at IS NOT NULL
         AND catalog.account_deletions.expires_at > NOW()
        THEN catalog.account_deletions.journal_committed
        ELSE TRUE
      END,
      next_attempt_at = CASE
        WHEN catalog.account_deletions.status = 'completed'
         AND catalog.account_deletions.expires_at IS NOT NULL
         AND catalog.account_deletions.expires_at > NOW()
        THEN catalog.account_deletions.next_attempt_at
        ELSE NOW()
      END,
      updated_at = NOW(), last_error = NULL,
      lease_owner = NULL, lease_expires_at = NULL;
  END IF;
  IF to_regclass('ingestion.llm_invocations') IS NOT NULL THEN
    DELETE FROM ingestion.llm_invocations WHERE owner_subject = {subject};
  END IF;
  IF to_regclass('ingestion.ingredient_normalization_operations') IS NOT NULL THEN
    DELETE FROM ingestion.ingredient_normalization_operations WHERE owner_subject = {subject};
  END IF;
  IF to_regclass('ingestion.ai_daily_usage') IS NOT NULL THEN
    DELETE FROM ingestion.ai_daily_usage WHERE owner_subject = {subject};
  END IF;
  IF to_regclass('ingestion.import_jobs') IS NOT NULL THEN
    DELETE FROM ingestion.import_jobs WHERE owner_subject = {subject};
  END IF;
  IF to_regclass('ingestion.account_deletion_tombstones') IS NOT NULL THEN
    INSERT INTO ingestion.account_deletion_tombstones (subject, deleted_at, expires_at)
    VALUES ({subject}, {requested_at}::timestamptz, {expires_at}::timestamptz)
    ON CONFLICT (subject) DO UPDATE SET
      deleted_at = EXCLUDED.deleted_at, expires_at = EXCLUDED.expires_at;
  END IF;
END $$;
SELECT NOT EXISTS (
  SELECT 1 FROM catalog.users WHERE auth_subject = {subject}
) AS catalog_subject_removed;
SELECT EXISTS (
  SELECT 1 FROM catalog.account_deletions WHERE auth_subject = {subject}
) AS catalog_tombstone_present;
SELECT NOT EXISTS (
  SELECT 1 FROM ingestion.import_jobs WHERE owner_subject = {subject}
) AS ingestion_subject_removed;
SELECT EXISTS (
  SELECT 1 FROM ingestion.account_deletion_tombstones WHERE subject = {subject}
) AS ingestion_tombstone_present;
"""
)
PY
}

shopt -s nullglob
journal_files=("$TMP_DIR/journal"/*.json "$TMP_DIR/journal"/account-deletions/*.json)
replayed=0
for journal_file in "${journal_files[@]}"; do
  [[ -f $journal_file ]] || continue
  committed="${journal_file%.json}.committed"
  [[ -f $committed ]] || continue
  set +e
  replay_sql "$journal_file" > "$TMP_DIR/replay.sql"
  replay_status=$?
  set -e
  if (( replay_status == 10 )); then
    continue
  fi
  if (( replay_status != 0 )); then
    echo "Account-deletion journal object is invalid: ${journal_file##*/}" >&2
    exit 1
  fi
  replayed=1
  docker cp "$TMP_DIR/replay.sql" "$CONTAINER:/tmp/replay.sql"
  replay_result=$(docker exec -e PGPASSWORD="$RESTORE_PASSWORD" "$CONTAINER" \
    psql --username restore_admin --dbname storecipe_restore --no-psqlrc --tuples-only \
    --set ON_ERROR_STOP=1 --file /tmp/replay.sql)
  if [[ $(grep -c 't' <<<"$replay_result") -lt 4 ]]; then
    echo "Account-deletion journal replay failed for ${journal_file##*/}" >&2
    exit 1
  fi
done

if (( replayed == 1 )); then
  echo "Restore verification passed: checksum, schemas, migration heads, counts, foreign keys, and deletion-journal replay."
else
  echo "Restore verification passed: checksum, schemas, migration heads, counts, and foreign keys. Journal prefix was empty."
fi

#!/usr/bin/env bash
# Restores a backup made by backup.sh into the running Docker containers.
#   ./deploy/restore.sh backups/20261105-021500
# THIS REPLACES the current database and uploaded files. The .env on this machine must have
# the same DOCUMENT_ENCRYPTION_KEY as the machine the backup came from.
set -euo pipefail

cd "$(dirname "$0")/.."
src="${1:?Usage: ./deploy/restore.sh backups/<date-time>}"
[ -f "$src/med_db.dump" ] && [ -f "$src/uploads.tar.gz" ] || { echo "No backup in $src"; exit 1; }

echo "Checking the backup files..."
(cd "$src" && sha256sum -c SHA256SUMS)

read -r -p "This REPLACES the database and uploads with $src. Type RESTORE to continue: " answer
[ "$answer" = "RESTORE" ] || { echo "Cancelled."; exit 1; }

# Stop the app so nothing writes during the restore. It starts again only when both the
# database and the uploaded files are back.
docker compose stop api worker

# Database: drop and recreate every object from the dump, all or nothing
docker compose cp "$src/med_db.dump" db:/tmp/restore.dump
docker compose exec -T db pg_restore -U postgres -d med_db --clean --if-exists \
    --single-transaction --exit-on-error /tmp/restore.dump
docker compose exec -T db rm -f /tmp/restore.dump

# Uploaded files: a one-off container (same image and uploads volume) empties the folder and
# unpacks the backup, mounted read-only, while the API is still stopped
tarball="$(cd "$src" && pwd)/uploads.tar.gz"
docker compose run --rm --no-deps -T -v "$tarball:/restore/uploads.tar.gz:ro" api sh -c \
    'find /app/backend/uploads -mindepth 1 -delete && tar xzf /restore/uploads.tar.gz -C /app/backend/uploads'

docker compose start api worker

echo "Checking the restored database..."
docker compose exec -T db psql -U postgres -d med_db -Atc \
    "SELECT 'tables: ' || count(*) FROM pg_tables WHERE schemaname = 'public'
     UNION ALL SELECT 'patients: ' || count(*) FROM patients
     UNION ALL SELECT 'saved reports: ' || count(*) FROM health_reports
     UNION ALL SELECT 'audit chain broken at entry: ' || coalesce(verify_audit_chain()::text, 'none (intact)')"
echo "Restore finished."

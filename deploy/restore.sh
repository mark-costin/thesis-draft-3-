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

# Stop the app so nothing writes during the restore
docker compose stop api worker

# Database: drop and recreate every object from the dump, all or nothing
docker compose cp "$src/med_db.dump" db:/tmp/restore.dump
docker compose exec -T db pg_restore -U postgres -d med_db --clean --if-exists \
    --single-transaction --exit-on-error /tmp/restore.dump
docker compose exec -T db rm -f /tmp/restore.dump

docker compose start api worker

# Uploaded files: empty the folder, then unpack the backup
docker compose cp "$src/uploads.tar.gz" api:/tmp/restore.tar.gz
docker compose exec -T api sh -c \
    'find /app/backend/uploads -mindepth 1 -delete && tar xzf /tmp/restore.tar.gz -C /app/backend/uploads'
docker compose exec -T -u root api rm -f /tmp/restore.tar.gz  # docker cp made it root's file

echo "Checking the restored database..."
docker compose exec -T db psql -U postgres -d med_db -Atc \
    "SELECT 'tables: ' || count(*) FROM pg_tables WHERE schemaname = 'public'
     UNION ALL SELECT 'patients: ' || count(*) FROM patients
     UNION ALL SELECT 'saved reports: ' || count(*) FROM health_reports
     UNION ALL SELECT 'audit chain broken at entry: ' || coalesce(verify_audit_chain()::text, 'none (intact)')"
echo "Restore finished."

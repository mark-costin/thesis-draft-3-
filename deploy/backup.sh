#!/usr/bin/env bash
# Backs up the Docker database and the encrypted uploads into backups/<date-time>/.
#   ./deploy/backup.sh            (from the repo root, with docker compose running)
# On the server, run it every night with cron, e.g.:
#   15 2 * * * cd /srv/thesis && ./deploy/backup.sh >> backups/backup.log 2>&1
#
# The backup holds health data: keep backups/ out of git (it is in .gitignore) and copy it
# to another machine. DOCUMENT_ENCRYPTION_KEY is NOT in the backup on purpose; keep the key
# somewhere else, or the uploaded files cannot be read after a restore.
set -euo pipefail

cd "$(dirname "$0")/.."
KEEP_DAYS="${KEEP_DAYS:-14}"
stamp="$(date +%Y%m%d-%H%M%S)"
dest="backups/$stamp"
mkdir -p "$dest"

echo "Backing up to $dest"

# Database: custom format (-Fc), written inside the container and copied out
docker compose exec -T db pg_dump -U postgres -d med_db -Fc -f /tmp/med_db.dump
docker compose cp db:/tmp/med_db.dump "$dest/med_db.dump"
docker compose exec -T db rm -f /tmp/med_db.dump

# Uploaded files (already encrypted with DOCUMENT_ENCRYPTION_KEY)
docker compose exec -T api tar czf /tmp/uploads.tar.gz -C /app/backend/uploads .
docker compose cp api:/tmp/uploads.tar.gz "$dest/uploads.tar.gz"
docker compose exec -T api rm -f /tmp/uploads.tar.gz

# Checksums, checked again by restore.sh
(cd "$dest" && sha256sum med_db.dump uploads.tar.gz > SHA256SUMS)

# Remove backups older than KEEP_DAYS days
find backups -mindepth 1 -maxdepth 1 -type d -name '20*' -mtime +"$KEEP_DAYS" -exec rm -rf {} +

echo "Done: $(du -sh "$dest" | cut -f1) in $dest"

#!/bin/bash
# create_test_db.sh
# Purpose: (Re)builds med_db_test from the scripts in database/, so pytest never touches med_db.
# Run from the repo root:  bash tests/create_test_db.sh
# Safe to rerun: it drops and recreates med_db_test only.

set -euo pipefail

DB=med_db_test
cd "$(dirname "$0")/../database"

read -r -s -p "postgres password: " PGPASSWORD
echo
export PGPASSWORD
PSQL=(psql -h localhost -U postgres -v ON_ERROR_STOP=1 -q)

echo "Recreating $DB"
"${PSQL[@]}" -d postgres -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" -c "CREATE DATABASE $DB"

# 00_roles.sh creates med_app, but roles belong to the whole server and med_app already
# exists from med_db. Only the database-level part of 00_roles.sh is needed here.
"${PSQL[@]}" -d "$DB" <<EOF
REVOKE CONNECT ON DATABASE $DB FROM PUBLIC;
GRANT CONNECT ON DATABASE $DB TO med_app;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO med_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO med_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO med_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
EOF

# Numbered scripts in order (01 ... 21, then 99), then the draft care plan templates
for f in $(ls [0-9][0-9]_*.sql | sort); do
    echo "  $f"
    "${PSQL[@]}" -d "$DB" -f "$f"
done
echo "  seed_care_plan_templates.sql"
"${PSQL[@]}" -d "$DB" -f seed_care_plan_templates.sql

TABLES=$("${PSQL[@]}" -d "$DB" -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
echo "Done: $DB has $TABLES tables (expected 19)"

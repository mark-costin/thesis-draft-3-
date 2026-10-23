#!/bin/bash
# 00_roles.sh
# Purpose: Creates the least-privilege application role (med_app).
# Zero Trust: the app never connects as the postgres superuser, which bypasses
# every privilege and Row-Level Security rule.
# Runs first (Docker runs /docker-entrypoint-initdb.d files in alphabetical order).
# Requires APP_DB_PASSWORD in the environment (.env) - never hardcode it here.

set -euo pipefail
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 \
     -v app_pw="$APP_DB_PASSWORD" \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" <<-'EOSQL'
	-- Restricted login role: no superuser, no role/database creation, cannot bypass RLS
	CREATE ROLE med_app LOGIN PASSWORD :'app_pw'
	    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;

	-- Only med_app (and the owner) may connect to this database
	REVOKE CONNECT ON DATABASE :"DBNAME" FROM PUBLIC;
	GRANT CONNECT ON DATABASE :"DBNAME" TO med_app;

	-- No one can create objects in public except the owner
	REVOKE ALL ON SCHEMA public FROM PUBLIC;
	GRANT USAGE ON SCHEMA public TO med_app;

	-- Every table created after this point grants med_app data access only (no DDL).
	-- 99_security.sql then narrows this further per table.
	ALTER DEFAULT PRIVILEGES IN SCHEMA public
	    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO med_app;
	ALTER DEFAULT PRIVILEGES IN SCHEMA public
	    GRANT USAGE, SELECT ON SEQUENCES TO med_app;

	-- Functions are not executable by everyone by default
	ALTER DEFAULT PRIVILEGES IN SCHEMA public
	    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
EOSQL

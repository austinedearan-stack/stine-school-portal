#!/bin/sh
# Runs once, as the PostgreSQL bootstrap superuser, when the data directory is first initialised.
# Creates least-privilege roles (spec §27, ARCHITECTURE D14):
#   portal_owner - owns the database/schema; used ONLY to run migrations
#   portal_app   - runtime role used by the web app: DML only, no DDL, not superuser, not owner
# Audit tables additionally lose UPDATE/DELETE for portal_app in the Phase 2 migration.
set -eu

: "${DB_NAME:?DB_NAME required}"
: "${DB_OWNER_USER:?DB_OWNER_USER required}"
: "${DB_OWNER_PASSWORD:?DB_OWNER_PASSWORD required}"
: "${DB_USER:?DB_USER required}"
: "${DB_PASSWORD:?DB_PASSWORD required}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner="$DB_OWNER_USER" -v owner_pw="$DB_OWNER_PASSWORD" \
  -v app="$DB_USER" -v app_pw="$DB_PASSWORD" -v db="$DB_NAME" <<'SQL'
CREATE ROLE :"owner" LOGIN PASSWORD :'owner_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE ROLE :"app"   LOGIN PASSWORD :'app_pw'   NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE DATABASE :"db" OWNER :"owner";
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db" TO :"app";
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$DB_NAME" \
  -v owner="$DB_OWNER_USER" -v app="$DB_USER" <<'SQL'
-- btree_gist is needed for the timetable exclusion constraints (trusted extension, created by superuser here).
CREATE EXTENSION IF NOT EXISTS btree_gist;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO :"owner";
GRANT USAGE ON SCHEMA public TO :"app";
-- Objects later created by the owner (migrations) are usable by the app role, DML only.
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO :"app";
-- Session-level safety limits for the runtime role (see DATABASE.md audit seals).
ALTER ROLE :"app" SET statement_timeout = '30s';
ALTER ROLE :"app" SET idle_in_transaction_session_timeout = '5min';
SQL

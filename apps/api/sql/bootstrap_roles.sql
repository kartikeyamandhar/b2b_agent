-- Idempotent cluster bootstrap, run by `make setup` as the postgres superuser through psql.
-- Roles are cluster-global, so they are created here and never by an Alembic migration.
-- psql variables: owner_password, app_password, internal_password, dbname.
\set ON_ERROR_STOP on

SELECT format('CREATE ROLE recallops_owner LOGIN PASSWORD %L', :'owner_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'recallops_owner') \gexec
SELECT format('CREATE ROLE recallops_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'recallops_app') \gexec
SELECT format('CREATE ROLE recallops_internal LOGIN PASSWORD %L', :'internal_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'recallops_internal') \gexec

-- Re-assert attributes every run so a drifted role is corrected.
ALTER ROLE recallops_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD :'owner_password';
ALTER ROLE recallops_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD :'app_password';
ALTER ROLE recallops_internal LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS PASSWORD :'internal_password';

SELECT format('CREATE DATABASE %I OWNER recallops_owner', :'dbname')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'dbname') \gexec

SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'dbname') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO recallops_owner, recallops_app, recallops_internal', :'dbname') \gexec

\connect :dbname
-- The database owner (recallops_owner) owns schema public in Postgres 15+; app roles get USAGE only.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO recallops_app, recallops_internal;

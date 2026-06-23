-- Bootstrap the least-privilege application role.
--
-- RLS is bypassed by superusers and by roles with BYPASSRLS. The app MUST connect as a
-- non-superuser, non-bypass role so row-level security actually binds. In production these
-- credentials are issued dynamically by Vault (infra.md); locally/CI we create a fixed role
-- with the dev password (vigil_app_pw — matches the dev DSN).
--
-- Interpolation-free + idempotent: safe over piped STDIN (no psql -v needed) and a re-run on an
-- existing role is a clean no-op. Run as a superuser against the target database, e.g.:
--   psql -U vigil -d vigil -f - < scripts/bootstrap_db.sql   # piped stdin (documented call sites)
--   psql -U vigil -d vigil -f scripts/bootstrap_db.sql       # or as a file argument

-- The password is a literal DEV/DEMO value, NOT psql :'var' interpolation: psql does NOT substitute
-- variables inside a dollar-quoted (DO ...) block, so the old :'app_password' was a parse error on a
-- FRESH DB (the role got created only because it already existed cluster-wide). Production uses Vault.
-- NOBYPASSRLS is the tenancy guarantee and is preserved here — never grant this role BYPASSRLS.
-- (No dollar-quote markers in any comment INSIDE the block below — that would close the quote early.)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vigil_app') THEN
    CREATE ROLE vigil_app LOGIN PASSWORD 'vigil_app_pw' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END
$$;

-- Schema usage + table DML. The app never owns tables (so it cannot ALTER away RLS) and
-- never gets BYPASSRLS. Migrations run as the owner/superuser, not as vigil_app.
GRANT USAGE ON SCHEMA public TO vigil_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO vigil_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vigil_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO vigil_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO vigil_app;

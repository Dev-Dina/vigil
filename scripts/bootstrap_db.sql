-- Bootstrap the least-privilege application role.
--
-- RLS is bypassed by superusers and by roles with BYPASSRLS. The app MUST connect as a
-- non-superuser, non-bypass role so row-level security actually binds. In production these
-- credentials are issued dynamically by Vault (infra.md); locally/CI we create a fixed role.
--
-- Run as a superuser against the target database:
--   psql -U <super> -d vigil -v app_password=<pw> -f scripts/bootstrap_db.sql

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vigil_app') THEN
    CREATE ROLE vigil_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
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

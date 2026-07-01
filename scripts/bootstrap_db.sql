-- Bootstrap the least-privilege application role.
--
-- RLS is bypassed by superusers and by roles with BYPASSRLS. The app MUST connect as a
-- non-superuser, non-bypass role so row-level security actually binds. In production these
-- credentials are issued dynamically by Vault (infra.md); locally/CI the dev password is supplied
-- at RUN TIME — it is NEVER hardcoded in this file.
--
-- Supply the password one of two ways (the dev value is `vigil_app_pw` — it matches the dev DSN):
--   psql -U vigil -d vigil -v app_pw=vigil_app_pw -f - < scripts/bootstrap_db.sql     # a psql var
--   VIGIL_APP_PW=vigil_app_pw psql -U vigil -d vigil -f - < scripts/bootstrap_db.sql  # or an env var
-- Idempotent (a re-run on an existing role is a clean no-op) and safe over piped STDIN.

-- 1. Resolve the password: prefer `-v app_pw`, else fall back to the VIGIL_APP_PW env var (psql >= 14).
--    Fail LOUD if neither is set — no silent default, no empty-password role (CLAUDE.md: errors never
--    pass silently).
\if :{?app_pw}
\else
  \getenv app_pw VIGIL_APP_PW
\endif
\if :{?app_pw}
\else
  \warn '[bootstrap_db] ERROR: no app-role password. Pass  -v app_pw=<pw>  or set  VIGIL_APP_PW=<pw>  (dev value: vigil_app_pw).'
  \quit
\endif

-- 2. Create the role idempotently, WITHOUT a literal password. `:'app_pw'` is interpolated at the
--    TOP LEVEL — NOT inside a dollar-quoted (DO ...) block, where psql would not substitute it — so
--    this works over piped stdin. The SELECT emits the CREATE statement (with the password quoted via
--    format %L) ONLY when the role is absent; \gexec runs it, so a re-run is a clean no-op.
--    NOSUPERUSER / NOBYPASSRLS is the tenancy guarantee and is preserved — never grant this role BYPASSRLS.
SELECT format(
  'CREATE ROLE vigil_app LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE',
  :'app_pw'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vigil_app')
\gexec

-- Schema usage + table DML. The app never owns tables (so it cannot ALTER away RLS) and
-- never gets BYPASSRLS. Migrations run as the owner/superuser, not as vigil_app.
GRANT USAGE ON SCHEMA public TO vigil_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO vigil_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vigil_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO vigil_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO vigil_app;

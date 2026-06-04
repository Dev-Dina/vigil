---
name: schema-migration
description: Use when creating or editing the Postgres schema, SQLAlchemy models, or Alembic migrations for Vigil. Encodes the non-negotiable tenancy rules — sponsor_id on every tenant table and row-level security from the first migration. Trigger for any DB schema, model, or migration change.
---

# Schema & migrations (Vigil)

Authoritative contracts: `/specs/domain.md`, `/specs/infra.md`.

## Non-negotiable rules
- **Every tenant-scoped table has a `sponsor_id` column** (NOT NULL, FK to sponsor).
- **Row-level security is enabled in the same migration that creates the table** — never
  added later. Policy: rows are visible only when `sponsor_id = current_setting('app.current_sponsor')::uuid`.
- The application sets `app.current_sponsor` (and trial/site scope) per request, derived from
  the JWT — never from client input. Application code must NOT be the only thing that filters.
- Credentials come from Vault; migrations never embed a password.

## Steps for any schema change
1. Read `/specs/domain.md` for the entity and its scope.
2. Add the column(s) + FK; write the Alembic migration.
3. In the SAME migration: `ALTER TABLE ... ENABLE ROW LEVEL SECURITY;` and `CREATE POLICY ...`.
4. Add/extend the cross-tenant leakage test for the new table.
5. Run the spec-conformance check.

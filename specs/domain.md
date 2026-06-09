# Domain Spec — hierarchy, roles, tenancy

## Decisions (fixed)
- Hierarchy: sponsor -> CRO -> site -> trial -> participant.
- **Sponsor = hard tenant boundary**: separate sponsors' data must never mix. Enforced by the
  database itself — every tenant-scoped row carries `sponsor_id`, and Postgres row-level
  security returns only rows matching the sponsor in the caller's token. A missed `WHERE`
  clause in application code cannot leak across sponsors; the engine blocks it.
- CRO is scoped per assignment, never blanket: CRO staff reach only the sponsors and trials
  they are explicitly staffed on.
- User creation and assignment are scoped administration: a user may only create, grant, or
  assign a scope that is a subset of their own, never outside their tenant.

## Roles (seven) and scope

Canonical JWT `role` strings (snake_case; these are the exact strings emitted in tokens and used by all consumers — specs, backend, frontend, tests):

| JWT string | Display name | Level | Scoped to | Key capabilities |
|---|---|---|---|---|
| `study_manager` | Study / project manager | CRO | assigned sponsors & trials | view cohort/detail, reports; assigns CRAs/coordinators to trials & follow-ups within own scope; creates/scopes staff within assigned sponsors & trials |
| `cra` | CRA / monitor | CRO | assigned sites within trials | view cohort/detail for those sites |
| `sponsor_oversight` | Sponsor oversight | Sponsor | own sponsor, all its trials | view coded data & reports; no cross-sponsor; creates/scopes users within own sponsor |
| `principal_investigator` | Principal investigator | Site | own site & trial | view detail; holds participant identities; manages users at own site |
| `coordinator` | Coordinator (CRC) | Site | own site & trial | daily triage, log interventions; holds identities |
| `platform_admin` | ML / platform admin | Platform | models, monitoring, cost; creates top-level sponsor/CRO accounts and their first admins | NO access to identifiable participant data |
| `auditor` | Auditor | Platform | read-only, all activity | audit logs & dashboards; no actions |

**Canonical string rule:** `platform_admin` is the JWT role string for the ML / platform admin role. All specs, backend enums, and frontend types must use this exact string. The strings `ml_admin` and `ML_ADMIN` are non-canonical aliases and must not appear in JWTs or authorization checks.

## Tenancy rules

**User → scope mapping.** A user has exactly one **home tenant** plus a **scope**, both resolved
from trusted sources, never asserted by the client:
- Home tenant: a sponsor (sponsor-side and site-side users), the CRO (CRO-side users), or none
  (platform users). Stored as `home_sponsor_id` / `home_cro_id` (each nullable; exactly one set,
  or neither for platform users).
- Scope = the set of `(sponsor_id, trial_id?, site_id?)` tuples a user may reach. It is resolved
  at login, written into the JWT, and used to set the RLS session variable(s) per request:
  - **Sponsor oversight** → fixed to `(own sponsor, *, *)`: every trial/site under the home
    sponsor, nothing outside it.
  - **Site user** (PI, coordinator) → fixed to `(own sponsor, own trial, own site)`.
  - **CRO user** (study/project manager, CRA) → the explicit grant list below; no home sponsor.
  - **Platform** user (ML admin, auditor) → no sponsor scope; reaches only platform/global tables
    (admin: never identifiable participant data; auditor: read-only).

**CRO cross-sponsor assignment = explicit per-assignment grants.** A CRO user never holds a
blanket "all sponsors" flag. Cross-sponsor reach is rows in an **`assignment_grant`** table, the
single source of truth for CRO scope:
- `assignment_grant(id, user_id, sponsor_id, trial_id NULL, site_id NULL, granted_by, granted_at)`.
- One row = one granted scope. NULL `trial_id`/`site_id` widen the grant to all trials/sites of
  that sponsor (study manager); setting them narrows it (CRA on specific sites).
- At login the resolver reads the user's `assignment_grant` rows and emits the JWT scope as the
  union of those tuples; RLS then admits only rows whose `(sponsor_id[, trial_id, site_id])` is in
  the granted set.
- The subset rule binds writes to this table: a grantor may only create a grant whose scope is a
  subset of their own (see Decisions). Every insert/revoke is audited.

**Non-tenant / RLS-exempt tables.** Sponsor-keyed RLS applies to every table holding a sponsor's
operational or participant data. The following are deliberately NOT sponsor-RLS'd; each must
justify its exemption in the migration that creates it:
- **Tenant-root** — `sponsor`: RLS keyed on its own `id` (a caller sees only their sponsor row),
  not a `sponsor_id` column.
- **Cross-tenant by design** — `user`, `assignment_grant`, `audit_log`, `message_events`: no
  single-sponsor predicate fits (CRO spans sponsors; auditor/admin read across). These get bespoke
  role-scoped policies, not the default sponsor predicate.
- **Global / reference** (no tenant data at all): the `cro` registry, `role` definitions, lookup
  tables (therapeutic_area, the withdrawal-reason vocabulary), the **AACT-derived public reference**
  tables (`ref_trial`, `ref_arm`, `ref_withdrawal_reason` per `/specs/data.md` — public
  clinical-trials data, no PHI; the `ref_` prefix marks them as RLS-exempt and **distinct from a
  sponsor's operational `trial`/`site` records**, which ARE sponsor-scoped with RLS on),
  the model registry / monitoring / drift / cost tables (platform scope), and `alembic_version`.
- Sessions live in Redis, not Postgres, so they are outside this list.

Rule of thumb: a table is RLS-exempt ONLY if it holds no per-sponsor participant or operational
data — i.e. global infrastructure, cross-tenant by role design, or public reference. Anything
holding a sponsor's data carries `sponsor_id` + the default RLS policy, full stop.

- **Assignment authority flows down the hierarchy, bounded by the subset rule.** Platform admin
  creates a sponsor, the CRO link, and first admins. A senior CRO admin / study manager assigns
  study/project managers to the sponsors and trials the CRO is staffed on; those managers assign
  CRAs to sites and coordinators to trials/follow-ups within their own scope; a site lead/PI
  manages users at their own site. No one assigns a scope they do not themselves hold.
- Every user creation, assignment, or scope change is an audited action (written to the audit trail).
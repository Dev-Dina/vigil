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
| Role | Level | Scoped to | Key capabilities |
|---|---|---|---|
| Study / project manager | CRO | assigned sponsors & trials | view cohort/detail, reports; assigns CRAs/coordinators to trials & follow-ups within own scope; creates/scopes staff within assigned sponsors & trials |
| CRA / monitor | CRO | assigned sites within trials | view cohort/detail for those sites |
| Sponsor oversight | Sponsor | own sponsor, all its trials | view coded data & reports; no cross-sponsor; creates/scopes users within own sponsor |
| Principal investigator | Site | own site & trial | view detail; holds participant identities; manages users at own site |
| Coordinator (CRC) | Site | own site & trial | daily triage, log interventions; holds identities |
| ML / platform admin | Platform | models, monitoring, cost; creates top-level sponsor/CRO accounts and their first admins | NO access to identifiable participant data |
| Auditor | Platform | read-only, all activity | audit logs & dashboards; no actions |

## Tenancy rules
- A user maps to exactly one home tenant: a sponsor (sponsor-side and site-side users) or the
  CRO (CRO-side users). Platform users belong to no sponsor.
- Scope is the set of (sponsor, trials, sites) a user may reach, carried in the JWT and enforced
  by RLS. Sponsor users are fixed to their own sponsor; site users to their own site/trial.
- CRO cross-sponsor access is represented as explicit per-assignment grants: a CRO user holds a
  list of (sponsor, trial[, site]) assignments — never a blanket "all sponsors" flag.
- **Assignment authority flows down the hierarchy, bounded by the subset rule.** Platform admin
  creates a sponsor, the CRO link, and first admins. A senior CRO admin / study manager assigns
  study/project managers to the sponsors and trials the CRO is staffed on; those managers assign
  CRAs to sites and coordinators to trials/follow-ups within their own scope; a site lead/PI
  manages users at their own site. No one assigns a scope they do not themselves hold.
- Every user creation, assignment, or scope change is an audited action (written to the audit trail).
# API Spec

## Decisions (fixed)
- FastAPI, versioned under `/api/v1`, one `APIRouter` per domain.
- Auth/scope is a dependency injected into every protected route.

## JWT claim shape
TODO: { sub, role, sponsor_id, trials: [...], sites: [...], exp, ... } — finalise exact claims.

## Endpoints
TODO: list endpoints per domain (auth, cohort, participants, assistant, monitoring, admin),
with request/response Pydantic schemas. No route reaches data without a resolved scope.

- User management (`POST /users`, assignment/scope changes) is tenant-scoped and RLS-protected:
  the created user is bound to the creator's sponsor, and the requested scope must be a subset
  of the creator's. A Sponsor A admin cannot create or assign a user in Sponsor B. Every such
  action is written to the audit trail.
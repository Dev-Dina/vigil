# Model Routing Spec

## Decisions (fixed)
- **This spec is the contract home for model routing.** `specs/scoring.md:10`'s reference to
  `/specs/infra.md` for routing is superseded by this file. `specs/infra.md` names "routing
  tables" and "model registry pointers" as ConfigMap contents but defines no schema or protocol;
  that contract lives here.
- **The resolver does not exist yet.** `score_trial(model_version=None)` currently resolves to
  the sentinel constant `"sequence_v1.0:demo"` (`vigil/workers/tasks.py:23`), not a DB lookup.
  `specs/scoring.md:152`'s phrase "registered champion at job time" is aspirational. This spec
  defines the contract that implementation will satisfy.
- **`audit_log` is immutable transition history; the routing-state table is current projection.**
  These are distinct. The routing-state table holds the live champion/challenger/shadow mapping
  per regime. `audit_log` records every state transition permanently. Never read `audit_log` to
  determine the current champion — that is routing-state's role. Reading audit history to
  identify a prior `model_version` as a rollback target is permitted (it is a history query,
  not a source-of-current-champion query). Never treat routing-state as an audit trail.
- **Drift computation is out of scope.** PSI/KS/calibration logic, drift tables, and drift
  dashboards belong to `specs/observability.md`. Routing CONSUMES `DriftPoint.breached`
  (from `specs/api.md`) as an opaque boolean; it does not produce it, store it, or define how it
  is measured.
- **Safety rule (ratified):** automatic fallback DOWN on a breach is allowed and system-initiated.
  Promotion UP (challenger → champion, shadow → challenger) is ALWAYS manual, initiated by a
  `platform_admin` user, and written to `audit_log` with a non-null `actor_user_id`. Automatic
  promotion never occurs.
- **Model-role isolation and tenant isolation are independent axes.** RLS enforces tenant
  isolation across all `participant_score` rows regardless of model role. Champion-only surfacing
  enforces model-role isolation at the application layer. One does not substitute for the other.

## Regime routing

A **regime** is an opaque text string whose sole current dimension is the indication code
(e.g. `"t2d"`, `"afib"`). The column type is `text`, not an enum — the shape is extensible to
composite keys (e.g. `"t2d:phase3"`) without schema change.

**Rationale for indication-specific routing:** the per-indication thesis holds that the model
signal is indication-specific; cross-indication pooling introduced base-rate inflation. Each
regime resolves its champion independently.

### Routing-state table (prose schema)

The routing-state table is the current projection of active model assignments per regime. It
backs `ModelStatus` (`specs/api.md`). Platform-scoped; RLS-exempt (global/infrastructure tier,
`specs/domain.md § Tenancy rules`).

```
routing_state(
  id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  regime         text        NOT NULL,
  role           text        NOT NULL  CHECK (role IN ('champion', 'challenger', 'shadow')),
  model_version  text        NOT NULL,
  model_card_ref text        NOT NULL,
  health         text        NOT NULL DEFAULT 'healthy'
                             CHECK (health IN ('healthy', 'degraded', 'fallback')),
  promoted_at    timestamptz NOT NULL DEFAULT now(),
  promoted_by    uuid        REFERENCES user(id) ON DELETE SET NULL,  -- NULL = system
  UNIQUE (regime, role)
)
```

One row per (regime, role) pair. At most one champion, one challenger, and one shadow per
regime at any time (enforced by the unique constraint).

`health` backs `ModelStatus.health` in `specs/api.md`. Transitions: `'healthy'` is the default;
`'fallback'` is set by the drift-triggered fallback rule (see `## Drift-triggered fallback`
step 3). The `'degraded'` transition (breach observed but fallback not yet triggered) is
**deferred** — its semantics are owned by `specs/observability.md` and must not be defined or
set by routing code until that spec is ratified.

### Resolver contract

`_load_scorer(model_version=None, regime)` MUST:
1. If `model_version` is explicitly provided, use it.
2. Otherwise query `routing_state WHERE regime = <regime> AND role = 'champion'` to obtain the
   current champion `model_version`.
3. If no champion row exists for the regime, raise a hard error — there is no silent default.

**Current reality:** this query does not exist. `_load_scorer` uses `_DEMO_MODEL_VERSION =
"sequence_v1.0:demo"` as the sentinel. The resolver query is the implementation obligation this
spec creates.

## Champion / challenger / shadow

| Role | Reads clinical surfaces | Writes denorm cache | Writes `participant_score` |
|---|---|---|---|
| champion | Yes — `GET /cohort` and `/participants/*/risk` return champion only | Yes — updates `participant.risk_score` + `risk_band` | Yes |
| challenger | No | No | Yes (stored for evaluation; invisible to clinical reads) |
| shadow | No | No | Yes (stored for monitoring; invisible to clinical reads) |

**Invariant:** `GET /cohort` and `GET /participants/{id}/risk` MUST return champion-only scores —
specifically the **NEWEST champion row per participant** (`participant_score` now APPENDS
timestamped history per `specs/scoring.md § Writeback`; the champion-allowlist read orders by
`computed_at DESC` and takes the latest). Older champion history rows and all shadow/challenger
rows never surface to clinical reads. This is enforced in two layers:
1. **Denorm-cache write (current guard):** only the champion scoring job writes
   `participant.risk_score` and `participant.risk_band`. Challenger/shadow jobs write
   `participant_score` rows only; they never touch `participant.*`.
2. **Champion-allowlist read guard (implemented):** the per-participant clinical read
   `GET /participants/{id}/risk` reads `participant_score` through
   `scoring.get_surfaceable_score(..., champion_versions=routing.champion_model_versions())`,
   which filters `model_version` to the champion allowlist by construction. A shadow/challenger
   row can never be surfaced; with no champion row the read returns `None` and the endpoint
   fails closed with `404` (never a non-champion fallback). When `list_cohort` later reads
   `participant_score` directly (Phase 5), it MUST use the same champion-allowlist filter.

### Storage open question — RESOLVED (shared table)

**Resolved design:** challenger/shadow scores share the `participant_score` table, discriminated
by `model_version`. Champion-only surfacing is enforced by the two-layer guard above.

**Verdict (verified under real Postgres, 2026-06-11):** the arbiter invariant (i) below —
`tests/spine/test_b2c_scoring.py::test_invariant_i_champion_only_surfacing` plus the fail-closed
guard `test_layer2_risk_fails_closed_on_shadow_only` — PASSES with an adversarial shadow row
present. `GET /cohort` surfaces the champion denorm only; `GET /participants/{id}/risk` surfaces
the champion `model_version` only (the shadow row is filtered out, not merely hidden by RLS); a
direct sponsor-scoped query returns BOTH rows (suppression is app-layer, not RLS). The shared
table is therefore retained; the separate-`shadow_score`-table fallback below is NOT triggered.

**Fallback (NOT taken):** had the champion-only invariant (i) required an app-layer
`model_version` filter judged too fragile, the design would have fallen back to a separate
`shadow_score` table (isolation-by-construction; +1 table, RLS policy, migration). Because the
champion-allowlist filter is applied inside the repository query — not asserted after a broad
read — it is correct-by-construction, so the fallback is unnecessary. This paragraph is retained
as the recorded rationale for the rejected alternative.

**Arbiter:** the champion-only leakage invariant (i) below. The decision was framed against that
test, not against preference.

## Drift-triggered fallback

Routing reacts to a breach signal; it does not produce or store drift metrics.

### Reaction interface

Routing expects a breach signal with at minimum: `(regime, model_version, breached: bool)`.
This is a subset of `DriftPoint` in `specs/api.md`. The exact delivery mechanism (DB poll,
event, direct call from the observability worker) is an **open question** to be resolved when
`specs/observability.md` is extended to cover drift storage. Routing defines only what it
consumes; observability defines how the signal is produced.

**Implemented (B3):** the reaction is a callable — `routing_service.handle_breach(BreachSignal)`
(`BreachSignal = (regime, model_version, breached)`). It consumes the opaque signal and performs
the fallback transition; it does NOT compute, store, or poll for drift. The signal SOURCE/delivery
remains deferred to the observability phase.

### Fallback rule

If the breach signal indicates `breached = True` for the current champion in a regime:
1. Routing queries `audit_log` for the most recent non-breached promotion record in that regime
   to identify the last-known-good version. (This is a permitted history query per
   `## Decisions (fixed)` — finding a prior `model_version` to roll back to, not sourcing the
   current champion.)
2. It updates `routing_state` to set the champion `model_version` to the last-known-good version.
3. If no prior champion record exists, the regime is marked `health: "fallback"` in the
   routing-state table and scoring for that regime is suspended pending a manual platform_admin
   review.
4. An `audit_log` row is written for the fallback transition (see `## Audited promotion`).

Fallback is automatic and system-initiated (`actor_user_id = NULL`).

## Audited promotion

Every routing transition — promote, demote, fallback — writes one row to the existing
`audit_log` table (schema: `vigil/db/migrations/versions/0001_initial_rls.py:240-262`).
The structure mirrors `write_score_audit()` (`vigil/repositories/scoring.py:98-124`):

```python
AuditLog(
    actor_user_id = <platform_admin user id>,  # NULL for system-initiated transitions
    action        = "model_promote" | "model_demote" | "model_fallback",
    target_type   = "routing_state",
    target_id     = <routing_state.id>,
    sponsor_id    = None,   # platform-scoped; NULL is valid (nullable column); visible only
                            # to platform-role sessions under the audit_scope policy
    detail        = {
        "from_version":     str,  # previous model_version for the role in this regime
        "to_version":       str,  # new model_version
        "regime":           str,
        "reason":           str,  # human label for manual; "drift_breach" for automatic
        "eval_provenance":  str,  # see honesty hook below
        "model_card_ref":   str,  # MUST be non-null; hard error if missing
    },
)
```

### Honesty hook

`eval_provenance` MUST declare the data used for the evaluation supporting the transition.
Synthetic-cohort eval (the only kind currently available for T2D) MUST be labeled
`"architecture_validation"` — it proves method validity, NOT clinical prediction. Promoting
on synthetic eval alone is permitted only with this label; any code path that writes a
promotion record MUST assert `model_card_ref` is non-null (hard error otherwise).

### Safety rule

- **Fallback (automatic, system):** `actor_user_id = NULL`. Allowed without human action.
- **Promotion (manual, platform_admin only):** `actor_user_id` MUST be the initiating
  `platform_admin` user's UUID. A promotion record with `actor_user_id = NULL` is a spec
  violation. Promotion MUST be triggered via an authenticated platform_admin API call, never
  by the scoring worker or a drift signal alone.

## Tenancy

- The routing-state table is platform-scoped (no `sponsor_id` column). It belongs to the
  global/infrastructure RLS-exempt tier (`specs/domain.md § Tenancy rules`). Sponsor-role
  sessions MUST NOT be able to read or write it.
- Per-participant challenger/shadow `participant_score` rows carry `sponsor_id` under the same
  sponsor RLS policy as champion rows. A challenger score for Sponsor A is invisible to Sponsor
  B — tenant isolation fires on every row regardless of `model_version`.
- Model-role isolation (which rows surface to clinical reads) and tenant isolation (which rows a
  sponsor can see) are independent axes enforced at different layers. Neither substitutes for
  the other. A test asserting one does not cover the other.
- `audit_log` rows for routing transitions carry `sponsor_id = NULL`. This is valid per the
  existing nullable schema and the bespoke `audit_scope` RLS policy:
  `USING (is_platform OR sponsor_id = guc::uuid)`. A `NULL` sponsor_id row is visible only to
  platform-role sessions; sponsor-role sessions cannot read it.

## Leakage / isolation invariants

Extend the sacred suite from `specs/scoring.md § Leakage-test invariants`.

### (i) Champion-only surfacing — arbiter of the storage open question

Write a `participant_score` row for a **challenger** `model_version` (different from the current
champion in the regime). Assert:
- `GET /cohort` returns `risk_score` / `risk_band` sourced from `participant.risk_score` (the
  denorm cache), which the challenger never wrote. The challenger row must not appear.
- `GET /participants/{id}/risk` returns a `RiskExplanation` with
  `model_version == <champion version>`.
- Direct DB query under a sponsor-scoped session returns BOTH rows from `participant_score`
  (champion and challenger) — confirming RLS does not hide challenger rows, and that surfacing
  suppression is enforced at the application layer, not RLS.

This invariant is the arbiter: if it cannot be satisfied without an app-layer `model_version`
filter as the sole guard, and that filter is judged fragile, the design falls back to a separate
`shadow_score` table.

### (ii) Non-champion inference fires leakage guards — no exemption

Challenger and shadow scoring jobs MUST call `run_smoke` and `assert_no_outcome_features`
before inference, identical to the champion path. `model_version` being non-champion is not a
leakage exemption. A test that inserts a forbidden column (e.g. `dropout_rate`) into a
challenger feature matrix MUST cause the challenger job to raise, not silently proceed.

### (iii) Routing-state table is platform-accessible, not sponsor-accessible

- A sponsor-role JWT MUST receive an app-layer permission error when attempting to read
  `routing_state`. (The table carries no RLS; sponsor sessions are blocked because only
  platform-scoped endpoints query it — there is no RLS empty-result fallback to rely on.)
- A `platform_admin` JWT MUST be able to read `routing_state` rows.
- Assert both directions in a single test.

### (iv) Fallback audit entry carries sponsor_id=NULL and is platform-only readable

A system-triggered fallback MUST write an `audit_log` row with `sponsor_id = NULL` and
`actor_user_id = NULL`. Assert:
- A sponsor-role session MUST NOT be able to read this row (it falls outside the `audit_scope`
  USING predicate for non-platform users — `NULL != guc::uuid`).
- A `platform_admin` session MUST be able to read it (the `is_platform` branch of `audit_scope`
  admits it).

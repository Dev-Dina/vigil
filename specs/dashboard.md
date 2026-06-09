# Dashboard Spec

## Decisions (fixed)
- The dashboard is a **consumer** of the committed scoring API and RLS layer. It reads
  `GET /cohort`, `GET /cohort/summary`, `GET /participants/{id}`, and
  `GET /participants/{id}/risk`. It never touches the model artifacts or the database
  directly; it never adds its own `sponsor_id` filter on top of what the API returns.
- **RLS is the isolation guarantee.** Sponsor A data is invisible to Sponsor B at the
  Postgres layer, enforced before the API response is assembled. The dashboard must not
  add, remove, or shadow any RLS-enforced filter.
- Watchlist ordering: `GET /cohort?sort=risk_desc` returns rows already ordered by
  `risk_score` descending within each `risk_band`. The dashboard renders that order; it
  does not re-sort client-side.
- Every scored row carries `synthetic: bool` from `participant_score.synthetic`. The
  dashboard MUST surface this flag visibly on every row and panel (see
  `## Synthetic data disclosure`). A synthetic row must never be rendered without the
  disclosure.
- The per-participant panel reads temporal attribution from `GET /participants/{id}/risk`
  (`RiskExplanation.factors`, `/specs/api.md`). It never infers attribution from the
  score alone.
- Role-scoped rendering is enforced in two layers: (1) the API returns only what the
  caller's JWT scope admits (RLS + endpoint guards); (2) the dashboard hides UI surfaces
  for which the caller's role has no API access. UI suppression is cosmetic; the API
  guard is the authoritative boundary.
- Trend over visits is computed from the sequence of `risk_score` values across scored
  visits for a participant. It is read from successive `participant_score` rows (via the
  repository / API), never from the model's internal state.
- The demo loop (`## Demo loop`) is the only demo-scoped surface. All downstream
  rendering (watchlist refresh, panel update, alert) uses the same real API path as
  production.

## Views

### Watchlist (ranked triage list)
Primary surface for daily triage. Reads `GET /cohort` (paginated, `sort=risk_desc`).

```
Source endpoint : GET /api/v1/cohort
                  GET /api/v1/cohort/summary
Columns         : risk_band (HIGH / MEDIUM / LOW badge), risk_score (0–1),
                  participant_id (coded), trial_id, site_id, top_factors (tags),
                  updated_at, synthetic (badge — see § Synthetic data disclosure)
Default sort    : risk_band (HIGH first) then risk_score descending — server-side;
                  dashboard renders the API order without re-sorting
Filters         : trial_id (must be within scope; 403 scope_denied otherwise),
                  site_id, risk_band — passed as query params to GET /cohort
Pagination      : cursor-based; limit ≤ 200 per /specs/api.md
Summary strip   : CohortSummary — total, by_band counts, mean_risk
```

### Per-participant panel
Opened by selecting a row in the watchlist. Reads participant detail and risk explanation.

```
Source endpoints: GET /api/v1/participants/{participant_id}
                  GET /api/v1/participants/{participant_id}/risk
                  GET /api/v1/participants/{participant_id}/interventions

Sections:
  Risk card      : risk_score (gauge), risk_band badge, model_version,
                   computed_at, synthetic badge (always visible when synthetic=True)
  Trend          : time series of risk_score across visits (sourced from successive
                   participant_score rows; rendered as sparkline)
  Top reasons    : RiskExplanation.factors — signed temporal attributions sorted by
                   |contribution|; human-readable factor label + bar
  Identity block : ParticipantDetail.identity — populated ONLY for site roles (PI /
                   coordinator); null / hidden for all other roles
  Interventions  : GET interventions history; POST /participants/{id}/interventions
                   action panel (site roles + sponsor oversight + CRO; auditor read-only)
```

## Role-scoped rendering

Dashboard CONSUMES RLS; it never re-filters app-side. The API rejects out-of-scope
requests before the dashboard receives data. The UI suppresses controls for which the
caller has no API access — suppression is cosmetic only.

| Role | Watchlist | Participant panel | Identity block | Scoring trigger | Monitoring tab | Notes |
|---|---|---|---|---|---|---|
| Sponsor oversight | Full sponsor cohort (all trials/sites) | Risk card + trend + reasons + interventions (log only) | Hidden (no identity) | `POST /scoring/trigger` for own trials | Hidden | No PII; coded data only |
| Study / project manager (CRO) | Assigned sponsors + trials only (scope = `assignment_grant` union) | Risk + reasons + interventions (log only) | Hidden | `POST /scoring/trigger` for assigned trials | Hidden | Narrower than sponsor oversight |
| CRA / monitor (CRO) | Assigned sites within trials | Risk + reasons | Hidden | Hidden | Hidden | Read-only on scoring data |
| Principal investigator | Own site + trial | Risk + reasons + identity + interventions | Visible | Hidden | Hidden | `identity` populated (site role) |
| Coordinator (CRC) | Own site + trial | Risk + reasons + identity + interventions | Visible | Hidden | Hidden | Daily triage primary user; `identity` populated |
| ML / platform admin | **Hidden** (403 on `GET /cohort`) | **Hidden** (403 on `GET /participants/*`) | N/A | `POST /scoring/trigger` (model ops — separate model-ops panel; not the cohort watchlist) | Monitoring tab only: `GET /monitoring/models`, `/drift`, `/cost`, `/messages` | NO access to participant or cohort data; `scope: []` → RLS returns empty |
| Auditor | **Hidden** (403 on `GET /cohort`) | **Hidden** (403 on `GET /participants/*`) | N/A | Hidden (403 on `POST /scoring/trigger`) | Monitoring tab (read-only): same endpoints as ML admin | No write actions; read-only on all surfaces |

### ML-admin surface (model-ops panel)
Visible only when `role == ml_admin`. Replaces the watchlist entirely; no cohort or
participant surfaces are rendered.

```
Source endpoints: GET /api/v1/monitoring/models
                  GET /api/v1/monitoring/drift
                  GET /api/v1/monitoring/cost
                  GET /api/v1/monitoring/messages
Controls        : POST /scoring/trigger (trial_id input + model_version optional)
Hidden          : GET /cohort, GET /participants/*, GET /cohort/summary
```

### Auditor surface
Identical source endpoints to ML-admin monitoring tab, all read-only. No trigger control
rendered. Audit log viewer (`GET /audit_log` — endpoint defined in roadmap Phase 5+)
appears here when available.

## Demo loop

The demo loop exercises the full path from event injection through rescore to UI update.
It is gated behind `DEMO_MODE=true` (`VIGIL_DEMO_MODE` env, Vault-held). In production
`POST /scoring/inject_events` returns `404`; the demo loop UI is not rendered.

### Trigger
```
POST /api/v1/scoring/inject_events
Body: { trial_id, events: [...], demo_mode_key }
Response: 204 No Content → UI transitions to "Scoring in progress…" state
```

### Polling
```
GET /api/v1/scoring/jobs/{job_id}    (returned by trigger_scoring internally)
Poll interval: 2 s; stop on status ∈ {"done", "failed"}
```

### What re-renders on completion
Exactly these surfaces update; nothing else re-renders:

1. **Watchlist** — refreshes `GET /cohort` for the affected trial. Participant rows whose
   `risk_band` changed move to their new position in the sorted list. Rows that crossed
   the `high` threshold (new `risk_band == "high"`) are highlighted for one render cycle.
2. **Summary strip** — `GET /cohort/summary` re-fetched; `by_band` counts and `mean_risk`
   update in place.
3. **Alert** — a dismissible in-app alert fires when at least one participant crosses into
   `risk_band == "high"` as a result of the rescore. Alert content:
   ```
   Alert: {n} participant(s) newly flagged HIGH risk in trial {trial_id}.
   Model: {model_version}. Data: synthetic — scores are method demonstrations only.
   ```
   The alert includes the `synthetic` disclosure unconditionally when `synthetic=True`.

### What does NOT re-render
- Open per-participant panels (user must navigate back to watchlist and re-open).
- Other trials not covered by the injected events.
- The monitoring tab drift/cost charts (those update on their own poll cycle, not tied
  to the demo trigger).

## Synthetic data disclosure

`participant_score.synthetic` propagates through every API response and must be surfaced
at every level where scored data is shown. Suppressing or omitting the disclosure when
`synthetic=True` is a spec violation.

### Watchlist row
A visible badge (e.g. "SYNTHETIC") on every row where `CohortRow.synthetic == true`.
The badge must be present in the initial render and must not be removable by the user.

### Per-participant panel
A persistent non-dismissible banner at the top of the risk card:
```
[SYNTHETIC DATA] Risk scores for this participant are method demonstrations only.
They do not represent clinical predictions and must not inform care decisions.
```
Shown whenever `ParticipantScore.synthetic == true`. Hidden only when `synthetic=false`.

### Alert (demo loop)
Alert body always includes the synthetic disclosure string when triggered via the demo
loop (see `## Demo loop` alert content above).

### Principle
The disclosure is not a tooltip or collapsible note — it is a permanent, prominent UI
element on every surface that shows a synthetic score. Any design that renders a synthetic
score without the disclosure (e.g. a print view, an export, a summary card) violates this
spec and must be fixed before the surface ships.

## Out of scope (roadmap only)
The following are explicitly excluded from this spec. They appear in ROADMAP.md and will
be specced in a future phase before implementation:

- Score distribution charts (histogram of `risk_score` across the cohort)
- Model drift signals (PSI / KS / calibration curves) — `GET /monitoring/drift`
  data exists but dashboard rendering of drift is not specced here
- Champion / challenger comparison UI
- Model retraining trigger or scheduling UI
- Model artifact packaging or promotion UI
- Export / print views of scored data

Adding any of the above to the dashboard before a ratified spec update is a CLAUDE.md
ritual violation (spec first, then code).

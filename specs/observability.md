# Observability Spec

## Decisions (fixed)
- Every chatbot/assistant message (BOTH surfaces) writes one `message_events` row.
- Redaction happens BEFORE persistence; raw message is never stored. Langfuse for traces.

## message_events schema
One row per chatbot/assistant message on BOTH surfaces (local assistant + public Guide). Written
AFTER redaction — the raw message is never stored. The Guide writes to its OWN sink; the app
writes to the app sink; same schema, separate stores (`/specs/infra.md`).

| column | type | rule / notes |
|---|---|---|
| `id` | uuid PK | row id |
| `conversation_id` | uuid | groups a turn sequence; from `/assistant/conversations` or the Guide session |
| `request_id` | str | per-request correlation id; matches the structured log line |
| `role_or_guest_scope` | str | app: the caller role (e.g. `study_manager`); Guide: `guest` |
| `surface` | enum | `local_assistant` \| `public_guide` — which surface emitted the row |
| `ts` | timestamptz | event time (UTC) |
| `route_or_agent` | str | API route or agent that handled the turn (e.g. `agent:retention`) |
| `guardrail_decision` | enum | `allowed` \| `blocked` (a refusal is an explicit logged outcome) |
| `retrieved_chunks` | jsonb | list of citation refs `{source_type, source_id, locator}` (`/specs/rag.md`); `[]` if none |
| `llm_provider_model` | str | provider + model id used (e.g. `anthropic/claude-...`) |
| `latency_ms` | int | end-to-end turn latency, ≥ 0 |
| `token_cost_estimate` | numeric | estimated cost (USD); `input_tokens × input_rate + output_tokens × output_rate` (COST-1, split rates) |
| `prompt_tokens` | int | REAL input/prompt tokens for the turn (router + agent), ≥ 0 (COST-1) |
| `completion_tokens` | int | REAL output/completion tokens for the turn (router + agent), ≥ 0 (COST-1) |
| `status` | enum | `ok` \| `refused` \| `error` |
| `redacted_user_msg` | text | PII-redacted inbound message; raw text NEVER stored |
| `redacted_assistant_msg` | text | PII-redacted response; raw text NEVER stored |

Notes: redaction is fail-loud (`/specs/rag.md`) — if it errors the turn is blocked, not stored
raw. No secrets or PII in any column. `retrieved_chunks` makes every answer auditable back to its
sources. Langfuse holds the full trace; this table is the durable, queryable audit record.

## Admin observability page
A read-only operations view behind **admin RBAC** (ML/platform admin and auditor per
`/specs/domain.md`; `GET /monitoring/messages`, `/specs/api.md`). It reads `message_events` only —
never raw messages (none exist) — and is the human-facing proof that guardrails and redaction work.
- **Inspect messages**: browse/filter `message_events` by `surface`, `conversation_id`,
  `role_or_guest_scope`, `guardrail_decision`, `status`, time window. Shows the redacted user +
  assistant text, route/agent, model, latency, and cost estimate.
- **Verify guardrails fired**: surface `guardrail_decision = blocked` turns (including the Guide
  red-team attempts, `/specs/isolation.md` §2) so a reviewer can confirm refusals happened and are
  auditable.
- **Debug retrieval**: expand `retrieved_chunks` to see which sources/citations grounded an answer
  — diagnose a bad or empty retrieval, with a deep link to the Langfuse trace.
- **Show redaction**: display only the redacted fields, demonstrating raw PII is never persisted;
  the page has no path to any unredacted text.
- Auditor scope is read-only (no actions); admin may not see identifiable participant data — the
  page exposes coded ids only.

## Phase 6 contracts
The Phase-6 build (observe what Phases 4–5 produce; the table + write path already exist — 5.1
created `message_events`, every assistant turn writes one row — 5.5/5.6).

### Inspect endpoint scope contract (the sacred contract)
`GET /monitoring/messages` (the inspect surface) is **PLATFORM / AUDITOR ONLY** — `403` for every
sponsor and site role. It MUST run under `scoped_session(scope)` (RLS-bound), **never**
`platform_session` unconditionally: so even a misrouted non-platform caller is **narrowed by the
`message_events` RLS** (`USING (is_platform OR sponsor_id = current_sponsor)`), never widened — the
inspect surface can only honour the cross-tenant-by-role boundary, never broaden it. It exposes
**only redacted fields** (`redacted_user_msg`/`redacted_assistant_msg` + metadata); there is no raw
column, so raw PII cannot be surfaced by construction. The inspect surface adds NO new visibility
beyond what the row's RLS already grants the caller.

### Cost / latency / token capture (required) — real per-turn usage (Gate COST-1)
`latency_ms`, `token_cost_estimate`, `prompt_tokens`, and `completion_tokens` MUST be persisted on
the `message_events` row by the turn pipeline, threaded from the provider response (`LLMResponse`
usage) and SUMMED across the turn's router-classification + agent-generation calls (a pre-router
guardrail block has no LLM call → honest-zero). The token counts are the REAL provider counts
(Anthropic `input_tokens`/`output_tokens`; OpenRouter `prompt_tokens`/`completion_tokens`). The
dollar cost uses **SPLIT input/output per-1k-token rates** (`core/config.py`
`anthropic_input_cost_per_1k_tokens` / `anthropic_output_cost_per_1k_tokens` — defaulting to the real
claude-haiku-4-5 list price $1.00/$5.00 per 1M; OpenRouter rates 0 for the free model), because
Anthropic prices output ~5× input — a single blended rate would misstate cost. The cost surface
(`GET /monitoring/cost`) aggregates **real captured token totals + cost + latency by surface×model**,
or honest-zero/absent — **NEVER a fabricated number**. The deterministic stub stays honest-zero cost.

### Drift read-surface (Gate M1 — now backed by a real producer)
Phase 6 shipped `GET /monitoring/drift` as a **READ SURFACE ONLY** (honest-empty, never fabricated)
while the drift *source* was deferred. **Gate M1 builds the producer** — see **§ Drift detection
(Gate M1)** below. The surface now returns **real computed PSI/KS points** (or empty when no run has
computed any yet — still never a fabricated number), platform/auditor-gated as before.

### Langfuse
Langfuse holds the **full per-turn trace** (a real instance); `message_events` is the durable,
queryable audit record. Traces carry **only redacted content** — no raw PII ever reaches Langfuse
(redaction is before the LLM, and only redacted text is persisted/traced). Langfuse is a **new
outbound egress**: allow-list it explicitly (deny-by-default posture, `/specs/isolation.md`). **CI
is hermetic** — Langfuse is disabled/stubbed in CI (no key, no network), like the LLM client.

**Implemented (Gate 6.3).** `vigil/agents/tracing.py` is the tracer (`get_tracer` → `NullTracer`
unless `VIGIL_LANGFUSE_ENABLED=true`; `LangfuseTracer` lazy-imports the SDK). `run_assistant_turn`
emits one `TurnTrace` per turn **after** the durable `message_events` write, **best-effort**
(`safe_trace` swallows any failure) — a disabled/failing Langfuse never breaks the turn or the
audit row. **Redacted-only by construction:** `TurnTrace` has NO raw field — only the same
`redacted_user_msg`/`redacted_assistant_msg` + structural metadata (route/agent, retrieval,
model, guardrail, latency, cost) the event row carries; raw text has no path to a trace.
- **CI-hermetic flag:** `VIGIL_LANGFUSE_ENABLED` (default `false`) — OFF in `ci.yml` + the spine
  conftest, so CI makes NO Langfuse call, imports no SDK, needs no key (mirrors `VIGIL_LLM_STUB`).
- **Egress allow-list entry:** the Langfuse host (`VIGIL_LANGFUSE_HOST`, default
  `https://cloud.langfuse.com`) joins `api.anthropic.com` + `openrouter.ai` as the only allowed
  outbound destinations (deny-by-default; NetworkPolicy in `/specs/infra.md`).
- **Vault keys:** `secret/vigil/langfuse/public_key` and `secret/vigil/langfuse/secret_key` (KV v2,
  field `value`) — read only when tracing is enabled; never in code/repo/.env. Local-dev env shim:
  `VIGIL_LANGFUSE_PUBLIC_KEY` / `VIGIL_LANGFUSE_SECRET_KEY`.

## Drift detection (Gate M1)
The **producer** behind `GET /monitoring/drift` — step 1 of the human-in-the-loop MLOps loop (M2 =
alert the ML engineer on breach; M3 = governed promotion — later). It computes **real** distribution
drift; it **never fabricates** a drift number.

- **What drifts.** The **champion prediction distribution** — the `risk_score` values of the
  champion model's `participant_score` rows. A **reference** window (older scores) is compared to a
  **current** window (recent scores), split by `computed_at`. (Feature drift over the engagement
  covariates is a future extension; M1 ships prediction drift.)
- **Statistics (correct, asserted).** **PSI** (population stability index; reference-quantile bins;
  `>0.2` = significant shift) and **two-sample KS** (`scipy.stats.ks_2samp`; breach at the α=0.05
  critical value `D_crit = 1.358·√((n+m)/(n·m))`, equivalently `p<0.05`). Each point is
  `value / threshold / breached` (uniform `breached ⇔ value > threshold`); the KS p-value rides in
  the note. The math is unit-asserted against hand-computed / scipy values (`tests/models/test_drift.py`).
- **Cross-tenant aggregation, security preserved.** `participant_score` is sponsor-RLS (no platform
  bypass), so the model's output distribution can't be platform-read in one query. The worker job
  enumerates sponsors (the sponsor table IS platform-readable) and reads each sponsor's champion
  scores via `sponsor_bootstrap_session` (the same trusted infra path scoring uses), then pools ONLY
  the scalar scores. The result is a **model-level** statistic.
- **Storage.** A **platform-tier `drift_metric` table — RLS-EXEMPT** (same class as `routing_state`):
  it carries **no tenant key and no tenant data**, only the scalar metric + provenance, so no RLS
  predicate fits and none is needed. The read surface is **platform/auditor-only** (the existing
  role gate); the **trigger** (`POST /monitoring/drift/run`) is **platform_admin-only**.
- **Schedulable + triggerable.** An Arq job (`compute_drift`) runs on a **cron schedule**
  (`WorkerSettings.cron_jobs`) and is **manually triggerable** on demand (`POST /monitoring/drift/run`).
- **HONESTY (non-negotiable).** Every point carries `synthetic` (the cohort the distribution came
  from is synthetic) and `constructed_demo`. To DEMONSTRATE a breach firing, a `demo_shift` run sets
  the current window to the reference shifted by a constant — a **CONSTRUCTED demonstration of the
  detector**, labelled `constructed_demo=true` + a note, **NOT observed production drift**. With
  `demo_shift=0` the numbers are real PSI/KS on the real (unshifted) windows.

### Drift-breach alert (Gate M2)
**Step 2** of the human-in-the-loop MLOps loop (M1 = detect; M3 = governed promotion — later): when
`compute_drift` produces a **breach**, it **alerts the ML/platform engineer** so a human decides
(M3). A real alert on a real breach; a constructed-demo breach stays labelled a demonstration.

- **Reuses the Phase-9 notification machinery.** The PII-free `email_sender` (`StubEmailSender` by
  default via `VIGIL_EMAIL_STUB`; real SMTP only on the live opt-in) + an **Arq job**
  (`notify_drift_breach`, never inline) — the same pattern as the serious-risk crossing doorbell.
- **Recipient = the platform/ML engineer (PLATFORM-SCOPED).** Drift is a **model-level** statistic
  with **no participant `(sponsor, trial, site)` tuple**, so routing is by **role**
  (`platform_admin`), NOT by `scope.permits`. `resolve_drift_recipients` reads the
  `notification_email` off the `platform_admin` user record(s) — **env-seeded
  (`VIGIL_DEMO_ML_NOTIFY_EMAIL`), never a code constant** — distinct from the tenant-scoped crossing
  recipients. A site coordinator is NEVER a drift recipient.
- **PII-free body (by construction).** Only model-level scalars: which metric breached (`psi`/`ks`),
  `value` vs `threshold`, the `distribution`, the window sizes (`reference_n`/`current_n`), and the
  provenance (`synthetic` / `constructed_demo`) + a deep link to the platform drift surface. No
  participant, coded id, or risk row — there is none in drift to leak.
- **Dedupe — once per breach EVENT.** The producer alerts only on the **not-breached → breached
  EDGE** per `(regime, model_version, metric)` (the analog of the crossing non-high→high edge), so a
  re-run while the **same breach is still ongoing does NOT re-alert**. The anchor point's
  **`notified` flag** is the per-send guard (flipped true ONLY on a successful send), so a **retried**
  job never double-sends; a send failure leaves it false so Arq retries.
- **Constructed-demo honesty.** A breach from a `demo_shift` run is labelled a **CONSTRUCTED
  demonstration** in the alert (subject + body), so a demo alert is never mistaken for observed drift.

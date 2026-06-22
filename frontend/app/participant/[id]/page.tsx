"use client"

import { useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { useParams } from "next/navigation"
import { Card, CardContent } from "@/components/ui/card"
import {
  RiskDial,
  PulseDivider,
  ContributingFactors,
  ExplanationCard,
  RiskTrajectory,
} from "@/components/vigil"
import {
  getParticipant,
  getParticipantRisk,
  getParticipantRiskHistory,
  logIntervention,
  getInterventions,
  ApiError,
} from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { daysSince, participantLabel } from "@/lib/utils"
import { PLATFORM_ROLES, CAN_LOG_INTERVENTIONS } from "@/lib/role-gates"
import { groupFactors } from "@/lib/factors"
import type {
  ParticipantDetail,
  RiskExplanation,
  RiskHistoryPoint,
  InterventionOut,
  InterventionKind,
  BaselineContext,
} from "@/lib/types"

// One baseline covariate cell. SYNTHETIC literature-prior — an imputed value MUST carry the
// "imputed" badge; nothing here is ever a bare clinical measurement.
function BaselineItem({
  label,
  value,
  imputed,
}: {
  label: string
  value: string
  imputed?: boolean
}) {
  return (
    <div>
      <p className="mb-0.5 text-[11px] text-muted-foreground">{label}</p>
      <p className="font-mono text-sm text-foreground">{value}</p>
      {imputed && (
        <span className="mt-0.5 inline-block rounded-full border-[0.5px] border-status-watch/30 bg-status-watch/10 px-1.5 py-0.5 text-[10px] font-medium text-status-watch">
          imputed (literature prior)
        </span>
      )}
    </div>
  )
}

function BaselineContextCard({ baseline }: { baseline: BaselineContext }) {
  const num = (v: number | null, suffix = "") => (v == null ? "—" : `${v}${suffix}`)
  return (
    <div className="mb-4 rounded border border-border bg-card px-4 py-3">
      <div className="mb-3 flex items-center gap-2">
        <p className="text-xs font-medium text-muted-foreground">Baseline context</p>
        <span className="rounded-full border-[0.5px] border-status-watch/30 bg-status-watch/10 px-1.5 py-0.5 text-[10px] font-medium text-status-watch">
          synthetic
        </span>
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
        <BaselineItem label="HbA1c" value={num(baseline.hba1c_pct, "%")} imputed={baseline.hba1c_imputed} />
        <BaselineItem label="BMI" value={num(baseline.bmi)} imputed={baseline.bmi_imputed} />
        <BaselineItem label="Age" value={num(baseline.age_years, " yrs")} imputed={baseline.age_imputed} />
        <BaselineItem label="Sex" value={baseline.sex ?? "—"} />
        <BaselineItem label="Arm" value={baseline.arm_type ?? "—"} />
      </div>
      <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
        Synthetic literature-prior values calibrated to published population statistics — NOT real
        patient measurements. Values marked{" "}
        <span className="text-status-watch">imputed</span> are literature-prior estimates. These
        baseline covariates also feed the scoring model&apos;s context.
      </p>
    </div>
  )
}

export default function ParticipantDetailPage() {
  const params = useParams<{ id: string }>()
  const id = params.id
  const { me } = useAuth()

  const [detail, setDetail] = useState<ParticipantDetail | null>(null)
  const [risk, setRisk] = useState<RiskExplanation | null>(null)
  const [history, setHistory] = useState<RiskHistoryPoint[]>([])
  const [interventions, setInterventions] = useState<InterventionOut[]>([])
  const [scopeDenied, setScopeDenied] = useState(false)

  const isPlatformRole = me?.role != null && (PLATFORM_ROLES as string[]).includes(me.role)
  const canLogIntervention =
    me?.role != null && (CAN_LOG_INTERVENTIONS as string[]).includes(me.role)

  useEffect(() => {
    if (!id || isPlatformRole) return
    setScopeDenied(false)
    // Real scoped reads. Detail is the security gate: 403/404 on it → access-denied state.
    // /risk 404 (no champion score) and /risk/history 404 are tolerated as "no data yet".
    getParticipant(id)
      .then((d) => setDetail(d))
      .catch((e) => {
        // 403 (platform) or 404 (out-of-scope / not found) → access denied, no fabricated data.
        if (e instanceof ApiError && (e.status === 403 || e.status === 404)) {
          setScopeDenied(true)
        }
      })
    getParticipantRisk(id)
      .then((r) => setRisk(r))
      .catch(() => setRisk(null)) // no champion score yet → leave null (UI shows —)
    getParticipantRiskHistory(id)
      .then((h) => setHistory(h.points))
      .catch(() => setHistory([])) // 404/empty → honest empty sparkline
    getInterventions(id)
      .then((page) => setInterventions(page.items))
      .catch(() => setInterventions([]))
  }, [id, isPlatformRole])

  // Role gate: platform roles see a message, not participant data.
  if (isPlatformRole || scopeDenied) {
    return (
      <div className="min-h-screen bg-background">
        <main className="mx-auto max-w-7xl px-6 py-16">
          <p className="text-muted-foreground">
            Participant detail is not available for your role.
          </p>
        </main>
      </div>
    )
  }

  // Group the correlated missed-visit channels + emphasize the primary driver (display-layer only;
  // the model's occlusion numbers are unchanged — see lib/factors).
  const factors = useMemo(() => groupFactors(risk?.factors ?? []), [risk])

  const riskPct = detail ? Math.round(detail.risk_score * 100) : 0

  const handleSchedule = async () => {
    if (!id) return
    await logIntervention(id, { kind: "call", note: "Scheduled outreach from triage." })
    // Refresh the real, server-audited history (actor is the authenticated user server-side).
    try {
      const page = await getInterventions(id)
      setInterventions(page.items)
    } catch {
      // leave existing list; the POST itself succeeded
    }
  }

  // RiskExplanation has factors but no prose; the assistant produces prose.
  // TODO(phase5): replace with assistant-generated explanation (/assistant).
  const explanation = risk
    ? `Risk score ${riskPct}% over a ${risk.horizon_days}-day horizon (model ${risk.model_version}). Top drivers are listed at left; review with site staff before acting. The score also reflects synthetic baseline context (HbA1c, BMI, age, arm), not visit patterns alone — only the visit-trajectory features receive attribution.`
    : "Loading risk explanation…"

  // Non-dismissible synthetic disclosure (dashboard.md): flag-driven, never a toggle. Shown when
  // EITHER the detail OR the risk explanation (reasons/actions) is synthetic-derived (structural).
  const isSynthetic = (detail?.synthetic ?? false) || (risk?.synthetic ?? false)

  // Recommended protocol actions (Gate 9.3) are SUGGESTIONS — acting logs the existing audited
  // intervention (kind pre-filled from the suggestion); nothing is auto-logged.
  const handleSuggested = async (action: {
    action: string
    intervention_kind: string
  }) => {
    if (!id) return
    await logIntervention(id, {
      kind: action.intervention_kind as InterventionKind,
      note: `Suggested action: ${action.action}`,
    })
    try {
      const page = await getInterventions(id)
      setInterventions(page.items)
    } catch {
      // POST succeeded; leave existing list
    }
  }

  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-14 z-20 border-b-[0.5px] border-border bg-card">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-4">
            <Link
              href="/triage"
              className="flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              <svg
                className="h-4 w-4"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={1.5}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18"
                />
              </svg>
              Back to Cohort
            </Link>
            <div className="h-5 w-px bg-border" />
            <div>
              {/* Coded ref is the primary label (identity roles); the UUID stays in the URL/keys. */}
              <h1 className="font-mono text-lg font-semibold text-foreground">
                {participantLabel(detail?.identity?.coded_ref, id)}
              </h1>
              <p className="text-xs text-muted-foreground">
                {detail ? `Trial ${detail.trial_id} · Site ${detail.site_id}` : "Loading…"}
              </p>
            </div>
          </div>

          <RiskDial value={riskPct} size="md" />
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">
        {/* Synthetic data disclosure banner — non-dismissible, always visible when synthetic=true */}
        {isSynthetic && (
          <div className="mb-6 rounded-md border border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <span className="font-semibold">[SYNTHETIC DATA]</span> Risk scores for this
            participant are method demonstrations only. They do not represent clinical predictions
            and must not inform care decisions.
          </div>
        )}

        <div className="mb-8 grid grid-cols-4 gap-4">
          <Card className="border-[0.5px] border-border shadow-sm">
            <CardContent className="pt-4">
              <p className="mb-1 text-xs text-muted-foreground">Status</p>
              <p className="font-mono text-xl font-semibold capitalize text-foreground">
                {detail?.status ?? "—"}
              </p>
            </CardContent>
          </Card>
          <Card className="border-[0.5px] border-border shadow-sm">
            <CardContent className="pt-4">
              <p className="mb-1 text-xs text-muted-foreground">Days Enrolled</p>
              <p className="font-mono text-xl font-semibold text-foreground">
                {detail ? (daysSince(detail.enrolled_at) ?? "—") : "—"}
              </p>
            </CardContent>
          </Card>
          <Card className="border-[0.5px] border-border shadow-sm">
            <CardContent className="pt-4">
              <p className="mb-1 text-xs text-muted-foreground">Enrolled</p>
              <p className="font-mono text-xl font-semibold text-foreground">
                {detail ? detail.enrolled_at.slice(0, 10) : "—"}
              </p>
            </CardContent>
          </Card>
          <Card className="border-[0.5px] border-border shadow-sm">
            <CardContent className="pt-4">
              <p className="mb-1 text-xs text-muted-foreground">Model Version</p>
              <p className="font-mono text-xl font-semibold text-foreground">
                {risk?.model_version ?? "—"}
              </p>
            </CardContent>
          </Card>
        </div>

        {/* Baseline context — SYNTHETIC literature-prior covariates (site roles only). Every
            imputed value carries its own "imputed" badge; the section is labelled synthetic. */}
        {detail?.baseline && <BaselineContextCard baseline={detail.baseline} />}

        {/* Champion risk trajectory (H2b): cross-version, version boundaries VISIBLE.
            RiskTrajectory breaks the line + labels each model change; synthetic points are
            drawn distinctly. Empty (in-scope, no history) → honest empty state. */}
        <div className="mb-4 rounded border border-border bg-card px-4 py-3">
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            Risk Trajectory (champion-of-record over time)
          </p>
          <RiskTrajectory points={history} />
        </div>

        <PulseDivider />

        <div className="grid grid-cols-2 gap-6">
          <ContributingFactors factors={factors} />
          <ExplanationCard
            participantId={id ?? ""}
            riskScore={riskPct}
            explanation={explanation}
          />
        </div>

        {/* Recommended protocol actions (Gate 9.3): operational coordinator next-steps mapped
            from the risk band + the model's own attribution factors — SUGGESTIONS, not clinical
            advice. Acting logs the existing audited intervention (kind pre-filled). */}
        {risk && risk.recommended_actions.length > 0 && (
          <>
            <PulseDivider />
            <div className="mb-4">
              <h3 className="mb-1 text-sm font-semibold text-foreground">
                Suggested coordinator actions
              </h3>
              <p className="mb-3 text-xs text-muted-foreground">
                Operational next-steps — not clinical advice. Logging an action records an audited
                intervention.
              </p>
              <ul className="space-y-2">
                {risk.recommended_actions.map((a, i) => (
                  <li
                    key={`${a.intervention_kind}-${i}`}
                    className="flex items-center justify-between rounded border border-border bg-card px-3 py-2 text-sm"
                  >
                    <span className="text-foreground">{a.action}</span>
                    <span className="flex items-center gap-3">
                      <span className="font-mono text-xs capitalize text-muted-foreground">
                        {a.intervention_kind.replace(/_/g, " ")}
                      </span>
                      {canLogIntervention && (
                        <button
                          onClick={() => handleSuggested(a)}
                          className="rounded border border-border px-3 py-1 text-xs text-foreground transition-colors hover:bg-muted"
                        >
                          Log
                        </button>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </>
        )}

        <PulseDivider />

        {/* Real intervention history (audited server-side; actor = authenticated user). */}
        <div className="mb-4">
          <h3 className="mb-2 text-sm font-semibold text-foreground">Interventions</h3>
          {interventions.length === 0 ? (
            <p className="text-sm text-muted-foreground">No interventions logged yet.</p>
          ) : (
            <ul className="space-y-1">
              {interventions.map((iv) => (
                <li
                  key={iv.id}
                  className="flex items-center justify-between rounded border border-border bg-card px-3 py-2 text-sm"
                >
                  <span className="font-mono capitalize text-foreground">
                    {iv.kind.replace(/_/g, " ")}
                  </span>
                  <span className="truncate px-3 text-muted-foreground">{iv.note}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {iv.created_at.slice(0, 16).replace("T", " ")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex items-center justify-end gap-4">
          {canLogIntervention && (
            <button
              onClick={handleSchedule}
              className="rounded-md bg-foreground px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-foreground/90"
            >
              Schedule Intervention
            </button>
          )}
        </div>
      </main>
    </div>
  )
}

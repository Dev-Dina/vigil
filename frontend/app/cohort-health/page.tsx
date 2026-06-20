"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  ContributingFactors,
  GuardrailMix,
  MetricCard,
  PageHeader,
  PulseDivider,
  RiskDial,
  RiskDistribution,
  RiskTrajectory,
  TrafficSplitBar,
} from "@/components/vigil"
import {
  ApiError,
  getCohort,
  getCohortSummary,
  getCost,
  getDrift,
  getMessages,
  getModels,
  getParticipantRisk,
  getParticipantRiskHistory,
} from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { PLATFORM_ROLES } from "@/lib/role-gates"
import { groupFactors } from "@/lib/factors"
import type {
  CohortRow,
  CohortSummary,
  CostReport,
  DriftPoint,
  MessageEventOut,
  ModelStatus,
  RiskExplanation,
  RiskHistoryPoint,
} from "@/lib/types"

type CohortState = "loading" | "ready" | "scoped_out" | "error"

// =============================================================================
// Cohort Health — an analytics surface built ENTIRELY on existing endpoints.
//
// Two trust tiers:
//   • Cohort analytics (overview, distribution, risk spotlight) are SCOPE-BOUND — any
//     sponsor/site role sees their own RLS-scoped data. A platform/auditor role has no sponsor
//     scope, so /cohort 403s; we render an honest "scoped to sponsor/site roles" note, not an error.
//   • Platform analytics (model traffic, cost/latency, guardrail mix, drift) are PLATFORM/AUDITOR
//     ONLY. For everyone else they are not fetched and a single "platform-only" placeholder shows.
//
// No new backend, no charting library — existing endpoints + hand-rolled SVG/CSS components only.
// Provenance (synthetic / constructed-demo) is surfaced honestly; sparse data → honest empty state.
// =============================================================================

export default function CohortHealthPage() {
  const router = useRouter()
  const { me } = useAuth()
  const isPlatformRole = me?.role != null && (PLATFORM_ROLES as string[]).includes(me.role)

  // ---- cohort (scope-bound) ----
  const [cohortState, setCohortState] = useState<CohortState>("loading")
  const [rows, setRows] = useState<CohortRow[]>([])
  const [summary, setSummary] = useState<CohortSummary | null>(null)

  useEffect(() => {
    // Platform/auditor roles have no sponsor scope — skip the scoped read entirely.
    if (isPlatformRole) {
      setCohortState("scoped_out")
      return
    }
    setCohortState("loading")
    Promise.all([getCohort({ sort: "risk_desc" }), getCohortSummary()])
      .then(([page, sum]) => {
        setRows(page.items)
        setSummary(sum)
        setCohortState("ready")
      })
      .catch((e) => {
        setRows([])
        setSummary(null)
        setCohortState(e instanceof ApiError && e.status === 403 ? "scoped_out" : "error")
      })
  }, [isPlatformRole])

  const spotlightRows = useMemo(() => rows.slice(0, 5), [rows])

  return (
    <div className="min-h-screen bg-background">
      <main className="mx-auto max-w-7xl px-6 py-8">
        <PageHeader
          eyebrow="Analytics"
          title="Cohort Health"
          subtitle="Beyond the dashboard KPIs — a per-participant risk spotlight (trajectory + contributing factors) and, for platform roles, the model, cost, guardrail, and drift signals behind the scores."
        />

        {/* ---------------------------------------------------------------- */}
        {/* Cohort overview + distribution (scope-bound: GET /cohort/summary) */}
        {/* ---------------------------------------------------------------- */}
        <section className="mb-2">
          <h2 className="mb-4 text-lg font-semibold text-foreground">Cohort Overview</h2>

          {cohortState === "loading" && (
            <p className="text-sm text-muted-foreground">Loading cohort analytics…</p>
          )}

          {cohortState === "scoped_out" && (
            <div className="rounded-lg border-[0.5px] border-border bg-card p-6">
              <p className="text-sm text-muted-foreground">
                Cohort analytics are scoped to sponsor and site roles. Your role has no participant
                scope, so no cohort is shown here — the platform analytics below are your surface.
              </p>
            </div>
          )}

          {cohortState === "error" && (
            <p className="text-sm text-destructive" role="alert">
              Couldn&apos;t load the cohort. Please retry — no data is shown rather than stale or
              fabricated figures.
            </p>
          )}

          {cohortState === "ready" && summary && summary.total === 0 && (
            <div className="rounded-lg border-[0.5px] border-border bg-card p-6">
              <p className="text-sm text-muted-foreground">No participants in your scoped cohort.</p>
            </div>
          )}

          {cohortState === "ready" && summary && summary.total > 0 && (
            <>
              <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-4">
                <Card className="border-[0.5px] border-border shadow-sm lg:row-span-2">
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm font-medium text-muted-foreground">
                      Mean Cohort Risk
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="flex flex-col items-center justify-center pt-4">
                    <RiskDial value={Math.round(summary.mean_risk * 100)} size="lg" />
                    <p className="mt-4 max-w-[180px] text-center text-xs text-muted-foreground">
                      {summary.by_band.high} participant{summary.by_band.high === 1 ? "" : "s"} in
                      the high-risk band.
                    </p>
                  </CardContent>
                </Card>

                <MetricCard title="Total" value={summary.total} subtitle="In scope" />
                <MetricCard
                  title="High Risk"
                  value={summary.by_band.high}
                  subtitle="Risk band"
                  status="risk"
                />
                <MetricCard
                  title="Medium Risk"
                  value={summary.by_band.medium}
                  subtitle="Watch band"
                  status="watch"
                />
                <MetricCard
                  title="Low Risk"
                  value={summary.by_band.low}
                  subtitle="Calm band"
                  status="calm"
                />
                <MetricCard
                  title="Mean Risk"
                  value={`${Math.round(summary.mean_risk * 100)}%`}
                  subtitle="Cohort average"
                  status="watch"
                />
              </div>

              <RiskDistribution
                calm={summary.by_band.low}
                watch={summary.by_band.medium}
                risk={summary.by_band.high}
              />
            </>
          )}
        </section>

        {/* ---------------------------------------------------------------- */}
        {/* Risk spotlight (scope-bound: GET /participants/{id}/risk[/history]) */}
        {/* ---------------------------------------------------------------- */}
        {cohortState === "ready" && spotlightRows.length > 0 && (
          <>
            <PulseDivider />
            <RiskSpotlight rows={spotlightRows} onOpen={(id) => router.push(`/participant/${id}`)} />
          </>
        )}

        {/* ---------------------------------------------------------------- */}
        {/* Platform analytics (platform/auditor only)                        */}
        {/* ---------------------------------------------------------------- */}
        <PulseDivider />
        <section className="mb-8">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-foreground">Platform Analytics</h2>
            <span className="font-mono text-xs uppercase tracking-wide text-muted-foreground">
              platform · auditor
            </span>
          </div>

          {isPlatformRole ? (
            <PlatformAnalytics />
          ) : (
            <div className="rounded-lg border-[0.5px] border-border bg-card p-6">
              <p className="text-sm text-muted-foreground">
                Model traffic, cost &amp; latency, guardrail mix, and drift are available only to
                platform and auditor roles.
              </p>
            </div>
          )}
        </section>
      </main>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Risk spotlight — reuses RiskTrajectory (champion-at-each-point, version boundaries)
// and ContributingFactors (signed contributions) for a selected participant.
// ---------------------------------------------------------------------------

function RiskSpotlight({
  rows,
  onOpen,
}: {
  rows: CohortRow[]
  onOpen: (id: string) => void
}) {
  const [selectedId, setSelectedId] = useState<string>(rows[0].participant_id)
  const [risk, setRisk] = useState<RiskExplanation | null>(null)
  const [history, setHistory] = useState<RiskHistoryPoint[]>([])
  const [loading, setLoading] = useState(true)

  // Keep a valid selection if the cohort changes underneath us.
  useEffect(() => {
    if (!rows.some((r) => r.participant_id === selectedId)) {
      setSelectedId(rows[0].participant_id)
    }
  }, [rows, selectedId])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setRisk(null)
    setHistory([])
    Promise.allSettled([
      getParticipantRisk(selectedId),
      getParticipantRiskHistory(selectedId),
    ]).then(([r, h]) => {
      if (cancelled) return
      if (r.status === "fulfilled") setRisk(r.value)
      if (h.status === "fulfilled") setHistory(h.value.points)
      setLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [selectedId])

  const selectedRow = rows.find((r) => r.participant_id === selectedId)
  const isSynthetic = (selectedRow?.synthetic ?? false) || (risk?.synthetic ?? false)

  // Group the correlated missed-visit channels + emphasize the primary driver (display-layer only;
  // the model's occlusion numbers are unchanged — see lib/factors).
  const factors = useMemo(() => groupFactors(risk?.factors ?? []), [risk])

  return (
    <section className="mb-2">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Risk Spotlight</h2>
          <p className="text-sm text-muted-foreground">
            Champion risk trajectory and contributing factors for a highest-risk participant.
          </p>
        </div>
        <button
          onClick={() => onOpen(selectedId)}
          className="text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          Open participant →
        </button>
      </div>

      {/* Top-N selector — the cohort is already ranked server-side. */}
      <div className="mb-4 flex flex-wrap gap-2">
        {rows.map((r) => {
          const active = r.participant_id === selectedId
          return (
            <button
              key={r.participant_id}
              onClick={() => setSelectedId(r.participant_id)}
              className={
                active
                  ? "rounded-md border-[0.5px] border-foreground bg-foreground px-3 py-1.5 font-mono text-xs text-primary-foreground"
                  : "rounded-md border-[0.5px] border-border bg-card px-3 py-1.5 font-mono text-xs text-muted-foreground transition-colors hover:text-foreground"
              }
            >
              {r.participant_id} · {Math.round(r.risk_score * 100)}%
            </button>
          )
        })}
      </div>

      {isSynthetic && (
        <div className="mb-4 flex items-start gap-2 rounded-lg border-[0.5px] border-status-watch/40 bg-status-watch/[0.06] px-4 py-3 text-sm text-foreground">
          <span className="font-mono text-xs font-semibold uppercase tracking-wide text-status-watch">
            Synthetic data
          </span>
          <span className="text-muted-foreground">
            This participant&apos;s risk is a method demonstration only — not a clinical prediction.
          </span>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="border-[0.5px] border-border shadow-sm">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-foreground">
              Risk Trajectory (champion-of-record over time)
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <p className="py-6 text-center text-sm text-muted-foreground">Loading trajectory…</p>
            ) : (
              <RiskTrajectory points={history} />
            )}
          </CardContent>
        </Card>

        <ContributingFactors factors={factors} />
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------
// Platform analytics — model traffic, cost/latency, guardrail mix, real drift.
// Each panel reads an existing platform/auditor-only endpoint; failures fall back to
// honest-empty (never fabricated). Only fetched when the caller is platform/auditor.
// ---------------------------------------------------------------------------

function usd(v: number): string {
  return `$${v.toFixed(4)}`
}

function PlatformAnalytics() {
  const [models, setModels] = useState<ModelStatus[]>([])
  const [cost, setCost] = useState<CostReport | null>(null)
  const [messages, setMessages] = useState<MessageEventOut[]>([])
  const [drift, setDrift] = useState<DriftPoint[]>([])

  useEffect(() => {
    getModels()
      .then((p) => setModels(p.items))
      .catch(() => setModels([]))
    getCost()
      .then(setCost)
      .catch(() => setCost(null))
    getMessages({ limit: 200 })
      .then((p) => setMessages(p.items))
      .catch(() => setMessages([]))
    getDrift()
      .then((p) => setDrift(p.items))
      .catch(() => setDrift([]))
  }, [])

  // Model traffic — champion / challenger / shadow split (GET /monitoring/models).
  const modelSegments = useMemo(() => {
    const champion = models.filter((m) => m.role === "champion").length
    const challenger = models.filter((m) => m.role === "challenger").length
    const shadow = models.filter((m) => m.role === "shadow").length
    return [
      { label: "Champion", value: champion, color: "calm" as const },
      { label: "Challenger", value: challenger, color: "watch" as const },
      { label: "Shadow", value: shadow, color: "muted" as const },
    ]
  }, [models])

  // Cost/latency rollups (GET /monitoring/cost). /cohort/cost has NO time axis (it rolls up by
  // surface×model), so a time-series line would imply a trend that doesn't exist — we show metric
  // cards + per-bucket avg-latency bars instead. Honest-zero cost until a per-1k rate is configured.
  const rollups = cost?.rollups ?? []
  const totalTurns = cost?.total_turns ?? 0
  const totalLatency = rollups.reduce((s, r) => s + r.total_latency_ms, 0)
  const overallAvgLatency = totalTurns > 0 ? Math.round(totalLatency / totalTurns) : 0
  const maxAvgLatency = Math.max(1, ...rollups.map((r) => r.avg_latency_ms))

  const hasModelTraffic = modelSegments.some((s) => s.value > 0)

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Model traffic */}
        {hasModelTraffic ? (
          <TrafficSplitBar segments={modelSegments} title="Model Traffic (champion / challenger / shadow)" />
        ) : (
          <Card className="border-[0.5px] border-border shadow-sm">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium text-foreground">
                Model Traffic (champion / challenger / shadow)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="py-4 text-center text-sm text-muted-foreground">
                No routing projection available yet.
              </p>
            </CardContent>
          </Card>
        )}

        {/* Guardrail / refusal mix */}
        <GuardrailMix events={messages} />
      </div>

      {/* Cost & latency */}
      <Card className="border-[0.5px] border-border shadow-sm">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-foreground">Cost &amp; Latency</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="mb-5 grid grid-cols-2 gap-4 lg:grid-cols-4">
            <MetricCard label="Total Turns" value={cost ? totalTurns : "—"} />
            <MetricCard label="Total Cost (USD)" value={cost ? usd(cost.total_cost) : "—"} />
            <MetricCard
              label="Total Tokens"
              value={cost ? cost.total_tokens.toLocaleString() : "—"}
              subtitle={
                cost
                  ? `${cost.total_prompt_tokens.toLocaleString()} prompt · ${cost.total_completion_tokens.toLocaleString()} completion`
                  : undefined
              }
            />
            <MetricCard label="Avg Latency" value={cost ? `${overallAvgLatency}ms` : "—"} />
          </div>

          {cost && cost.total_cost === 0 && (
            <p className="mb-4 text-xs text-muted-foreground">
              Cost is $0.0000 — token usage is captured per turn, but no per-1k rate is configured
              yet, so dollar cost is honestly zero. Turn counts, tokens, and latency below are real.
            </p>
          )}

          {rollups.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">No turns recorded yet.</p>
          ) : (
            <div className="space-y-3">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Avg latency by surface &amp; model
              </p>
              {rollups.map((r) => (
                <div key={`${r.surface}-${r.llm_provider_model}`} className="space-y-1">
                  <div className="flex items-center justify-between font-mono text-xs">
                    <span className="text-foreground">
                      {r.surface} · {r.llm_provider_model || "—"}
                    </span>
                    <span className="text-muted-foreground">
                      {Math.round(r.avg_latency_ms)}ms · {r.turns} turn{r.turns === 1 ? "" : "s"} ·{" "}
                      {r.total_tokens.toLocaleString()} tok
                    </span>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-chart-4 transition-all duration-500"
                      style={{ width: `${(r.avg_latency_ms / maxAvgLatency) * 100}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Drift — real PSI/KS with provenance (GET /monitoring/drift) */}
      <DriftPanel points={drift} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Drift panel — REAL computed PSI/KS points with provenance labels. Insufficient/absent data
// → honest empty state (never a fabricated chart). Each point shows value vs threshold, breach
// state, and synthetic / constructed-demo provenance.
// ---------------------------------------------------------------------------

function DriftPanel({ points }: { points: DriftPoint[] }) {
  return (
    <Card className="border-[0.5px] border-border shadow-sm">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-foreground">
          Drift (PSI / KS)
        </CardTitle>
      </CardHeader>
      <CardContent>
        {points.length === 0 ? (
          <p className="py-4 text-center text-sm text-muted-foreground">
            No drift points computed yet (insufficient data) — this surface stays honestly empty
            rather than showing fabricated signals.
          </p>
        ) : (
          <div className="space-y-3">
            {points.map((d, i) => {
              const ratio = Math.min(1, d.threshold > 0 ? d.value / d.threshold : 0)
              return (
                <div
                  key={`${d.model_name}-${d.metric}-${d.distribution}-${i}`}
                  className="space-y-1.5"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-mono text-xs text-foreground">
                      {d.model_name} · {d.distribution} ·{" "}
                      <span className="uppercase">{d.metric}</span>
                    </span>
                    <span className="flex items-center gap-2">
                      {d.breached && (
                        <span className="rounded bg-status-risk/10 px-1.5 py-0.5 text-[10px] font-semibold text-status-risk">
                          BREACH · ML ENGINEER ALERTED
                        </span>
                      )}
                      {d.synthetic && (
                        <span className="rounded bg-status-watch/10 px-1.5 py-0.5 text-[10px] font-semibold text-status-watch">
                          SYNTHETIC
                        </span>
                      )}
                      {d.constructed_demo && (
                        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
                          CONSTRUCTED DEMO
                        </span>
                      )}
                      <span
                        className={
                          d.breached
                            ? "font-mono text-xs font-semibold text-status-risk"
                            : "font-mono text-xs font-semibold text-status-calm"
                        }
                      >
                        {d.value.toFixed(3)} / {d.threshold.toFixed(2)}
                      </span>
                    </span>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className={
                        d.breached
                          ? "h-full rounded-full bg-status-risk transition-all duration-500"
                          : "h-full rounded-full bg-status-calm transition-all duration-500"
                      }
                      style={{ width: `${ratio * 100}%` }}
                    />
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    n={d.current_n} vs ref {d.reference_n}
                    {d.note ? ` · ${d.note}` : ""}
                  </p>
                </div>
              )
            })}
            {points.some((d) => d.breached) && (
              <p className="pt-1 text-[11px] text-muted-foreground">
                A breach enqueues a PII-free alert to the ML engineer (email + structured log) and
                marks the drift point notified. Per-point alert-delivery state isn&apos;t exposed by
                the API yet; this badge reflects breach state.
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import {
  MetricCard,
  PageHeader,
  PulseDivider,
  RiskDial,
  RiskDistribution,
  TriageTable,
  type TriageParticipant,
} from "@/components/vigil"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { getCohort, getCohortSummary, ApiError } from "@/lib/api"
import { daysSince } from "@/lib/utils"
import type { CohortRow, CohortSummary } from "@/lib/types"

function humanizeFactor(tag: string): string {
  return tag.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}

// Adapt CohortRow (api.md) to the TriageTable display shape. `riskTrend` still has no
// CohortRow field (needs a risk-history endpoint); `daysEnrolled` is REAL from enrolled_at.
function toRow(r: CohortRow): TriageParticipant {
  return {
    id: r.participant_id,
    codedRef: r.coded_ref, // primary user-facing label (UUID stays the route/key)
    riskBand: r.risk_band, // authoritative band drives the dot/dial color (never re-thresholded)
    riskScore: Math.round(r.risk_score * 100),
    riskTrend: [], // TODO(phase4/5): no CohortRow field; needs a risk-history endpoint
    topRiskReason: r.top_factors[0] ? humanizeFactor(r.top_factors[0]) : "—",
    daysEnrolled: daysSince(r.enrolled_at),
  }
}

export default function DashboardPage() {
  const router = useRouter()
  const [rows, setRows] = useState<CohortRow[]>([])
  const [summary, setSummary] = useState<CohortSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    // Real GET /cohort + GET /cohort/summary — RLS-scoped server-side to the caller's
    // token; the frontend adds NO sponsor filter. Honest error state on failure (403 for a
    // platform role, or transport error) — never stale/fabricated rows.
    setError(null)
    Promise.all([getCohort(), getCohortSummary()])
      .then(([page, sum]) => {
        setRows(page.items)
        setSummary(sum)
      })
      .catch((e) => {
        setRows([])
        setSummary(null)
        if (e instanceof ApiError && e.status === 403) {
          setError("This view is not available for your role.")
        } else {
          setError("Could not load the cohort. Please try again.")
        }
      })
      .finally(() => setLoaded(true))
  }, [])

  // /cohort is already ranked server-side; cap the dashboard to the top 5.
  const topRows = useMemo(() => rows.slice(0, 5).map(toRow), [rows])
  const syntheticIds = useMemo(
    () => new Set(rows.filter((r) => r.synthetic).map((r) => r.participant_id)),
    [rows],
  )

  return (
    <div className="min-h-screen bg-background">
      <main className="mx-auto max-w-7xl px-6 py-8">
        <PageHeader
          eyebrow="Dashboard"
          title="Trial Overview"
          subtitle="Cohort risk at a glance — composition, mean risk, and the highest-risk participants, scoped to your access. A synthetic-cohort demonstration."
        />

        <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-4">
          <Card className="border-[0.5px] border-border shadow-sm lg:row-span-2">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Overall Trial Risk
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col items-center justify-center pt-4">
              <RiskDial value={summary ? Math.round(summary.mean_risk * 100) : null} size="lg" />
              <p className="mt-4 max-w-[180px] text-center text-xs text-muted-foreground">
                {summary
                  ? `${summary.by_band.high} participants in the high-risk band.`
                  : "Loading cohort summary…"}
              </p>
            </CardContent>
          </Card>

          <MetricCard
            title="Total Participants"
            value={summary?.total ?? "—"}
            subtitle="In scope"
          />
          <MetricCard
            title="High Risk"
            value={summary?.by_band.high ?? "—"}
            subtitle="High band"
            status="risk"
          />
          <MetricCard
            title="Mean Risk"
            value={summary ? `${Math.round(summary.mean_risk * 100)}%` : "—"}
            subtitle="Cohort average"
            status="watch"
          />

          <MetricCard
            title="Medium Risk"
            value={summary?.by_band.medium ?? "—"}
            subtitle="Watch band"
            status="watch"
          />
          <MetricCard
            title="Low Risk"
            value={summary?.by_band.low ?? "—"}
            subtitle="Calm band"
            status="calm"
          />
        </div>

        <PulseDivider />

        <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
          <RiskDistribution
            calm={summary?.by_band.low ?? 0}
            watch={summary?.by_band.medium ?? 0}
            risk={summary?.by_band.high ?? 0}
            className="lg:col-span-3"
          />
        </div>

        <PulseDivider />

        <div className="mb-8">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-foreground">
                Highest Risk Participants
              </h2>
              <p className="text-sm text-muted-foreground">
                Participants ranked by dropout risk
              </p>
            </div>
            <button
              onClick={() => router.push("/triage")}
              className="text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              View All
            </button>
          </div>
          {error ? (
            <p className="py-8 text-center text-sm text-muted-foreground" role="alert">
              {error}
            </p>
          ) : loaded && topRows.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No participants in your scoped cohort.
            </p>
          ) : (
            <TriageTable
              participants={topRows}
              onCallParticipant={(id) => router.push(`/participant/${id}`)}
              syntheticIds={syntheticIds}
            />
          )}
        </div>
      </main>
    </div>
  )
}

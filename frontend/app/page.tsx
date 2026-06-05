"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import {
  MetricCard,
  PulseDivider,
  RiskDial,
  RiskDistribution,
  TriageTable,
  type TriageParticipant,
} from "@/components/vigil"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { getCohort, getCohortSummary } from "@/lib/stubs"
import type { CohortRow, CohortSummary } from "@/lib/types"

function humanizeFactor(tag: string): string {
  return tag.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}

// Adapt CohortRow (api.md) to the TriageTable display shape. `riskTrend` and
// `daysEnrolled` have no CohortRow field — display-only placeholders.
function toRow(r: CohortRow): TriageParticipant {
  return {
    id: r.participant_id,
    riskScore: Math.round(r.risk_score * 100),
    riskTrend: [], // TODO(phase4/5): no CohortRow field; needs a risk-history endpoint
    topRiskReason: r.top_factors[0] ? humanizeFactor(r.top_factors[0]) : "—",
    daysEnrolled: 0, // TODO(phase4/5): no CohortRow field; from ParticipantDetail.enrolled_at
  }
}

export default function DashboardPage() {
  const router = useRouter()
  const [rows, setRows] = useState<CohortRow[]>([])
  const [summary, setSummary] = useState<CohortSummary | null>(null)

  useEffect(() => {
    // TODO(phase4/5): wire to GET /cohort and GET /cohort/summary
    getCohort().then((page) => setRows(page.items))
    getCohortSummary().then(setSummary)
  }, [])

  const topRows = useMemo(
    () =>
      [...rows]
        .sort((a, b) => b.risk_score - a.risk_score)
        .slice(0, 5)
        .map(toRow),
    [rows],
  )

  return (
    <div className="min-h-screen bg-background">
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Trial Overview
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            CARDINAL-001 Phase III Cardiovascular Study
          </p>
        </div>

        <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-4">
          <Card className="border-[0.5px] border-border shadow-sm lg:row-span-2">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Overall Trial Risk
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col items-center justify-center pt-4">
              <RiskDial value={summary ? Math.round(summary.mean_risk * 100) : 0} size="lg" />
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
                Participants ranked by dropout probability
              </p>
            </div>
            <button
              onClick={() => router.push("/triage")}
              className="text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              View All
            </button>
          </div>
          <TriageTable
            participants={topRows}
            onCallParticipant={(id) => router.push(`/participant/${id}`)}
          />
        </div>
      </main>
    </div>
  )
}

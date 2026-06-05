"use client"

import { useState, useMemo, useEffect } from "react"
import { useRouter } from "next/navigation"
import { TriageHeader } from "@/components/vigil/triage-header"
import { TriageTable, type Participant } from "@/components/vigil/triage-table"
import { MetricCard } from "@/components/vigil/metric-card"
import { PulseDivider } from "@/components/vigil/pulse-divider"
import { getCohort, getCohortSummary } from "@/lib/stubs"
import type { CohortRow, CohortSummary } from "@/lib/types"

// Trial selector options are UI-local; trial scope ultimately comes from the
// JWT scope (api.md). TODO(phase4): populate from resolved scope / a trials endpoint.
const TRIALS = [
  { id: "trl_22", name: "BEACON-2024", phase: "III" },
  { id: "trl_23", name: "HORIZON-A", phase: "II" },
  { id: "trl_24", name: "CLARITY-1", phase: "III" },
]

function humanizeFactor(tag: string): string {
  return tag.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}

// Adapt a CohortRow (api.md) to the TriageTable display shape.
// NOTE: `riskTrend` (sparkline) and `daysEnrolled` have no field in CohortRow;
// they are display-only placeholders until a backing endpoint exists.
function toRow(r: CohortRow): Participant {
  return {
    id: r.participant_id,
    riskScore: Math.round(r.risk_score * 100),
    riskTrend: [], // TODO(phase4/5): no CohortRow field; needs a risk-history endpoint
    topRiskReason: r.top_factors[0] ? humanizeFactor(r.top_factors[0]) : "—",
    daysEnrolled: 0, // TODO(phase4/5): no CohortRow field; comes from ParticipantDetail.enrolled_at
  }
}

export default function CohortTriagePage() {
  const router = useRouter()
  const [selectedTrial, setSelectedTrial] = useState(TRIALS[0].id)
  const [searchQuery, setSearchQuery] = useState("")
  const [rows, setRows] = useState<CohortRow[]>([])
  const [summary, setSummary] = useState<CohortSummary | null>(null)

  useEffect(() => {
    // TODO(phase4/5): wire to GET /cohort and GET /cohort/summary (scope-filtered by trial)
    getCohort().then((page) => setRows(page.items))
    getCohortSummary().then(setSummary)
  }, [selectedTrial])

  const filteredRows = useMemo(() => {
    if (!searchQuery.trim()) return rows
    const q = searchQuery.toLowerCase()
    return rows.filter(
      (r) =>
        r.participant_id.toLowerCase().includes(q) ||
        r.top_factors.some((f) => f.toLowerCase().includes(q)),
    )
  }, [rows, searchQuery])

  const participants = useMemo(() => filteredRows.map(toRow), [filteredRows])

  const handleCallParticipant = (participantId: string) => {
    // TODO(phase4/5): wire to POST /participants/{participant_id}/interventions { kind: "call" }
    router.push(`/participant/${participantId}`)
  }

  return (
    <div className="min-h-screen bg-background">
      <TriageHeader
        trials={TRIALS}
        selectedTrial={selectedTrial}
        onTrialChange={setSelectedTrial}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
      />

      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Cohort Triage
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Prioritized participant list sorted by dropout risk
          </p>
        </div>

        <div className="grid grid-cols-4 gap-4">
          <MetricCard
            label="At-Risk"
            value={summary?.by_band.high ?? "—"}
            status="risk"
          />
          <MetricCard
            label="Watch"
            value={summary?.by_band.medium ?? "—"}
            status="watch"
          />
          <MetricCard label="Cohort Size" value={summary?.total ?? "—"} />
          <MetricCard
            label="Avg Risk Score"
            value={summary ? Math.round(summary.mean_risk * 100) : "—"}
            status={
              summary && summary.mean_risk >= 0.7
                ? "risk"
                : summary && summary.mean_risk >= 0.4
                ? "watch"
                : "calm"
            }
          />
        </div>

        <PulseDivider className="my-8" />

        <TriageTable
          participants={participants}
          onCallParticipant={handleCallParticipant}
        />
      </main>
    </div>
  )
}

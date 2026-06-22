"use client"

import { useState, useMemo, useEffect } from "react"
import { useRouter } from "next/navigation"
import { TriageHeader } from "@/components/vigil/triage-header"
import { TriageTable, type Participant } from "@/components/vigil/triage-table"
import { MetricCard } from "@/components/vigil/metric-card"
import { PageHeader } from "@/components/vigil/page-header"
import { PulseDivider } from "@/components/vigil/pulse-divider"
import { DemoLoop } from "@/components/vigil/demo-loop"
import { getCohort, getCohortSummary, ApiError } from "@/lib/api"
import { daysSince } from "@/lib/utils"
import { useAuth } from "@/lib/auth-context"
import { PLATFORM_ROLES } from "@/lib/role-gates"
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
// NOTE: `riskTrend` (sparkline) still has no CohortRow field (needs a risk-history endpoint);
// `daysEnrolled` is REAL, derived from the row's enrolled_at.
function toRow(r: CohortRow): Participant {
  return {
    id: r.participant_id,
    codedRef: r.coded_ref, // primary user-facing label (UUID stays the route/key)
    riskScore: Math.round(r.risk_score * 100),
    riskTrend: [], // TODO(phase5): no CohortRow field; needs a risk-history endpoint
    topRiskReason: r.top_factors[0] ? humanizeFactor(r.top_factors[0]) : "—",
    daysEnrolled: daysSince(r.enrolled_at),
  }
}

export default function CohortTriagePage() {
  const router = useRouter()
  const { me } = useAuth()
  const [selectedTrial, setSelectedTrial] = useState(TRIALS[0].id)
  const [searchQuery, setSearchQuery] = useState("")
  const [rows, setRows] = useState<CohortRow[]>([])
  const [summary, setSummary] = useState<CohortSummary | null>(null)
  const [scopeDenied, setScopeDenied] = useState(false)

  // Role gate: platform roles (ml_admin / auditor) see a message, not the cohort.
  const isPlatformRole = me?.role != null && (PLATFORM_ROLES as string[]).includes(me.role)

  useEffect(() => {
    if (isPlatformRole) return
    setScopeDenied(false)
    Promise.all([
      getCohort({ trial_id: selectedTrial }),
      getCohortSummary({ trial_id: selectedTrial }),
    ])
      .then(([page, sum]) => {
        setRows(page.items)
        setSummary(sum)
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 403) {
          setScopeDenied(true)
        }
        // Other errors: leave rows empty; user sees blank table.
      })
  }, [selectedTrial, isPlatformRole])

  const filteredRows = useMemo(() => {
    if (!searchQuery.trim()) return rows
    const q = searchQuery.toLowerCase()
    return rows.filter(
      (r) =>
        r.participant_id.toLowerCase().includes(q) ||
        (r.coded_ref?.toLowerCase().includes(q) ?? false) ||
        r.top_factors.some((f) => f.toLowerCase().includes(q)),
    )
  }, [rows, searchQuery])

  const participants = useMemo(() => filteredRows.map(toRow), [filteredRows])

  const handleCallParticipant = (participantId: string) => {
    router.push(`/participant/${participantId}`)
  }

  const handleDemoRefresh = (newRows: CohortRow[], newSummary: CohortSummary) => {
    setRows(newRows)
    setSummary(newSummary)
  }

  // Platform role message replaces the cohort view.
  if (isPlatformRole || scopeDenied) {
    return (
      <div className="min-h-screen bg-background">
        <main className="mx-auto max-w-7xl px-6 py-16">
          <p className="text-muted-foreground">
            This view is not available for your role.
          </p>
        </main>
      </div>
    )
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
        <PageHeader
          eyebrow="Triage"
          title="Cohort Triage"
          subtitle="The prioritized worklist — participants ranked by dropout risk so you can work the cohort top-down."
        />

        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
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
          syntheticIds={new Set(filteredRows.filter((r) => r.synthetic).map((r) => r.participant_id))}
        />

        <PulseDivider className="my-8" />

        <DemoLoop trialId={selectedTrial} onRefresh={handleDemoRefresh} />
      </main>
    </div>
  )
}

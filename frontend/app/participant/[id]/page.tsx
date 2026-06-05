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
} from "@/components/vigil"
import { getParticipant, getParticipantRisk, logIntervention } from "@/lib/stubs"
import type { ParticipantDetail, RiskExplanation } from "@/lib/types"

function humanizeFactor(tag: string): string {
  return tag.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}

function daysSince(iso: string): number {
  return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000))
}

export default function ParticipantDetailPage() {
  const params = useParams<{ id: string }>()
  const id = params.id

  const [detail, setDetail] = useState<ParticipantDetail | null>(null)
  const [risk, setRisk] = useState<RiskExplanation | null>(null)

  useEffect(() => {
    if (!id) return
    // TODO(phase4/5): wire to GET /participants/{participant_id}
    getParticipant(id).then(setDetail)
    // TODO(phase4/5): wire to GET /participants/{participant_id}/risk
    getParticipantRisk(id).then(setRisk)
  }, [id])

  // Map signed FactorContribution -> ContributingFactors display props.
  const factors = useMemo(
    () =>
      (risk?.factors ?? []).map((f) => ({
        name: humanizeFactor(f.feature),
        impact: Math.round(Math.abs(f.contribution) * 100),
        description: f.contribution >= 0 ? "Increases risk" : "Decreases risk",
      })),
    [risk],
  )

  const riskPct = detail ? Math.round(detail.risk_score * 100) : 0

  const handleSchedule = async () => {
    if (!id) return
    // TODO(phase4/5): wire to POST /participants/{participant_id}/interventions
    await logIntervention(id, { kind: "call", note: "Scheduled outreach from triage." })
  }

  // RiskExplanation has factors but no prose; the assistant produces prose.
  // TODO(phase4/5): replace with assistant-generated explanation (/assistant).
  const explanation = risk
    ? `Risk score ${riskPct}% over a ${risk.horizon_days}-day horizon (model ${risk.model_version}). Top drivers are listed at left; review with site staff before acting.`
    : "Loading risk explanation…"

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
              <h1 className="font-mono text-lg font-semibold text-foreground">{id}</h1>
              <p className="text-xs text-muted-foreground">
                {detail ? `Trial ${detail.trial_id} · Site ${detail.site_id}` : "Loading…"}
              </p>
            </div>
          </div>

          <RiskDial value={riskPct} size="md" />
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">
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
                {detail ? daysSince(detail.enrolled_at) : "—"}
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

        <PulseDivider />

        <div className="grid grid-cols-2 gap-6">
          <ContributingFactors factors={factors} />
          <ExplanationCard
            participantId={id ?? ""}
            riskScore={riskPct}
            explanation={explanation}
          />
        </div>

        <PulseDivider />

        <div className="flex items-center justify-end gap-4">
          <button className="px-4 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground">
            View Full History
          </button>
          <button
            onClick={handleSchedule}
            className="rounded-md bg-foreground px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-foreground/90"
          >
            Schedule Intervention
          </button>
        </div>
      </main>
    </div>
  )
}

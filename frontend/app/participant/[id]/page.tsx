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
import { getParticipant, getParticipantRisk, logIntervention, ApiError } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { PLATFORM_ROLES, CAN_LOG_INTERVENTIONS } from "@/lib/role-gates"
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
  const { me } = useAuth()

  const [detail, setDetail] = useState<ParticipantDetail | null>(null)
  const [risk, setRisk] = useState<RiskExplanation | null>(null)
  const [scopeDenied, setScopeDenied] = useState(false)

  const isPlatformRole = me?.role != null && (PLATFORM_ROLES as string[]).includes(me.role)
  const canLogIntervention =
    me?.role != null && (CAN_LOG_INTERVENTIONS as string[]).includes(me.role)

  useEffect(() => {
    if (!id || isPlatformRole) return
    setScopeDenied(false)
    Promise.all([getParticipant(id), getParticipantRisk(id)])
      .then(([d, r]) => {
        setDetail(d)
        setRisk(r)
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 403) {
          setScopeDenied(true)
        }
      })
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
    await logIntervention(id, { kind: "call", note: "Scheduled outreach from triage." })
  }

  // RiskExplanation has factors but no prose; the assistant produces prose.
  // TODO(phase5): replace with assistant-generated explanation (/assistant).
  const explanation = risk
    ? `Risk score ${riskPct}% over a ${risk.horizon_days}-day horizon (model ${risk.model_version}). Top drivers are listed at left; review with site staff before acting.`
    : "Loading risk explanation…"

  const isSynthetic = detail?.synthetic ?? false

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

        {/* Visit trend sparkline: RiskExplanation has no history field.
            TODO(phase5): risk history endpoint needed for trend sparkline */}
        <div className="mb-4 rounded border border-border bg-card px-4 py-3">
          <p className="mb-1 text-xs font-medium text-muted-foreground">Risk Score (current)</p>
          <div className="flex items-center gap-2">
            <span className="font-mono text-2xl font-semibold text-foreground">{riskPct}%</span>
            <span className="text-xs text-muted-foreground">
              {/* TODO(phase5): risk history endpoint needed for trend sparkline */}
              Single-point — trend requires history endpoint
            </span>
          </div>
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

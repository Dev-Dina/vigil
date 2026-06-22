"use client"

import { useState } from "react"
import Link from "next/link"
import { Phone, ChevronUp, ChevronDown, ChevronsUpDown } from "lucide-react"
import { cn, participantLabel, statusFromBand, statusFromScore } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { StatusDot } from "./status-dot"
import { MiniRiskDial } from "./mini-risk-dial"
import { Sparkline } from "./sparkline"

export interface Participant {
  id: string // internal UUID — routing/keys only, never the primary display label
  codedRef?: string | null // human-readable pseudonymous ref (e.g. "A-0001"); the primary label
  riskBand?: string | null // AUTHORITATIVE backend band — drives the dot/dial color (never re-thresholded)
  riskScore: number
  riskTrend: number[]
  topRiskReason: string
  daysEnrolled: number | null // null = unknown/invalid enrolled_at → rendered as "—"
}

type SortKey = "riskScore" | "daysEnrolled" | "id"
type SortDirection = "asc" | "desc"

interface TriageTableProps {
  participants: Participant[]
  onCallParticipant: (participantId: string) => void
  className?: string
  /** Participant IDs whose scores are synthetic — a permanent visible badge is shown. */
  syntheticIds?: Set<string>
}

// Status (dot + dial color) from the AUTHORITATIVE backend band; falls back to aligned score cuts
// only when a row has no band — so the dot/dial can never contradict the band shown elsewhere.
function rowStatus(p: Participant): "calm" | "watch" | "risk" {
  return statusFromBand(p.riskBand) ?? statusFromScore(p.riskScore)
}

export function TriageTable({
  participants,
  onCallParticipant,
  className,
  syntheticIds,
}: TriageTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>("riskScore")
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc")

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDirection(sortDirection === "asc" ? "desc" : "asc")
    } else {
      setSortKey(key)
      setSortDirection(key === "riskScore" ? "desc" : "asc")
    }
  }

  const sortedParticipants = [...participants].sort((a, b) => {
    const modifier = sortDirection === "asc" ? 1 : -1
    if (sortKey === "riskScore") {
      return (a.riskScore - b.riskScore) * modifier
    }
    if (sortKey === "daysEnrolled") {
      // null (unknown enrolled_at) sorts as 0 so the comparator stays total.
      return ((a.daysEnrolled ?? 0) - (b.daysEnrolled ?? 0)) * modifier
    }
    // Sort by the displayed label (coded_ref when present), not the internal UUID.
    return (
      participantLabel(a.codedRef, a.id).localeCompare(participantLabel(b.codedRef, b.id)) *
      modifier
    )
  })

  const highestRiskId =
    participants.length > 0
      ? participants.reduce((max, p) =>
          p.riskScore > max.riskScore ? p : max
        ).id
      : null

  const SortIcon = ({ column }: { column: SortKey }) => {
    if (sortKey !== column) {
      return <ChevronsUpDown className="ml-1 h-3 w-3 text-muted-foreground/50" />
    }
    return sortDirection === "asc" ? (
      <ChevronUp className="ml-1 h-3 w-3 text-foreground" />
    ) : (
      <ChevronDown className="ml-1 h-3 w-3 text-foreground" />
    )
  }

  if (participants.length === 0) {
    return (
      <div className={cn("rounded-lg border border-border bg-card", className)}>
        <div className="flex flex-col items-center justify-center py-16">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-muted">
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="text-muted-foreground"
            >
              <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
              <path d="M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>
          </div>
          <p className="text-sm font-medium text-foreground">
            No participants found
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            Try adjusting your search or filters
          </p>
        </div>
      </div>
    )
  }

  return (
    <div
      className={cn(
        "overflow-hidden rounded-lg border border-border bg-card shadow-sm",
        className
      )}
    >
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-border bg-muted/30">
              <th className="w-10 px-4 py-3 text-left">
                <span className="sr-only">Status</span>
              </th>
              <th className="px-4 py-3 text-left">
                <button
                  onClick={() => handleSort("id")}
                  className="inline-flex items-center text-xs font-medium uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground"
                >
                  Participant
                  <SortIcon column="id" />
                </button>
              </th>
              <th className="px-4 py-3 text-right">
                <button
                  onClick={() => handleSort("riskScore")}
                  className="inline-flex items-center justify-end text-xs font-medium uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground"
                >
                  Risk Score
                  <SortIcon column="riskScore" />
                </button>
              </th>
              <th className="px-4 py-3 text-center">
                <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  7-Day Trend
                </span>
              </th>
              <th className="px-4 py-3 text-left">
                <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  Top Risk Reason
                </span>
              </th>
              <th className="px-4 py-3 text-right">
                <button
                  onClick={() => handleSort("daysEnrolled")}
                  className="inline-flex items-center justify-end text-xs font-medium uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground"
                >
                  Days Enrolled
                  <SortIcon column="daysEnrolled" />
                </button>
              </th>
              <th className="w-24 px-4 py-3 text-center">
                <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  Action
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {sortedParticipants.map((participant) => {
              const isHighestRisk = participant.id === highestRiskId
              const status = rowStatus(participant)

              return (
                <tr
                  key={participant.id}
                  className={cn(
                    "group border-b border-border transition-colors last:border-b-0 hover:bg-muted/40"
                  )}
                >
                  <td
                    className={cn(
                      "px-4 py-3",
                      isHighestRisk && "border-l-2 border-status-risk"
                    )}
                  >
                    <StatusDot status={status} />
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-col gap-0.5">
                      <Link
                        href={`/participant/${participant.id}`}
                        className="font-mono text-sm font-medium text-foreground underline-offset-4 hover:underline"
                      >
                        {participantLabel(participant.codedRef, participant.id)}
                      </Link>
                      {syntheticIds?.has(participant.id) && (
                        <span className="inline-flex w-fit items-center rounded border border-status-watch/30 bg-status-watch/10 px-1.5 py-0 text-[10px] font-semibold uppercase tracking-wide text-status-watch">
                          SYNTHETIC
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-2">
                      <MiniRiskDial value={participant.riskScore} size={28} status={status} />
                      <span className="w-10 text-right font-mono text-sm font-semibold text-foreground">
                        {participant.riskScore}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-center">
                      <Sparkline data={participant.riskTrend} width={56} height={20} />
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-sm text-muted-foreground">
                      {participant.topRiskReason}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="font-mono text-sm text-foreground">
                      {participant.daysEnrolled ?? "—"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => onCallParticipant(participant.id)}
                      className="h-8 gap-1.5 border-border bg-transparent text-xs font-medium text-foreground shadow-none transition-colors hover:bg-muted hover:text-foreground"
                    >
                      <Phone className="h-3 w-3" />
                      Call
                    </Button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

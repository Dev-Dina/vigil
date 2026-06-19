"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { PageHeader } from "@/components/vigil"
import { getCohort, ApiError } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { PLATFORM_ROLES } from "@/lib/role-gates"
import type { CohortRow } from "@/lib/types"

// The clinical-ops AT-RISK surface (Gate 9.4). Scope-binding is enforced by the BACKEND
// (Gate 9.2: /cohort?risk_band=high&sort=risk_desc runs under RLS + SEC-1 scope_filter), so a
// coordinator sees ONLY their own site's at-risk participants. The UI renders what it returns —
// it never adds or removes a scope filter (dashboard.md § Decisions).

function humanizeFactor(tag: string): string {
  return tag.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}

export default function AtRiskPage() {
  const router = useRouter()
  const { me } = useAuth()
  const [rows, setRows] = useState<CohortRow[]>([])
  const [state, setState] = useState<"loading" | "ready" | "denied" | "error">("loading")

  const isPlatformRole = me?.role != null && (PLATFORM_ROLES as string[]).includes(me.role)

  useEffect(() => {
    if (isPlatformRole) return
    setState("loading")
    // The scope-bound at-risk query (9.2): high band, ranked by risk desc — server-side.
    getCohort({ risk_band: "high", sort: "risk_desc" })
      .then((page) => {
        setRows(page.items)
        setState("ready")
      })
      .catch((e) => {
        // 403 → access denied (not stale/fabricated data); anything else → honest error.
        setState(e instanceof ApiError && e.status === 403 ? "denied" : "error")
      })
  }, [isPlatformRole])

  if (isPlatformRole || state === "denied") {
    return (
      <div className="min-h-screen bg-background">
        <main className="mx-auto max-w-7xl px-6 py-16">
          <p className="text-muted-foreground">
            The at-risk surface is not available for your role.
          </p>
        </main>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background">
      <main className="mx-auto max-w-7xl px-6 py-8">
        <PageHeader
          eyebrow="At-risk"
          title="At-Risk Participants"
          subtitle="Participants at your site(s) who have crossed the serious-risk threshold, ranked by risk. Select one to review reasons, trajectory, and suggested actions."
        />

        {state === "loading" && (
          <p className="text-sm text-muted-foreground">Loading at-risk participants…</p>
        )}

        {state === "error" && (
          <p className="text-sm text-destructive">
            Couldn&apos;t load the at-risk list. Please retry — no data is shown rather than stale
            or fabricated rows.
          </p>
        )}

        {state === "ready" && rows.length === 0 && (
          <div className="rounded-md border border-border bg-card px-4 py-8 text-center">
            <p className="text-sm text-muted-foreground">
              No participants are currently at the serious-risk threshold in your scope.
            </p>
          </div>
        )}

        {state === "ready" && rows.length > 0 && (
          <div className="overflow-hidden rounded-xl border-[0.5px] border-border bg-card shadow-sm">
            <table className="w-full text-sm">
              <thead className="border-b-[0.5px] border-border bg-secondary/60 text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-4 py-2.5 text-left font-medium">Participant</th>
                  <th className="px-4 py-2.5 text-left font-medium">Risk</th>
                  <th className="px-4 py-2.5 text-left font-medium">Top Reason</th>
                  <th className="px-4 py-2.5 text-left font-medium">Provenance</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.participant_id}
                    onClick={() => router.push(`/participant/${r.participant_id}`)}
                    className="group cursor-pointer border-t-[0.5px] border-border transition-colors hover:bg-secondary/50"
                  >
                    <td className="px-4 py-3 font-mono text-foreground">{r.participant_id}</td>
                    <td className="px-4 py-3">
                      <span className="inline-flex items-center gap-1.5 rounded-md bg-status-risk/10 px-2 py-0.5 font-mono text-xs font-semibold text-status-risk">
                        {Math.round(r.risk_score * 100)}% · {r.risk_band.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {r.top_factors[0] ? humanizeFactor(r.top_factors[0]) : "—"}
                    </td>
                    <td className="px-4 py-3">
                      {/* Synthetic badge is flag-driven (CohortRow.synthetic) — not a toggle. */}
                      {r.synthetic && (
                        <span className="rounded-md bg-status-watch/10 px-2 py-0.5 text-xs font-semibold text-status-watch">
                          SYNTHETIC
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  )
}

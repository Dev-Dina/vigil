"use client"

import { useEffect, useState } from "react"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { MetricCard, PulseDivider } from "@/components/vigil"
import { getCost } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { PLATFORM_ROLES } from "@/lib/role-gates"
import type { CostReport } from "@/lib/types"

// USD cost is derived from REAL token usage × a configured per-1k rate. Until a rate is set the
// rate is 0, so cost is honestly $0.00 while token/latency counts are still real (Gate 6.2). This
// screen shows the real rollups — never fabricated spend.
function usd(v: number): string {
  return `$${v.toFixed(4)}`
}

export default function CostRoutingPage() {
  const { me } = useAuth()
  const [report, setReport] = useState<CostReport | null>(null)

  const isPlatformRole = me?.role != null && (PLATFORM_ROLES as string[]).includes(me.role)

  useEffect(() => {
    if (!isPlatformRole) return
    // GET /monitoring/cost — rollups from real persisted message_events usage.
    getCost()
      .then(setReport)
      .catch(() => setReport(null))
  }, [isPlatformRole])

  if (!isPlatformRole) {
    return (
      <div className="min-h-screen bg-background">
        <main className="mx-auto max-w-7xl px-6 py-16">
          <p className="text-muted-foreground">
            This view is available only to platform and auditor roles.
          </p>
        </main>
      </div>
    )
  }

  const rollups = report?.rollups ?? []
  const allZero = (report?.total_cost ?? 0) === 0

  return (
    <div className="min-h-screen bg-background">
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Cost &amp; Usage
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Token/cost rollups from real per-turn usage (message_events) — honest-zero until a
            cost rate is configured, never fabricated.
          </p>
        </div>

        <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
          <MetricCard label="Total Turns" value={report?.total_turns ?? "—"} />
          <MetricCard
            label="Total Cost (USD)"
            value={report ? usd(report.total_cost) : "—"}
          />
          <MetricCard
            label="Total Tokens"
            value={report ? report.total_tokens.toLocaleString() : "—"}
            subtitle={
              report
                ? `${report.total_prompt_tokens.toLocaleString()} prompt · ${report.total_completion_tokens.toLocaleString()} completion`
                : undefined
            }
          />
          <MetricCard label="Rollup Buckets" value={rollups.length || "—"} />
        </div>

        {allZero && report && (
          <div className="mb-6 rounded-lg border-[0.5px] border-border bg-card p-4">
            <p className="text-sm text-muted-foreground">
              Cost is $0.00 — token usage is captured per turn, but no per-1k cost rate is
              configured yet, so dollar cost is honestly zero. Latency and turn counts below are
              real.
            </p>
          </div>
        )}

        <PulseDivider />

        {/* Per (surface, model) rollups — GET /monitoring/cost (CostReport.rollups) */}
        <div className="mb-8">
          <h2 className="mb-4 text-lg font-semibold text-foreground">Usage by Surface &amp; Model</h2>
          {rollups.length === 0 ? (
            <div className="rounded-lg border-[0.5px] border-border bg-card p-6">
              <p className="text-sm text-muted-foreground">No turns recorded yet.</p>
            </div>
          ) : (
            <div className="overflow-hidden rounded-lg border-[0.5px] border-border">
              <Table>
                <TableHeader>
                  <TableRow className="border-b-[0.5px] bg-background hover:bg-background">
                    <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Surface
                    </TableHead>
                    <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Model
                    </TableHead>
                    <TableHead className="text-right text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Turns
                    </TableHead>
                    <TableHead className="text-right text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Prompt Tok
                    </TableHead>
                    <TableHead className="text-right text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Completion Tok
                    </TableHead>
                    <TableHead className="text-right text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Total Tok
                    </TableHead>
                    <TableHead className="text-right text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Total Cost
                    </TableHead>
                    <TableHead className="text-right text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Avg Latency
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rollups.map((r) => (
                    <TableRow
                      key={`${r.surface}-${r.llm_provider_model}`}
                      className="border-b-[0.5px]"
                    >
                      <TableCell className="font-mono text-sm text-foreground">
                        {r.surface}
                      </TableCell>
                      <TableCell className="font-mono text-sm text-muted-foreground">
                        {r.llm_provider_model || "—"}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm text-foreground">
                        {r.turns}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm text-muted-foreground">
                        {r.prompt_tokens.toLocaleString()}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm text-muted-foreground">
                        {r.completion_tokens.toLocaleString()}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm text-foreground">
                        {r.total_tokens.toLocaleString()}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm text-foreground">
                        {usd(r.total_cost)}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm text-muted-foreground">
                        {Math.round(r.avg_latency_ms)}ms
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </div>
      </main>
    </div>
  )
}

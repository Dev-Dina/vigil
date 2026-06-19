"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import type { MessageEventOut } from "@/lib/types"

// Allowed / refused / blocked mix over REDACTED message_events (coded ids only, no PII).
// Derived honestly from the two real columns:
//   blocked = the guardrail tripped (guardrail_decision === "blocked")
//   refused = the guardrail allowed the turn but it was refused (status === "refused")
//   allowed = everything else
// Empty input → honest empty state, never a fabricated bar. On-brand stacked bar matching
// risk-distribution.tsx (calm/watch/risk triad).

interface GuardrailMixProps {
  events: MessageEventOut[]
  className?: string
}

export function GuardrailMix({ events, className }: GuardrailMixProps) {
  const total = events.length
  const blocked = events.filter((e) => e.guardrail_decision === "blocked").length
  const refused = events.filter(
    (e) => e.guardrail_decision !== "blocked" && e.status === "refused",
  ).length
  const allowed = Math.max(0, total - blocked - refused)

  return (
    <Card className={cn("border-[0.5px] border-border shadow-sm", className)}>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium text-foreground">
          Guardrail &amp; Refusal Mix
        </CardTitle>
      </CardHeader>
      <CardContent>
        {total === 0 ? (
          <p className="py-4 text-center text-sm text-muted-foreground">
            No message events in range — nothing to summarise yet.
          </p>
        ) : (
          <>
            {/* Stacked bar */}
            <div className="mb-4 flex h-3 overflow-hidden rounded-full">
              <div
                className="bg-status-calm transition-all duration-500"
                style={{ width: `${(allowed / total) * 100}%` }}
              />
              <div
                className="bg-status-watch transition-all duration-500"
                style={{ width: `${(refused / total) * 100}%` }}
              />
              <div
                className="bg-status-risk transition-all duration-500"
                style={{ width: `${(blocked / total) * 100}%` }}
              />
            </div>

            {/* Legend */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-status-calm" />
                <span className="text-xs text-muted-foreground">Allowed</span>
                <span className="ml-1 font-mono text-xs font-medium">{allowed}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-status-watch" />
                <span className="text-xs text-muted-foreground">Refused</span>
                <span className="ml-1 font-mono text-xs font-medium">{refused}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-status-risk" />
                <span className="text-xs text-muted-foreground">Blocked</span>
                <span className="ml-1 font-mono text-xs font-medium">{blocked}</span>
              </div>
            </div>

            <p className="mt-4 text-xs text-muted-foreground">
              Over {total} redacted message event{total === 1 ? "" : "s"} shown. Blocked = guardrail
              tripped; refused = allowed turn the assistant declined; allowed = the rest.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}

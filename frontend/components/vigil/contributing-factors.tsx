"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"

// Display props mirror lib/factors.DisplayFactor: a grouped impact is the SUM of its real
// sub-contributions (shown as `subs`); the model's numbers are never invented or rescaled here.
interface SubFactor {
  name: string
  impact: number
  description: string
}

interface Factor {
  name: string
  impact: number // 0-100
  description?: string // direction: "Increases risk" | "Decreases risk"
  primary?: boolean // the dominant driver (largest |impact|)
  grouped?: boolean // impact is the grouped sum of `subs`
  subs?: SubFactor[]
}

interface ContributingFactorsProps {
  factors: Factor[]
  className?: string
}

function getBarColor(impact: number): string {
  if (impact < 40) return "bg-status-calm"
  if (impact < 70) return "bg-status-watch"
  return "bg-status-risk"
}

export function ContributingFactors({ factors, className }: ContributingFactorsProps) {
  const sortedFactors = [...factors].sort((a, b) => b.impact - a.impact)

  return (
    <Card className={cn("border-[0.5px] border-border shadow-sm", className)}>
      <CardHeader className="pb-4">
        <CardTitle className="text-sm font-medium text-foreground">Contributing Factors</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {sortedFactors.map((factor) => (
          <div key={factor.name} className="space-y-1.5">
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5 text-sm text-foreground">
                <span className={cn(factor.primary && "font-medium")}>{factor.name}</span>
                {factor.primary && (
                  <span className="rounded-full border-[0.5px] border-status-risk/30 bg-status-risk/10 px-1.5 py-0.5 text-[10px] font-medium text-status-risk">
                    Primary driver
                  </span>
                )}
              </span>
              <span className="font-mono text-sm text-muted-foreground">{factor.impact}%</span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
              <div
                className={cn("h-full rounded-full transition-all duration-500", getBarColor(factor.impact))}
                style={{ width: `${Math.min(100, factor.impact)}%` }}
              />
            </div>
            {factor.description && (
              <p className="text-xs text-muted-foreground">{factor.description}</p>
            )}
            {/* Grouped factor: name the real sub-contributions so the combined bar is honest. */}
            {factor.grouped && factor.subs && factor.subs.length > 0 && (
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                Grouped sum of{" "}
                {factor.subs.map((s, i) => (
                  <span key={s.name}>
                    {i > 0 && ", "}
                    <span className="text-foreground">{s.name}</span>{" "}
                    <span className="font-mono">{s.impact}%</span>
                  </span>
                ))}
                .
              </p>
            )}
          </div>
        ))}

        {sortedFactors.length === 0 && (
          <p className="py-4 text-center text-sm text-muted-foreground">
            No contributing factors available
          </p>
        )}
      </CardContent>
    </Card>
  )
}

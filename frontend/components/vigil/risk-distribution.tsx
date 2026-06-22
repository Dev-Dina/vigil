"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"

interface RiskDistributionProps {
  calm: number
  watch: number
  risk: number
  className?: string
}

export function RiskDistribution({ calm, watch, risk, className }: RiskDistributionProps) {
  const total = calm + watch + risk
  // Guard the empty cohort: total === 0 would make every percentage NaN → invalid `width: NaN%`.
  const pct = (n: number) => (total > 0 ? (n / total) * 100 : 0)
  const calmPercent = pct(calm)
  const watchPercent = pct(watch)
  const riskPercent = pct(risk)

  return (
    <Card className={cn("border-[0.5px] border-border shadow-sm", className)}>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          Risk Distribution
        </CardTitle>
      </CardHeader>
      <CardContent>
        {/* Stacked bar */}
        <div className="flex h-3 rounded-full overflow-hidden mb-4">
          <div
            className="bg-status-calm transition-all duration-500"
            style={{ width: `${calmPercent}%` }}
          />
          <div
            className="bg-status-watch transition-all duration-500"
            style={{ width: `${watchPercent}%` }}
          />
          <div
            className="bg-status-risk transition-all duration-500"
            style={{ width: `${riskPercent}%` }}
          />
        </div>

        {/* Legend */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-status-calm" />
            <span className="text-xs text-muted-foreground">Calm</span>
            <span className="font-mono text-xs font-medium ml-1">{calm}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-status-watch" />
            <span className="text-xs text-muted-foreground">Watch</span>
            <span className="font-mono text-xs font-medium ml-1">{watch}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-status-risk" />
            <span className="text-xs text-muted-foreground">At-Risk</span>
            <span className="font-mono text-xs font-medium ml-1">{risk}</span>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

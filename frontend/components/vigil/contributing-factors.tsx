"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"

interface Factor {
  name: string
  impact: number // 0-100
  description?: string
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
        <CardTitle className="text-sm font-medium text-foreground">
          Contributing Factors
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {sortedFactors.map((factor, index) => (
          <div key={factor.name} className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-sm text-foreground">{factor.name}</span>
              <span className="font-mono text-sm text-muted-foreground">
                {factor.impact}%
              </span>
            </div>
            <div className="h-2 w-full bg-muted rounded-full overflow-hidden">
              <div
                className={cn(
                  "h-full rounded-full transition-all duration-500",
                  getBarColor(factor.impact)
                )}
                style={{ width: `${factor.impact}%` }}
              />
            </div>
            {factor.description && (
              <p className="text-xs text-muted-foreground">{factor.description}</p>
            )}
          </div>
        ))}
        
        {sortedFactors.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-4">
            No contributing factors available
          </p>
        )}
      </CardContent>
    </Card>
  )
}

"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"

interface TrafficSegment {
  label: string
  value: number
  color: "calm" | "watch" | "muted"
}

interface TrafficSplitBarProps {
  segments: TrafficSegment[]
  title?: string
  className?: string
}

const colorMap = {
  calm: "bg-status-calm",
  watch: "bg-status-watch",
  muted: "bg-muted-foreground/30",
}

const textColorMap = {
  calm: "text-status-calm",
  watch: "text-status-watch",
  muted: "text-muted-foreground",
}

export function TrafficSplitBar({ segments, title = "Traffic Split", className }: TrafficSplitBarProps) {
  const total = segments.reduce((sum, s) => sum + s.value, 0)
  
  return (
    <Card className={cn("border-[0.5px] border-border shadow-sm", className)}>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium text-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Bar */}
        <div className="h-3 w-full bg-muted rounded-full overflow-hidden flex">
          {segments.map((segment, i) => {
            const percentage = (segment.value / total) * 100
            return (
              <div
                key={segment.label}
                className={cn(
                  "h-full transition-all duration-500",
                  colorMap[segment.color],
                  i === 0 && "rounded-l-full",
                  i === segments.length - 1 && "rounded-r-full"
                )}
                style={{ width: `${percentage}%` }}
              />
            )
          })}
        </div>
        
        {/* Legend */}
        <div className="flex flex-wrap gap-6">
          {segments.map(segment => {
            const percentage = ((segment.value / total) * 100).toFixed(1)
            return (
              <div key={segment.label} className="flex items-center gap-2">
                <div className={cn("h-2 w-2 rounded-full", colorMap[segment.color])} />
                <span className="text-sm text-muted-foreground">{segment.label}</span>
                <span className={cn("font-mono text-sm font-medium", textColorMap[segment.color])}>
                  {percentage}%
                </span>
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}

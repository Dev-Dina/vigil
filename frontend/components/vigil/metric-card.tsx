"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import { StatusDot, StatusType } from "./status-dot"

interface MetricCardProps {
  /** Card heading. `label` is accepted as an alias (callers use either). */
  title?: string
  label?: string
  value: string | number
  subtitle?: string
  status?: StatusType
  trend?: {
    value: number
    label: string
  }
  className?: string
}

export function MetricCard({
  title,
  label,
  value,
  subtitle,
  status,
  trend,
  className,
}: MetricCardProps) {
  const heading = title ?? label
  return (
    <Card
      className={cn(
        "border-[0.5px] border-border shadow-sm transition-colors duration-200 hover:border-foreground/15",
        className,
      )}
    >
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
          {status && <StatusDot status={status} size="sm" />}
          {heading}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="font-mono text-2xl font-semibold text-foreground text-right">
          {value}
        </div>
        {subtitle && (
          <p className="text-xs text-muted-foreground mt-1 text-right">{subtitle}</p>
        )}
        {trend && (
          <div className="flex items-center justify-end gap-1 mt-2">
            <span
              className={cn(
                "font-mono text-xs font-medium",
                trend.value >= 0 ? "text-status-calm" : "text-status-risk"
              )}
            >
              {trend.value >= 0 ? "+" : ""}
              {trend.value}%
            </span>
            <span className="text-xs text-muted-foreground">{trend.label}</span>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

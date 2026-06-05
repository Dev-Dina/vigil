"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import { StatusDot, StatusType } from "./status-dot"

interface BudgetAlert {
  id: string
  message: string
  status: StatusType
  timestamp: string
}

interface BudgetAlertListProps {
  alerts: BudgetAlert[]
  className?: string
}

export function BudgetAlertList({ alerts, className }: BudgetAlertListProps) {
  return (
    <Card className={cn("border-[0.5px] border-border shadow-sm", className)}>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium text-foreground flex items-center gap-2">
          <svg
            className="h-4 w-4 text-muted-foreground"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={1.5}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0"
            />
          </svg>
          Budget Alerts
        </CardTitle>
      </CardHeader>
      <CardContent>
        {alerts.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <div className="h-10 w-10 rounded-full bg-status-calm/10 flex items-center justify-center mb-3">
              <svg
                className="h-5 w-5 text-status-calm"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={1.5}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M4.5 12.75l6 6 9-13.5"
                />
              </svg>
            </div>
            <p className="text-sm text-muted-foreground">No active alerts</p>
            <p className="text-xs text-muted-foreground mt-1">
              Budget thresholds are within normal range
            </p>
          </div>
        ) : (
          <ul className="space-y-3">
            {alerts.map(alert => (
              <li
                key={alert.id}
                className="flex items-start gap-3 p-3 rounded-md bg-muted/50 border-[0.5px] border-border"
              >
                <StatusDot status={alert.status} size="md" className="mt-1" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-foreground">{alert.message}</p>
                  <p className="text-xs text-muted-foreground font-mono mt-1">
                    {alert.timestamp}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

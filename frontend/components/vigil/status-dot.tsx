"use client"

import { cn } from "@/lib/utils"

export type StatusType = "calm" | "watch" | "risk"

interface StatusDotProps {
  status: StatusType
  size?: "sm" | "md" | "lg"
  pulse?: boolean
  className?: string
}

const statusColors: Record<StatusType, string> = {
  calm: "bg-status-calm",
  watch: "bg-status-watch",
  risk: "bg-status-risk",
}

const sizeClasses: Record<"sm" | "md" | "lg", string> = {
  sm: "h-1.5 w-1.5",
  md: "h-2 w-2",
  lg: "h-2.5 w-2.5",
}

export function StatusDot({ status, size = "md", pulse = false, className }: StatusDotProps) {
  return (
    <span
      className={cn(
        "inline-block rounded-full shrink-0",
        statusColors[status],
        sizeClasses[size],
        pulse && "animate-pulse",
        className
      )}
      aria-label={`Status: ${status}`}
    />
  )
}

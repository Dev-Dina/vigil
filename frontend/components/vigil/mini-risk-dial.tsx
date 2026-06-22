"use client"

import { cn, statusFromScore, type RiskStatus } from "@/lib/utils"

interface MiniRiskDialProps {
  value: number
  size?: number
  className?: string
  /** The authoritative status (from the backend risk_band). When provided, it drives the arc color
   *  so the dial can never contradict the participant's band; falls back to aligned score cuts. */
  status?: RiskStatus
}

export function MiniRiskDial({ value, size = 24, className, status }: MiniRiskDialProps) {
  const clampedValue = Math.max(0, Math.min(100, value))
  
  const centerX = size / 2
  const centerY = size * 0.7
  const radius = size * 0.38
  const strokeWidth = size * 0.12

  const startAngle = 180
  const endAngle = 0
  const valueAngle = startAngle - (clampedValue / 100) * 180

  const polarToCartesian = (angle: number) => {
    const radians = (angle * Math.PI) / 180
    return {
      x: centerX + radius * Math.cos(radians),
      y: centerY - radius * Math.sin(radians),
    }
  }

  const start = polarToCartesian(startAngle)
  const end = polarToCartesian(endAngle)
  const valuePoint = polarToCartesian(valueAngle)

  const backgroundArc = `M ${start.x} ${start.y} A ${radius} ${radius} 0 0 1 ${end.x} ${end.y}`
  const valueArc = `M ${start.x} ${start.y} A ${radius} ${radius} 0 0 1 ${valuePoint.x} ${valuePoint.y}`

  // Color from the authoritative band-derived status when given; else aligned score cuts (>60/>30).
  const resolvedStatus = status ?? statusFromScore(clampedValue)
  const strokeColor =
    resolvedStatus === "risk"
      ? "var(--status-risk)"
      : resolvedStatus === "watch"
        ? "var(--status-watch)"
        : "var(--status-calm)"

  return (
    <svg
      width={size}
      height={size * 0.6}
      viewBox={`0 0 ${size} ${size * 0.75}`}
      className={cn("flex-shrink-0", className)}
    >
      <path
        d={backgroundArc}
        fill="none"
        stroke="#E8E6E0"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
      />
      {clampedValue > 0 && (
        <path
          d={valueArc}
          fill="none"
          stroke={strokeColor}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
        />
      )}
    </svg>
  )
}

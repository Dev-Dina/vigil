"use client"

import { cn } from "@/lib/utils"

interface SparklineProps {
  data: number[]
  className?: string
  width?: number
  height?: number
}

function getStatusFromValue(value: number): "calm" | "watch" | "risk" {
  if (value >= 70) return "risk"
  if (value >= 40) return "watch"
  return "calm"
}

export function Sparkline({
  data,
  className,
  width = 56,
  height = 20,
}: SparklineProps) {
  if (data.length < 2) return null

  const padding = 2
  const effectiveWidth = width - padding * 2
  const effectiveHeight = height - padding * 2

  const min = Math.min(...data)
  const max = Math.max(...data)
  const range = max - min || 1

  const points = data.map((value, index) => {
    const x = padding + (index / (data.length - 1)) * effectiveWidth
    const y = padding + effectiveHeight - ((value - min) / range) * effectiveHeight
    return `${x},${y}`
  })

  const pathD = `M ${points.join(" L ")}`
  const lastValue = data[data.length - 1]
  const status = getStatusFromValue(lastValue)

  const strokeColor = {
    calm: "var(--status-calm)",
    watch: "var(--status-watch)",
    risk: "var(--status-risk)",
  }[status]

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("flex-shrink-0", className)}
    >
      <path
        d={pathD}
        fill="none"
        stroke={strokeColor}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle
        cx={padding + effectiveWidth}
        cy={padding + effectiveHeight - ((lastValue - min) / range) * effectiveHeight}
        r={2}
        fill={strokeColor}
      />
    </svg>
  )
}

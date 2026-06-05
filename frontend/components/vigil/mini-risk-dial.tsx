"use client"

import { cn } from "@/lib/utils"

interface MiniRiskDialProps {
  value: number
  size?: number
  className?: string
}

export function MiniRiskDial({ value, size = 24, className }: MiniRiskDialProps) {
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

  let strokeColor: string
  if (clampedValue >= 70) {
    strokeColor = "var(--status-risk)"
  } else if (clampedValue >= 40) {
    strokeColor = "var(--status-watch)"
  } else {
    strokeColor = "var(--status-calm)"
  }

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

"use client"

import { cn } from "@/lib/utils"
import { StatusType } from "./status-dot"

interface RiskDialProps {
  value: number // 0-100
  size?: "sm" | "md" | "lg"
  showLabel?: boolean
  className?: string
}

const sizeConfig = {
  sm: { width: 80, height: 48, strokeWidth: 6, fontSize: "text-lg" },
  md: { width: 120, height: 72, strokeWidth: 8, fontSize: "text-2xl" },
  lg: { width: 160, height: 96, strokeWidth: 10, fontSize: "text-3xl" },
}

function getStatus(value: number): StatusType {
  if (value < 40) return "calm"
  if (value < 70) return "watch"
  return "risk"
}

const statusColors: Record<StatusType, string> = {
  calm: "#17A07A",
  watch: "#BA7517",
  risk: "#D85A30",
}

export function RiskDial({ value, size = "md", showLabel = true, className }: RiskDialProps) {
  const config = sizeConfig[size]
  const status = getStatus(value)
  const color = statusColors[status]
  
  // Semicircle calculations
  const radius = (config.width - config.strokeWidth) / 2
  const centerX = config.width / 2
  const centerY = config.height - 4
  
  // Arc path for the background
  const arcPath = `M ${config.strokeWidth / 2} ${centerY} A ${radius} ${radius} 0 0 1 ${config.width - config.strokeWidth / 2} ${centerY}`
  
  // Calculate the arc length
  const circumference = Math.PI * radius
  
  // Progress as percentage of semicircle
  const progress = (value / 100) * circumference
  
  return (
    <div className={cn("flex flex-col items-center", className)}>
      <svg
        width={config.width}
        height={config.height}
        viewBox={`0 0 ${config.width} ${config.height}`}
        className="overflow-visible"
      >
        {/* Background arc */}
        <path
          d={arcPath}
          fill="none"
          stroke="#E8E6E0"
          strokeWidth={config.strokeWidth}
          strokeLinecap="round"
        />
        {/* Progress arc */}
        <path
          d={arcPath}
          fill="none"
          stroke={color}
          strokeWidth={config.strokeWidth}
          strokeLinecap="round"
          strokeDasharray={`${progress} ${circumference}`}
          className="transition-all duration-700 ease-out"
        />
        {/* Center value */}
        <text
          x={centerX}
          y={centerY - 8}
          textAnchor="middle"
          className={cn("font-mono font-semibold fill-foreground", config.fontSize)}
          style={{ fontSize: size === "sm" ? "18px" : size === "md" ? "24px" : "32px" }}
        >
          {value}
        </text>
      </svg>
      {showLabel && (
        <span className="text-xs text-muted-foreground mt-1 uppercase tracking-wide">
          Risk Score
        </span>
      )}
    </div>
  )
}

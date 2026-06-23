"use client"

import { cn, statusFromScore, type RiskStatus } from "@/lib/utils"
import { StatusType } from "./status-dot"

interface RiskDialProps {
  value: number | null // 0-100; null = loading/no score yet → renders "—", never a confident "0"
  size?: "sm" | "md" | "lg"
  showLabel?: boolean
  className?: string
  /** The authoritative status (from the backend risk_band) when the caller has a banded participant.
   *  Omitted for a score-only aggregate (e.g. the cohort MEAN), which falls back to aligned cuts. */
  status?: RiskStatus
}

const sizeConfig = {
  sm: { width: 80, height: 48, strokeWidth: 6, fontSize: "text-lg" },
  md: { width: 120, height: 72, strokeWidth: 8, fontSize: "text-2xl" },
  lg: { width: 160, height: 96, strokeWidth: 10, fontSize: "text-3xl" },
}

const statusColors: Record<StatusType, string> = {
  calm: "#17A07A",
  watch: "#BA7517",
  risk: "#D85A30",
}

export function RiskDial({ value, size = "md", showLabel = true, className, status }: RiskDialProps) {
  const config = sizeConfig[size]
  // null = loading / no score yet → empty state ("—" + no progress arc), so the dial never shows a
  // confident "0" while data is loading (matching the "—" metric cards beside it).
  const isEmpty = value == null
  const numeric = value ?? 0
  // Authoritative band-derived status when given; else cuts ALIGNED to the backend bands (>60/>30)
  // so a score-only dial never contradicts a banded chip elsewhere on the same participant.
  const resolvedStatus = status ?? statusFromScore(numeric)
  const color = statusColors[resolvedStatus]
  
  // Semicircle calculations
  const radius = (config.width - config.strokeWidth) / 2
  const centerX = config.width / 2
  const centerY = config.height - 4
  
  // Arc path for the background
  const arcPath = `M ${config.strokeWidth / 2} ${centerY} A ${radius} ${radius} 0 0 1 ${config.width - config.strokeWidth / 2} ${centerY}`
  
  // Calculate the arc length
  const circumference = Math.PI * radius
  
  // Progress as percentage of semicircle (0 when empty → the colored arc is invisible).
  const progress = isEmpty ? 0 : (numeric / 100) * circumference
  
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
          className={cn(
            "font-mono font-semibold",
            isEmpty ? "fill-muted-foreground" : "fill-foreground",
            config.fontSize
          )}
          style={{ fontSize: size === "sm" ? "18px" : size === "md" ? "24px" : "32px" }}
        >
          {isEmpty ? "—" : numeric}
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

import { cn } from "../../lib/utils"

export type RiskBand = "low" | "medium" | "high" | "critical"

interface RiskBandBadgeProps {
  band: RiskBand
  marker?: "synthetic" | "none"
  className?: string
}

const BAND_STYLES: Record<RiskBand, string> = {
  low: "border-emerald-200 bg-emerald-50 text-emerald-800",
  medium: "border-amber-200 bg-amber-50 text-amber-800",
  high: "border-orange-200 bg-orange-50 text-orange-800",
  critical: "border-red-200 bg-red-50 text-red-800",
}

const BAND_LABELS: Record<RiskBand, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  critical: "Critical",
}

export function RiskBandBadge({
  band,
  marker = "none",
  className,
}: RiskBandBadgeProps) {
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <span
        className={cn(
          "inline-flex h-6 items-center rounded-md border px-2 text-xs font-medium",
          BAND_STYLES[band]
        )}
      >
        {BAND_LABELS[band]}
      </span>
      {marker === "synthetic" && (
        <span
          className="inline-flex h-6 items-center rounded-md border border-slate-300 bg-slate-50 px-2 text-xs font-medium text-slate-700"
        >
          Synthetic / non-clinical
        </span>
      )}
    </span>
  )
}

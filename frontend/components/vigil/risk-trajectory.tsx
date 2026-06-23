"use client"

import { cn } from "@/lib/utils"
import type { RiskHistoryPoint } from "@/lib/types"

// Champion risk trajectory (H2b semantic b). The series is CROSS-VERSION: after a model
// promotion the model_version changes mid-history. UI honesty (Wire-3): a version change MUST
// be VISIBLE — never one smooth line that hides the promotion. We split the line into segments
// per consecutive model_version, draw a dashed boundary + version label at each change, and
// render synthetic points distinctly (hollow/dashed) from real ones (filled). Empty → honest
// empty state, never a fabricated line.

interface RiskTrajectoryProps {
  points: RiskHistoryPoint[]
  height?: number
  className?: string
}

function bandColor(score: number): string {
  if (score < 0.4) return "#17A07A"
  if (score < 0.7) return "#BA7517"
  return "#D85A30"
}

// Indices where model_version differs from the previous point — the promotion boundaries.
export function versionBoundaryIndices(points: RiskHistoryPoint[]): number[] {
  const out: number[] = []
  for (let i = 1; i < points.length; i++) {
    if (points[i].model_version !== points[i - 1].model_version) out.push(i)
  }
  return out
}

export function RiskTrajectory({ points, height = 160, className }: RiskTrajectoryProps) {
  if (points.length === 0) {
    return (
      <div
        className={cn(
          "rounded border border-border bg-card px-4 py-6 text-center text-sm text-muted-foreground",
          className,
        )}
        data-testid="risk-trajectory-empty"
      >
        No champion risk history yet for this participant.
      </div>
    )
  }

  const padding = { top: 16, right: 16, bottom: 28, left: 36 }
  const width = 600
  const chartW = width - padding.left - padding.right
  const chartH = height - padding.top - padding.bottom

  const n = points.length
  const xScale = (i: number) =>
    padding.left + (n === 1 ? chartW / 2 : (i / (n - 1)) * chartW)
  const yScale = (score: number) =>
    padding.top + chartH - Math.max(0, Math.min(1, score)) * chartH

  // Split into segments of consecutive identical model_version so each is its own polyline
  // (a visible break at every promotion boundary — not one continuous flattened line).
  const segments: { from: number; to: number; version: string }[] = []
  let start = 0
  for (let i = 1; i <= n; i++) {
    if (i === n || points[i].model_version !== points[start].model_version) {
      segments.push({ from: start, to: i - 1, version: points[start].model_version })
      start = i
    }
  }

  const boundaries = versionBoundaryIndices(points)

  return (
    <div className={cn("w-full", className)} data-testid="risk-trajectory">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" preserveAspectRatio="xMidYMid meet">
        {/* version-change boundaries: dashed vertical line + version label */}
        {boundaries.map((bi) => (
          <g key={`b-${bi}`} data-testid="version-boundary">
            <line
              x1={xScale(bi)}
              y1={padding.top}
              x2={xScale(bi)}
              y2={padding.top + chartH}
              stroke="currentColor"
              strokeWidth={1}
              strokeDasharray="3 3"
              className="text-muted-foreground"
            />
            <text
              x={xScale(bi)}
              y={padding.top + chartH + 18}
              textAnchor="middle"
              className="fill-muted-foreground font-mono text-[9px]"
            >
              → {points[bi].model_version}
            </text>
          </g>
        ))}

        {/* one polyline per version segment (break at each boundary) */}
        {segments.map((seg, si) => {
          const segPoints = points.slice(seg.from, seg.to + 1)
          const d = segPoints
            .map((p, k) => `${k === 0 ? "M" : "L"} ${xScale(seg.from + k)} ${yScale(p.risk_score)}`)
            .join(" ")
          const color = bandColor(points[seg.to].risk_score)
          return (
            <path
              key={`s-${si}`}
              d={d}
              fill="none"
              stroke={color}
              strokeWidth={1.5}
              data-version={seg.version}
            />
          )
        })}

        {/* per-point markers: synthetic = hollow ring (distinguishable from real = filled) */}
        {points.map((p, i) => (
          <circle
            key={`p-${i}`}
            cx={xScale(i)}
            cy={yScale(p.risk_score)}
            r={3}
            fill={p.synthetic ? "var(--card, #fff)" : bandColor(p.risk_score)}
            stroke={bandColor(p.risk_score)}
            strokeWidth={p.synthetic ? 1.5 : 0}
            strokeDasharray={p.synthetic ? "2 1" : undefined}
            data-synthetic={p.synthetic}
          />
        ))}

        {/* A single history point has no line to draw — label the lone dot so it reads as one
            observation, not a broken chart. (The zero-point empty state is handled above.) */}
        {n === 1 && (
          <text
            x={xScale(0)}
            y={yScale(points[0].risk_score) - 10}
            textAnchor="middle"
            className="fill-muted-foreground text-[9px]"
            data-testid="risk-trajectory-single"
          >
            single observation
          </text>
        )}
      </svg>
      <div className="mt-1 flex items-center gap-4 px-2 text-[10px] text-muted-foreground">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full border border-current" /> synthetic point
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full bg-current" /> real point
        </span>
        {boundaries.length > 0 && <span>dashed line = model promotion (version change)</span>}
      </div>
    </div>
  )
}

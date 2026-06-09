import { cn } from "../../lib/utils"

interface RiskTrendSparklineProps {
  scores: number[]
  className?: string
  width?: number
  height?: number
}

export function RiskTrendSparkline({
  scores,
  className,
  width = 88,
  height = 28,
}: RiskTrendSparklineProps) {
  const padding = 3
  const effectiveWidth = width - padding * 2
  const effectiveHeight = height - padding * 2
  const min = Math.min(...scores)
  const max = Math.max(...scores)
  const range = max - min || 1
  const points = scores.map((score, index) => {
    const x =
      scores.length === 1
        ? width / 2
        : padding + (index / (scores.length - 1)) * effectiveWidth
    const y = padding + effectiveHeight - ((score - min) / range) * effectiveHeight
    return { x, y }
  })
  const pathD = points.map((point) => `${point.x},${point.y}`).join(" L ")
  const lastPoint = points.at(-1)

  if (scores.length === 0 || !lastPoint) {
    return (
      <span className={cn("text-xs text-muted-foreground", className)}>
        No trend
      </span>
    )
  }

  return (
    <svg
      role="img"
      aria-label="Risk trend"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("block shrink-0", className)}
    >
      {scores.length > 1 && (
        <path
          d={`M ${pathD}`}
          fill="none"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={1.75}
          className="text-slate-700"
        />
      )}
      <circle
        cx={lastPoint.x}
        cy={lastPoint.y}
        r={2.25}
        className="fill-slate-700"
      />
    </svg>
  )
}

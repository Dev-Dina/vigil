"use client"

import { cn } from "@/lib/utils"

interface DataPoint {
  date: string
  value: number
}

interface RiskLineChartProps {
  data: DataPoint[]
  height?: number
  className?: string
}

function getColor(value: number): string {
  if (value < 40) return "#17A07A"
  if (value < 70) return "#BA7517"
  return "#D85A30"
}

export function RiskLineChart({ data, height = 200, className }: RiskLineChartProps) {
  if (data.length === 0) return null
  
  const padding = { top: 20, right: 16, bottom: 32, left: 40 }
  const width = 600
  const chartWidth = width - padding.left - padding.right
  const chartHeight = height - padding.top - padding.bottom
  
  const maxValue = Math.max(...data.map(d => d.value), 100)
  const minValue = 0
  
  const xScale = (index: number) => padding.left + (index / (data.length - 1)) * chartWidth
  const yScale = (value: number) => padding.top + chartHeight - ((value - minValue) / (maxValue - minValue)) * chartHeight
  
  // Create path
  const pathD = data
    .map((d, i) => `${i === 0 ? "M" : "L"} ${xScale(i)} ${yScale(d.value)}`)
    .join(" ")
  
  // Create gradient path (area under line)
  const areaPathD = `${pathD} L ${xScale(data.length - 1)} ${padding.top + chartHeight} L ${padding.left} ${padding.top + chartHeight} Z`
  
  const latestValue = data[data.length - 1]?.value ?? 0
  const lineColor = getColor(latestValue)
  
  // Y-axis ticks
  const yTicks = [0, 25, 50, 75, 100]
  
  return (
    <div className={cn("w-full", className)}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          <linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={lineColor} stopOpacity="0.15" />
            <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
          </linearGradient>
        </defs>
        
        {/* Grid lines */}
        {yTicks.map(tick => (
          <g key={tick}>
            <line
              x1={padding.left}
              y1={yScale(tick)}
              x2={width - padding.right}
              y2={yScale(tick)}
              stroke="#E8E6E0"
              strokeWidth="0.5"
            />
            <text
              x={padding.left - 8}
              y={yScale(tick)}
              textAnchor="end"
              dominantBaseline="middle"
              className="font-mono text-[10px] fill-muted-foreground"
            >
              {tick}
            </text>
          </g>
        ))}
        
        {/* Area fill */}
        <path d={areaPathD} fill="url(#areaGradient)" />
        
        {/* Line */}
        <path
          d={pathD}
          fill="none"
          stroke={lineColor}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        
        {/* Data points */}
        {data.map((d, i) => (
          <circle
            key={i}
            cx={xScale(i)}
            cy={yScale(d.value)}
            r="3"
            fill="white"
            stroke={getColor(d.value)}
            strokeWidth="2"
          />
        ))}
        
        {/* X-axis labels */}
        {data.map((d, i) => (
          i % Math.ceil(data.length / 6) === 0 && (
            <text
              key={i}
              x={xScale(i)}
              y={height - 8}
              textAnchor="middle"
              className="font-mono text-[10px] fill-muted-foreground"
            >
              {d.date}
            </text>
          )
        ))}
      </svg>
    </div>
  )
}

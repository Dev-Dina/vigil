"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"

interface DataPoint {
  date: string
  value: number
}

interface SpendChartProps {
  data: DataPoint[]
  title?: string
  height?: number
  className?: string
}

export function SpendChart({ data, title = "Daily Spend", height = 140, className }: SpendChartProps) {
  if (data.length === 0) return null
  
  const padding = { top: 16, right: 16, bottom: 28, left: 48 }
  const width = 500
  const chartWidth = width - padding.left - padding.right
  const chartHeight = height - padding.top - padding.bottom
  
  const maxValue = Math.max(...data.map(d => d.value)) * 1.1
  const minValue = 0
  
  const xScale = (index: number) => padding.left + (index / (data.length - 1)) * chartWidth
  const yScale = (value: number) => padding.top + chartHeight - ((value - minValue) / (maxValue - minValue)) * chartHeight
  
  // Create path
  const pathD = data
    .map((d, i) => `${i === 0 ? "M" : "L"} ${xScale(i)} ${yScale(d.value)}`)
    .join(" ")
  
  // Create gradient path (area under line)
  const areaPathD = `${pathD} L ${xScale(data.length - 1)} ${padding.top + chartHeight} L ${padding.left} ${padding.top + chartHeight} Z`
  
  // Y-axis ticks
  const yTickCount = 4
  const yTicks = Array.from({ length: yTickCount }, (_, i) => 
    Math.round((maxValue / (yTickCount - 1)) * i)
  )
  
  return (
    <Card className={cn("border-[0.5px] border-border shadow-sm", className)}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="w-full"
          preserveAspectRatio="xMidYMid meet"
        >
          <defs>
            <linearGradient id="spendGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#15323A" stopOpacity="0.1" />
              <stop offset="100%" stopColor="#15323A" stopOpacity="0" />
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
                className="font-mono text-[9px] fill-muted-foreground"
              >
                ${tick}
              </text>
            </g>
          ))}
          
          {/* Area fill */}
          <path d={areaPathD} fill="url(#spendGradient)" />
          
          {/* Line */}
          <path
            d={pathD}
            fill="none"
            stroke="#15323A"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          
          {/* Data points */}
          {data.map((d, i) => (
            <circle
              key={i}
              cx={xScale(i)}
              cy={yScale(d.value)}
              r="2.5"
              fill="white"
              stroke="#15323A"
              strokeWidth="1.5"
            />
          ))}
          
          {/* X-axis labels */}
          {data.map((d, i) => (
            i % Math.ceil(data.length / 5) === 0 && (
              <text
                key={i}
                x={xScale(i)}
                y={height - 6}
                textAnchor="middle"
                className="font-mono text-[9px] fill-muted-foreground"
              >
                {d.date}
              </text>
            )
          ))}
        </svg>
      </CardContent>
    </Card>
  )
}

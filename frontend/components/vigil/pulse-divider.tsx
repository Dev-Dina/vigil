"use client"

import { cn } from "@/lib/utils"

interface PulseDividerProps {
  className?: string
}

export function PulseDivider({ className }: PulseDividerProps) {
  return (
    <div className={cn("relative w-full h-px my-6", className)}>
      {/* Base line */}
      <div className="absolute inset-0 bg-border" />
      {/* Pulse effect */}
      <svg
        className="absolute inset-0 w-full h-6 -top-3"
        viewBox="0 0 400 24"
        preserveAspectRatio="none"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path
          d="M0 12 H150 L160 4 L170 20 L180 8 L190 16 L200 12 H400"
          stroke="currentColor"
          strokeWidth="1"
          className="text-border"
          fill="none"
        />
        <path
          d="M0 12 H150 L160 4 L170 20 L180 8 L190 16 L200 12 H400"
          stroke="currentColor"
          strokeWidth="1"
          className="text-status-calm opacity-60"
          fill="none"
          strokeDasharray="4 4"
        >
          <animate
            attributeName="stroke-dashoffset"
            from="8"
            to="0"
            dur="1s"
            repeatCount="indefinite"
          />
        </path>
      </svg>
    </div>
  )
}
